"""Privileged maintenance operations for scheduled platform tasks."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db import get_db
from app.models import User
from app.schemas import OrderExpirySweepResponse
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
