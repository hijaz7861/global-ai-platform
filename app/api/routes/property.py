"""User-owned property listings, bilateral sale confirmation and manual payment review."""

import re
from datetime import timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.audit import record_audit_event
from app.core.config import settings
from app.db import get_db
from app.models import (
    PropertyListing, PropertyPayment, PropertyTransaction, User, UserPhoneVerification, utc_now,
)
from app.schemas import (
    PropertyListingCreate, PropertyListingOwnerResponse, PropertyListingResponse,
    PropertyListingReviewRequest, PropertyPaymentAdminResponse, PropertyPaymentResponse,
    PropertyPaymentReviewRequest, PropertyPaymentSubmitRequest, PropertySaleConfirmRequest,
    PropertyTransactionResponse,
)
from app.services.property_commissions import (
    DEFAULT_PROPERTY_LISTING_FEE_MINOR, PROPERTY_CURRENCY, calculate_property_commissions,
)

router = APIRouter(tags=["property"])
PHONE_VERIFICATION_MAX_AGE = timedelta(days=30)
MAX_PROPERTY_AMOUNT_MINOR = 9_000_000_000_000_000
_PROPERTY_PHONE_PATTERN = re.compile(r"\\+[1-9][0-9]{7,14}\\Z")


def _require_verified_email(user: User) -> None:
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Verified email required")


def _require_platform_admin(user: User) -> None:
    allowed = {
        item.strip().lower()
        for item in settings.platform_admin_emails.split(",")
        if item.strip()
    }
    if not user.email_verified or user.email.strip().lower() not in allowed:
        raise HTTPException(status_code=403, detail="Verified platform administrator required")


def _fresh_verified_phone(db: Session, user: User) -> UserPhoneVerification:
    verification = db.get(UserPhoneVerification, user.id)
    if verification is None or not _PROPERTY_PHONE_PATTERN.fullmatch(verification.phone_e164):
        raise HTTPException(status_code=403, detail="A provider-verified phone number is required")
    verified_at = verification.verified_at
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    now = utc_now()
    if verified_at > now or now - verified_at > PHONE_VERIFICATION_MAX_AGE:
        raise HTTPException(status_code=403, detail="Phone verification must be no older than 30 days")
    return verification


def _listing_payload(item: PropertyListing) -> PropertyListingResponse:
    return PropertyListingResponse.model_validate(item)


def _owner_listing_payload(item: PropertyListing) -> PropertyListingOwnerResponse:
    return PropertyListingOwnerResponse.model_validate(item)


def _payment_payload(item: PropertyPayment) -> PropertyPaymentResponse:
    return PropertyPaymentResponse.model_validate(item)


def _transaction_payload(item: PropertyTransaction) -> PropertyTransactionResponse:
    return PropertyTransactionResponse.model_validate(item)


def _seller_declaration(dispute_status: str) -> str:
    if dispute_status == "seller_declared_non_disputed":
        return "Seller declares no known dispute; not independently verified."
    if dispute_status == "disputed":
        return "Seller declares a property dispute."
    return "Seller has not confirmed dispute status."


