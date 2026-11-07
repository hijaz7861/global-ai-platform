"""Tenant-scoped API for durable AI task submission and inspection."""
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.runtime.schemas import AITaskEventResponse, AITaskResponse, CreateAITaskRequest
from app.ai.runtime.tasks import (
    TaskConflict,
    TaskStateConflict,
    cancel_queued_task,
    create_task,
)
from app.api.deps import require_membership
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
    try:
        task, created = create_task(
            db,
            organization_id=membership.organization_id,
            actor_user_id=membership.user_id,
            task_type=payload.task_type,
            title=payload.title,
            input_payload=payload.input_payload,
            idempotency_key=idempotency_key,
            max_attempts=payload.max_attempts,
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
        cancel_queued_task(
            db,
            task_id=task_id,
            organization_id=membership.organization_id,
            actor_user_id=membership.user_id,
        )
    except TaskStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    updated = db.get(AITask, task_id)
    assert updated is not None
    return AITaskResponse.model_validate(updated)
