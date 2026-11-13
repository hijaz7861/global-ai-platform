"""Idempotent expiry of due active and suspended role entitlements."""
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.models import RoleEntitlementEvent, UserRoleEntitlement, utc_now


def expire_due_role_entitlements(
    db: Session,
    *,
    limit: int = 100,
    now: datetime | None = None,
    actor_user_id: str | None = None,
) -> list[str]:
    """Expire a bounded batch and append immutable event/audit records atomically."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    current_time = now or utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    due_entitlements = db.scalars(
        select(UserRoleEntitlement)
        .where(
            UserRoleEntitlement.status.in_({"active", "suspended"}),
            UserRoleEntitlement.expires_at.is_not(None),
            UserRoleEntitlement.expires_at <= current_time,
        )
        .order_by(UserRoleEntitlement.expires_at, UserRoleEntitlement.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()

    expired_ids: list[str] = []
    for entitlement in due_entitlements:
        previous_status = entitlement.status
        result = db.execute(
            update(UserRoleEntitlement)
            .where(
                UserRoleEntitlement.id == entitlement.id,
                UserRoleEntitlement.status.in_({"active", "suspended"}),
                UserRoleEntitlement.expires_at.is_not(None),
                UserRoleEntitlement.expires_at <= current_time,
            )
            .values(status="expired", updated_at=current_time)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue

        db.add(
            RoleEntitlementEvent(
                entitlement_id=entitlement.id,
                actor_user_id=actor_user_id,
                event_type="expired",
                details={
                    "role_key": entitlement.role_key,
                    "previous_status": previous_status,
                    "expired_at": current_time.isoformat(),
                },
            )
        )
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            organization_id=entitlement.organization_id,
            action="role_entitlement.expired",
            entity_type="user_role_entitlement",
            entity_id=entitlement.id,
            details={"role_key": entitlement.role_key, "previous_status": previous_status},
        )
        expired_ids.append(entitlement.id)

    return expired_ids
