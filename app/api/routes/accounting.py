"""Tenant-scoped chart of accounts and immutable balanced journal posting."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.models import Account, AccountingPeriod, JournalEntry, JournalLine, Membership
from app.schemas import (
    AccountCreate,
    AccountResponse,
    AccountingPeriodCreate,
    AccountingPeriodResponse,
    JournalEntryCreate,
    JournalEntryResponse,
    JournalLineResponse,
    TrialBalanceLineResponse,
)

router = APIRouter(tags=["accounting"])


def _require_owner_or_admin(membership: Membership) -> None:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")


def _ensure_open_period_for_today(db: Session, organization_id: str) -> None:
    """Preserve legacy posting until periods are configured; enforce them once configured."""
    periods = db.scalars(
        select(AccountingPeriod).where(AccountingPeriod.organization_id == organization_id)
    ).all()
    if not periods:
        return
    today = datetime.now(timezone.utc).date()
    active = next((period for period in periods if period.starts_on <= today <= period.ends_on), None)
    if active is None or active.status != "open":
        raise HTTPException(status_code=409, detail="No open accounting period covers today's posting date")


@router.post(
    "/organizations/{organization_id}/accounting-periods",
    response_model=AccountingPeriodResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_accounting_period(
    payload: AccountingPeriodCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AccountingPeriodResponse:
    _require_owner_or_admin(membership)
    overlap = db.scalar(
        select(AccountingPeriod.id).where(
            AccountingPeriod.organization_id == membership.organization_id,
            AccountingPeriod.starts_on <= payload.ends_on,
            AccountingPeriod.ends_on >= payload.starts_on,
        )
    )
    if overlap is not None:
        raise HTTPException(status_code=409, detail="Accounting periods must not overlap")
    period = AccountingPeriod(
        organization_id=membership.organization_id,
        name=payload.name,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        status="open",
        created_by_user_id=membership.user_id,
    )
    db.add(period)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="accounting_period.created",
        entity_type="accounting_period",
        entity_id=period.id,
        details={"name": period.name, "starts_on": period.starts_on.isoformat(), "ends_on": period.ends_on.isoformat()},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Accounting period conflicts with an existing period") from None
    db.refresh(period)
    return AccountingPeriodResponse.model_validate(period)


@router.get(
    "/organizations/{organization_id}/accounting-periods",
    response_model=list[AccountingPeriodResponse],
)
def list_accounting_periods(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AccountingPeriodResponse]:
    _require_owner_or_admin(membership)
    periods = db.scalars(
        select(AccountingPeriod)
        .where(AccountingPeriod.organization_id == membership.organization_id)
        .order_by(AccountingPeriod.starts_on, AccountingPeriod.id)
    ).all()
    return [AccountingPeriodResponse.model_validate(period) for period in periods]


@router.post(
    "/organizations/{organization_id}/accounting-periods/{period_id}/close",
    response_model=AccountingPeriodResponse,
)
def close_accounting_period(
    period_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AccountingPeriodResponse:
    _require_owner_or_admin(membership)
    period = db.scalar(
        select(AccountingPeriod).where(
            AccountingPeriod.id == period_id,
            AccountingPeriod.organization_id == membership.organization_id,
        )
    )
    if period is None:
        raise HTTPException(status_code=404, detail="Accounting period not found")
    if period.status == "closed":
        return AccountingPeriodResponse.model_validate(period)
    period.status = "closed"
    period.closed_at = datetime.now(timezone.utc)
    period.closed_by_user_id = membership.user_id
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="accounting_period.closed",
        entity_type="accounting_period",
        entity_id=period.id,
        details={"name": period.name},
    )
    db.commit()
    db.refresh(period)
    return AccountingPeriodResponse.model_validate(period)



def _entry_response(entry: JournalEntry) -> JournalEntryResponse:
    lines = sorted(entry.lines, key=lambda item: (item.account_id, item.id))
    return JournalEntryResponse(
        id=entry.id,
        description=entry.description,
        currency=entry.currency,
        total_minor=entry.total_minor,
        status=entry.status,
        created_at=entry.created_at,
        lines=[
            JournalLineResponse(
                account_id=line.account_id,
                debit_minor=line.debit_minor,
                credit_minor=line.credit_minor,
            )
            for line in lines
        ],
    )


@router.post(
    "/organizations/{organization_id}/accounts",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_account(
    payload: AccountCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> AccountResponse:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    account = Account(
        organization_id=membership.organization_id,
        code=payload.code.strip().upper(),
        name=payload.name.strip(),
        account_type=payload.account_type,
        currency=payload.currency,
        is_active=True,
    )
    db.add(account)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="account.created",
        entity_type="account",
        entity_id=account.id,
        details={"currency": account.currency, "status": "active"},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Account code already exists for this currency") from None
    db.refresh(account)
    return AccountResponse.model_validate(account)


@router.get("/organizations/{organization_id}/accounts", response_model=list[AccountResponse])
def list_accounts(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AccountResponse]:
    accounts = db.scalars(
        select(Account)
        .where(Account.organization_id == membership.organization_id)
        .order_by(Account.code, Account.currency)
    ).all()
    return [AccountResponse.model_validate(account) for account in accounts]


@router.post(
    "/organizations/{organization_id}/journal-entries",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_journal_entry(
    payload: JournalEntryCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> JournalEntryResponse:
    _require_owner_or_admin(membership)

    normalized_lines = sorted(
        [
            {"account_id": line.account_id, "debit_minor": line.debit_minor, "credit_minor": line.credit_minor}
            for line in payload.lines
        ],
        key=lambda line: (line["account_id"], line["debit_minor"], line["credit_minor"]),
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {"description": payload.description.strip(), "lines": normalized_lines},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    existing = db.scalar(
        select(JournalEntry).where(
            JournalEntry.organization_id == membership.organization_id,
            JournalEntry.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency-Key was used for a different journal entry")
        return _entry_response(existing)

    _ensure_open_period_for_today(db, membership.organization_id)
    account_ids = {line.account_id for line in payload.lines}
    accounts = db.scalars(
        select(Account).where(
            Account.organization_id == membership.organization_id,
            Account.id.in_(account_ids),
            Account.is_active.is_(True),
        )
    ).all()
    account_by_id = {account.id: account for account in accounts}
    if set(account_by_id) != account_ids:
        raise HTTPException(status_code=422, detail="Every account must be active and belong to this organization")
    currencies = {account.currency for account in accounts}
    if len(currencies) != 1:
        raise HTTPException(status_code=422, detail="A journal entry cannot mix currencies")
    currency = next(iter(currencies))
    debit_total = sum(line.debit_minor for line in payload.lines)
    credit_total = sum(line.credit_minor for line in payload.lines)
    if debit_total <= 0 or debit_total != credit_total:
        raise HTTPException(status_code=422, detail="Journal debits and credits must balance to a positive amount")

    entry = JournalEntry(
        id=str(uuid4()),
        organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        description=payload.description.strip(),
        currency=currency,
        total_minor=debit_total,
        status="posted",
    )
    db.add(entry)
    db.flush()
    for line in payload.lines:
        db.add(
            JournalLine(
                id=str(uuid4()),
                journal_entry_id=entry.id,
                account_id=line.account_id,
                debit_minor=line.debit_minor,
                credit_minor=line.credit_minor,
            )
        )
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="journal_entry.posted",
        entity_type="journal_entry",
        entity_id=entry.id,
        details={"total_minor": debit_total, "currency": currency, "status": "posted"},
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
            return _entry_response(existing)
        raise HTTPException(status_code=409, detail="Journal entry could not be posted") from None
    db.refresh(entry)
    return _entry_response(entry)


@router.get(
    "/organizations/{organization_id}/journal-entries",
    response_model=list[JournalEntryResponse],
)
def list_journal_entries(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[JournalEntryResponse]:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    entries = db.scalars(
        select(JournalEntry)
        .where(JournalEntry.organization_id == membership.organization_id)
        .order_by(JournalEntry.created_at.desc(), JournalEntry.id.desc())
    ).all()
    return [_entry_response(entry) for entry in entries]



@router.post(
    "/organizations/{organization_id}/journal-entries/{entry_id}/reverse",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
def reverse_journal_entry(
    entry_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> JournalEntryResponse:
    """Post one immutable counter-entry; never mutate or delete the original."""
    _require_owner_or_admin(membership)

    fingerprint = hashlib.sha256(
        json.dumps({"reversal_of_id": entry_id}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    existing = db.scalar(
        select(JournalEntry).where(
            JournalEntry.organization_id == membership.organization_id,
            JournalEntry.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency-Key was used for a different journal entry")
        return _entry_response(existing)

    _ensure_open_period_for_today(db, membership.organization_id)
    source = db.scalar(
        select(JournalEntry).where(
            JournalEntry.id == entry_id,
            JournalEntry.organization_id == membership.organization_id,
        )
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    if source.reversal_of_id is not None:
        raise HTTPException(status_code=409, detail="A reversal entry cannot itself be reversed")
    prior_reversal = db.scalar(
        select(JournalEntry.id).where(
            JournalEntry.organization_id == membership.organization_id,
            JournalEntry.reversal_of_id == source.id,
        )
    )
    if prior_reversal is not None:
        raise HTTPException(status_code=409, detail="Journal entry has already been reversed")

    reversal = JournalEntry(
        id=str(uuid4()),
        organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        reversal_of_id=source.id,
        description=f"Reversal of {source.id}: {source.description}"[:500],
        currency=source.currency,
        total_minor=source.total_minor,
        status="posted",
    )
    db.add(reversal)
    db.flush()
    for line in source.lines:
        db.add(
            JournalLine(
                id=str(uuid4()),
                journal_entry_id=reversal.id,
                account_id=line.account_id,
                debit_minor=line.credit_minor,
                credit_minor=line.debit_minor,
            )
        )
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="journal_entry.reversed",
        entity_type="journal_entry",
        entity_id=reversal.id,
        details={"reversal_of_id": source.id, "total_minor": source.total_minor, "currency": source.currency},
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
            return _entry_response(existing)
        raise HTTPException(status_code=409, detail="Journal entry has already been reversed or could not be posted") from None
    db.refresh(reversal)
    return _entry_response(reversal)


@router.get(
    "/organizations/{organization_id}/trial-balance",
    response_model=list[TrialBalanceLineResponse],
)
def get_trial_balance(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[TrialBalanceLineResponse]:
    """Derive balances from posted journal lines; no mutable balance cache is stored."""
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")

    totals = (
        select(
            JournalLine.account_id.label("account_id"),
            func.sum(JournalLine.debit_minor).label("debit_total_minor"),
            func.sum(JournalLine.credit_minor).label("credit_total_minor"),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(JournalEntry.organization_id == membership.organization_id)
        .group_by(JournalLine.account_id)
        .subquery()
    )
    rows = db.execute(
        select(
            Account.id,
            Account.code,
            Account.name,
            Account.account_type,
            Account.currency,
            func.coalesce(totals.c.debit_total_minor, 0),
            func.coalesce(totals.c.credit_total_minor, 0),
        )
        .outerjoin(totals, totals.c.account_id == Account.id)
        .where(Account.organization_id == membership.organization_id)
        .order_by(Account.currency, Account.code)
    ).all()

    result: list[TrialBalanceLineResponse] = []
    for account_id, code, name, account_type, currency, debit, credit in rows:
        debit_total = int(debit)
        credit_total = int(credit)
        normal_balance = (
            debit_total - credit_total
            if account_type in {"asset", "expense"}
            else credit_total - debit_total
        )
        result.append(
            TrialBalanceLineResponse(
                account_id=account_id,
                code=code,
                name=name,
                account_type=account_type,
                currency=currency,
                debit_total_minor=debit_total,
                credit_total_minor=credit_total,
                normal_balance_minor=normal_balance,
            )
        )
    return result
