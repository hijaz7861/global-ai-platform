"""Allowlisted, transaction-bound audit event recording."""
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent

_ALLOWED_DETAIL_KEYS = {
    "status", "quantity", "price_minor", "total_minor", "currency", "role",
    "membership_count", "buyer_order_count",
}


def record_audit_event(
    db: Session,
    *,
    actor_user_id: str | None,
    organization_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit event to the current transaction without logging secrets or request bodies."""
    safe_details: dict[str, str | int | bool] = {}
    for key, value in (details or {}).items():
        if key in _ALLOWED_DETAIL_KEYS and isinstance(value, (str, int, bool)):
            safe_details[key] = value
    db.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=safe_details,
        )
    )
