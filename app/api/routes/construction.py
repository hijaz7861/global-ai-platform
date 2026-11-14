"""Tenant-scoped construction projects and Decimal-safe BOQ operations."""

from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_active_role, require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.models import ConstructionBoqItem, ConstructionClient, ConstructionProject, Membership, UserRoleEntitlement
from app.schemas import (
    ConstructionBoqItemCreate, ConstructionBoqItemResponse, ConstructionClientCreate,
    ConstructionClientResponse, ConstructionProjectCreate, ConstructionProjectDetailResponse,
    ConstructionProjectResponse, ConstructionProjectUpdate,
)

router = APIRouter(tags=["construction"])
_MAX_MINOR = 9_000_000_000_000_000
_ALLOWED_TRANSITIONS = {
    "lead": {"quoted", "cancelled"}, "quoted": {"approved", "cancelled"},
    "approved": {"in_progress", "on_hold", "cancelled"},
    "in_progress": {"on_hold", "completed", "cancelled"},
    "on_hold": {"in_progress", "cancelled"}, "completed": set(), "cancelled": set(),
}


def _can_manage_project(membership: Membership, project: ConstructionProject) -> bool:
    return membership.role in {"owner", "admin"} or project.created_by_user_id == membership.user_id


def _get_project(db: Session, organization_id: str, project_id: str) -> ConstructionProject:
    project = db.scalar(select(ConstructionProject).where(
        ConstructionProject.id == project_id, ConstructionProject.organization_id == organization_id))
    if project is None:
        raise HTTPException(status_code=404, detail="Construction project not found")
    return project


@router.post("/organizations/{organization_id}/construction/clients",
             response_model=ConstructionClientResponse, status_code=status.HTTP_201_CREATED)
