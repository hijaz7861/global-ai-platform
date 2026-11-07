"""Tenant-aware memory lifecycle and explainable lexical retrieval."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
import re
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.models import AIMemoryRecord

MemoryScope = Literal["user", "organization"]
_MEMORY_TYPES = {"preference", "feedback", "project", "reference", "decision", "session_summary"}
_SOURCE_TYPES = {"user_input", "manual", "task", "document", "tool"}
_SENSITIVITY = {"normal", "sensitive", "restricted"}
_TOKEN = re.compile(r"\w+", re.UNICODE)


class MemoryNotFound(LookupError):
    """No memory record exists in the caller's authorized scope."""


class MemoryConflict(ValueError):
    """A memory supersession request conflicts with the existing record."""


@dataclass(frozen=True)
class MemoryMatch:
    record: AIMemoryRecord
    score: float
    matched_terms: tuple[str, ...]


def _tokens(value: str) -> list[str]:
    return [token for token in _TOKEN.findall(value.casefold()) if len(token) > 1][:2048]


def create_memory(
    db: Session,
    *,
    actor_user_id: str,
    scope: MemoryScope,
    organization_id: str | None,
    memory_type: str,
    content: str,
    source_type: str = "user_input",
    source_ref: str | None = None,
    confidence_percent: int = 50,
    sensitivity: str = "normal",
    expires_at: datetime | None = None,
    supersedes_id: str | None = None,
) -> AIMemoryRecord:
    normalized = content.strip()
    if not normalized or len(normalized.encode("utf-8")) > 30_000:
        raise ValueError("Memory content must be non-empty and no larger than 30000 UTF-8 bytes")
    if memory_type not in _MEMORY_TYPES or source_type not in _SOURCE_TYPES:
        raise ValueError("Unsupported memory type or source type")
    if sensitivity not in _SENSITIVITY or not 0 <= confidence_percent <= 100:
        raise ValueError("Invalid memory sensitivity or confidence")
    if scope not in {"user", "organization"}:
        raise ValueError("Invalid memory scope")
    if scope == "organization" and not organization_id:
        raise ValueError("Organization memory requires an organization")
    if scope == "user" and organization_id is not None:
        raise ValueError("User-private memory must not be attached to an organization")
    if source_ref is not None and len(source_ref) > 256:
        raise ValueError("source_ref must not exceed 256 characters")
    now = datetime.now(timezone.utc)
    if expires_at is not None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("Memory expiry must include a timezone")
        expires_at = expires_at.astimezone(timezone.utc)
        if expires_at <= now:
            raise ValueError("Memory expiry must be in the future")

    previous = None
    if supersedes_id is not None:
        previous = db.scalar(select(AIMemoryRecord).where(
            AIMemoryRecord.id == supersedes_id,
            AIMemoryRecord.status == "active",
        ))
        if previous is None:
            raise MemoryNotFound("Memory record to supersede was not found")
        same_scope = (
            previous.scope == scope
            and previous.organization_id == organization_id
            and (
                (scope == "user" and previous.owner_user_id == actor_user_id)
                or scope == "organization"
            )
        )
        if not same_scope:
            raise MemoryNotFound("Memory record to supersede was not found")
        if previous.sensitivity == "restricted":
            raise MemoryNotFound("Memory record to supersede was not found")

    record = AIMemoryRecord(
        scope=scope,
        organization_id=organization_id if scope == "organization" else None,
        owner_user_id=actor_user_id if scope == "user" else None,
        created_by_user_id=actor_user_id,
        memory_type=memory_type,
        content=normalized,
        content_sha256=sha256(normalized.encode("utf-8")).hexdigest(),
        source_type=source_type,
        source_ref=source_ref,
        confidence_percent=confidence_percent,
        sensitivity=sensitivity,
        verification_status="unverified",
        status="active",
        supersedes_id=previous.id if previous is not None else None,
        expires_at=expires_at,
    )
    db.add(record)
    db.flush()
    if previous is not None:
        previous.status = "superseded"
        previous.verification_status = "superseded"
        previous.updated_at = now
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            organization_id=organization_id if scope == "organization" else None,
            action="ai.memory.superseded",
            entity_type="ai_memory_record",
            entity_id=previous.id,
            details={"status": "superseded"},
        )
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        organization_id=organization_id if scope == "organization" else None,
        action="ai.memory.created",
        entity_type="ai_memory_record",
        entity_id=record.id,
        details={"status": "active"},
    )
    db.commit()
    db.refresh(record)
    return record


