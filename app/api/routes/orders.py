"""Authenticated order creation with stock checks, idempotency, and cancellation."""
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.audit import record_audit_event
from app.db import get_db
from app.models import Listing, Order, OrderItem, User
from app.schemas import OrderCreate, OrderItemResponse, OrderResponse

router = APIRouter(prefix="/orders", tags=["orders"])


def _response(order: Order) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        status=order.status,
        total_minor=order.total_minor,
        currency=order.currency,
        created_at=order.created_at,
        items=[
            OrderItemResponse(
                listing_id=item.listing_id,
                title=item.title_snapshot,
                quantity=item.quantity,
                unit_price_minor=item.unit_price_minor,
                line_total_minor=item.quantity * item.unit_price_minor,
            )
            for item in order.items
        ],
    )


def _existing_idempotent_order(
    db: Session, user_id: str, idempotency_key: str, payload: OrderCreate
) -> OrderResponse | None:
    existing = db.scalar(
        select(Order).where(Order.user_id == user_id, Order.idempotency_key == idempotency_key)
    )
    if existing is None:
        return None
    if (
        len(existing.items) != 1
        or existing.items[0].listing_id != payload.listing_id
        or existing.items[0].quantity != payload.quantity
    ):
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for a different order request",
        )
    return _response(existing)


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> OrderResponse:
    existing = _existing_idempotent_order(db, user.id, idempotency_key, payload)
    if existing is not None:
        return existing

    listing = db.scalar(
        select(Listing).where(Listing.id == payload.listing_id, Listing.status == "active")
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Active listing not found")

    stock_update = db.execute(
        update(Listing)
        .where(
            Listing.id == listing.id,
            Listing.status == "active",
            Listing.inventory_quantity >= payload.quantity,
        )
        .values(inventory_quantity=Listing.inventory_quantity - payload.quantity)
        .execution_options(synchronize_session=False)
    )
    if stock_update.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Insufficient inventory")

    order = Order(
        id=str(uuid4()),
        user_id=user.id,
        idempotency_key=idempotency_key,
        status="pending_payment",
        total_minor=listing.price_minor * payload.quantity,
        currency=listing.currency,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            id=str(uuid4()),
            order_id=order.id,
            listing_id=listing.id,
            listing_organization_id=listing.organization_id,
            title_snapshot=listing.title,
            quantity=payload.quantity,
            unit_price_minor=listing.price_minor,
        )
    )
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=listing.organization_id,
        action="order.created",
        entity_type="order",
        entity_id=order.id,
        details={
            "quantity": payload.quantity,
            "price_minor": order.total_minor,
            "currency": order.currency,
            "status": order.status,
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _existing_idempotent_order(db, user.id, idempotency_key, payload)
        if existing is not None:
            return existing
        raise HTTPException(status_code=409, detail="Order could not be created") from None
    db.refresh(order)
    return _response(order)


@router.get("", response_model=list[OrderResponse])
def list_my_orders(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[OrderResponse]:
    orders = db.scalars(
        select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc())
    ).all()
    return [_response(order) for order in orders]


@router.get("/{order_id}", response_model=OrderResponse)
def get_my_order(
    order_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> OrderResponse:
    order = db.scalar(select(Order).where(Order.id == order_id, Order.user_id == user.id))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return _response(order)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
def cancel_my_order(
    order_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> OrderResponse:
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id, Order.user_id == user.id)
        .with_for_update()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status == "cancelled":
        return _response(order)
    if order.status != "pending_payment":
        raise HTTPException(status_code=409, detail="Order can no longer be cancelled")

    organization_id = order.items[0].listing_organization_id if order.items else None
    for item in order.items:
        db.execute(
            update(Listing)
            .where(Listing.id == item.listing_id)
            .values(inventory_quantity=Listing.inventory_quantity + item.quantity)
            .execution_options(synchronize_session=False)
        )
    order.status = "cancelled"
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=organization_id,
        action="order.cancelled",
        entity_type="order",
        entity_id=order.id,
        details={"status": order.status},
    )
    db.commit()
    db.refresh(order)
    return _response(order)
