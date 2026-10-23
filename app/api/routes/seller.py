"""Seller-facing order views restricted to the seller's organization."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_membership
from app.db import get_db
from app.models import Membership, Order, OrderItem
from app.schemas import JournalLineResponse, OrderItemResponse, OrderResponse

router = APIRouter(tags=["seller-orders"])


@router.get(
    "/organizations/{organization_id}/orders",
    response_model=list[OrderResponse],
)
def list_organization_orders(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[OrderResponse]:
    """Return order summaries for this seller without exposing buyer identity or contact data."""
    orders = db.scalars(
        select(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(OrderItem.listing_organization_id == membership.organization_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
    ).unique().all()
    return [
        OrderResponse(
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
                if item.listing_organization_id == membership.organization_id
            ],
        )
        for order in orders
    ]
