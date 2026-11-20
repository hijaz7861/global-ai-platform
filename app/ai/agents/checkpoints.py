"""Encrypted, lease-bound checkpoints for resumable single-agent runs."""
from __future__ import annotations

import base64
from hashlib import sha256
import json
import secrets
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AIAgentCheckpoint, AITask

_MAX_STATE_BYTES = 32_768
_PHASES = {"ready", "model_call_started", "decision_saved", "tool_started", "final_saved"}


class AgentCheckpointError(RuntimeError):
    """Checkpoint state is missing, malformed, or cannot be authenticated."""


class AgentCheckpointKeyError(AgentCheckpointError):
    """A valid checkpoint encryption key is not configured."""


def _key(secret: str) -> bytes:
    if len(secret) < 32:
        raise AgentCheckpointKeyError("checkpoint_key_unavailable")
    return sha256(b"global-ai-platform:agent-checkpoint:v1:" + secret.encode("utf-8")).digest()


def _aad(task_id: str, organization_id: str, revision: int) -> bytes:
    return f"agent-checkpoint:v1:{task_id}:{organization_id}:{revision}".encode("utf-8")


def _encrypt(state: dict[str, Any], *, task_id: str, organization_id: str, revision: int) -> str:
    secret = settings.secret_key
    if not secret:
        raise AgentCheckpointKeyError("checkpoint_key_unavailable")
    try:
        raw = json.dumps(
            state, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise AgentCheckpointError("checkpoint_state_invalid") from exc
    if len(raw) > _MAX_STATE_BYTES:
        raise AgentCheckpointError("checkpoint_state_too_large")
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_key(secret)).encrypt(
        nonce, raw, _aad(task_id, organization_id, revision)
    )
    key_id = sha256(secret.encode("utf-8")).hexdigest()[:16]
    return f"v1.{key_id}.{base64.urlsafe_b64encode(nonce + encrypted).decode('ascii')}"


def _decrypt(envelope: str, *, task_id: str, organization_id: str, revision: int) -> dict[str, Any]:
    try:
        version, key_id, encoded = envelope.split(".", 2)
        if version != "v1":
            raise ValueError("unsupported checkpoint version")
        packed = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if len(packed) < 29:
            raise ValueError("truncated checkpoint")
        nonce, ciphertext = packed[:12], packed[12:]
    except (ValueError, UnicodeError) as exc:
        raise AgentCheckpointError("checkpoint_integrity_failed") from exc

    candidates = [settings.secret_key, *[
        item.strip() for item in settings.previous_secret_keys.split(",") if item.strip()
    ]]
    for secret in candidates:
        if not secret or sha256(secret.encode("utf-8")).hexdigest()[:16] != key_id:
            continue
        try:
            raw = AESGCM(_key(secret)).decrypt(
                nonce, ciphertext, _aad(task_id, organization_id, revision)
            )
            state = json.loads(raw.decode("utf-8"))
        except (InvalidTag, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise AgentCheckpointError("checkpoint_integrity_failed") from exc
        if not isinstance(state, dict):
            raise AgentCheckpointError("checkpoint_integrity_failed")
        return state
    raise AgentCheckpointKeyError("checkpoint_decryption_key_unavailable")


def load_agent_checkpoint(
    db: Session, *, task_id: str, organization_id: str,
) -> dict[str, Any] | None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    live.heartbeat_at = now
    live.lease_expires_at = now + timedelta(seconds=lease_seconds)
    live.updated_at = now
    row = db.scalar(select(AIAgentCheckpoint).where(
        AIAgentCheckpoint.task_id == task_id,
        AIAgentCheckpoint.organization_id == organization_id,
    ))
    if row is None:
        return None
    if sha256(row.state_ciphertext.encode("utf-8")).hexdigest() != row.state_sha256:
        raise AgentCheckpointError("checkpoint_integrity_failed")
    state = _decrypt(
        row.state_ciphertext,
        task_id=task_id,
        organization_id=organization_id,
        revision=row.revision,
    )
    if row.phase not in _PHASES or not 1 <= row.turn_number <= 5:
        raise AgentCheckpointError("checkpoint_integrity_failed")
    return {
        "phase": row.phase,
        "turn": row.turn_number,
        "revision": row.revision,
        "state": state,
    }


def save_agent_checkpoint(
    db: Session,
    *,
    task: AITask,
    worker_id: str,
    phase: str,
    turn: int,
    state: dict[str, Any],
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Persist one authenticated state transition while the caller owns a live task lease."""
    from app.ai.runtime.tasks import TaskCancelled, TaskLeaseLost

    if phase not in _PHASES or not 1 <= turn <= 5:
        raise ValueError("Invalid checkpoint phase or turn")
    live = db.scalar(select(AITask).where(
        AITask.id == task.id,
        AITask.organization_id == task.organization_id,
        AITask.status == "running",
        AITask.claimed_by == worker_id,
        AITask.lease_expires_at > __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    ).with_for_update())
    if live is None:
        db.rollback()
        raise TaskLeaseLost("Worker lease is no longer active")
    if live.cancel_requested_at is not None:
        db.rollback()
        raise TaskCancelled("Task cancellation acknowledged")

    row = db.scalar(select(AIAgentCheckpoint).where(
        AIAgentCheckpoint.task_id == task.id,
    ).with_for_update())
    revision = (row.revision + 1) if row is not None else 1
    ciphertext = _encrypt(
        state,
        task_id=task.id,
        organization_id=task.organization_id,
        revision=revision,
    )
    digest = sha256(ciphertext.encode("utf-8")).hexdigest()
    if row is None:
        row = AIAgentCheckpoint(
            task_id=task.id,
            organization_id=task.organization_id,
            revision=revision,
            turn_number=turn,
            phase=phase,
            state_ciphertext=ciphertext,
            state_sha256=digest,
        )
        db.add(row)
    else:
        row.revision = revision
        row.turn_number = turn
        row.phase = phase
        row.state_ciphertext = ciphertext
        row.state_sha256 = digest
    db.commit()
    return {"phase": phase, "turn": turn, "revision": revision, "state": state}
