"""Security and recovery tests for encrypted durable agent checkpoints."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.ai.agents.checkpoints import (
    AgentCheckpointError,
    _decrypt,
    _encrypt,
    load_agent_checkpoint,
    save_agent_checkpoint,
)
from app.ai.runtime.tasks import claim_next_task, create_task, start_task_step
from app.core.config import settings
from app.db import Base
from app.models import AIAgentCheckpoint, Organization, User


@pytest.fixture
def checkpoint_context(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "checkpoint-test-secret-key-0123456789")
    monkeypatch.setattr(settings, "previous_secret_keys", "")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = factory()
    user = User(email="checkpoint-owner@example.com", password_hash="test-hash")
    organization = Organization(name="Checkpoint Organization")
    db.add_all([user, organization])
    db.commit()
    db.refresh(user)
    db.refresh(organization)
    create_task(
        db,
        organization_id=organization.id,
        actor_user_id=user.id,
        task_type="agent.run",
        title="Checkpoint test",
        input_payload={"goal": "Test safe recovery"},
        idempotency_key="checkpoint-task-0001",
        max_attempts=3,
    )
    worker_id = "checkpoint-worker-a"
    task = claim_next_task(db, worker_id=worker_id, lease_seconds=60)
    assert task is not None
    start_task_step(db, task=task, worker_id=worker_id, step_name="agent.run")
    yield db, factory, task, user, organization, worker_id
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_checkpoint_ciphertext_round_trips_and_binds_task_identity(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "checkpoint-test-secret-key-0123456789")
    state = {"history": [{"tool": "organization.profile", "result": {"name": "private"}}]}
    encrypted = _encrypt(state, task_id="task-a", organization_id="org-a", revision=4)

    assert "private" not in encrypted
    assert _decrypt(encrypted, task_id="task-a", organization_id="org-a", revision=4) == state
    with pytest.raises(AgentCheckpointError):
        _decrypt(encrypted, task_id="task-b", organization_id="org-a", revision=4)


def test_checkpoint_decrypts_with_previous_signing_key(monkeypatch):
    old_key = "checkpoint-old-secret-key-0123456789"
    new_key = "checkpoint-new-secret-key-0123456789"
    monkeypatch.setattr(settings, "secret_key", old_key)
    encrypted = _encrypt({"turn": 2}, task_id="task-a", organization_id="org-a", revision=1)

    monkeypatch.setattr(settings, "secret_key", new_key)
    monkeypatch.setattr(settings, "previous_secret_keys", old_key)
    assert _decrypt(
        encrypted, task_id="task-a", organization_id="org-a", revision=1
    ) == {"turn": 2}


def test_checkpoint_save_and_load_are_lease_bound(checkpoint_context):
    db, _, task, _, organization, worker_id = checkpoint_context
    state = {"history": [], "decision": None}
    saved = save_agent_checkpoint(
        db,
        task=task,
        worker_id=worker_id,
        phase="ready",
        turn=1,
        state=state,
        lease_seconds=60,
    )
    loaded = load_agent_checkpoint(
        db, task_id=task.id, organization_id=organization.id
    )

    assert saved["revision"] == 1
    assert loaded["phase"] == "ready"
    assert loaded["state"] == state
    row = db.scalar(select(AIAgentCheckpoint).where(AIAgentCheckpoint.task_id == task.id))
    assert row is not None
    assert row.state_sha256


def test_expired_agent_with_ambiguous_provider_call_is_not_replayed(checkpoint_context):
    db, _, task, _, _, worker_id = checkpoint_context
    save_agent_checkpoint(
        db,
        task=task,
        worker_id=worker_id,
        phase="model_call_started",
        turn=1,
        state={"history": []},
        lease_seconds=60,
    )
    task.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    assert claim_next_task(db, worker_id="checkpoint-worker-b", lease_seconds=60) is None
    db.refresh(task)
    assert task.status == "failed"
    assert task.error_code == "agent_call_outcome_unknown"


def test_expired_agent_with_saved_decision_can_resume(checkpoint_context):
    db, _, task, _, organization, worker_id = checkpoint_context
    state = {
        "history": [],
        "decision": {"action": "final", "tool": None, "arguments": {}, "answer": "saved"},
    }
    save_agent_checkpoint(
        db,
        task=task,
        worker_id=worker_id,
        phase="decision_saved",
        turn=1,
        state=state,
        lease_seconds=60,
    )
    task.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    reclaimed = claim_next_task(db, worker_id="checkpoint-worker-b", lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.id == task.id
    assert reclaimed.claimed_by == "checkpoint-worker-b"
    assert reclaimed.attempt_count == 2
    assert load_agent_checkpoint(
        db, task_id=task.id, organization_id=organization.id
    )["state"] == state
