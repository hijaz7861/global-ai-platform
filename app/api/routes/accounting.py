"""Tenant-scoped chart of accounts and immutable balanced journal posting."""
import hashlib
import json
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.models import Account, JournalEntry, JournalLine, Membership
from app.schemas import (
    AccountCreate,
    AccountResponse,
    JournalEntryCreate,
    JournalEntryResponse,
    JournalLineResponse,
)

router = APIRouter(tags=["accounting"])


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
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")

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
