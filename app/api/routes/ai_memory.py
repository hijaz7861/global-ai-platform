"""Tenant-scoped AI memory APIs with provenance-aware lexical retrieval."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.ai.memory.schemas import AIMemoryResponse, CreateAIMemoryRequest
from app.ai.memory.service import (
    MemoryNotFound,
    archive_memory,
    create_memory,
    get_memory,
    search_memories,
)
from app.api.deps import require_membership
from app.db import get_db
from app.models import Membership

router = APIRouter(tags=["ai-memory"])


@router.post(
    "/organizations/{organization_id}/ai/memory",
    response_model=AIMemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_memory_record(
    payload: CreateAIMemoryRequest,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AIMemoryResponse:
    if payload.scope == "organization" and membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required for shared memory")
    if payload.sensitivity == "restricted" and membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required for restricted memory")
    if payload.supersedes_id is not None and membership.role not in {"owner", "admin"}:
        if payload.scope == "organization":
            raise HTTPException(status_code=403, detail="Owner or admin role required to supersede shared memory")
    try:
        record = create_memory(
            db,
            actor_user_id=membership.user_id,
            scope=payload.scope,
            organization_id=membership.organization_id if payload.scope == "organization" else None,
            memory_type=payload.memory_type,
            content=payload.content,
            source_type=payload.source_type,
            source_ref=payload.source_ref,
            confidence_percent=payload.confidence_percent,
            sensitivity=payload.sensitivity,
            expires_at=payload.expires_at,
            supersedes_id=payload.supersedes_id,
        )
    except MemoryNotFound:
        raise HTTPException(status_code=404, detail="Memory record to supersede was not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return AIMemoryResponse.model_validate(record)


@router.get(
    "/organizations/{organization_id}/ai/memory",
    response_model=list[AIMemoryResponse],
)
def list_memory_records(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[AIMemoryResponse]:
    try:
        matches = search_memories(
            db,
            actor_user_id=membership.user_id,
            organization_id=membership.organization_id,
            role=membership.role,
            query=q,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return [
        AIMemoryResponse.model_validate(match.record).model_copy(
            update={"score": match.score, "matched_terms": list(match.matched_terms)}
        )
        for match in matches
    ]


@router.get(
    "/organizations/{organization_id}/ai/memory/{memory_id}",
    response_model=AIMemoryResponse,
)
def get_memory_record(
    memory_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AIMemoryResponse:
    try:
        record = get_memory(
            db,
            memory_id=memory_id,
            actor_user_id=membership.user_id,
            organization_id=membership.organization_id,
            role=membership.role,
        )
    except MemoryNotFound:
        raise HTTPException(status_code=404, detail="Memory record not found") from None
    return AIMemoryResponse.model_validate(record)


@router.delete(
    "/organizations/{organization_id}/ai/memory/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def archive_memory_record(
    memory_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        archive_memory(
            db,
            memory_id=memory_id,
            actor_user_id=membership.user_id,
            organization_id=membership.organization_id,
            role=membership.role,
        )
    except MemoryNotFound:
        raise HTTPException(status_code=404, detail="Memory record not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
