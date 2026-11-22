"""Allowlisted, transaction-bound audit event recording."""
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent

_ALLOWED_DETAIL_KEYS = {
    "status", "quantity", "price_minor", "total_minor", "currency", "role",
    "membership_count", "buyer_order_count", "estimated_tokens", "token_estimation",
    "role_key", "previous_status",
    "fee_amount_minor_before", "fee_amount_minor_after",
    "fee_currency_before", "fee_currency_after",
    "min_age_before", "min_age_after",
    "is_active_before", "is_active_after",
    "fee_configured_before", "fee_configured_after",
    "scope", "reason", "provider_state", "consecutive_failures",
    "open_until_epoch", "probe_until_epoch", "error_code",
    "amount_minor", "listing_status", "payment_status", "payment_type",
    "property_type", "commission_minor", "buyer_confirmed", "seller_confirmed",
    "sender_side", "body_length",
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
    created_at: datetime | None = None,
) -> None:
    """Add an audit event to the current transaction without logging secrets or request bodies."""
    safe_details: dict[str, str | int | bool] = {}
    for key, value in (details or {}).items():
        if key in _ALLOWED_DETAIL_KEYS and isinstance(value, (str, int, bool)):
            if key == "scope" and value not in {"user", "organization"}:
                continue
            if key == "reason" and value not in {"user_requested", "expired"}:
                continue
            if key == "previous_status" and value not in {"pending_payment", "pending_review", "active", "rejected", "expired", "suspended", "revoked"}:
                continue
            if key == "provider_state" and value not in {"closed", "open", "half_open"}:
                continue
            if key == "sender_side" and value not in {"client", "provider"}:
                continue
            if key == "body_length" and (
                isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2000
            ):
                continue
            if key == "error_code" and value not in {"", "retryable", "non_retryable", "timeout", "connection", "http_429", "http_5xx", "probe_abandoned"}:
                continue
            if key in {"consecutive_failures", "open_until_epoch", "probe_until_epoch"} and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                continue
            safe_details[key] = value
    event = AuditEvent(
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=safe_details,
    )
    if created_at is not None:
        event.created_at = created_at
    db.add(event)
