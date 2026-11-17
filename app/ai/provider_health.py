"""PostgreSQL-serialized provider circuit state stored in the append-only audit log.

Only a provider name and a one-way model fingerprint are persisted. Raw model names,
credentials, prompts, provider URLs, and response bodies are never written to this log.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.models import AuditEvent

_ACTION = "ai.provider.health"
_ENTITY_TYPE = "ai_provider_health"
_STATES = {"closed", "open", "half_open"}
_ERROR_CODES = {"", "retryable", "non_retryable", "timeout", "connection", "http_429", "http_5xx", "probe_abandoned"}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _now(value: datetime | None) -> datetime:
    normalized = _as_utc(value) if value is not None else datetime.now(timezone.utc)
    assert normalized is not None
    return normalized


def _health_key(provider_name: str, model_name: str, route: str = "") -> str:
    provider = provider_name.strip().lower()
    model = model_name.strip()
    route_key = route.strip().lower()
    if not provider or not model:
        raise ValueError("Provider and model identifiers are required")
    return hashlib.sha256(f"{provider}\0{model}\0{route_key}".encode("utf-8")).hexdigest()


def _lock_provider(db: Session, key: str) -> None:
    """Serialize provider transitions across workers on PostgreSQL."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        lock_id = int.from_bytes(bytes.fromhex(key[:16]), "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    elif dialect != "sqlite":
        raise RuntimeError("Provider circuit state requires PostgreSQL or SQLite tests")


def _latest_event(db: Session, key: str) -> AuditEvent | None:
    return db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.action == _ACTION,
            AuditEvent.entity_type == _ENTITY_TYPE,
            AuditEvent.entity_id == key,
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )


def _read_state(event: AuditEvent | None) -> dict[str, Any]:
    details = event.details if event is not None and isinstance(event.details, dict) else {}
    state = details.get("provider_state", "closed")
    if state not in _STATES:
        state = "closed"
    failures = details.get("consecutive_failures", 0)
    if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
        failures = 0

    def epoch_value(name: str) -> datetime | None:
        value = details.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return datetime.fromtimestamp(value, timezone.utc)

    return {
        "state": state,
        "consecutive_failures": failures,
        "open_until": epoch_value("open_until_epoch"),
        "probe_until": epoch_value("probe_until_epoch"),
    }


def _append_state(
    db: Session,
    *,
    key: str,
    latest: AuditEvent | None,
    state: str,
    consecutive_failures: int,
    open_until: datetime | None,
    probe_until: datetime | None,
    error_code: str,
    now: datetime,
) -> None:
    event_time = now
    if latest is not None:
        previous_time = _as_utc(latest.created_at)
        if previous_time is not None and event_time <= previous_time:
            event_time = previous_time + timedelta(microseconds=1)

    safe_error_code = error_code if error_code in _ERROR_CODES else "retryable"
    record_audit_event(
        db,
        actor_user_id=None,
        organization_id=None,
        action=_ACTION,
        entity_type=_ENTITY_TYPE,
        entity_id=key,
        details={
            "provider_state": state,
            "consecutive_failures": consecutive_failures,
            "open_until_epoch": int(open_until.timestamp()) if open_until else 0,
            "probe_until_epoch": int(probe_until.timestamp()) if probe_until else 0,
            "error_code": safe_error_code,
        },
        created_at=event_time,
    )


