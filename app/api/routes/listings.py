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
) -> list[ListingResponse]:
    listings = db.scalars(
        select(Listing)
        .where(Listing.status == "active")
        .order_by(Listing.created_at.desc(), Listing.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [ListingResponse.model_validate(item) for item in listings]
