"""Tenant-scoped manual bank statement reconciliation."""
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.models import (
    Account,
    BankReconciliationLine,
    BankReconciliationSession,
    JournalEntry,
    JournalLine,
    Membership,
)
from app.schemas import (
    BankReconciliationCreate,
    BankReconciliationLineCreate,
    BankReconciliationLineResponse,
    BankReconciliationMatch,
    BankReconciliationResponse,
)

router = APIRouter(tags=["bank-reconciliation"])


def _require_owner_or_admin(membership: Membership) -> None:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")


def _session_response(db: Session, session: BankReconciliationSession) -> BankReconciliationResponse:
    line_count = db.scalar(
        select(func.count(BankReconciliationLine.id)).where(
            BankReconciliationLine.session_id == session.id
        )
    ) or 0
    matched_count = db.scalar(
        select(func.count(BankReconciliationLine.id)).where(
            BankReconciliationLine.session_id == session.id,
            BankReconciliationLine.matched_journal_line_id.is_not(None),
        )
    ) or 0
    return BankReconciliationResponse(
        id=session.id,
        organization_id=session.organization_id,
        account_id=session.account_id,
        statement_start_date=session.statement_start_date,
        statement_end_date=session.statement_end_date,
        opening_balance_minor=session.opening_balance_minor,
        closing_balance_minor=session.closing_balance_minor,
        status=session.status,
        created_at=session.created_at,
        completed_at=session.completed_at,
        line_count=int(line_count),
        matched_line_count=int(matched_count),
        unmatched_line_count=int(line_count - matched_count),
    )


def _get_session(
    db: Session,
    *,
    organization_id: str,
    session_id: str,
    lock: bool = False,
) -> BankReconciliationSession:
    query = select(BankReconciliationSession).where(
        BankReconciliationSession.id == session_id,
        BankReconciliationSession.organization_id == organization_id,
    )
    if lock:
        query = query.with_for_update()
    session = db.scalar(query)
    if session is None:
        raise HTTPException(status_code=404, detail="Reconciliation session not found")
    return session