@router.get("/properties", response_model=list[PropertyListingResponse])
def search_properties(
    city: str | None = Query(default=None, max_length=100),
    state_region: str | None = Query(default=None, max_length=100),
    country: str | None = Query(default=None, max_length=100),
    property_type: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[PropertyListingResponse]:
    statement = select(PropertyListing).where(PropertyListing.listing_status == "published")
    if city and city.strip():
        statement = statement.where(func.lower(PropertyListing.city) == city.strip().lower())
    if state_region and state_region.strip():
        statement = statement.where(func.lower(PropertyListing.state_region) == state_region.strip().lower())
    if country and country.strip():
        statement = statement.where(func.lower(PropertyListing.country) == country.strip().lower())
    if property_type and property_type.strip():
        statement = statement.where(PropertyListing.property_type == property_type.strip().lower())
    rows = db.scalars(statement.order_by(PropertyListing.created_at.desc()).limit(limit)).all()
    return [_listing_payload(item) for item in rows]


@router.post(
    "/me/property-listings",
    status_code=status.HTTP_201_CREATED,
)
def create_property_listing(
    payload: PropertyListingCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_verified_email(user)
    phone = _fresh_verified_phone(db, user)
    item = PropertyListing(
        listing_code=f"HJZ-P-{uuid4().hex[:12].upper()}",
        seller_user_id=user.id,
        seller_phone=phone.phone_e164,
        property_type=payload.property_type,
        title=payload.title.strip(),
        description=payload.description or "",
        city=payload.city.strip(),
        state_region=payload.state_region,
        country=payload.country.strip(),
        locality=payload.locality,
        address_private=payload.address_private,
        latitude=payload.latitude,
        longitude=payload.longitude,
        area_value=payload.area_value,
        area_unit=payload.area_unit.strip(),
        asking_price_minor=payload.asking_price_minor,
        currency=PROPERTY_CURRENCY,
        dispute_status=payload.dispute_status,
        seller_declaration=_seller_declaration(payload.dispute_status),
        title_verification_status="not_verified",
        listing_fee_minor=DEFAULT_PROPERTY_LISTING_FEE_MINOR,
        listing_fee_status="pending",
        listing_status="pending_fee",
    )
    db.add(item)
    db.flush()
    payment = PropertyPayment(
        listing_id=item.id,
        payer_user_id=user.id,
        payer_role="seller",
        payment_type="listing_fee",
        amount_minor=DEFAULT_PROPERTY_LISTING_FEE_MINOR,
        currency=PROPERTY_CURRENCY,
        status="pending",
    )
    db.add(payment)
    record_audit_event(
        db, actor_user_id=user.id, organization_id=None,
        action="property_listing.created", entity_type="property_listing", entity_id=item.id,
        details={
            "status": "pending_fee", "amount_minor": DEFAULT_PROPERTY_LISTING_FEE_MINOR,
            "currency": PROPERTY_CURRENCY, "property_type": item.property_type,
        },
    )
    db.commit()
    db.refresh(item)
    db.refresh(payment)
    return {
        "listing": _owner_listing_payload(item),
        "listing_fee_payment": _payment_payload(payment),
    }


@router.get("/me/property-listings", response_model=list[PropertyListingOwnerResponse])
def list_my_property_listings(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[PropertyListingOwnerResponse]:
    rows = db.scalars(
        select(PropertyListing)
        .where(PropertyListing.seller_user_id == user.id)
        .order_by(PropertyListing.created_at.desc())
    ).all()
    return [_owner_listing_payload(item) for item in rows]


@router.get("/me/property-payments", response_model=list[PropertyPaymentResponse])
def list_my_property_payments(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[PropertyPaymentResponse]:
    rows = db.scalars(
        select(PropertyPayment)
        .where(PropertyPayment.payer_user_id == user.id)
        .order_by(PropertyPayment.created_at.desc())
    ).all()
    return [_payment_payload(item) for item in rows]


@router.post("/me/property-payments/{payment_id}/submit", response_model=PropertyPaymentResponse)
def submit_property_payment(
    payment_id: str,
    payload: PropertyPaymentSubmitRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PropertyPaymentResponse:
    _require_verified_email(user)
    payment = db.scalar(
        select(PropertyPayment)
        .where(PropertyPayment.id == payment_id, PropertyPayment.payer_user_id == user.id)
        .with_for_update()
    )
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status not in {"pending", "rejected"}:
        raise HTTPException(status_code=409, detail="Only pending or rejected payments can be submitted")
    payment.payment_method = payload.payment_method
    payment.payment_reference = payload.payment_reference
    payment.status = "submitted"
    payment.verified_at = None
    record_audit_event(
        db, actor_user_id=user.id, organization_id=None,
        action="property_payment.submitted", entity_type="property_payment", entity_id=payment.id,
        details={"status": payment.status, "payment_type": payment.payment_type,
                 "amount_minor": payment.amount_minor, "currency": payment.currency},
    )
    db.commit()
    db.refresh(payment)
    return _payment_payload(payment)


@router.post("/properties/{listing_id}/interest", response_model=PropertyTransactionResponse,
             status_code=status.HTTP_201_CREATED)
def register_property_interest(
    listing_id: str,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PropertyTransactionResponse:
    _require_verified_email(user)
    listing = db.scalar(
        select(PropertyListing).where(
            PropertyListing.id == listing_id, PropertyListing.listing_status == "published"
        )
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Property listing not found")
    if listing.seller_user_id == user.id:
        raise HTTPException(status_code=400, detail="A seller cannot register buyer interest in their own listing")
    existing = db.scalar(
        select(PropertyTransaction).where(
            PropertyTransaction.listing_id == listing.id,
            PropertyTransaction.buyer_user_id == user.id,
        )
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _transaction_payload(existing)
    item = PropertyTransaction(
        transaction_code=f"HJZ-PT-{uuid4().hex[:12].upper()}",
        listing_id=listing.id,
        buyer_user_id=user.id,
        seller_user_id=listing.seller_user_id,
        currency=PROPERTY_CURRENCY,
        status="interest",
    )
    db.add(item)
    db.flush()
    record_audit_event(
        db, actor_user_id=user.id, organization_id=None,
        action="property_interest.created", entity_type="property_transaction", entity_id=item.id,
        details={"status": item.status},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(PropertyTransaction).where(
                PropertyTransaction.listing_id == listing.id,
                PropertyTransaction.buyer_user_id == user.id,
            )
        )
        if existing is None:
            raise HTTPException(status_code=409, detail="Property interest could not be registered") from None
        response.status_code = status.HTTP_200_OK
        return _transaction_payload(existing)
    db.refresh(item)
    return _transaction_payload(item)


@router.get("/me/property-transactions", response_model=list[PropertyTransactionResponse])
def list_my_property_transactions(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[PropertyTransactionResponse]:
    rows = db.scalars(
        select(PropertyTransaction)
        .where(or_(
            PropertyTransaction.buyer_user_id == user.id,
            PropertyTransaction.seller_user_id == user.id,
        ))
        .order_by(PropertyTransaction.created_at.desc())
    ).all()
    return [_transaction_payload(item) for item in rows]


@router.post(
    "/me/property-transactions/{transaction_id}/confirm-sale",
    response_model=PropertyTransactionResponse,
)
def confirm_property_sale(
    transaction_id: str,
    payload: PropertySaleConfirmRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PropertyTransactionResponse:
    _require_verified_email(user)
    item = db.scalar(
        select(PropertyTransaction)
        .where(
            PropertyTransaction.id == transaction_id,
            or_(
                PropertyTransaction.buyer_user_id == user.id,
                PropertyTransaction.seller_user_id == user.id,
            ),
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Property transaction not found")
    if item.status in {"cancelled", "disputed", "completed"}:
        raise HTTPException(status_code=409, detail="This property transaction is closed")
    if item.status == "sale_confirmed":
        return _transaction_payload(item)
    listing = db.scalar(
        select(PropertyListing).where(PropertyListing.id == item.listing_id).with_for_update()
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Property listing not found")
    if listing.listing_status != "published":
        raise HTTPException(status_code=409, detail="Property listing is no longer available")
    if (item.final_sale_price_minor is not None
            and item.final_sale_price_minor != payload.final_sale_price_minor):
        raise HTTPException(status_code=409, detail="Buyer and seller must confirm the same final sale price")
    is_buyer = item.buyer_user_id == user.id
    if (is_buyer and item.buyer_confirmed) or (not is_buyer and item.seller_confirmed):
        return _transaction_payload(item)
    item.final_sale_price_minor = payload.final_sale_price_minor
    if is_buyer:
        item.buyer_confirmed = True
    else:
        item.seller_confirmed = True
    if item.buyer_confirmed and item.seller_confirmed:
        commission = calculate_property_commissions(payload.final_sale_price_minor)
        item.buyer_commission_minor = commission.buyer_commission_minor
        item.seller_commission_minor = commission.seller_commission_minor
        item.status = "sale_confirmed"
        for payment_type, payer_role, payer_id, amount in (
            ("buyer_commission", "buyer", item.buyer_user_id, commission.buyer_commission_minor),
            ("seller_commission", "seller", item.seller_user_id, commission.seller_commission_minor),
        ):
            existing = db.scalar(
                select(PropertyPayment).where(
                    PropertyPayment.transaction_id == item.id,
                    PropertyPayment.payment_type == payment_type,
                )
            )
            if existing is None:
                db.add(PropertyPayment(
                    listing_id=item.listing_id, transaction_id=item.id,
                    payer_user_id=payer_id, payer_role=payer_role,
                    payment_type=payment_type, amount_minor=amount,
                    currency=PROPERTY_CURRENCY, status="pending",
                ))
        listing.listing_status = "sold"
    else:
        item.status = "awaiting_confirmation"
    record_audit_event(
        db, actor_user_id=user.id, organization_id=None,
        action="property_sale.confirmed", entity_type="property_transaction", entity_id=item.id,
        details={
            "status": item.status, "amount_minor": payload.final_sale_price_minor,
            "buyer_confirmed": item.buyer_confirmed, "seller_confirmed": item.seller_confirmed,
            "commission_minor": item.buyer_commission_minor + item.seller_commission_minor
            if item.buyer_commission_minor is not None and item.seller_commission_minor is not None else 0,
        },
    )
    db.commit()
    db.refresh(item)
    return _transaction_payload(item)


@router.get("/platform/property-listings", response_model=list[PropertyListingResponse])
def list_platform_property_listings(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[PropertyListingResponse]:
    _require_platform_admin(user)
    rows = db.scalars(
        select(PropertyListing).order_by(PropertyListing.created_at.desc()).limit(500)
    ).all()
    return [_listing_payload(item) for item in rows]


@router.post(
    "/platform/property-listings/{listing_id}/review",
    response_model=PropertyListingResponse,
)
def review_property_listing(
    listing_id: str,
    payload: PropertyListingReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PropertyListingResponse:
    _require_platform_admin(user)
    item = db.scalar(
        select(PropertyListing).where(PropertyListing.id == listing_id).with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Property listing not found")
    if item.listing_fee_status != "paid" or item.listing_status != "pending_review":
        raise HTTPException(status_code=409, detail="A verified listing fee and pending moderation are required")
    item.listing_status = "published" if payload.decision == "publish" else "rejected"
    # Publication is content moderation only. It must never assert title/deed verification.
    item.title_verification_status = "not_verified"
    item.updated_at = utc_now()
    record_audit_event(
        db, actor_user_id=user.id, organization_id=None,
        action="property_listing.moderated", entity_type="property_listing", entity_id=item.id,
        details={"status": item.listing_status, "listing_status": item.listing_status},
    )
    db.commit()
    db.refresh(item)
    return _listing_payload(item)


@router.get("/platform/property-payments", response_model=list[PropertyPaymentAdminResponse])
def list_platform_property_payments(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: str | None = Query(default=None, max_length=16),
) -> list[PropertyPaymentAdminResponse]:
    _require_platform_admin(user)
    statement = select(PropertyPayment)
    if status_filter:
        if status_filter not in {"pending", "submitted", "verified", "rejected"}:
            raise HTTPException(status_code=422, detail="Invalid payment status filter")
        statement = statement.where(PropertyPayment.status == status_filter)
    rows = db.scalars(statement.order_by(PropertyPayment.created_at.desc()).limit(500)).all()
    return [PropertyPaymentAdminResponse.model_validate(item) for item in rows]


@router.post(
    "/platform/property-payments/{payment_id}/review",
    response_model=PropertyPaymentResponse,
)
def review_property_payment(
    payment_id: str,
    payload: PropertyPaymentReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PropertyPaymentResponse:
    _require_platform_admin(user)
    payment = db.scalar(
        select(PropertyPayment).where(PropertyPayment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise HTTPException(status_code=404, detail="Property payment not found")
    if payment.status != "submitted":
        raise HTTPException(status_code=409, detail="Only submitted payment evidence can be reviewed")
    verified = payload.decision == "verify"
    payment.status = "verified" if verified else "rejected"
    payment.reviewer_user_id = user.id
    payment.reviewer_notes = payload.notes
    payment.verified_at = utc_now() if verified else None
    if payment.payment_type == "listing_fee":
        listing = db.scalar(
            select(PropertyListing).where(PropertyListing.id == payment.listing_id).with_for_update()
        )
        if listing is None:
            raise HTTPException(status_code=409, detail="Property listing is missing")
        if verified:
            listing.listing_fee_status = "paid"
            listing.listing_status = "pending_review"
        else:
            listing.listing_fee_status = "rejected"
            listing.listing_status = "pending_fee"
        listing.updated_at = utc_now()
    elif verified and payment.transaction_id is not None:
        outstanding = db.scalar(
            select(func.count(PropertyPayment.id)).where(
                PropertyPayment.transaction_id == payment.transaction_id,
                PropertyPayment.payment_type.in_(["buyer_commission", "seller_commission"]),
                PropertyPayment.status != "verified",
            )
        )
        if outstanding == 0:
            transaction = db.scalar(
                select(PropertyTransaction)
                .where(PropertyTransaction.id == payment.transaction_id)
                .with_for_update()
            )
            if transaction is not None and transaction.status == "sale_confirmed":
                transaction.status = "completed"
                transaction.updated_at = utc_now()
    record_audit_event(
        db, actor_user_id=user.id, organization_id=None,
        action="property_payment.reviewed", entity_type="property_payment", entity_id=payment.id,
        details={
            "status": payment.status, "payment_status": payment.status,
            "payment_type": payment.payment_type, "amount_minor": payment.amount_minor,
            "currency": payment.currency,
        },
    )
    db.commit()
    db.refresh(payment)
    return _payment_payload(payment)
