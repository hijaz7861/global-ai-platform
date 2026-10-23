"""Owner/admin access to tenant-scoped audit events."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_membership
from app.db import get_db
from app.models import AuditEvent, Membership

router = APIRouter(tags=["audit"])


class AuditEventResponse(BaseModel):
    id: str
    actor_user_id: str | None
    organization_id: str | None
    action: str
    entity_type: str
    entity_id: str
    details: dict[str, Any]
    created_at: Any

    model_config = ConfigDict(from_attributes=True)


@router.get("/organizations/{organization_id}/audit-events", response_model=list[AuditEventResponse])
def list_organization_audit_events(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEventResponse]:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.organization_id == membership.organization_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [AuditEventResponse.model_validate(event) for event in events]
