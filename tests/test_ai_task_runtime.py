"""Tests for durable AI task idempotency, leases, recovery, and API isolation."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.ai.runtime.tasks import (
    TaskConflict,
    TaskLeaseLost,
    TaskStateConflict,
    cancel_queued_task,
    claim_next_task,
    complete_task,
    create_task,
    heartbeat_task,
    run_once,
    start_task_step,
)
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import AITaskStep, Organization, User


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = factory()
    user = User(email="task-owner@example.com", password_hash="test-hash")
    organization = Organization(name="Task Organization")
    db.add_all([user, organization])
    db.commit()
    db.refresh(user)
    db.refresh(organization)
    yield db, factory, user, organization
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _create(db: Session, user: User, organization: Organization, **overrides):
    values = {
        "organization_id": organization.id,
        "actor_user_id": user.id,
        "task_type": "agent.inspect",
        "title": "Inspect repository",
        "input_payload": {"path": "app"},
        "idempotency_key": "task-key-0001",
    }
    values.update(overrides)
    return create_task(db, **values)


def test_task_creation_is_idempotent_and_payload_bound(database):
    db, _, user, organization = database
    task, created = _create(db, user, organization)
    same, created_again = _create(db, user, organization)

    assert created is True
    assert created_again is False
    assert same.id == task.id
    assert same.status == "queued"
    with pytest.raises(TaskConflict):
        _create(db, user, organization, input_payload={"path": "secrets"})


def test_idempotency_key_is_scoped_to_actor_and_organization(database):
    db, _, user, organization = database
    first, _ = _create(db, user, organization)
    another_user = User(email="other-task-owner@example.com", password_hash="test-hash")
    db.add(another_user)
    db.commit()
    second, created = _create(db, another_user, organization)
    assert created is True
    assert second.id != first.id


def test_worker_claim_is_single_use_and_completion_records_step(database):
    db, _, user, organization = database
    task, _ = _create(db, user, organization)
    claimed = claim_next_task(db, worker_id="worker-a", lease_seconds=30)

    assert claimed is not None
    assert claimed.id == task.id
    assert claimed.status == "running"
    assert claimed.attempt_count == 1
    assert claim_next_task(db, worker_id="worker-b") is None

    start_task_step(db, task=claimed, worker_id="worker-a", step_name="agent.inspect")
    complete_task(db, task_id=task.id, worker_id="worker-a", result_payload={"files": 3})
    db.refresh(task)
    step = db.scalar(select(AITaskStep).where(AITaskStep.task_id == task.id))

    assert task.status == "succeeded"
    assert task.result_payload == {"files": 3}
    assert step is not None and step.status == "succeeded"
    assert len(step.output_sha256) == 64


def test_expired_worker_lease_is_reclaimed_and_old_step_abandoned(database):
    db, _, user, organization = database
    task, _ = _create(db, user, organization)
    first = claim_next_task(db, worker_id="worker-a", lease_seconds=30)
    assert first is not None
    old_step = start_task_step(db, task=first, worker_id="worker-a", step_name="agent.inspect")
    first.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    second = claim_next_task(db, worker_id="worker-b", lease_seconds=30)
    db.refresh(old_step)

    assert second is not None and second.id == task.id
    assert second.claimed_by == "worker-b"
    assert second.attempt_count == 2
    assert old_step.status == "abandoned"
    with pytest.raises(TaskLeaseLost):
        heartbeat_task(db, task_id=task.id, worker_id="worker-a")


def test_exhausted_expired_lease_becomes_terminal_failure(database):
    db, _, user, organization = database
    task, _ = _create(db, user, organization, max_attempts=1)
    claimed = claim_next_task(db, worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    task.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    assert claim_next_task(db, worker_id="worker-b") is None
    db.refresh(task)
    assert task.status == "failed"
    assert task.error_code == "worker_lease_exhausted"


def test_worker_runs_only_registered_handler(database):
    db, _, user, organization = database
    task, _ = _create(db, user, organization)
    processed = run_once(
        db,
        worker_id="worker-a",
        handlers={"agent.inspect": lambda payload: {"received_path": payload["path"]}},
    )
    db.refresh(task)

    assert processed is True
    assert task.status == "succeeded"
    assert task.result_payload == {"received_path": "app"}


def test_unregistered_handler_fails_without_dynamic_execution(database):
    db, _, user, organization = database
    task, _ = _create(db, user, organization)
    assert run_once(db, worker_id="worker-a", handlers={}) is True
    db.refresh(task)
    assert task.status == "failed"
    assert task.error_code == "handler_not_registered"


def test_only_queued_tasks_can_be_cancelled(database):
    db, _, user, organization = database
    task, _ = _create(db, user, organization)
    cancel_queued_task(
        db, task_id=task.id, organization_id=organization.id, actor_user_id=user.id
    )
    db.refresh(task)
    assert task.status == "cancelled"
    with pytest.raises(TaskStateConflict):
        cancel_queued_task(
            db, task_id=task.id, organization_id=organization.id, actor_user_id=user.id
        )


@pytest.fixture
def api_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _owner(client: TestClient) -> tuple[str, str]:
    password = "correct-horse-battery-12"
    assert client.post(
        "/api/v1/auth/register",
        json={"email": "task-api-owner@example.com", "password": password},
    ).status_code == 201
    login = client.post(
        "/api/v1/auth/token",
        json={"email": "task-api-owner@example.com", "password": password},
    )
    token = login.json()["access_token"]
    org = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Task API Organization"},
    )
    assert org.status_code == 201, org.text
    return token, org.json()["id"]


def test_task_api_requires_idempotency_and_returns_safe_status(api_client):
    token, organization_id = _owner(api_client)
    url = f"/api/v1/organizations/{organization_id}/ai/tasks"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "task_type": "agent.inspect",
        "title": "Inspect repository",
        "input_payload": {"path": "app"},
    }
    assert api_client.post(url, headers=headers, json=payload).status_code == 422

    headers["Idempotency-Key"] = "task-key-api-001"
    first = api_client.post(url, headers=headers, json=payload)
    second = api_client.post(url, headers=headers, json=payload)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert "input_payload" not in first.json()


def test_task_api_rejects_reused_key_with_different_payload(api_client):
    token, organization_id = _owner(api_client)
    url = f"/api/v1/organizations/{organization_id}/ai/tasks"
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "task-key-api-002",
    }
    first = api_client.post(url, headers=headers, json={
        "task_type": "agent.inspect", "title": "Inspect repository",
        "input_payload": {"path": "app"},
    })
    second = api_client.post(url, headers=headers, json={
        "task_type": "agent.inspect", "title": "Inspect repository",
        "input_payload": {"path": "tests"},
    })
    assert first.status_code == 202
    assert second.status_code == 409



def test_context_handler_receives_tenant_bound_task_and_can_heartbeat(database):
    db, _, user, organization = database
    task, _ = _create(
        db, user, organization,
        task_type="agent.run",
        idempotency_key="context-handler-key-01",
    )
    observed = {}

    def handler(context):
        observed["task_id"] = context.task.id
        observed["organization_id"] = context.task.organization_id
        observed["worker_id"] = context.worker_id
        context.heartbeat()
        return {"task_id": context.task.id, "status": "context_received"}

    assert run_once(
        db,
        worker_id="context-worker",
        context_handlers={"agent.run": handler},
        lease_seconds=60,
    ) is True
    db.refresh(task)
    assert task.status == "succeeded"
    assert task.result_payload["status"] == "context_received"
    assert observed == {
        "task_id": task.id,
        "organization_id": organization.id,
        "worker_id": "context-worker",
    }
