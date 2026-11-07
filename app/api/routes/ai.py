"""Budget-gated AI chat endpoint; live provider calls are disabled by default."""
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.model_router import ModelRouteError, create_model_router
from app.ai.providers import ProviderError
from app.api.deps import require_membership
from app.audit import record_audit_event
from app.core.config import settings
from app.db import get_db
from app.models import AuditEvent, Membership, Organization
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(tags=["ai-orchestration"])


@router.post("/organizations/{organization_id}/ai/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatResponse:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required to use AI services")
    if not settings.ai_live_calls_enabled:
        raise HTTPException(status_code=503, detail="Live AI calls are disabled by configuration")
    if settings.ai_daily_request_limit < 1:
        raise HTTPException(status_code=503, detail="A positive AI daily request limit must be configured")
    if payload.max_output_tokens > settings.ai_max_output_tokens:
        raise HTTPException(
            status_code=422,
            detail=f"max_output_tokens cannot exceed configured limit ({settings.ai_max_output_tokens})",
        )
    try:
        model_router = create_model_router(settings)
    except ValueError as exc:
        detail = (
            "AI fallback provider configuration is incomplete"
            if "fallback" in str(exc).lower()
            else "AI provider configuration is incomplete"
        )
        raise HTTPException(status_code=503, detail=detail) from None
    try:
        model_router.select(
            route=payload.provider,
            task_type=payload.task_type,
            data_class=payload.data_class,
            max_output_tokens=payload.max_output_tokens,
        )
    except ModelRouteError as exc:
        code = 503 if "not configured" in str(exc).lower() else 422
        raise HTTPException(status_code=code, detail=str(exc)) from None

    if not settings.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise HTTPException(
            status_code=503,
            detail="Live AI calls require PostgreSQL to enforce the organization quota safely",
        )

    organization = db.scalar(
        select(Organization)
        .where(Organization.id == membership.organization_id)
        .with_for_update()
    )
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    used = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.organization_id == membership.organization_id,
            AuditEvent.action == "ai.request.started",
            AuditEvent.created_at >= cutoff,
        )
    ) or 0
    if used >= settings.ai_daily_request_limit:
        db.rollback()
        raise HTTPException(status_code=429, detail="Organization AI request limit reached")

    request_id = str(uuid4())
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="ai.request.started",
        entity_type="ai_request",
        entity_id=request_id,
        details={"status": "started"},
    )
    db.commit()

    try:
        result = await model_router.complete(
            route=payload.provider,
            task_type=payload.task_type,
            data_class=payload.data_class,
            prompt=payload.prompt,
            max_output_tokens=payload.max_output_tokens,
        )
    except ValueError:
        record_audit_event(
            db,
            actor_user_id=membership.user_id,
            organization_id=membership.organization_id,
            action="ai.request.failed",
            entity_type="ai_request",
            entity_id=request_id,
            details={"status": "configuration_error"},
        )
        db.commit()
        raise HTTPException(status_code=503, detail="AI provider configuration is invalid") from None
    except ProviderError as exc:
        record_audit_event(
            db,
            actor_user_id=membership.user_id,
            organization_id=membership.organization_id,
            action="ai.request.failed",
            entity_type="ai_request",
            entity_id=request_id,
            details={"status": "failed"},
        )
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from None

    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="ai.request.completed",
        entity_type="ai_request",
        entity_id=request_id,
        details={"status": "completed", "quantity": result["output_tokens"], "provider": result["provider"], "model": result["model"]},
    )
    db.commit()
    return ChatResponse(
        request_id=request_id,
        provider=result["provider"],
        model=result["model"],
        content=result["content"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
    )
