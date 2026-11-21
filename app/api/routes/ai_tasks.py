"""Tenant-scoped API for durable AI task submission and inspection."""
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.agents.schemas import AgentTaskInput
from app.ai.model_router import ModelRouteError, create_model_router
from app.ai.runtime.schemas import AITaskEventResponse, AITaskResponse, CreateAITaskRequest
from app.ai.runtime.tasks import (
    TaskConflict,
    TaskStateConflict,
    create_task,
    request_task_cancel,
)
from app.api.deps import require_membership
from app.core.config import settings
from app.db import get_db
from app.models import AITask, AITaskEvent, Membership

router = APIRouter(tags=["ai-agent-runtime"])


def _authorize_task(task: AITask | None, organization_id: str, membership: Membership) -> AITask:
    if task is None or task.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="AI task not found")
    if task.created_by_user_id != membership.user_id and membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=404, detail="AI task not found")
    return task


@router.post(
    "/organizations/{organization_id}/ai/tasks",
    response_model=AITaskResponse,
    status_code=202,
)
def submit_task(
    payload: CreateAITaskRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AITaskResponse:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required to submit AI tasks")
    input_payload = payload.input_payload
    if payload.task_type == "agent.run":
        try:
            agent_input = AgentTaskInput.model_validate(input_payload)
        except ValidationError:
            raise HTTPException(status_code=422, detail="Invalid agent task input") from None
        if not settings.ai_live_calls_enabled:
            raise HTTPException(status_code=503, detail="Live AI agent runs are disabled by configuration")
        if not settings.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise HTTPException(status_code=503, detail="AI agent tasks require PostgreSQL")
        if settings.ai_daily_request_limit < 1:
            raise HTTPException(status_code=503, detail="A positive AI daily request limit must be configured")
        if agent_input.max_output_tokens > settings.ai_max_output_tokens:
            raise HTTPException(status_code=422, detail="Agent output token limit exceeds configured maximum")
        try:
            router = create_model_router(settings)
            router.select(
                route=agent_input.model_route,
                task_type=agent_input.task_type,
                data_class=agent_input.data_class,
                max_output_tokens=agent_input.max_output_tokens,
            )
        except ModelRouteError as exc:
            code = 503 if "not configured" in str(exc).lower() else 422
            raise HTTPException(status_code=code, detail=str(exc)) from None
        except ValueError:
            raise HTTPException(status_code=503, detail="AI model provider configuration is incomplete") from None
        input_payload = agent_input.model_dump(mode="json")
    try:
        task, created = create_task(
            db,
            organization_id=membership.organization_id,
            actor_user_id=membership.user_id,
            task_type=payload.task_type,
            title=payload.title,
            input_payload=input_payload,
            idempotency_key=idempotency_key,
            max_attempts=1 if payload.task_type == "agent.run" else payload.max_attempts,
        )
    except TaskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return AITaskResponse.model_validate(task).model_copy(update={"created": created})


@router.get(
    "/organizations/{organization_id}/ai/tasks/{task_id}",
    response_model=AITaskResponse,
)
def get_task(
    organization_id: str,
    task_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AITaskResponse:
    task = db.scalar(select(AITask).where(
        AITask.id == task_id,
        AITask.organization_id == membership.organization_id,
    ))
    return AITaskResponse.model_validate(_authorize_task(task, organization_id, membership))


@router.get(
    "/organizations/{organization_id}/ai/tasks/{task_id}/events",
    response_model=list[AITaskEventResponse],
)
def get_task_events(
    organization_id: str,
    task_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AITaskEventResponse]:
    task = db.scalar(select(AITask).where(
        AITask.id == task_id,
        AITask.organization_id == membership.organization_id,
    ))
    _authorize_task(task, organization_id, membership)
    events = db.scalars(
        select(AITaskEvent).where(
            AITaskEvent.task_id == task_id,
            AITaskEvent.organization_id == membership.organization_id,
        ).order_by(desc(AITaskEvent.created_at), desc(AITaskEvent.id)).limit(limit)
    ).all()
    return [AITaskEventResponse.model_validate(event) for event in events]


@router.post(
    "/organizations/{organization_id}/ai/tasks/{task_id}/cancel",
    response_model=AITaskResponse,
)
def cancel_task(
    organization_id: str,
    task_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AITaskResponse:
    task = db.scalar(select(AITask).where(
        AITask.id == task_id,
        AITask.organization_id == membership.organization_id,
    ))
    _authorize_task(task, organization_id, membership)
    try:
        updated = request_task_cancel(
            db,
            task_id=task_id,
            organization_id=membership.organization_id,
            actor_user_id=membership.user_id,
            role=membership.role,
        )
    except TaskStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return AITaskResponse.model_validate(updated)
