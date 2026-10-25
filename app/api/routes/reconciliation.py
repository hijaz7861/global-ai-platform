"""Tenant-scoped manual bank statement reconciliation."""
import csv
import hashlib
import io
import os
from datetime import date, datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.models import (
    Account,
    BankReconciliationImportBatch,
    BankReconciliationLine,
    BankReconciliationSession,
    JournalEntry,
    JournalLine,
    Membership,
)
from app.schemas import (
    BankReconciliationBalancesUpdate,
    BankReconciliationCreate,
    BankReconciliationLineCreate,
    BankReconciliationLineUpdate,
    BankReconciliationLineResponse,
    BankReconciliationImportResponse,
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
    "/organizations/{organization_id}/reconciliations/{session_id}/lines/import-csv",
    response_model=BankReconciliationImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_statement_csv(
    organization_id: str,
    session_id: str,
    request: Request,
    response: Response,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> BankReconciliationImportResponse:
    """Import a bounded, strict CSV atomically and retain content-hash provenance.

    Required UTF-8 CSV columns: statement_date,description,amount_minor,external_reference.
    Amounts are signed integer minor units; no locale-sensitive decimal parsing occurs.
    """
    _require_owner_or_admin(membership)
    session = _get_session(
        db, organization_id=membership.organization_id, session_id=session_id, lock=True
    )
    if session.status != "open":
        raise HTTPException(status_code=409, detail="Completed reconciliation sessions are immutable")

    raw = await request.body()
    if not raw or len(raw) > 1_000_000:
        raise HTTPException(status_code=413 if len(raw) > 1_000_000 else 422, detail="CSV must be non-empty and at most 1 MB")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"text/csv", "text/plain", "application/csv", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Send the CSV as a raw UTF-8 request body")
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="CSV must be valid UTF-8") from None

    required_columns = ["statement_date", "description", "amount_minor", "external_reference"]
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != required_columns:
        raise HTTPException(
            status_code=422,
            detail={"message": "CSV header must match the required columns in the documented order", "required_columns": required_columns},
        )

    payloads: list[BankReconciliationLineCreate] = []
    references: set[str] = set()
    try:
        for row_number, row in enumerate(reader, start=2):
            if row_number > 1001:
                raise HTTPException(status_code=413, detail="CSV may contain at most 1000 statement rows")
            if None in row or any(value is None for value in row.values()):
                raise HTTPException(status_code=422, detail=f"Malformed CSV row {row_number}")
            if not any((value or "").strip() for value in row.values()):
                continue
            try:
                parsed_date = date.fromisoformat((row["statement_date"] or "").strip())
                parsed_amount = int((row["amount_minor"] or "").strip())
                payload = BankReconciliationLineCreate(
                    statement_date=parsed_date,
                    description=row["description"],
                    amount_minor=parsed_amount,
                    external_reference=row["external_reference"] or None,
                )
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=422, detail=f"Invalid CSV row {row_number}: {str(exc)[:160]}") from None
            if not session.statement_start_date <= payload.statement_date <= session.statement_end_date:
                raise HTTPException(status_code=422, detail=f"CSV row {row_number} date is outside the statement window")
            if payload.external_reference:
                if payload.external_reference in references:
                    raise HTTPException(status_code=422, detail=f"Duplicate external reference in CSV row {row_number}")
                references.add(payload.external_reference)
            payloads.append(payload)
    except csv.Error:
        raise HTTPException(status_code=422, detail="Malformed CSV data") from None

    if not payloads:
        raise HTTPException(status_code=422, detail="CSV must contain at least one valid statement row")
    digest = hashlib.sha256(raw).hexdigest()
    existing_batch = db.scalar(
        select(BankReconciliationImportBatch).where(
            BankReconciliationImportBatch.session_id == session.id,
            BankReconciliationImportBatch.content_sha256 == digest,
        )
    )
    if existing_batch is not None:
        response.status_code = status.HTTP_200_OK
        return BankReconciliationImportResponse(
            import_batch_id=existing_batch.id,
            session_id=session.id,
            content_sha256=existing_batch.content_sha256,
            row_count=existing_batch.row_count,
            source_filename=existing_batch.source_filename,
            created_at=existing_batch.created_at,
            replayed=True,
        )

    if references:
        existing_reference = db.scalar(
            select(BankReconciliationLine.id).where(
                BankReconciliationLine.session_id == session.id,
                BankReconciliationLine.external_reference.in_(references),
            ).limit(1)
        )
        if existing_reference is not None:
            raise HTTPException(status_code=409, detail="CSV contains an external reference already imported in this session")

    filename_header = request.headers.get("x-import-filename", "").strip()
    filename = os.path.basename(filename_header.replace("\\", "/"))[:255] or None
    batch = BankReconciliationImportBatch(
        id=str(uuid4()),
        session_id=session.id,
        created_by_user_id=membership.user_id,
        content_sha256=digest,
        source_filename=filename,
        row_count=len(payloads),
    )
    db.add(batch)
    for payload in payloads:
        db.add(
            BankReconciliationLine(
                id=str(uuid4()),
                session_id=session.id,
                statement_date=payload.statement_date,
                description=payload.description,
                amount_minor=payload.amount_minor,
                external_reference=payload.external_reference,
                import_batch_id=batch.id,
            )
        )
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="bank_reconciliation.csv_imported",
        entity_type="bank_reconciliation_import",
        entity_id=batch.id,
        details={"session_id": session.id, "row_count": len(payloads), "content_sha256": digest},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CSV import conflicts with existing statement references or import batch") from None
    db.refresh(batch)
    return BankReconciliationImportResponse(
        import_batch_id=batch.id,
        session_id=session.id,
        content_sha256=batch.content_sha256,
        row_count=batch.row_count,
        source_filename=batch.source_filename,
        created_at=batch.created_at,
        replayed=False,
    )


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