def _visible_query(
    db: Session,
    *,
    actor_user_id: str,
    organization_id: str,
    role: str,
):
    now = datetime.now(timezone.utc)
    user_private_scope = and_(
        AIMemoryRecord.scope == "user",
        AIMemoryRecord.owner_user_id == actor_user_id,
    )
    organization_scope = and_(
        AIMemoryRecord.scope == "organization",
        AIMemoryRecord.organization_id == organization_id,
        (
            AIMemoryRecord.sensitivity == "normal"
            if role not in {"owner", "admin"}
            else (
                AIMemoryRecord.sensitivity != "restricted"
                if role == "admin"
                else True
            )
        ),
    )
    return select(AIMemoryRecord).where(
        or_(user_private_scope, organization_scope),
        AIMemoryRecord.status == "active",
        or_(AIMemoryRecord.expires_at.is_(None), AIMemoryRecord.expires_at > now),
    )


def search_memories(
    db: Session,
    *,
    actor_user_id: str,
    organization_id: str,
    role: str,
    query: str = "",
    limit: int = 20,
) -> list[MemoryMatch]:
    if not 1 <= limit <= 100:
        raise ValueError("Memory search limit must be between 1 and 100")
    terms = list(dict.fromkeys(_tokens(query)))
    candidates = db.scalars(
        _visible_query(
            db, actor_user_id=actor_user_id, organization_id=organization_id, role=role
        ).order_by(AIMemoryRecord.updated_at.desc(), AIMemoryRecord.id).limit(500)
    ).all()
    if not terms:
        return [MemoryMatch(record, 0.0, ()) for record in candidates[:limit]]

    documents = [_tokens(record.content) for record in candidates]
    if not documents:
        return []
    document_frequency = Counter(
        term for term in terms for tokens in documents if term in set(tokens)
    )
    average_length = sum(len(tokens) for tokens in documents) / len(documents) or 1.0
    total = len(documents)
    scored: list[MemoryMatch] = []
    k1, b = 1.5, 0.75
    for record, tokens in zip(candidates, documents):
        frequencies = Counter(tokens)
        matched = tuple(term for term in terms if frequencies[term] > 0)
        if not matched:
            continue
        length = len(tokens)
        score = 0.0
        for term in matched:
            df = document_frequency[term]
            inverse = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            frequency = frequencies[term]
            denominator = frequency + k1 * (1.0 - b + b * length / average_length)
            score += inverse * frequency * (k1 + 1.0) / denominator
        scored.append(MemoryMatch(record, round(score, 6), matched))
    scored.sort(key=lambda item: (item.score, item.record.updated_at), reverse=True)
    return scored[:limit]


def get_memory(
    db: Session,
    *,
    memory_id: str,
    actor_user_id: str,
    organization_id: str,
    role: str,
) -> AIMemoryRecord:
    record = db.scalar(_visible_query(
        db, actor_user_id=actor_user_id, organization_id=organization_id, role=role
    ).where(AIMemoryRecord.id == memory_id))
    if record is None:
        raise MemoryNotFound("Memory record not found")
    return record


def archive_memory(
    db: Session,
    *,
    memory_id: str,
    actor_user_id: str,
    organization_id: str,
    role: str,
) -> None:
    record = get_memory(
        db,
        memory_id=memory_id,
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        role=role,
    )
    if record.scope == "user" and record.owner_user_id != actor_user_id:
        raise MemoryNotFound("Memory record not found")
    if record.scope == "organization" and role not in {"owner", "admin"}:
        raise MemoryNotFound("Memory record not found")
    record.status = "archived"
    record.updated_at = datetime.now(timezone.utc)
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        organization_id=record.organization_id,
        action="ai.memory.archived",
        entity_type="ai_memory_record",
        entity_id=record.id,
        details={"status": "archived"},
    )
    db.commit()