@router.post(
    "/organizations/{organization_id}/reconciliations",
    response_model=BankReconciliationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_reconciliation(
    payload: BankReconciliationCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> BankReconciliationResponse:
    _require_owner_or_admin(membership)
    account = db.scalar(
        select(Account)
        .where(
            Account.id == payload.account_id,
            Account.organization_id == membership.organization_id,
            Account.account_type == "asset",
            Account.is_active.is_(True),
        )
        .with_for_update()
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Active asset account not found")
    overlap = db.scalar(
        select(BankReconciliationSession.id).where(
            BankReconciliationSession.organization_id == membership.organization_id,
            BankReconciliationSession.account_id == account.id,
            BankReconciliationSession.statement_start_date <= payload.statement_end_date,
            BankReconciliationSession.statement_end_date >= payload.statement_start_date,
        )
    )
    if overlap is not None:
        raise HTTPException(status_code=409, detail="Reconciliation statement windows must not overlap")

    session = BankReconciliationSession(
        id=str(uuid4()),
        organization_id=membership.organization_id,
        account_id=account.id,
        created_by_user_id=membership.user_id,
        statement_start_date=payload.statement_start_date,
        statement_end_date=payload.statement_end_date,
        opening_balance_minor=payload.opening_balance_minor,
        closing_balance_minor=payload.closing_balance_minor,
        status="open",
    )
    db.add(session)
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="bank_reconciliation.created",
        entity_type="bank_reconciliation",
        entity_id=session.id,
        details={
            "account_id": account.id,
            "statement_start_date": payload.statement_start_date.isoformat(),
            "statement_end_date": payload.statement_end_date.isoformat(),
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Reconciliation statement window conflicts with an existing session") from None
    db.refresh(session)
    return _session_response(db, session)


@router.get(
    "/organizations/{organization_id}/reconciliations",
    response_model=list[BankReconciliationResponse],
)
def list_reconciliations(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BankReconciliationResponse]:
    _require_owner_or_admin(membership)
    sessions = db.scalars(
        select(BankReconciliationSession)
        .where(BankReconciliationSession.organization_id == membership.organization_id)
        .order_by(BankReconciliationSession.created_at.desc(), BankReconciliationSession.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [_session_response(db, item) for item in sessions]


@router.get(
    "/organizations/{organization_id}/reconciliations/{session_id}/lines",
    response_model=list[BankReconciliationLineResponse],
)
def list_reconciliation_lines(
    session_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[BankReconciliationLineResponse]:
    _require_owner_or_admin(membership)
    session = _get_session(
        db, organization_id=membership.organization_id, session_id=session_id
    )
    lines = db.scalars(
        select(BankReconciliationLine)
        .where(BankReconciliationLine.session_id == session.id)
        .order_by(BankReconciliationLine.statement_date, BankReconciliationLine.id)
    ).all()
    return [BankReconciliationLineResponse.model_validate(item) for item in lines]


@router.post(
    "/organizations/{organization_id}/reconciliations/{session_id}/lines",
    response_model=BankReconciliationLineResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_statement_line(
    session_id: str,
    payload: BankReconciliationLineCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> BankReconciliationLineResponse:
    _require_owner_or_admin(membership)
    session = _get_session(
        db, organization_id=membership.organization_id, session_id=session_id, lock=True
    )
    if session.status != "open":
        raise HTTPException(status_code=409, detail="Completed reconciliation sessions are immutable")
    if not session.statement_start_date <= payload.statement_date <= session.statement_end_date:
        raise HTTPException(status_code=422, detail="Statement line date is outside the reconciliation window")
    line = BankReconciliationLine(
        id=str(uuid4()),
        session_id=session.id,
        statement_date=payload.statement_date,
        description=payload.description,
        amount_minor=payload.amount_minor,
        external_reference=payload.external_reference,
    )
    db.add(line)
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="bank_reconciliation.line_imported",
        entity_type="bank_reconciliation_line",
        entity_id=line.id,
        details={"session_id": session.id, "amount_minor": line.amount_minor},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="External statement reference already exists in this session") from None
    db.refresh(line)
    return BankReconciliationLineResponse.model_validate(line)


@router.post(
    "/organizations/{organization_id}/reconciliations/{session_id}/lines/{line_id}/match",
    response_model=BankReconciliationLineResponse,
)
def match_statement_line(
    session_id: str,
    line_id: str,
    payload: BankReconciliationMatch,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> BankReconciliationLineResponse:
    _require_owner_or_admin(membership)
    session = _get_session(
        db, organization_id=membership.organization_id, session_id=session_id, lock=True
    )
    if session.status != "open":
        raise HTTPException(status_code=409, detail="Completed reconciliation sessions are immutable")
    statement_line = db.scalar(
        select(BankReconciliationLine)
        .where(
            BankReconciliationLine.id == line_id,
            BankReconciliationLine.session_id == session.id,
        )
        .with_for_update()
    )
    if statement_line is None:
        raise HTTPException(status_code=404, detail="Statement line not found")
    if statement_line.matched_journal_line_id is not None:
        raise HTTPException(status_code=409, detail="Statement line is already matched")

    journal_line = db.scalar(
        select(JournalLine)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(
            JournalLine.id == payload.journal_line_id,
            JournalLine.account_id == session.account_id,
            JournalEntry.organization_id == membership.organization_id,
            JournalEntry.status == "posted",
        )
    )
    if journal_line is None:
        raise HTTPException(status_code=404, detail="Posted journal line for this account not found")
    journal_date = journal_line.entry.created_at.date()
    if not session.statement_start_date <= journal_date <= session.statement_end_date:
        raise HTTPException(status_code=422, detail="Journal line date is outside the reconciliation window")
    signed_amount = journal_line.debit_minor - journal_line.credit_minor
    if signed_amount != statement_line.amount_minor:
        raise HTTPException(status_code=422, detail="Statement amount does not match the journal line amount")

    statement_line.matched_journal_line_id = journal_line.id
    statement_line.matched_at = datetime.now(timezone.utc)
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="bank_reconciliation.line_matched",
        entity_type="bank_reconciliation_line",
        entity_id=statement_line.id,
        details={"journal_line_id": journal_line.id, "amount_minor": signed_amount},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Journal line is already matched to another statement line") from None
    db.refresh(statement_line)
    return BankReconciliationLineResponse.model_validate(statement_line)


@router.post(
    "/organizations/{organization_id}/reconciliations/{session_id}/complete",
    response_model=BankReconciliationResponse,
)
def complete_reconciliation(
    session_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> BankReconciliationResponse:
    _require_owner_or_admin(membership)
    session = _get_session(
        db, organization_id=membership.organization_id, session_id=session_id, lock=True
    )
    if session.status == "completed":
        return _session_response(db, session)
    lines = db.scalars(
        select(BankReconciliationLine)
        .where(BankReconciliationLine.session_id == session.id)
        .with_for_update()
    ).all()
    if not lines:
        raise HTTPException(status_code=409, detail="At least one statement line is required")
    unmatched = [item.id for item in lines if item.matched_journal_line_id is None]
    if unmatched:
        raise HTTPException(status_code=409, detail="Every imported statement line must be matched before completion")
    calculated_closing = session.opening_balance_minor + sum(item.amount_minor for item in lines)
    if calculated_closing != session.closing_balance_minor:
        raise HTTPException(status_code=409, detail="Opening balance plus statement movements does not equal closing balance")
    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="bank_reconciliation.completed",
        entity_type="bank_reconciliation",
        entity_id=session.id,
        details={"line_count": len(lines), "closing_balance_minor": calculated_closing},
    )
    db.commit()
    db.refresh(session)
    return _session_response(db, session)
