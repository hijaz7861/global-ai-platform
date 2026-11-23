"""Atomic database-backed abuse throttling for workforce message sends."""
import hashlib
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AuthAttempt, AuthRateLimitLock, utc_now

_SCOPE = "workforce_message_send"


def record_workforce_message_attempt(db: Session, user_id: str) -> None:
    """Count a sender's message attempts in a cross-worker sliding window.

    Only a one-way digest of the authenticated user ID is persisted. The existing
    auth-attempt/lock tables and bounded maintenance cleanup are reused, avoiding
    a second rate-limit store or a new migration.
    """
    now = utc_now()
    window_minutes = settings.workforce_message_send_window_minutes
    limit = settings.workforce_message_send_limit
    cutoff = now - timedelta(minutes=window_minutes)
    subject_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    lock_values = {
        "scope": _SCOPE,
        "subject_hash": subject_hash,
        "touched_at": now,
    }

    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        lock_insert = sqlite_insert(AuthRateLimitLock)
    elif dialect == "postgresql":
        lock_insert = postgresql_insert(AuthRateLimitLock)
    else:
        raise RuntimeError("Workforce message rate limiting requires SQLite or PostgreSQL")

    # Serialize the check-and-insert for this sender across API workers.
    db.execute(
        lock_insert.values(**lock_values).on_conflict_do_update(
            index_elements=[AuthRateLimitLock.scope, AuthRateLimitLock.subject_hash],
            set_={"touched_at": now},
        )
    )
    count = db.scalar(
        select(func.count())
        .select_from(AuthAttempt)
        .where(
            AuthAttempt.scope == _SCOPE,
            AuthAttempt.subject_hash == subject_hash,
            AuthAttempt.created_at >= cutoff,
        )
    ) or 0
    if count >= limit:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail="Too many messages. Please wait before sending again.",
            headers={"Retry-After": str(window_minutes * 60)},
        )

    db.add(AuthAttempt(scope=_SCOPE, subject_hash=subject_hash, created_at=now))
    # Keep the lock and attempt in the caller's transaction. The message row and
    # audit event commit atomically with this reservation, so the request-state
    # row lock is not released between authorization and message creation.
    db.flush()