def claim_provider_attempt(
    db: Session,
    *,
    provider_name: str,
    model_name: str,
    probe_lease_seconds: int,
    route: str = "",
    now: datetime | None = None,
) -> bool:
    """Claim a provider call; only one worker may probe an expired open circuit."""
    if not 5 <= probe_lease_seconds <= 300:
        raise ValueError("Probe lease must be between 5 and 300 seconds")
    current = _now(now)
    key = _health_key(provider_name, model_name, route)
    _lock_provider(db, key)
    latest = _latest_event(db, key)
    state = _read_state(latest)

    if state["state"] == "open":
        if state["open_until"] is not None and state["open_until"] > current:
            db.commit()
            return False
        next_state = "half_open"
        probe_until = current + timedelta(seconds=probe_lease_seconds)
        _append_state(
            db, key=key, latest=latest, state=next_state,
            consecutive_failures=state["consecutive_failures"],
            open_until=None, probe_until=probe_until, error_code="", now=current,
        )
        db.commit()
        return True

    if state["state"] == "half_open":
        if state["probe_until"] is not None and state["probe_until"] > current:
            db.commit()
            return False
        probe_until = current + timedelta(seconds=probe_lease_seconds)
        _append_state(
            db, key=key, latest=latest, state="half_open",
            consecutive_failures=state["consecutive_failures"],
            open_until=None, probe_until=probe_until, error_code="", now=current,
        )
        db.commit()
        return True

    db.commit()
    return True


def record_provider_failure(
    db: Session,
    *,
    provider_name: str,
    model_name: str,
    retryable: bool,
    failure_threshold: int,
    cooldown_seconds: int,
    route: str = "",
    error_code: str = "retryable",
    now: datetime | None = None,
) -> None:
    """Append a safe failure transition; non-retryable errors do not poison health."""
    if not 1 <= failure_threshold <= 20:
        raise ValueError("Failure threshold must be between 1 and 20")
    if not 1 <= cooldown_seconds <= 3600:
        raise ValueError("Circuit cooldown must be between 1 and 3600 seconds")
    current = _now(now)
    key = _health_key(provider_name, model_name)
    _lock_provider(db, key)
    latest = _latest_event(db, key)
    previous = _read_state(latest)

    failures = previous["consecutive_failures"] + 1 if retryable else 0
    should_open = retryable and (
        previous["state"] == "half_open" or failures >= failure_threshold
    )
    next_state = "open" if should_open else "closed"
    open_until = current + timedelta(seconds=cooldown_seconds) if should_open else None
    _append_state(
        db, key=key, latest=latest, state=next_state,
        consecutive_failures=failures, open_until=open_until, probe_until=None,
        error_code=error_code if retryable else "non_retryable", now=current,
    )
    db.commit()


def record_provider_success(
    db: Session,
    *,
    provider_name: str,
    model_name: str,
    route: str = "",
    now: datetime | None = None,
) -> None:
    """Close the circuit and reset the consecutive failure count after success."""
    current = _now(now)
    key = _health_key(provider_name, model_name)
    _lock_provider(db, key)
    latest = _latest_event(db, key)
    _append_state(
        db, key=key, latest=latest, state="closed",
        consecutive_failures=0, open_until=None, probe_until=None,
        error_code="", now=current,
    )
    db.commit()


def release_provider_attempt(
    db: Session,
    *,
    provider_name: str,
    model_name: str,
    route: str = "",
    now: datetime | None = None,
) -> None:
    """Release a half-open probe when quota or local policy prevents the call."""
    current = _now(now)
    key = _health_key(provider_name, model_name)
    _lock_provider(db, key)
    latest = _latest_event(db, key)
    previous = _read_state(latest)
    if previous["state"] == "half_open":
        _append_state(
            db, key=key, latest=latest, state="open",
            consecutive_failures=previous["consecutive_failures"],
            open_until=current, probe_until=None,
            error_code="probe_abandoned", now=current,
        )
    db.commit()


def get_provider_health_snapshot(
    db: Session,
    *,
    provider_name: str,
    model_name: str,
    route: str = "",
) -> dict[str, Any]:
    """Return sanitized operational state without exposing the configured model name."""
    key = _health_key(provider_name, model_name)
    state = _read_state(_latest_event(db, key))
    return {
        "provider_name": provider_name.strip().lower(),
        "model_fingerprint": key[:12],
        "state": state["state"],
        "consecutive_failures": state["consecutive_failures"],
        "open_until": state["open_until"].isoformat() if state["open_until"] else None,
        "probe_until": state["probe_until"].isoformat() if state["probe_until"] else None,
    }