@router.put(
    "/organizations/{organization_id}/reconciliations/{session_id}/balances",
    response_model=BankReconciliationResponse,
)
def update_reconciliation_balances(
    session_id: str,
    payload: BankReconciliationBalancesUpdate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> BankReconciliationResponse:
    _require_owner_or_admin(membership)
    session = _get_session(
        db, organization_id=membership.organization_id, session_id=session_id, lock=True
    )
    if session.status != "open":
        raise HTTPException(status_code=409, detail="Completed reconciliation sessions are immutable")
    session.opening_balance_minor = payload.opening_balance_minor
    session.closing_balance_minor = payload.closing_balance_minor
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="bank_reconciliation.balances_updated",
        entity_type="bank_reconciliation",
        entity_id=session.id,
        details={
            "opening_balance_minor": session.opening_balance_minor,
            "closing_balance_minor": session.closing_balance_minor,
        },
    )
    db.commit()
    db.refresh(session)
    return _session_response(db, session)


@router.put(
    "/organizations/{organization_id}/reconciliations/{session_id}/lines/{line_id}",
    response_model=BankReconciliationLineResponse,
)
def update_statement_line(
    session_id: str,
    line_id: str,
    payload: BankReconciliationLineUpdate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> BankReconciliationLineResponse:
    _require_owner_or_admin(membership)
    session = _get_session(
        db, organization_id=membership.organization_id, session_id=session_id, lock=True
    )
    if session.status != "open":
        raise HTTPException(status_code=409, detail="Completed reconciliation sessions are immutable")
    line = db.scalar(
        select(BankReconciliationLine)
        .where(
            BankReconciliationLine.id == line_id,
            BankReconciliationLine.session_id == session.id,
        )
        .with_for_update()
    )
    if line is None:
        raise HTTPException(status_code=404, detail="Statement line not found")
    if line.matched_journal_line_id is not None:
        raise HTTPException(status_code=409, detail="Unmatch the statement line before editing it")
    if not session.statement_start_date <= payload.statement_date <= session.statement_end_date:
        raise HTTPException(status_code=422, detail="Statement line date is outside the reconciliation window")
    line.statement_date = payload.statement_date
    line.description = payload.description
    line.amount_minor = payload.amount_minor
    line.external_reference = payload.external_reference
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="bank_reconciliation.line_updated",
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


@router.delete(
    "/organizations/{organization_id}/reconciliations/{session_id}/lines/{line_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_statement_line(
    session_id: str,
    line_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    _require_owner_or_admin(membership)
    session = _get_session(
        db, organization_id=membership.organization_id, session_id=session_id, lock=True
    )
    if session.status != "open":
        raise HTTPException(status_code=409, detail="Completed reconciliation sessions are immutable")
    line = db.scalar(
        select(BankReconciliationLine)
        .where(
            BankReconciliationLine.id == line_id,
            BankReconciliationLine.session_id == session.id,
        )
        .with_for_update()
    )
    if line is None:
        raise HTTPException(status_code=404, detail="Statement line not found")
    if line.matched_journal_line_id is not None:
        raise HTTPException(status_code=409, detail="Unmatch the statement line before deleting it")
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="bank_reconciliation.line_deleted",
        entity_type="bank_reconciliation_line",
        entity_id=line.id,
        details={"session_id": session.id, "amount_minor": line.amount_minor},
    )
    db.delete(line)
    db.commit()


@router.post(
    "/organizations/{organization_id}/reconciliations/{session_id}/lines/{line_id}/unmatch",
    response_model=BankReconciliationLineResponse,
)
def unmatch_statement_line(
    session_id: str,
    line_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> BankReconciliationLineResponse:
    _require_owner_or_admin(membership)
    session = _get_session(
        db, organization_id=membership.organization_id, session_id=session_id, lock=True
    )
    if session.status != "open":
        raise HTTPException(status_code=409, detail="Completed reconciliation sessions are immutable")
    line = db.scalar(
        select(BankReconciliationLine)
        .where(
            BankReconciliationLine.id == line_id,
            BankReconciliationLine.session_id == session.id,
        )
        .with_for_update()
    )
    if line is None:
        raise HTTPException(status_code=404, detail="Statement line not found")
    if line.matched_journal_line_id is None:
        return BankReconciliationLineResponse.model_validate(line)
    prior_journal_line_id = line.matched_journal_line_id
    line.matched_journal_line_id = None
    line.matched_at = None
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="bank_reconciliation.line_unmatched",
        entity_type="bank_reconciliation_line",
        entity_id=line.id,
        details={"session_id": session.id, "journal_line_id": prior_journal_line_id},
    )
    db.commit()
    db.refresh(line)
    return BankReconciliationLineResponse.model_validate(line)


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
