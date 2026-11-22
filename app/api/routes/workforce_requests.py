"""Role-gated workforce hiring requests with manual fee review and no phone leakage."""
import re
from datetime import timedelta, timezone
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_active_role
from app.api.routes.workforce import _has_current_role_entitlement
from app.audit import record_audit_event
from app.core.config import settings
from app.db import get_db
from app.models import (
    User,
    UserPhoneVerification,
    UserRoleEntitlement,
    WorkforceProfile,
    WorkforceServicePayment,
    WorkforceServiceRequest,
    utc_now,
)

router = APIRouter(tags=["workforce requests"])
PHONE_VERIFICATION_MAX_AGE = timedelta(days=30)
PHONE_PATTERN = re.compile(r"\+[1-9][0-9]{7,14}\Z")
SERVICE_FEE_MINOR = 5000
SERVICE_CURRENCY = "PKR"
CLIENT_TERMS_VERSION = "workforce-client-service-v1"
PROVIDER_TERMS_VERSION = "workforce-provider-service-v1"
MAX_MINOR_UNITS = 9_000_000_000_000_000


class WorkforceServiceRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_type: Literal["hire_worker", "hire_contractor"]
    title: str = Field(min_length=2, max_length=180)
    details: str = Field(min_length=10, max_length=3000)
    city: str = Field(min_length=2, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    country_code: str = Field(default="PK", min_length=2, max_length=2)
    budget_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR_UNITS)
    client_terms_accepted: bool = False

    @field_validator("title", "city", "details")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Value must contain at least two non-space characters")
        return normalized

    @field_validator("region")
    @classmethod
    def normalize_region(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("Country code must be two ASCII letters")
        return normalized


class WorkforceServicePaymentSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_method: Literal["bank_transfer", "cash_deposit"]
    payment_reference: str = Field(min_length=3, max_length=160)

    @field_validator("payment_reference")
    @classmethod
    def normalize_reference(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Payment reference is required")
        return normalized


class WorkforceServicePaymentReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["verify", "reject"]
    evidence_checked: bool = False
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class WorkforceServiceAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_profile_id: str = Field(min_length=1, max_length=36)


class WorkforceProviderStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "in_progress", "completed", "cancelled", "disputed"]
    provider_terms_accepted: bool = False


def _require_verified_email(user: User) -> None:
    if not user.email_verified or not user.is_active:
        raise HTTPException(status_code=403, detail="Active verified email required")


def _require_platform_admin(user: User) -> None:
    allowed = {
        item.strip().lower()
        for item in settings.platform_admin_emails.split(",")
        if item.strip()
    }
    if not user.email_verified or not user.is_active or user.email.strip().lower() not in allowed:
        raise HTTPException(status_code=403, detail="Verified platform administrator required")


def _fresh_verified_phone_at(db: Session, user: User):
    verification = db.get(UserPhoneVerification, user.id)
    if verification is None or not PHONE_PATTERN.fullmatch(verification.phone_e164):
        raise HTTPException(status_code=403, detail="A provider-verified phone number is required")
    verified_at = verification.verified_at
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    now = utc_now()
    if verified_at > now or now - verified_at > PHONE_VERIFICATION_MAX_AGE:
        raise HTTPException(status_code=403, detail="Phone verification must be no older than 30 days")
    return verified_at


def _provider_profile(db: Session, user: User, role_key: str) -> WorkforceProfile:
    profile = db.scalar(
        select(WorkforceProfile).where(
            WorkforceProfile.user_id == user.id,
            WorkforceProfile.role_key == role_key,
            WorkforceProfile.moderation_status == "published",
        )
    )
    if profile is None:
        raise HTTPException(status_code=403, detail="Published workforce profile required")
    return profile


def _request_payload(item: WorkforceServiceRequest) -> dict:
    return {
        "id": item.id,
        "request_code": item.request_code,
        "service_type": item.service_type,
        "title": item.title,
        "details": item.details,
        "city": item.city,
        "region": item.region,
        "country_code": item.country_code,
        "budget_minor": item.budget_minor,
        "currency": item.currency,
        "provider_profile_id": item.provider_profile_id,
        "status": item.status,
        "platform_fee_minor": item.platform_fee_minor,
        "platform_fee_currency": item.platform_fee_currency,
        "platform_fee_status": item.platform_fee_status,
        "client_phone_verified_at": item.client_phone_verified_at,
        "client_terms_version": item.client_terms_version,
        "client_terms_accepted_at": item.client_terms_accepted_at,
        "provider_terms_version": item.provider_terms_version,
        "provider_terms_accepted_at": item.provider_terms_accepted_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _admin_request_payload(db: Session, item: WorkforceServiceRequest) -> dict:
    payload = _request_payload(item)
    payload["client_user_id"] = item.client_user_id
    payment = db.scalar(
        select(WorkforceServicePayment)
        .where(WorkforceServicePayment.service_request_id == item.id)
        .order_by(WorkforceServicePayment.created_at.desc())
    )
    payload["latest_payment"] = None if payment is None else {
        "id": payment.id,
        "status": payment.status,
        "payment_method": payment.payment_method,
        "payment_reference": payment.payment_reference,
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "created_at": payment.created_at,
    }
    return payload


def _audit_request(db: Session, user: User, action: str, item: WorkforceServiceRequest) -> None:
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action=action,
        entity_type="workforce_service_request",
        entity_id=item.id,
        details={
            "status": item.status,
            "payment_status": item.platform_fee_status,
            "amount_minor": item.platform_fee_minor,
            "currency": item.platform_fee_currency,
        },
    )


@router.post(
    "/me/workforce/service-requests",
    status_code=status.HTTP_201_CREATED,
)
def create_workforce_service_request(
    payload: WorkforceServiceRequestCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_verified_email(user)
    if not payload.client_terms_accepted:
        raise HTTPException(status_code=409, detail="Client service terms must be accepted")
    phone_verified_at = _fresh_verified_phone_at(db, user)
    now = utc_now()
    item = WorkforceServiceRequest(
        request_code=f"HJZ-J-{uuid4().hex[:12].upper()}",
        client_user_id=user.id,
        service_type=payload.service_type,
        title=payload.title,
        details=payload.details,
        city=payload.city,
        region=payload.region,
        country_code=payload.country_code,
        budget_minor=payload.budget_minor,
        currency=SERVICE_CURRENCY,
        client_phone_verified_at=phone_verified_at,
        client_terms_version=CLIENT_TERMS_VERSION,
        client_terms_accepted_at=now,
        platform_fee_minor=SERVICE_FEE_MINOR,
        platform_fee_currency=SERVICE_CURRENCY,
        platform_fee_status="pending",
        status="new",
    )
    db.add(item)
    db.flush()
    _audit_request(db, user, "workforce.service_request.created", item)
    db.commit()
    db.refresh(item)
    return {"request": _request_payload(item)}


@router.get("/me/workforce/service-requests")
def list_my_workforce_service_requests(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    items = db.scalars(
        select(WorkforceServiceRequest)
        .where(WorkforceServiceRequest.client_user_id == user.id)
        .order_by(WorkforceServiceRequest.created_at.desc())
    ).all()
    return [_request_payload(item) for item in items]


@router.post("/me/workforce/service-requests/{request_id}/payment")
def submit_workforce_service_payment(
    request_id: str,
    payload: WorkforceServicePaymentSubmit,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_verified_email(user)
    item = db.scalar(
        select(WorkforceServiceRequest)
        .where(
            WorkforceServiceRequest.id == request_id,
            WorkforceServiceRequest.client_user_id == user.id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Service request not found")
    if item.status != "new" or item.platform_fee_status not in {"pending", "rejected"}:
        raise HTTPException(status_code=409, detail="Only a new request awaiting payment can accept fee evidence")
    payment = WorkforceServicePayment(
        service_request_id=item.id,
        amount_minor=item.platform_fee_minor,
        currency=item.platform_fee_currency,
        status="submitted",
        payment_method=payload.payment_method,
        payment_reference=payload.payment_reference,
    )
    item.platform_fee_status = "submitted"
    item.updated_at = utc_now()
    db.add(payment)
    try:
        db.flush()
        _audit_request(db, user, "workforce.service_fee.submitted", item)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An active service-fee payment already exists") from None
    db.refresh(item)
    return {
        "request": _request_payload(item),
        "payment": {
            "id": payment.id,
            "status": payment.status,
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
        },
    }


@router.get("/platform/workforce/service-requests/review-queue")
def list_workforce_service_request_queue(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    fee_status: Literal["pending", "submitted", "verified", "rejected"] = Query(
        default="submitted"
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    _require_platform_admin(user)
    items = db.scalars(
        select(WorkforceServiceRequest)
        .where(WorkforceServiceRequest.platform_fee_status == fee_status)
        .order_by(WorkforceServiceRequest.created_at.asc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [_admin_request_payload(db, item) for item in items]


@router.post("/platform/workforce/service-requests/{request_id}/payment-review")
def review_workforce_service_payment(
    request_id: str,
    payload: WorkforceServicePaymentReview,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = db.scalar(
        select(WorkforceServiceRequest)
        .where(WorkforceServiceRequest.id == request_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Service request not found")
    payment = db.scalar(
        select(WorkforceServicePayment)
        .where(
            WorkforceServicePayment.service_request_id == item.id,
            WorkforceServicePayment.status == "submitted",
        )
        .order_by(WorkforceServicePayment.created_at.desc())
        .with_for_update()
    )
    if payment is None:
        raise HTTPException(status_code=409, detail="Submitted service-fee evidence not found")
    if not payload.evidence_checked:
        raise HTTPException(status_code=409, detail="Evidence review must be confirmed")
    verified = payload.decision == "verify"
    if verified and item.status == "cancelled":
        raise HTTPException(status_code=409, detail="A cancelled request cannot be fee-verified")
    payment.status = "verified" if verified else "rejected"
    payment.reviewer_user_id = user.id
    payment.reviewer_notes = payload.notes
    payment.reviewed_at = utc_now()
    payment.verified_at = utc_now() if verified else None
    item.platform_fee_status = "verified" if verified else "rejected"
    item.updated_at = utc_now()
    _audit_request(db, user, "workforce.service_fee.reviewed", item)
    db.commit()
    db.refresh(item)
    return {
        "request": _request_payload(item),
        "payment": {
            "id": payment.id,
            "status": payment.status,
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
        },
    }


@router.post("/platform/workforce/service-requests/{request_id}/assign")
def assign_workforce_service_provider(
    request_id: str,
    payload: WorkforceServiceAssignment,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = db.scalar(
        select(WorkforceServiceRequest)
        .where(WorkforceServiceRequest.id == request_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Service request not found")
    if item.platform_fee_status != "verified" or item.status != "new":
        raise HTTPException(status_code=409, detail="Verified fee and a new request are required for assignment")
    if item.provider_profile_id is not None:
        raise HTTPException(status_code=409, detail="Service request is already assigned")
    profile = db.get(WorkforceProfile, payload.provider_profile_id)
    if (
        profile is None
        or profile.user_id == item.client_user_id
        or profile.moderation_status != "published"
        or profile.availability != "available"
        or not _has_current_role_entitlement(db, profile)
    ):
        raise HTTPException(status_code=409, detail="A published, available provider with a current role is required")
    item.provider_profile_id = profile.id
    item.status = "matched"
    item.assigned_at = utc_now()
    item.updated_at = utc_now()
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="workforce.service_request.assigned",
        entity_type="workforce_service_request",
        entity_id=item.id,
        details={"status": item.status, "role_key": profile.role_key},
    )
    db.commit()
    db.refresh(item)
    return {"request": _request_payload(item)}


def _list_provider_requests(
    role_key: str,
    user: User,
    db: Session,
) -> list[dict]:
    profile = _provider_profile(db, user, role_key)
    items = db.scalars(
        select(WorkforceServiceRequest)
        .where(
            WorkforceServiceRequest.provider_profile_id == profile.id,
            WorkforceServiceRequest.platform_fee_status == "verified",
        )
        .order_by(WorkforceServiceRequest.created_at.desc())
    ).all()
    return [_request_payload(item) for item in items]


def _update_provider_request(
    role_key: str,
    request_id: str,
    payload: WorkforceProviderStatusRequest,
    user: User,
    db: Session,
) -> dict:
    profile = _provider_profile(db, user, role_key)
    item = db.scalar(
        select(WorkforceServiceRequest)
        .where(
            WorkforceServiceRequest.id == request_id,
            WorkforceServiceRequest.provider_profile_id == profile.id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Assigned service request not found")
    if item.platform_fee_status != "verified":
        raise HTTPException(status_code=409, detail="Verified service fee is required")
    target = payload.status
    if target == item.status:
        return {"request": _request_payload(item)}
    allowed = {
        "matched": {"accepted", "cancelled", "disputed"},
        "accepted": {"in_progress", "cancelled", "disputed"},
        "in_progress": {"completed", "disputed"},
        "completed": set(),
        "cancelled": set(),
        "disputed": set(),
        "new": set(),
    }
    if target not in allowed.get(item.status, set()):
        raise HTTPException(status_code=409, detail="Invalid service-request state transition")
    now = utc_now()
    if target == "accepted":
        if not payload.provider_terms_accepted:
            raise HTTPException(status_code=409, detail="Provider service terms must be accepted")
        item.provider_terms_version = PROVIDER_TERMS_VERSION
        item.provider_terms_accepted_at = now
        item.accepted_at = now
    elif target == "in_progress":
        item.started_at = now
    elif target == "completed":
        item.completed_at = now
    elif target == "cancelled":
        item.cancelled_at = now
    elif target == "disputed":
        item.disputed_at = now
    item.status = target
    item.updated_at = now
    _audit_request(db, user, "workforce.service_request.provider_transition", item)
    db.commit()
    db.refresh(item)
    return {"request": _request_payload(item)}


@router.get("/me/workforce/contractor-service-requests")
def list_contractor_service_requests(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("contractor"))],
) -> list[dict]:
    return _list_provider_requests("contractor", user, db)


@router.get("/me/workforce/mason-service-requests")
def list_mason_service_requests(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("mason"))],
) -> list[dict]:
    return _list_provider_requests("mason", user, db)


@router.get("/me/workforce/labour-service-requests")
def list_labour_service_requests(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("labour"))],
) -> list[dict]:
    return _list_provider_requests("labour", user, db)


@router.post("/me/workforce/contractor-service-requests/{request_id}/status")
def update_contractor_service_request(
    request_id: str,
    payload: WorkforceProviderStatusRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("contractor"))],
) -> dict:
    return _update_provider_request("contractor", request_id, payload, user, db)


@router.post("/me/workforce/mason-service-requests/{request_id}/status")
def update_mason_service_request(
    request_id: str,
    payload: WorkforceProviderStatusRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("mason"))],
) -> dict:
    return _update_provider_request("mason", request_id, payload, user, db)


@router.post("/me/workforce/labour-service-requests/{request_id}/status")
def update_labour_service_request(
    request_id: str,
    payload: WorkforceProviderStatusRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("labour"))],
) -> dict:
    return _update_provider_request("labour", request_id, payload, user, db)


@router.post("/me/workforce/service-requests/{request_id}/cancel")
def cancel_my_workforce_service_request(
    request_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.scalar(
        select(WorkforceServiceRequest)
        .where(
            WorkforceServiceRequest.id == request_id,
            WorkforceServiceRequest.client_user_id == user.id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Service request not found")
    if item.status == "cancelled":
        return {"request": _request_payload(item)}
    if item.status not in {"new", "matched"}:
        raise HTTPException(status_code=409, detail="Accepted work must be disputed, not silently cancelled")
    item.status = "cancelled"
    item.cancelled_at = utc_now()
    item.updated_at = utc_now()
    _audit_request(db, user, "workforce.service_request.client_cancelled", item)
    db.commit()
    db.refresh(item)
    return {"request": _request_payload(item)}


@router.post("/me/workforce/service-requests/{request_id}/dispute")
def dispute_my_workforce_service_request(
    request_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.scalar(
        select(WorkforceServiceRequest)
        .where(
            WorkforceServiceRequest.id == request_id,
            WorkforceServiceRequest.client_user_id == user.id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Service request not found")
    if item.status == "disputed":
        return {"request": _request_payload(item)}
    if item.status not in {"accepted", "in_progress", "completed"}:
        raise HTTPException(status_code=409, detail="Only accepted or completed work can be disputed")
    item.status = "disputed"
    item.disputed_at = utc_now()
    item.updated_at = utc_now()
    _audit_request(db, user, "workforce.service_request.client_disputed", item)
    db.commit()
    db.refresh(item)
    return {"request": _request_payload(item)}
