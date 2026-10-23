"""Seller-facing order views restricted to the seller's organization."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_membership
from app.db import get_db
from app.models import Membership, Order, OrderItem
from app.schemas import SellerOrderItemResponse, SellerOrderResponse

router = APIRouter(tags=["seller-orders"])


@router.get(
    "/organizations/{organization_id}/orders",
    response_model=list[SellerOrderResponse],
)
def list_organization_orders(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[SellerOrderResponse]:
    """Return only this seller's line items, with no buyer identity or cross-seller totals."""
    orders = db.scalars(
        select(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(OrderItem.listing_organization_id == membership.organization_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
    ).unique().all()
    result: list[SellerOrderResponse] = []
    for order in orders:
        own_items = [
            item for item in order.items
            if item.listing_organization_id == membership.organization_id
        ]
        if not own_items:
            continue
        result.append(
            SellerOrderResponse(
                id=order.id,
                status=order.status,
                currency=order.currency,
                created_at=order.created_at,
                subtotal_minor=sum(item.quantity * item.unit_price_minor for item in own_items),
                items=[
                    SellerOrderItemResponse(
                        listing_id=item.listing_id,
                        title=item.title_snapshot,
                        quantity=item.quantity,
                        unit_price_minor=item.unit_price_minor,
                        line_total_minor=item.quantity * item.unit_price_minor,
                    )
                    for item in own_items
                ],
            )
        )
    return result
