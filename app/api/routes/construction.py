"""Tenant-scoped construction projects and Decimal-safe BOQ operations."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_active_role, require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.models import (Account, AccountingPeriod, ConstructionBoqItem, ConstructionCashbookEntry,
                        ConstructionClient, ConstructionLabourEntry, ConstructionMaterialEntry,
                        ConstructionProject, ConstructionQuote, ConstructionQuoteLine,
                        JournalEntry, JournalLine, Membership, Organization, UserRoleEntitlement, utc_now)
from app.schemas import (
    ConstructionCashbookCreate, ConstructionCashbookResponse,
    ConstructionLabourEntryCreate, ConstructionLabourEntryResponse,
    ConstructionMaterialEntryCreate, ConstructionMaterialEntryResponse,
    ConstructionBoqItemCreate, ConstructionBoqItemResponse, ConstructionClientCreate,
    ConstructionClientResponse, ConstructionProjectCreate, ConstructionProjectDetailResponse,
    ConstructionProjectResponse, ConstructionProjectUpdate, ConstructionQuoteCreate,
    ConstructionQuoteDecision, ConstructionQuoteDetailResponse, ConstructionQuoteLineResponse,
    ConstructionQuoteResponse,
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


def _construction_project_statement(
    organization_id: str, project_id: str, *, for_update: bool = False
):
    """Build an organization-scoped project query, optionally locking its row."""
    statement = select(ConstructionProject).where(
        ConstructionProject.id == project_id,
        ConstructionProject.organization_id == organization_id,
    )
    return statement.with_for_update() if for_update else statement


def _get_project(
    db: Session, organization_id: str, project_id: str, *, for_update: bool = False
) -> ConstructionProject:
    project = db.scalar(
        _construction_project_statement(organization_id, project_id, for_update=for_update)
    )
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
    project = _get_project(db, membership.organization_id, project_id, for_update=True)
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
    project = _get_project(db, membership.organization_id, project_id, for_update=True)
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



def _quote_today():
    return datetime.now(timezone.utc).date()


def _percentage_minor(amount_minor: int, percentage: Decimal) -> int:
    return int((Decimal(amount_minor) * percentage / Decimal("100")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def _construction_quote_statement(
    organization_id: str, project_id: str, quote_id: str, *, for_update: bool = False
):
    """Build a tenant- and project-scoped quote query with an optional row lock."""
    statement = select(ConstructionQuote).where(
        ConstructionQuote.id == quote_id,
        ConstructionQuote.organization_id == organization_id,
        ConstructionQuote.project_id == project_id,
    )
    return statement.with_for_update() if for_update else statement


def _get_quote(
    db: Session, organization_id: str, project_id: str, quote_id: str, *, for_update: bool = False
) -> ConstructionQuote:
    quote = db.scalar(
        _construction_quote_statement(organization_id, project_id, quote_id, for_update=for_update)
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="Construction quote not found")
    return quote


def _quote_detail(db: Session, quote: ConstructionQuote) -> ConstructionQuoteDetailResponse:
    lines = db.scalars(select(ConstructionQuoteLine).where(
        ConstructionQuoteLine.quote_id == quote.id,
        ConstructionQuoteLine.organization_id == quote.organization_id,
    ).order_by(ConstructionQuoteLine.line_number, ConstructionQuoteLine.id).limit(1000)).all()
    return ConstructionQuoteDetailResponse(
        quote=ConstructionQuoteResponse.model_validate(quote),
        lines=[ConstructionQuoteLineResponse.model_validate(item) for item in lines],
    )


@router.post("/organizations/{organization_id}/construction/projects/{project_id}/quotes",
             response_model=ConstructionQuoteDetailResponse, status_code=status.HTTP_201_CREATED)
def create_construction_quote(
    project_id: str,
    payload: ConstructionQuoteCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionQuoteDetailResponse:
    project = _get_project(db, membership.organization_id, project_id, for_update=True)
    if not _can_manage_project(membership, project):
        raise HTTPException(status_code=403, detail="Project creator or organization owner/admin required")
    if project.status in {"completed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Closed projects cannot be quoted")
    if project.status not in {"lead", "quoted"}:
        raise HTTPException(status_code=409, detail="Quotes can only be created before project approval")
    boq_items = db.scalars(select(ConstructionBoqItem).where(
        ConstructionBoqItem.project_id == project.id,
        ConstructionBoqItem.organization_id == membership.organization_id,
    ).order_by(ConstructionBoqItem.line_number, ConstructionBoqItem.id).limit(1001)).all()
    if not boq_items:
        raise HTTPException(status_code=409, detail="A quote requires at least one BOQ line")
    if len(boq_items) > 1000:
        raise HTTPException(status_code=409, detail="A quote is limited to 1000 BOQ lines")

    for item in boq_items:
        expected_line_total = int((Decimal(item.quantity) * Decimal(item.unit_rate_minor)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
        if expected_line_total != int(item.line_total_minor) or expected_line_total > _MAX_MINOR:
            raise HTTPException(status_code=409, detail="BOQ contains an inconsistent or unsupported line total")
    subtotal = sum(int(item.line_total_minor) for item in boq_items)
    if subtotal <= 0 or subtotal > _MAX_MINOR:
        raise HTTPException(status_code=409, detail="BOQ subtotal must be positive and within the supported amount range")
    overhead_amount = _percentage_minor(subtotal, payload.overhead_pct)
    after_overhead = subtotal + overhead_amount
    profit_amount = _percentage_minor(after_overhead, payload.profit_pct)
    before_discount = after_overhead + profit_amount
    discount_amount = _percentage_minor(before_discount, payload.discount_pct)
    taxable = before_discount - discount_amount
    tax_amount = _percentage_minor(taxable, payload.tax_pct)
    total = taxable + tax_amount
    if any(value < 0 or value > _MAX_MINOR for value in (
        overhead_amount, profit_amount, discount_amount, tax_amount, total
    )):
        raise HTTPException(status_code=422, detail="Quote amount exceeds the supported range")

    today = _quote_today()
    quote = ConstructionQuote(
        id=str(uuid4()), organization_id=membership.organization_id, project_id=project.id,
        created_by_user_id=membership.user_id,
        quote_no=f"HJZ-{today.strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
        status="draft", currency=project.currency, boq_subtotal_minor=subtotal,
        overhead_pct=payload.overhead_pct, overhead_amount_minor=overhead_amount,
        profit_pct=payload.profit_pct, profit_amount_minor=profit_amount,
        discount_pct=payload.discount_pct, discount_amount_minor=discount_amount,
        tax_pct=payload.tax_pct, tax_amount_minor=tax_amount, total_minor=total,
        validity_days=payload.validity_days, valid_until=today + timedelta(days=payload.validity_days),
        terms=payload.terms,
    )
    db.add(quote)
    db.flush()
    db.add_all([
        ConstructionQuoteLine(
            id=str(uuid4()), organization_id=membership.organization_id, quote_id=quote.id,
            line_number=index, source_boq_line_number=item.line_number,
            category=item.category, item_name=item.item_name, unit=item.unit,
            quantity=item.quantity, unit_rate_minor=item.unit_rate_minor,
            line_total_minor=item.line_total_minor,
        )
        for index, item in enumerate(boq_items, start=1)
    ])
    record_audit_event(db, actor_user_id=membership.user_id, organization_id=membership.organization_id,
        action="construction_quote.created", entity_type="construction_quote", entity_id=quote.id,
        details={"quote_no": quote.quote_no, "project_id": project.id,
                 "line_count": len(boq_items), "total_minor": total, "currency": quote.currency})
    db.commit()
    db.refresh(quote)
    return _quote_detail(db, quote)


@router.get("/organizations/{organization_id}/construction/projects/{project_id}/quotes",
            response_model=list[ConstructionQuoteResponse])
def list_construction_quotes(
    project_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConstructionQuoteResponse]:
    _get_project(db, membership.organization_id, project_id)
    rows = db.scalars(select(ConstructionQuote).where(
        ConstructionQuote.organization_id == membership.organization_id,
        ConstructionQuote.project_id == project_id,
    ).order_by(ConstructionQuote.created_at.desc(), ConstructionQuote.id).limit(limit)).all()
    return [ConstructionQuoteResponse.model_validate(item) for item in rows]


@router.get("/organizations/{organization_id}/construction/projects/{project_id}/quotes/{quote_id}",
            response_model=ConstructionQuoteDetailResponse)
def get_construction_quote(
    project_id: str,
    quote_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionQuoteDetailResponse:
    quote = _get_quote(db, membership.organization_id, project_id, quote_id)
    return _quote_detail(db, quote)


@router.post("/organizations/{organization_id}/construction/projects/{project_id}/quotes/{quote_id}/send",
             response_model=ConstructionQuoteDetailResponse)
def send_construction_quote(
    project_id: str,
    quote_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionQuoteDetailResponse:
    project = _get_project(db, membership.organization_id, project_id, for_update=True)
    quote = _get_quote(db, membership.organization_id, project_id, quote_id, for_update=True)
    if not _can_manage_project(membership, project):
        raise HTTPException(status_code=403, detail="Project creator or organization owner/admin required")
    if quote.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft quotes can be sent")
    if quote.valid_until < _quote_today():
        quote.status = "expired"
        record_audit_event(db, actor_user_id=membership.user_id, organization_id=membership.organization_id,
            action="construction_quote.expired", entity_type="construction_quote", entity_id=quote.id,
            details={"quote_no": quote.quote_no, "reason": "validity_elapsed_before_send"})
        db.commit()
        raise HTTPException(status_code=409, detail="Quote validity has elapsed")
    if project.status in {"completed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Closed projects cannot send quotes")
    if project.status not in {"lead", "quoted"}:
        raise HTTPException(status_code=409, detail="Quotes can only be sent before project approval")
    quote.status = "sent"
    quote.sent_at = utc_now()
    if project.status == "lead":
        project.status = "quoted"
    record_audit_event(db, actor_user_id=membership.user_id, organization_id=membership.organization_id,
        action="construction_quote.sent", entity_type="construction_quote", entity_id=quote.id,
        details={"quote_no": quote.quote_no, "project_id": project.id, "total_minor": quote.total_minor})
    db.commit()
    db.refresh(quote)
    return _quote_detail(db, quote)


@router.post("/organizations/{organization_id}/construction/projects/{project_id}/quotes/{quote_id}/decision",
             response_model=ConstructionQuoteDetailResponse)
def decide_construction_quote(
    project_id: str,
    quote_id: str,
    payload: ConstructionQuoteDecision,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionQuoteDetailResponse:
    project = _get_project(db, membership.organization_id, project_id, for_update=True)
    quote = _get_quote(db, membership.organization_id, project_id, quote_id, for_update=True)
    if project.status != "quoted":
        raise HTTPException(status_code=409, detail="Project must remain in quoted status to record a quote decision")
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Organization owner/admin must record the customer decision")
    if quote.status != "sent":
        raise HTTPException(status_code=409, detail="Only sent quotes can receive a decision")
    if quote.valid_until < _quote_today():
        quote.status = "expired"
        record_audit_event(db, actor_user_id=membership.user_id, organization_id=membership.organization_id,
            action="construction_quote.expired", entity_type="construction_quote", entity_id=quote.id,
            details={"quote_no": quote.quote_no, "reason": "validity_elapsed_before_decision"})
        db.commit()
        raise HTTPException(status_code=409, detail="Quote validity has elapsed")
    quote.status = "accepted" if payload.decision == "accept" else "rejected"
    quote.decision_by_user_id = membership.user_id
    quote.decided_at = utc_now()
    record_audit_event(db, actor_user_id=membership.user_id, organization_id=membership.organization_id,
        action=f"construction_quote.{quote.status}", entity_type="construction_quote", entity_id=quote.id,
        details={"quote_no": quote.quote_no, "decision": payload.decision, "total_minor": quote.total_minor})
    db.commit()
    db.refresh(quote)
    return _quote_detail(db, quote)



def _ensure_construction_cashbook_open_period(db: Session, organization_id: str) -> None:
    """Use the same tenant-first lock order as accounting period closure and journal posting."""
    db.scalar(
        select(Organization.id)
        .where(Organization.id == organization_id)
        .with_for_update()
    )
    periods = db.scalars(
        select(AccountingPeriod)
        .where(AccountingPeriod.organization_id == organization_id)
        .with_for_update()
    ).all()
    if not periods:
        return
    today = utc_now().date()
    active = next((period for period in periods if period.starts_on <= today <= period.ends_on), None)
    if active is None or active.status != "open":
        raise HTTPException(status_code=409, detail="No open accounting period covers today's posting date")


@router.post(
    "/organizations/{organization_id}/construction/projects/{project_id}/cashbook",
    response_model=ConstructionCashbookResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_construction_cashbook_entry(
    project_id: str,
    payload: ConstructionCashbookCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> ConstructionCashbookResponse:
    project = db.scalar(
        select(ConstructionProject)
        .where(
            ConstructionProject.id == project_id,
            ConstructionProject.organization_id == membership.organization_id,
        )
        .with_for_update()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Construction project not found")
    if not _can_manage_project(membership, project):
        raise HTTPException(status_code=403, detail="Project creator or organization owner/admin required")

    fingerprint_payload = {
        "kind": "construction_cashbook",
        "project_id": project.id,
        "direction": payload.direction,
        "cash_account_id": payload.cash_account_id,
        "category_account_id": payload.category_account_id,
        "category": payload.category,
        "amount_minor": payload.amount_minor,
        "description": payload.description or "",
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    existing = db.scalar(
        select(JournalEntry).where(
            JournalEntry.organization_id == membership.organization_id,
            JournalEntry.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency-Key was used for a different posting")
        existing_cashbook = db.scalar(
            select(ConstructionCashbookEntry).where(
                ConstructionCashbookEntry.organization_id == membership.organization_id,
                ConstructionCashbookEntry.project_id == project.id,
                ConstructionCashbookEntry.journal_entry_id == existing.id,
            )
        )
        if existing_cashbook is None:
            raise HTTPException(status_code=409, detail="Idempotency-Key belongs to a non-cashbook journal entry")
        return ConstructionCashbookResponse.model_validate(existing_cashbook)

    if project.status not in {"approved", "in_progress", "on_hold"}:
        raise HTTPException(status_code=409, detail="Cashbook entries require an approved, active or on-hold project")

    account_ids = {payload.cash_account_id, payload.category_account_id}
    accounts = db.scalars(
        select(Account).where(
            Account.organization_id == membership.organization_id,
            Account.id.in_(account_ids),
            Account.is_active.is_(True),
        )
        .with_for_update()
    ).all()
    account_by_id = {account.id: account for account in accounts}
    if set(account_by_id) != account_ids:
        raise HTTPException(status_code=422, detail="Every account must be active and belong to this organization")

    cash_account = account_by_id[payload.cash_account_id]
    category_account = account_by_id[payload.category_account_id]
    expected_category_type = "revenue" if payload.direction == "income" else "expense"
    if cash_account.account_type != "asset":
        raise HTTPException(status_code=422, detail="Cash account must be an asset account")
    if category_account.account_type != expected_category_type:
        raise HTTPException(
            status_code=422,
            detail=f"{payload.direction.title()} requires a {expected_category_type} category account",
        )
    if cash_account.currency != project.currency or category_account.currency != project.currency:
        raise HTTPException(status_code=422, detail="Cashbook accounts must use the project's currency")

    _ensure_construction_cashbook_open_period(db, membership.organization_id)
    journal_description = f"Construction cashbook {payload.direction}: {payload.category}"
    if payload.description:
        journal_description = f"{journal_description} — {payload.description}"
    journal_description = journal_description[:500]

    entry = JournalEntry(
        id=str(uuid4()),
        organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        description=journal_description,
        currency=project.currency,
        total_minor=payload.amount_minor,
        status="posted",
    )
    db.add(entry)
    db.flush()

    if payload.direction == "income":
        debit_account_id, credit_account_id = cash_account.id, category_account.id
    else:
        debit_account_id, credit_account_id = category_account.id, cash_account.id
    db.add_all([
        JournalLine(
            id=str(uuid4()), journal_entry_id=entry.id, account_id=debit_account_id,
            debit_minor=payload.amount_minor, credit_minor=0,
        ),
        JournalLine(
            id=str(uuid4()), journal_entry_id=entry.id, account_id=credit_account_id,
            debit_minor=0, credit_minor=payload.amount_minor,
        ),
    ])
    cashbook = ConstructionCashbookEntry(
        id=str(uuid4()),
        organization_id=membership.organization_id,
        project_id=project.id,
        journal_entry_id=entry.id,
        created_by_user_id=membership.user_id,
        direction=payload.direction,
        category=payload.category,
        description=payload.description,
        amount_minor=payload.amount_minor,
        currency=project.currency,
    )
    db.add(cashbook)
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="journal_entry.posted",
        entity_type="journal_entry",
        entity_id=entry.id,
        details={"total_minor": payload.amount_minor, "currency": project.currency, "status": "posted"},
    )
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="construction_cashbook.posted",
        entity_type="construction_cashbook_entry",
        entity_id=cashbook.id,
        details={
            "project_id": project.id,
            "journal_entry_id": entry.id,
            "direction": payload.direction,
            "category": payload.category,
            "amount_minor": payload.amount_minor,
            "currency": project.currency,
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(JournalEntry).where(
                JournalEntry.organization_id == membership.organization_id,
                JournalEntry.idempotency_key == idempotency_key,
            )
        )
        if existing is not None and existing.request_fingerprint == fingerprint:
            existing_cashbook = db.scalar(
                select(ConstructionCashbookEntry).where(
                    ConstructionCashbookEntry.organization_id == membership.organization_id,
                    ConstructionCashbookEntry.project_id == project.id,
                    ConstructionCashbookEntry.journal_entry_id == existing.id,
                )
            )
            if existing_cashbook is not None:
                return ConstructionCashbookResponse.model_validate(existing_cashbook)
        raise HTTPException(status_code=409, detail="Cashbook posting conflicted with an existing journal entry") from None
    db.refresh(cashbook)
    return ConstructionCashbookResponse.model_validate(cashbook)


@router.get(
    "/organizations/{organization_id}/construction/projects/{project_id}/cashbook",
    response_model=list[ConstructionCashbookResponse],
)
def list_construction_cashbook_entries(
    project_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(require_active_role("construction_provider", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConstructionCashbookResponse]:
    _get_project(db, membership.organization_id, project_id)
    rows = db.scalars(
        select(ConstructionCashbookEntry)
        .where(
            ConstructionCashbookEntry.organization_id == membership.organization_id,
            ConstructionCashbookEntry.project_id == project_id,
        )
        .order_by(ConstructionCashbookEntry.created_at.desc(), ConstructionCashbookEntry.id)
        .limit(limit)
    ).all()
    return [ConstructionCashbookResponse.model_validate(item) for item in rows]



@router.post(
    "/organizations/{organization_id}/construction/projects/{project_id}/labour-entries",
    response_model=ConstructionLabourEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_construction_labour_entry(
    project_id: str,
    payload: ConstructionLabourEntryCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("construction_provider", organization_scoped=True)
    )],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionLabourEntryResponse:
    project = _get_project(db, membership.organization_id, project_id, for_update=True)
    if not _can_manage_project(membership, project):
        raise HTTPException(status_code=403, detail="Project creator or organization owner/admin required")
    total_minor = int(
        (
            Decimal(payload.worker_count)
            * payload.days
            * Decimal(payload.daily_rate_minor)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if total_minor > _MAX_MINOR:
        raise HTTPException(status_code=422, detail="Labour total exceeds the supported PKR amount")
    item = ConstructionLabourEntry(
        id=str(uuid4()),
        organization_id=membership.organization_id,
        project_id=project.id,
        created_by_user_id=membership.user_id,
        worker_name=payload.worker_name,
        work_date=payload.work_date or utc_now().date(),
        worker_count=payload.worker_count,
        days=payload.days,
        daily_rate_minor=payload.daily_rate_minor,
        total_amount_minor=total_minor,
        currency=project.currency,
        notes=payload.notes,
    )
    db.add(item)
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="construction_labour.created",
        entity_type="construction_labour_entry",
        entity_id=item.id,
        details={
            "project_id": project.id,
            "work_date": item.work_date.isoformat(),
            "worker_count": item.worker_count,
            "days": str(item.days),
            "total_amount_minor": item.total_amount_minor,
            "currency": item.currency,
        },
    )
    db.commit()
    db.refresh(item)
    return ConstructionLabourEntryResponse.model_validate(item)


@router.get(
    "/organizations/{organization_id}/construction/projects/{project_id}/labour-entries",
    response_model=list[ConstructionLabourEntryResponse],
)
def list_construction_labour_entries(
    project_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("construction_provider", organization_scoped=True)
    )],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConstructionLabourEntryResponse]:
    project = _get_project(db, membership.organization_id, project_id)
    if not _can_manage_project(membership, project):
        raise HTTPException(status_code=403, detail="Project creator or organization owner/admin required")
    rows = db.scalars(
        select(ConstructionLabourEntry)
        .where(
            ConstructionLabourEntry.organization_id == membership.organization_id,
            ConstructionLabourEntry.project_id == project_id,
        )
        .order_by(ConstructionLabourEntry.work_date.desc(), ConstructionLabourEntry.id)
        .limit(limit)
    ).all()
    return [ConstructionLabourEntryResponse.model_validate(item) for item in rows]


@router.post(
    "/organizations/{organization_id}/construction/projects/{project_id}/material-entries",
    response_model=ConstructionMaterialEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_construction_material_entry(
    project_id: str,
    payload: ConstructionMaterialEntryCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("construction_provider", organization_scoped=True)
    )],
    db: Annotated[Session, Depends(get_db)],
) -> ConstructionMaterialEntryResponse:
    project = _get_project(db, membership.organization_id, project_id, for_update=True)
    if not _can_manage_project(membership, project):
        raise HTTPException(status_code=403, detail="Project creator or organization owner/admin required")
    total_minor = int(
        (payload.quantity * Decimal(payload.unit_price_minor)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    if total_minor > _MAX_MINOR:
        raise HTTPException(status_code=422, detail="Material total exceeds the supported PKR amount")
    item = ConstructionMaterialEntry(
        id=str(uuid4()),
        organization_id=membership.organization_id,
        project_id=project.id,
        created_by_user_id=membership.user_id,
        item_name=payload.item_name,
        quantity=payload.quantity,
        unit=payload.unit,
        unit_price_minor=payload.unit_price_minor,
        total_amount_minor=total_minor,
        supplier=payload.supplier,
        entry_date=payload.entry_date or utc_now().date(),
        currency=project.currency,
        notes=payload.notes,
    )
    db.add(item)
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="construction_material.created",
        entity_type="construction_material_entry",
        entity_id=item.id,
        details={
            "project_id": project.id,
            "entry_date": item.entry_date.isoformat(),
            "item_name": item.item_name,
            "quantity": str(item.quantity),
            "unit": item.unit,
            "total_amount_minor": item.total_amount_minor,
            "currency": item.currency,
        },
    )
    db.commit()
    db.refresh(item)
    return ConstructionMaterialEntryResponse.model_validate(item)


@router.get(
    "/organizations/{organization_id}/construction/projects/{project_id}/material-entries",
    response_model=list[ConstructionMaterialEntryResponse],
)
def list_construction_material_entries(
    project_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("construction_provider", organization_scoped=True)
    )],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConstructionMaterialEntryResponse]:
    project = _get_project(db, membership.organization_id, project_id)
    if not _can_manage_project(membership, project):
        raise HTTPException(status_code=403, detail="Project creator or organization owner/admin required")
    rows = db.scalars(
        select(ConstructionMaterialEntry)
        .where(
            ConstructionMaterialEntry.organization_id == membership.organization_id,
            ConstructionMaterialEntry.project_id == project_id,
        )
        .order_by(ConstructionMaterialEntry.entry_date.desc(), ConstructionMaterialEntry.id)
        .limit(limit)
    ).all()
    return [ConstructionMaterialEntryResponse.model_validate(item) for item in rows]
