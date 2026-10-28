"""Durable, atomic single-use nonce storage for AI evidence attestations.

Use a shared production database and a session factory bound to it. A failed
storage operation raises so the caller can fail closed; it is never treated as
a fresh nonce.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
from typing import Callable

from sqlalchemy import DateTime, String, Table, Column, delete, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base

ai_evidence_nonces = Table(
    "ai_evidence_nonces",
    Base.metadata,
    Column("nonce_hash", String(64), primary_key=True),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def make_durable_nonce_consumer(
    session_factory: sessionmaker[Session],
    *,
    ttl_seconds: int = 1800,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[str], bool]:
    """Build a nonce callback compatible with verify_attestation.

    Nonces are hashed before persistence. Insert uniqueness is the atomic
    cross-worker replay barrier. The table should be cleaned periodically;
    expired rows are opportunistically removed during verification as well.
    """
    if not 60 <= ttl_seconds <= 86_400:
        raise ValueError("Nonce TTL must be between 60 and 86400 seconds")

    def consume_nonce(nonce: str) -> bool:
        if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce):
            return False
        now = clock() if clock is not None else datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Nonce clock must return a timezone-aware datetime")
        digest = sha256(nonce.encode("utf-8")).hexdigest()
        session = session_factory()
        try:
            session.execute(
                delete(ai_evidence_nonces).where(ai_evidence_nonces.c.expires_at <= now)
            )
            session.execute(
                insert(ai_evidence_nonces).values(
                    nonce_hash=digest,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                    created_at=now,
                )
            )
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return consume_nonce
