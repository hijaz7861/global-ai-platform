"""Durable tenant-scoped AI task queue and lease lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AITask, AITaskEvent, AITaskStep

_WORKER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_TASK_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


class TaskConflict(ValueError):
    """An idempotency key was reused for a different task payload."""


class TaskLeaseLost(RuntimeError):
    """The worker no longer owns a live lease for the task."""


class TaskStateConflict(RuntimeError):
    """The requested transition is not valid for the current task state."""


class PermanentTaskError(RuntimeError):
    """A handler failure that must not be retried."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class RetryableTaskError(RuntimeError):
    """A handler failure that may be retried within the task attempt budget."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


@dataclass
class TaskExecutionContext:
    db: Session
    task: AITask
    worker_id: str
    lease_seconds: int

    def heartbeat(self) -> None:
        heartbeat_task(
            self.db,
            task_id=self.task.id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )


def _canonical_json(value: dict[str, Any], *, max_bytes: int = 32_768) -> tuple[str, str]:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Task payload must be finite JSON data") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("Task payload exceeds 32768 bytes")
    return encoded, sha256(encoded.encode("utf-8")).hexdigest()


def _event(
    db: Session, task: AITask, event_type: str, details: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
) -> None:
    # Lifecycle metadata only. Never put prompts, provider output, credentials, or raw errors here.
    db.add(AITaskEvent(
        task_id=task.id,
        organization_id=task.organization_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        details=details or {},
    ))


def create_task(
    db: Session, *, organization_id: str, actor_user_id: str, task_type: str,
    title: str, input_payload: dict[str, Any], idempotency_key: str,
    max_attempts: int = 3,
) -> tuple[AITask, bool]:
    """Create once per actor/organization/idempotency key; return (task, created)."""
    if not _TASK_TYPE.fullmatch(task_type):
        raise ValueError("Invalid task type")
    if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise ValueError("Idempotency-Key must be 8-128 safe ASCII characters")
    if not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts must be between 1 and 10")
    _, digest = _canonical_json(input_payload)
    existing = db.scalar(select(AITask).where(
        AITask.organization_id == organization_id,
        AITask.created_by_user_id == actor_user_id,
        AITask.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        if existing.request_sha256 != digest or existing.task_type != task_type or existing.title != title:
            raise TaskConflict("Idempotency-Key was already used for a different task")
        return existing, False

    task = AITask(
        organization_id=organization_id,
        created_by_user_id=actor_user_id,
        task_type=task_type,
        title=title,
        input_payload=input_payload,
        idempotency_key=idempotency_key,
        request_sha256=digest,
        max_attempts=max_attempts,
    )
    db.add(task)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(AITask).where(
            AITask.organization_id == organization_id,
            AITask.created_by_user_id == actor_user_id,
            AITask.idempotency_key == idempotency_key,
        ))
        if existing is None:
            raise
        if existing.request_sha256 != digest or existing.task_type != task_type or existing.title != title:
            raise TaskConflict("Idempotency-Key was already used for a different task") from None
        return existing, False

    _event(db, task, "created", {"status": "queued"}, actor_user_id)
    db.commit()
    db.refresh(task)
    return task, True


def claim_next_task(db: Session, *, worker_id: str, lease_seconds: int = 60) -> AITask | None:
    """Atomically claim one available task; expired leases are eligible for recovery."""
    if not _WORKER_ID.fullmatch(worker_id):
        raise ValueError("Invalid worker_id")
    if not 5 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 5 and 3600")
    now = datetime.now(timezone.utc)

    # Expired tasks that exhausted their attempts are terminal, never left running forever.
    exhausted = db.execute(update(AITask).where(
        AITask.status == "running",
        AITask.lease_expires_at <= now,
        AITask.attempt_count >= AITask.max_attempts,
    ).values(
        status="failed", claimed_by=None, lease_expires_at=None,
        error_code="worker_lease_exhausted", completed_at=now, updated_at=now,
    ).execution_options(synchronize_session=False))
    if exhausted.rowcount:
        db.commit()

    eligible = or_(
        and_(AITask.status == "queued", AITask.available_at <= now),
        and_(AITask.status == "running", AITask.lease_expires_at <= now),
    )
    candidate = db.scalar(
        select(AITask).where(
            eligible,
            AITask.attempt_count < AITask.max_attempts,
        ).order_by(AITask.available_at, AITask.created_at).with_for_update(skip_locked=True).limit(1)
    )
    if candidate is None:
        db.commit()
        return None

    task_id = candidate.id
    previous_status = candidate.status
    previous_attempt = candidate.attempt_count
    changed = db.execute(update(AITask).where(
        AITask.id == task_id,
        AITask.attempt_count == previous_attempt,
        or_(
            and_(AITask.status == "queued", AITask.available_at <= now),
            and_(AITask.status == "running", AITask.lease_expires_at <= now),
        ),
    ).values(
        status="running",
        claimed_by=worker_id,
        attempt_count=AITask.attempt_count + 1,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        updated_at=now,
        error_code=None,
    ).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        return None

    db.expire_all()
    task = db.get(AITask, task_id)
    assert task is not None
    if previous_status == "running":
        db.execute(update(AITaskStep).where(
            AITaskStep.task_id == task_id,
            AITaskStep.status == "running",
        ).values(status="abandoned", error_code="worker_lease_expired", completed_at=now))
        _event(db, task, "lease_expired", {"attempt": previous_attempt})
    _event(db, task, "claimed", {"worker_id": worker_id, "attempt": task.attempt_count})
    db.commit()
    db.refresh(task)
    return task


def heartbeat_task(
    db: Session, *, task_id: str, worker_id: str, lease_seconds: int = 60,
) -> None:
    if not _WORKER_ID.fullmatch(worker_id) or not 5 <= lease_seconds <= 3600:
        raise ValueError("Invalid worker identity or lease duration")
    now = datetime.now(timezone.utc)
    changed = db.execute(update(AITask).where(
        AITask.id == task_id,
        AITask.status == "running",
        AITask.claimed_by == worker_id,
        AITask.lease_expires_at > now,
    ).values(
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        updated_at=now,
    ).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        raise TaskLeaseLost("Worker lease is no longer active")
    db.expire_all()
    task = db.get(AITask, task_id)
    assert task is not None
    _event(db, task, "heartbeat", {"worker_id": worker_id})
    db.commit()


def start_task_step(db: Session, *, task: AITask, worker_id: str, step_name: str) -> AITaskStep:
    if not _WORKER_ID.fullmatch(worker_id) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,100}", step_name):
        raise ValueError("Invalid worker identity or step name")
    live = db.scalar(select(AITask.id).where(
        AITask.id == task.id,
        AITask.status == "running",
        AITask.claimed_by == worker_id,
        AITask.lease_expires_at > datetime.now(timezone.utc),
    ))
    if live is None:
        raise TaskLeaseLost("Worker lease is no longer active")
    step = AITaskStep(
        task_id=task.id,
        organization_id=task.organization_id,
        sequence_number=task.attempt_count,
        step_name=step_name,
        input_sha256=task.request_sha256,
    )
    db.add(step)
    db.commit()
    db.refresh(step)
    return step


def complete_task(
    db: Session, *, task_id: str, worker_id: str, result_payload: dict[str, Any],
) -> None:
    _, output_digest = _canonical_json(result_payload)
    now = datetime.now(timezone.utc)
    changed = db.execute(update(AITask).where(
        AITask.id == task_id,
        AITask.status == "running",
        AITask.claimed_by == worker_id,
        AITask.lease_expires_at > now,
    ).values(
        status="succeeded",
        result_payload=result_payload,
        claimed_by=None,
        lease_expires_at=None,
        completed_at=now,
        updated_at=now,
        error_code=None,
    ).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        raise TaskLeaseLost("Worker lease is no longer active")
    db.expire_all()
    task = db.get(AITask, task_id)
    assert task is not None
    db.execute(update(AITaskStep).where(
        AITaskStep.task_id == task_id,
        AITaskStep.sequence_number == task.attempt_count,
        AITaskStep.status == "running",
    ).values(status="succeeded", output_sha256=output_digest, completed_at=now))
    _event(db, task, "completed", {"attempt": task.attempt_count})
    db.commit()


def fail_task(
    db: Session, *, task_id: str, worker_id: str, error_code: str = "handler_failed",
    retryable: bool = False,
) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", error_code):
        raise ValueError("Invalid error code")
    now = datetime.now(timezone.utc)
    task = db.scalar(select(AITask).where(
        AITask.id == task_id,
        AITask.status == "running",
        AITask.claimed_by == worker_id,
        AITask.lease_expires_at > now,
    ))
    if task is None:
        db.rollback()
        raise TaskLeaseLost("Worker lease is no longer active")
    will_retry = retryable and task.attempt_count < task.max_attempts
    task.status = "queued" if will_retry else "failed"
    task.available_at = now + timedelta(seconds=min(2 ** task.attempt_count, 300)) if will_retry else task.available_at
    task.claimed_by = None
    task.lease_expires_at = None
    task.heartbeat_at = None
    task.error_code = error_code
    task.completed_at = None if will_retry else now
    task.updated_at = now
    db.execute(update(AITaskStep).where(
        AITaskStep.task_id == task_id,
        AITaskStep.sequence_number == task.attempt_count,
        AITaskStep.status == "running",
    ).values(status="failed", error_code=error_code, completed_at=now))
    _event(db, task, "retry_scheduled" if will_retry else "failed", {
        "attempt": task.attempt_count, "error_code": error_code,
    })
    db.commit()


def cancel_queued_task(
    db: Session, *, task_id: str, organization_id: str, actor_user_id: str,
) -> None:
    now = datetime.now(timezone.utc)
    task = db.scalar(select(AITask).where(
        AITask.id == task_id,
        AITask.organization_id == organization_id,
        AITask.created_by_user_id == actor_user_id,
        AITask.status == "queued",
    ))
    if task is None:
        raise TaskStateConflict("Only your queued tasks can be cancelled")
    task.status = "cancelled"
    task.completed_at = now
    task.updated_at = now
    _event(db, task, "cancelled", {"status": "cancelled"}, actor_user_id)
    db.commit()


def run_once(
    db: Session,
    *,
    worker_id: str,
    handlers: dict[str, Any] | None = None,
    context_handlers: dict[str, Any] | None = None,
    lease_seconds: int = 60,
) -> bool:
    """Execute one statically registered handler; no dynamic imports or code execution."""
    task = claim_next_task(db, worker_id=worker_id, lease_seconds=lease_seconds)
    if task is None:
        return False
    handler = (handlers or {}).get(task.task_type)
    context_handler = (context_handlers or {}).get(task.task_type)
    if handler is None and context_handler is None:
        fail_task(db, task_id=task.id, worker_id=worker_id, error_code="handler_not_registered")
        return True
    start_task_step(db, task=task, worker_id=worker_id, step_name=task.task_type)
    try:
        if context_handler is not None:
            result = context_handler(TaskExecutionContext(
                db=db, task=task, worker_id=worker_id, lease_seconds=lease_seconds
            ))
        else:
            result = handler(dict(task.input_payload))
        if not isinstance(result, dict):
            raise ValueError("Task handler must return a JSON object")
        complete_task(db, task_id=task.id, worker_id=worker_id, result_payload=result)
    except TaskLeaseLost:
        raise
    except RetryableTaskError as exc:
        fail_task(
            db, task_id=task.id, worker_id=worker_id,
            error_code=exc.error_code, retryable=True,
        )
    except PermanentTaskError as exc:
        fail_task(
            db, task_id=task.id, worker_id=worker_id,
            error_code=exc.error_code, retryable=False,
        )
    except Exception:
        fail_task(db, task_id=task.id, worker_id=worker_id, error_code="handler_failed")
    return True
