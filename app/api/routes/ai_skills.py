"""Organization-scoped skill registry; skill text never grants tool access."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai.skills.schemas import AISkillResponse, CreateAISkillRequest
from app.ai.skills.service import (
    SkillConflict,
    SkillNotFound,
    approve_skill_version,
    create_skill_version,
    disable_skill_version,
    get_skill_version,
    list_skill_versions,
)
from app.api.deps import require_membership
from app.db import get_db
from app.models import Membership

router = APIRouter(tags=["ai-skills"])


@router.post(
    "/organizations/{organization_id}/ai/skills",
    response_model=AISkillResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_skill(
    organization_id: str,
    payload: CreateAISkillRequest,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AISkillResponse:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required to register skills")
    try:
        record = create_skill_version(
            db,
            organization_id=membership.organization_id,
            actor_user_id=membership.user_id,
            skill_id=payload.skill_id,
            version=payload.version,
            title=payload.title,
            description=payload.description,
            content=payload.content,
            source_type=payload.source_type,
            source_ref=payload.source_ref,
            license_id=payload.license_id,
            required_capabilities=payload.required_capabilities,
            data_classes=payload.data_classes,
            risk_level=payload.risk_level,
        )
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return AISkillResponse.model_validate(record)


@router.get(
    "/organizations/{organization_id}/ai/skills",
    response_model=list[AISkillResponse],
)
def list_skills(
    organization_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AISkillResponse]:
    try:
        records = list_skill_versions(
            db, organization_id=membership.organization_id, role=membership.role
        )
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return [AISkillResponse.model_validate(item) for item in records]


@router.get(
    "/organizations/{organization_id}/ai/skills/{skill_id}/{version}",
    response_model=AISkillResponse,
)
def get_skill(
    organization_id: str,
    skill_id: str,
    version: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AISkillResponse:
    try:
        record = get_skill_version(
            db,
            organization_id=membership.organization_id,
            skill_id=skill_id,
            version=version,
            role=membership.role,
        )
    except SkillNotFound:
        raise HTTPException(status_code=404, detail="Skill version not found") from None
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return AISkillResponse.model_validate(record)


@router.post(
    "/organizations/{organization_id}/ai/skills/{skill_id}/{version}/approve",
    response_model=AISkillResponse,
)
def approve_skill(
    organization_id: str,
    skill_id: str,
    version: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AISkillResponse:
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Organization owner approval is required")
    try:
        record = approve_skill_version(
            db,
            organization_id=membership.organization_id,
            skill_id=skill_id,
            version=version,
            actor_user_id=membership.user_id,
        )
    except SkillNotFound:
        raise HTTPException(status_code=404, detail="Draft skill version not found") from None
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return AISkillResponse.model_validate(record)


@router.post(
    "/organizations/{organization_id}/ai/skills/{skill_id}/{version}/disable",
    response_model=AISkillResponse,
)
def disable_skill(
    organization_id: str,
    skill_id: str,
    version: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AISkillResponse:
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Organization owner approval is required")
    try:
        disable_skill_version(
            db,
            organization_id=membership.organization_id,
            skill_id=skill_id,
            version=version,
            actor_user_id=membership.user_id,
        )
        record = get_skill_version(
            db, organization_id=membership.organization_id,
            skill_id=skill_id, version=version, role="owner",
        )
    except SkillNotFound:
        raise HTTPException(status_code=404, detail="Skill version not found") from None
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return AISkillResponse.model_validate(record)
