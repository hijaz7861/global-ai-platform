"""Provider circuit-breaker state machine and audit redaction tests."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.ai.provider_health import (
    claim_provider_attempt,
    get_provider_health_snapshot,
    record_provider_failure,
    record_provider_success,
    release_provider_attempt,
)
from app.db import Base
from app.models import AuditEvent


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _record_failure(db, *, provider="test-provider", model="private-model-name", now, threshold=2):
    record_provider_failure(
        db,
        provider_name=provider,
        model_name=model,
        retryable=True,
        failure_threshold=threshold,
        cooldown_seconds=30,
        error_code="http_5xx",
        now=now,
    )


def test_retryable_failures_open_circuit_and_success_closes_it(db):
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    assert claim_provider_attempt(
        db, provider_name="test-provider", model_name="private-model-name",
        probe_lease_seconds=30, now=now,
    )
    _record_failure(db, now=now)
    assert get_provider_health_snapshot(
        db, provider_name="test-provider", model_name="private-model-name"
    )["state"] == "closed"

    _record_failure(db, now=now + timedelta(seconds=1))
    opened = get_provider_health_snapshot(
        db, provider_name="test-provider", model_name="private-model-name"
    )
    assert opened["state"] == "open"
    assert opened["consecutive_failures"] == 2
    assert opened["model_fingerprint"] != "private-model-name"

    assert not claim_provider_attempt(
        db, provider_name="test-provider", model_name="private-model-name",
        probe_lease_seconds=30, now=now + timedelta(seconds=10),
    )
    assert claim_provider_attempt(
        db, provider_name="test-provider", model_name="private-model-name",
        probe_lease_seconds=30, now=now + timedelta(seconds=32),
    )
    assert get_provider_health_snapshot(
        db, provider_name="test-provider", model_name="private-model-name"
    )["state"] == "half_open"
    assert not claim_provider_attempt(
        db, provider_name="test-provider", model_name="private-model-name",
        probe_lease_seconds=30, now=now + timedelta(seconds=33),
    )

    record_provider_success(
        db, provider_name="test-provider", model_name="private-model-name",
        now=now + timedelta(seconds=34),
    )
    closed = get_provider_health_snapshot(
        db, provider_name="test-provider", model_name="private-model-name"
    )
    assert closed["state"] == "closed"
    assert closed["consecutive_failures"] == 0
    assert closed["open_until"] is None


def test_non_retryable_error_does_not_open_or_poison_provider_health(db):
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    record_provider_failure(
        db,
        provider_name="test-provider",
        model_name="private-model-name",
        retryable=False,
        failure_threshold=1,
        cooldown_seconds=30,
        error_code="non_retryable",
        now=now,
    )
    state = get_provider_health_snapshot(
        db, provider_name="test-provider", model_name="private-model-name"
    )
    assert state["state"] == "closed"
    assert state["consecutive_failures"] == 0
    assert claim_provider_attempt(
        db, provider_name="test-provider", model_name="private-model-name",
        probe_lease_seconds=30, now=now + timedelta(seconds=1),
    )


def test_abandoned_half_open_probe_can_be_reclaimed_after_release(db):
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    _record_failure(db, now=now, threshold=1)
    assert claim_provider_attempt(
        db, provider_name="test-provider", model_name="private-model-name",
        probe_lease_seconds=5, now=now + timedelta(seconds=31),
    )
    release_provider_attempt(
        db, provider_name="test-provider", model_name="private-model-name",
        now=now + timedelta(seconds=32),
    )
    assert get_provider_health_snapshot(
        db, provider_name="test-provider", model_name="private-model-name"
    )["state"] == "open"
    assert claim_provider_attempt(
        db, provider_name="test-provider", model_name="private-model-name",
        probe_lease_seconds=5, now=now + timedelta(seconds=33),
    )


def test_health_isolated_by_provider_and_model_fingerprint(db):
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    _record_failure(db, provider="provider-a", model="model-a", now=now, threshold=1)
    a = get_provider_health_snapshot(db, provider_name="provider-a", model_name="model-a")
    b = get_provider_health_snapshot(db, provider_name="provider-b", model_name="model-a")
    c = get_provider_health_snapshot(db, provider_name="provider-a", model_name="model-b")
    assert a["state"] == "open"
    assert b["state"] == "closed"
    assert c["state"] == "closed"


def test_audit_state_contains_only_sanitized_fingerprints_and_allowlisted_fields(db):
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    _record_failure(
        db,
        provider="test-provider",
        model="model-name-must-not-be-stored",
        now=now,
        threshold=1,
    )
    event = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.entity_type == "ai_provider_health")
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    assert event is not None
    assert len(event.entity_id) == 64
    assert "model-name-must-not-be-stored" not in event.entity_id
    assert event.details == {
        "provider_state": "open",
        "consecutive_failures": 1,
        "open_until_epoch": int((now + timedelta(seconds=30)).timestamp()),
        "probe_until_epoch": 0,
        "error_code": "http_5xx",
    }
    assert "api_key" not in event.details
    assert "prompt" not in event.details


def test_circuit_state_isolated_between_primary_and_fallback_routes(db):
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    record_provider_failure(
        db,
        provider_name="same-provider",
        model_name="same-model",
        route="primary",
        retryable=True,
        failure_threshold=1,
        cooldown_seconds=30,
        now=now,
    )
    primary = get_provider_health_snapshot(
        db, provider_name="same-provider", model_name="same-model", route="primary"
    )
    fallback = get_provider_health_snapshot(
        db, provider_name="same-provider", model_name="same-model", route="fallback"
    )
    assert primary["state"] == "open"
    assert fallback["state"] == "closed"
