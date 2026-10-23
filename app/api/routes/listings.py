"""Tenant-scoped catalog listings and inventory adjustments."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.models import Listing, Membership
from app.schemas import InventoryAdjustmentRequest, ListingCreate, ListingResponse

router = APIRouter(tags=["catalog"])


@router.post(
    "/organizations/{organization_id}/listings",
    response_model=ListingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_listing(
    payload: ListingCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> ListingResponse:
    listing = Listing(
        organization_id=membership.organization_id,
        title=payload.title.strip(),
        description=payload.description.strip(),
        price_minor=payload.price_minor,
        currency=payload.currency,
        inventory_quantity=payload.inventory_quantity,
        status="draft",
    )
    db.add(listing)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="listing.created",
        entity_type="listing",
        entity_id=listing.id,
        details={
            "price_minor": listing.price_minor,
            "currency": listing.currency,
            "quantity": listing.inventory_quantity,
            "status": listing.status,
        },
    )
    db.commit()
    db.refresh(listing)
    return ListingResponse.model_validate(listing)


@router.get("/organizations/{organization_id}/listings", response_model=list[ListingResponse])
def list_organization_listings(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ListingResponse]:
    listings = db.scalars(
        select(Listing)
        .where(Listing.organization_id == membership.organization_id)
        .order_by(Listing.created_at.desc())
    ).all()
    return [ListingResponse.model_validate(item) for item in listings]


@router.post(
    "/organizations/{organization_id}/listings/{listing_id}/inventory-adjustments",
    response_model=ListingResponse,
)
def adjust_inventory(
    listing_id: str,
    payload: InventoryAdjustmentRequest,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> ListingResponse:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    listing = db.scalar(
        select(Listing).where(
            Listing.id == listing_id,
            Listing.organization_id == membership.organization_id,
        )
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    result = db.execute(
        update(Listing)
        .where(
            Listing.id == listing.id,
            Listing.organization_id == membership.organization_id,
            Listing.inventory_quantity + payload.delta >= 0,
        )
        .values(inventory_quantity=Listing.inventory_quantity + payload.delta)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Inventory cannot be reduced below zero")

    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="inventory.adjusted",
        entity_type="listing",
        entity_id=listing.id,
        details={"quantity": payload.delta, "status": "adjusted"},
    )
    db.commit()
    db.refresh(listing)
    return ListingResponse.model_validate(listing)


@router.post(
    "/organizations/{organization_id}/listings/{listing_id}/publish",
    response_model=ListingResponse,
)
def publish_listing(
    listing_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> ListingResponse:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    listing = db.scalar(
        select(Listing).where(
            Listing.id == listing_id,
            Listing.organization_id == membership.organization_id,
        )
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.status = "active"
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="listing.published",
        entity_type="listing",
        entity_id=listing.id,
        details={"status": listing.status},
    )
    db.commit()
    db.refresh(listing)
    return ListingResponse.model_validate(listing)


@router.get("/listings", response_model=list[ListingResponse])
def browse_active_listings(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
    min_price_minor: Annotated[int | None, Query(ge=0, le=9_000_000_000_000_000)] = None,
    max_price_minor: Annotated[int | None, Query(ge=0, le=9_000_000_000_000_000)] = None,
) -> list[ListingResponse]:
    """Browse only published listings with bounded filters and stable pagination."""
    if min_price_minor is not None and max_price_minor is not None and min_price_minor > max_price_minor:
        raise HTTPException(status_code=422, detail="Minimum price must not exceed maximum price")
    statement = select(Listing).where(Listing.status == "active")
    if q is not None:
        search_term = q.strip()
        if not search_term:
            raise HTTPException(status_code=422, detail="Search query must not be blank")
        statement = statement.where(
            Listing.title.ilike(f"%{search_term}%")
            | Listing.description.ilike(f"%{search_term}%")
        )
    if currency is not None:
        statement = statement.where(Listing.currency == currency.upper())
    if min_price_minor is not None:
        statement = statement.where(Listing.price_minor >= min_price_minor)
    if max_price_minor is not None:
        statement = statement.where(Listing.price_minor <= max_price_minor)
    results = db.scalars(
        statement.order_by(Listing.created_at.desc(), Listing.id).limit(limit).offset(offset)
    ).all()
    return [ListingResponse.model_validate(item) for item in results]
