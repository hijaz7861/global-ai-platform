"""Lease-based publisher primitives for the transactional AI task outbox."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import AIOutboxEvent

_WORKER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class OutboxLeaseLost(RuntimeError):
    """The publisher no longer owns a live outbox lease."""


def claim_outbox_events(
    db: Session, *, worker_id: str, limit: int = 25, lease_seconds: int = 60,
) -> list[AIOutboxEvent]:
    """Claim a bounded batch; expired publisher leases are safely reclaimable."""
    if not _WORKER_ID.fullmatch(worker_id):
        raise ValueError("Invalid outbox worker identity")
    if not 1 <= limit <= 100 or not 5 <= lease_seconds <= 3600:
        raise ValueError("Outbox batch or lease is outside its permitted bounds")
    now = datetime.now(timezone.utc)
    eligible = or_(
        and_(AIOutboxEvent.status == "pending", AIOutboxEvent.available_at <= now),
        and_(AIOutboxEvent.status == "processing", AIOutboxEvent.lease_expires_at <= now),
    )
    events = db.scalars(
        select(AIOutboxEvent).where(eligible)
        .order_by(AIOutboxEvent.available_at, AIOutboxEvent.created_at)
        .with_for_update(skip_locked=True).limit(limit)
    ).all()
    claimed: list[AIOutboxEvent] = []
    for event in events:
        if event.attempt_count >= event.max_attempts:
            event.status = "dead"
            event.locked_by = None
            event.lease_expires_at = None
            continue
        event.status = "processing"
        event.locked_by = worker_id
        event.attempt_count += 1
        event.lease_expires_at = now + timedelta(seconds=lease_seconds)
        claimed.append(event)
    db.commit()
    for event in claimed:
        db.refresh(event)
    return claimed


def acknowledge_outbox_event(
    db: Session, *, event_id: str, worker_id: str,
) -> bool:
    """Mark delivered only while the acknowledging publisher still owns its lease."""
    now = datetime.now(timezone.utc)
    event = db.scalar(select(AIOutboxEvent).where(
        AIOutboxEvent.id == event_id,
        AIOutboxEvent.status == "processing",
        AIOutboxEvent.locked_by == worker_id,
        AIOutboxEvent.lease_expires_at > now,
    ).with_for_update())
    if event is None:
        db.rollback()
        raise OutboxLeaseLost("Outbox publisher lease is no longer active")
    event.status = "delivered"
    event.delivered_at = now
    event.locked_by = None
    event.lease_expires_at = None
    event.last_error_code = None
    db.commit()
    return True


def retry_outbox_event(
    db: Session, *, event_id: str, worker_id: str, error_code: str,
) -> str:
    """Release a failed delivery with bounded backoff or move it to dead state."""
    if not _ERROR_CODE.fullmatch(error_code):
        raise ValueError("Invalid outbox error code")
    now = datetime.now(timezone.utc)
    event = db.scalar(select(AIOutboxEvent).where(
        AIOutboxEvent.id == event_id,
        AIOutboxEvent.status == "processing",
        AIOutboxEvent.locked_by == worker_id,
        AIOutboxEvent.lease_expires_at > now,
    ).with_for_update())
    if event is None:
        db.rollback()
        raise OutboxLeaseLost("Outbox publisher lease is no longer active")
    event.locked_by = None
    event.lease_expires_at = None
    event.last_error_code = error_code
    if event.attempt_count >= event.max_attempts:
        event.status = "dead"
    else:
        event.status = "pending"
        event.available_at = now + timedelta(seconds=min(2 ** event.attempt_count, 300))
    status = event.status
    db.commit()
    return status