def create_construction_client(
    payload: ConstructionClientCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionClientResponse:
    item = ConstructionClient(id=str(uuid4()), organization_id=membership.organization_id,
        created_by_user_id=membership.user_id, display_name=payload.display_name, city=payload.city)
    db.add(item)
    db.flush()
    record_audit_event(db, actor_user_id=membership.user_id, organization_id=membership.organization_id,
        action="construction_client.created", entity_type="construction_client", entity_id=item.id,
        details={"city": item.city})
    db.commit()
    db.refresh(item)
    return ConstructionClientResponse.model_validate(item)


@router.get("/organizations/{organization_id}/construction/clients",
            response_model=list[ConstructionClientResponse])
def list_construction_clients(
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConstructionClientResponse]:
    rows = db.scalars(select(ConstructionClient).where(
        ConstructionClient.organization_id == membership.organization_id
    ).order_by(ConstructionClient.display_name, ConstructionClient.created_at).limit(limit)).all()
    return [ConstructionClientResponse.model_validate(item) for item in rows]


@router.post("/organizations/{organization_id}/construction/projects",
             response_model=ConstructionProjectResponse, status_code=status.HTTP_201_CREATED)
def create_construction_project(
    payload: ConstructionProjectCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionProjectResponse:
    client = db.scalar(select(ConstructionClient).where(
        ConstructionClient.id == payload.client_id,
        ConstructionClient.organization_id == membership.organization_id))
    if client is None:
        raise HTTPException(status_code=404, detail="Construction client not found")
    item = ConstructionProject(id=str(uuid4()), organization_id=membership.organization_id,
        client_id=client.id, created_by_user_id=membership.user_id, title=payload.title,
        work_type=payload.work_type, contract_type=payload.contract_type,
        contract_amount_minor=payload.contract_amount_minor, currency=payload.currency,
        location_label=payload.location_label, start_date=payload.start_date,
        target_date=payload.target_date, status="lead", progress_percent=0, notes=payload.notes)
    db.add(item)
    db.flush()
    record_audit_event(db, actor_user_id=membership.user_id, organization_id=membership.organization_id,
        action="construction_project.created", entity_type="construction_project", entity_id=item.id,
        details={"client_id": client.id, "work_type": item.work_type, "contract_type": item.contract_type,
                 "contract_amount_minor": item.contract_amount_minor, "currency": item.currency, "status": item.status})
    db.commit()
    db.refresh(item)
    return ConstructionProjectResponse.model_validate(item)


@router.get("/organizations/{organization_id}/construction/projects",
            response_model=list[ConstructionProjectResponse])
def list_construction_projects(
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConstructionProjectResponse]:
    rows = db.scalars(select(ConstructionProject).where(
        ConstructionProject.organization_id == membership.organization_id
    ).order_by(ConstructionProject.created_at.desc(), ConstructionProject.id).limit(limit)).all()
    return [ConstructionProjectResponse.model_validate(item) for item in rows]


@router.get("/organizations/{organization_id}/construction/projects/{project_id}",
            response_model=ConstructionProjectDetailResponse)
def get_construction_project(
    project_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionProjectDetailResponse:
    project = _get_project(db, membership.organization_id, project_id)
    items = db.scalars(select(ConstructionBoqItem).where(
        ConstructionBoqItem.project_id == project.id,
        ConstructionBoqItem.organization_id == membership.organization_id
    ).order_by(ConstructionBoqItem.line_number, ConstructionBoqItem.id).limit(500)).all()
    total = db.scalar(select(func.coalesce(func.sum(ConstructionBoqItem.line_total_minor), 0)).where(
        ConstructionBoqItem.project_id == project.id,
        ConstructionBoqItem.organization_id == membership.organization_id,
    )) or 0
    return ConstructionProjectDetailResponse(
        project=ConstructionProjectResponse.model_validate(project),
        boq_items=[ConstructionBoqItemResponse.model_validate(item) for item in items],
        boq_total_minor=int(total))


@router.post("/organizations/{organization_id}/construction/projects/{project_id}/boq-items",
             response_model=ConstructionBoqItemResponse, status_code=status.HTTP_201_CREATED)
def add_construction_boq_item(
    project_id: str,
    payload: ConstructionBoqItemCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionBoqItemResponse:
    project = _get_project(db, membership.organization_id, project_id)
    if not _can_manage_project(membership, project):
        raise HTTPException(status_code=403, detail="Project creator or organization owner/admin required")
    if project.status in {"completed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Closed projects cannot accept BOQ changes")
    line_total = (payload.quantity * Decimal(payload.unit_rate_minor)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP)
    if line_total < 0 or line_total > _MAX_MINOR:
        raise HTTPException(status_code=422, detail="BOQ line total exceeds the supported amount range")
    item = ConstructionBoqItem(id=str(uuid4()), organization_id=membership.organization_id,
        project_id=project.id, created_by_user_id=membership.user_id, line_number=payload.line_number,
        category=payload.category, item_name=payload.item_name, unit=payload.unit,
        quantity=payload.quantity, unit_rate_minor=payload.unit_rate_minor, line_total_minor=int(line_total))
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="BOQ line number already exists for this project") from None
    record_audit_event(db, actor_user_id=membership.user_id, organization_id=membership.organization_id,
        action="construction_boq_item.created", entity_type="construction_boq_item", entity_id=item.id,
        details={"project_id": project.id, "line_number": item.line_number, "quantity": str(item.quantity),
                 "unit_rate_minor": item.unit_rate_minor, "line_total_minor": item.line_total_minor,
                 "currency": project.currency})
    db.commit()
    db.refresh(item)
    return ConstructionBoqItemResponse.model_validate(item)


@router.patch("/organizations/{organization_id}/construction/projects/{project_id}",
              response_model=ConstructionProjectResponse)
def update_construction_project(
    project_id: str,
    payload: ConstructionProjectUpdate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionProjectResponse:
    project = _get_project(db, membership.organization_id, project_id)
    if not _can_manage_project(membership, project):
        raise HTTPException(status_code=403, detail="Project creator or organization owner/admin required")
    if project.status in {"completed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Closed projects cannot be changed")
    if payload.status is not None:
        if payload.status not in _ALLOWED_TRANSITIONS[project.status]:
            raise HTTPException(status_code=409, detail="Invalid construction project status transition")
        if payload.status == "approved" and membership.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Organization owner/admin approval required")
        final_progress = payload.progress_percent if payload.progress_percent is not None else project.progress_percent
        if payload.status == "completed" and final_progress != 100:
            raise HTTPException(status_code=409, detail="Project must be at 100 percent before completion")
        project.status = payload.status
    if payload.progress_percent is not None:
        if payload.progress_percent > 0 and project.status not in {"in_progress", "on_hold", "completed"}:
            raise HTTPException(status_code=409, detail="Project must be in progress before progress can be recorded")
        if project.status == "completed" and payload.progress_percent != 100:
            raise HTTPException(status_code=409, detail="Completed project progress must remain at 100 percent")
        if payload.progress_percent == 100 and project.status != "completed":
            raise HTTPException(status_code=409, detail="Use the completed status for 100 percent progress")
        project.progress_percent = payload.progress_percent
    record_audit_event(db, actor_user_id=membership.user_id, organization_id=membership.organization_id,
        action="construction_project.updated", entity_type="construction_project", entity_id=project.id,
        details={"status": project.status, "progress_percent": project.progress_percent})
    db.commit()
    db.refresh(project)
    return ConstructionProjectResponse.model_validate(project)
