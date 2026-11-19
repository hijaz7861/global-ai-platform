"""Tests for the durable database-coordinated maintenance scheduler."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base
from app.maintenance_scheduler import _scheduler_transaction, run_scheduler_once
from app.models import AuditEvent


@pytest.fixture
def scheduler_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield engine, factory
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_scheduler_runs_due_batch_once_and_uses_audit_checkpoint(scheduler_db):
    _, factory = scheduler_db
    now = datetime.now(timezone.utc)

    first = run_scheduler_once(
        factory,
        interval_seconds=3600,
        order_limit=7,
        now=now,
    )
    assert first["status"] == "completed"
    assert first["expired_orders"] == 0
    assert "expired_order_ids" not in first

    second = run_scheduler_once(
        factory,
        interval_seconds=3600,
        order_limit=7,
        now=now + timedelta(minutes=5),
    )
    assert second["status"] == "not_due"
    assert "next_run_at" in second

    with factory() as db:
        events = db.query(AuditEvent).filter_by(
            action="platform.maintenance_completed"
        ).all()
        assert len(events) == 1


def test_scheduler_coalesces_missed_intervals_into_one_recovery_run(scheduler_db):
    _, factory = scheduler_db
    now = datetime.now(timezone.utc)

    with factory() as db:
        db.add(
            AuditEvent(
                action="platform.maintenance_completed",
                entity_type="maintenance_job",
                entity_id="scheduled-maintenance",
                details={"status": "completed"},
                created_at=now - timedelta(days=7),
            )
        )
        db.commit()

    recovered = run_scheduler_once(
        factory,
        interval_seconds=3600,
        now=now,
    )
    assert recovered["status"] == "completed"

    follow_up = run_scheduler_once(
        factory,
        interval_seconds=3600,
        now=now + timedelta(minutes=5),
    )
    assert follow_up["status"] == "not_due"

    with factory() as db:
        assert db.query(AuditEvent).filter_by(
            action="platform.maintenance_completed"
        ).count() == 2


def test_scheduler_skips_when_another_local_scheduler_holds_lock(scheduler_db):
    _, factory = scheduler_db
    with factory() as db:
        with _scheduler_transaction(db, lock_name="scheduler-lock-test") as acquired:
            assert acquired is True
            result = run_scheduler_once(
                factory,
                interval_seconds=3600,
                lock_name="scheduler-lock-test",
            )
    assert result == {"status": "locked"}


@pytest.mark.parametrize(
    ("interval_seconds", "order_limit"),
    [(59, 100), (86_401, 100), (3600, 0), (3600, 501)],
)
def test_scheduler_rejects_unbounded_configuration(
    scheduler_db, interval_seconds, order_limit
):
    _, factory = scheduler_db
    with pytest.raises(ValueError):
        run_scheduler_once(
            factory,
            interval_seconds=interval_seconds,
            order_limit=order_limit,
        )
