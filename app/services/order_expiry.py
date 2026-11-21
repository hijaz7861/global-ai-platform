"""Idempotent expiry of unpaid orders and stock reservations."""
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.models import Listing, Order, utc_now


def expire_due_orders(
    db: Session,
    *,
    limit: int = 100,
    now: datetime | None = None,
    actor_user_id: str | None = None,
) -> list[str]:
    """Expire due pending orders once and release their stock in the same transaction."""
    current_time = now or utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    due_orders = db.scalars(
        select(Order)
        .where(Order.status == "pending_payment", Order.expires_at <= current_time)
        .order_by(Order.expires_at, Order.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    expired_ids: list[str] = []
    for order in due_orders:
        result = db.execute(
            update(Order)
            .where(
                Order.id == order.id,
                Order.status == "pending_payment",
                Order.expires_at <= current_time,
            )
            .values(status="expired")
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        order.status = "expired"
        organization_id = order.items[0].listing_organization_id if order.items else None
        for item in order.items:
            db.execute(
                update(Listing)
                .where(Listing.id == item.listing_id)
                .values(inventory_quantity=Listing.inventory_quantity + item.quantity)
                .execution_options(synchronize_session=False)
            )
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            action="order.expired",
            entity_type="order",
            entity_id=order.id,
            details={"status": "expired"},
        )
        expired_ids.append(order.id)
    return expired_ids
