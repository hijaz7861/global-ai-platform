"""Privileged maintenance operations for scheduled platform tasks."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db import get_db
from app.audit import record_audit_event
from app.models import User, utc_now
from app.services.security_maintenance import cleanup_expired_security_records
from app.schemas import OrderExpirySweepResponse, SecurityCleanupResponse
from app.services.order_expiry import expire_due_orders

router = APIRouter(prefix="/platform", tags=["platform-operations"])


def _is_platform_admin(email: str) -> bool:
    allowed = {item.strip().lower() for item in settings.platform_admin_emails.split(",") if item.strip()}
    return email.strip().lower() in allowed


@router.post("/orders/expire-pending", response_model=OrderExpirySweepResponse)
def expire_pending_orders(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> OrderExpirySweepResponse:
    """Cron-friendly, idempotent sweep; requires a configured platform-admin identity."""
    if not _is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    expired_ids = expire_due_orders(db, limit=limit, actor_user_id=user.id)
    db.commit()
    return OrderExpirySweepResponse(expired_count=len(expired_ids), order_ids=expired_ids)



@router.post("/security/cleanup-expired-tokens", response_model=SecurityCleanupResponse)
def cleanup_expired_tokens(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SecurityCleanupResponse:
    """Remove expired credentials and stale throttle data; retain live refresh families."""
    if not _is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    counts = cleanup_expired_security_records(db)
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="security.expired_records_cleaned",
        entity_type="maintenance_job",
        entity_id="expired-auth-records",
        details={"status": "completed"},
    )
    db.commit()
    return SecurityCleanupResponse(**counts)
