"""Tests for the one-shot scheduled maintenance runner."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base
from app.ai.memory.service import create_memory
from app.maintenance import run_maintenance_once


def test_maintenance_runner_runs_bounded_cleanup_and_commits_audit_event():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        now = datetime.now(timezone.utc)
        result = run_maintenance_once(factory, order_limit=7, now=now)
        assert result["expired_orders"] == 0
        assert result["expired_order_ids"] == []
        assert result["security_cleanup"]["email_verification_tokens_deleted"] == 0
        assert result["expired_memory_records_deleted"] == 0
        with factory() as db:
            event = db.query(models.AuditEvent).filter_by(action="platform.maintenance_completed").one()
            assert event.details == {"status": "completed"}
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_maintenance_runner_rejects_unbounded_batch_size():
    try:
        run_maintenance_once(order_limit=501)
    except ValueError as exc:
        assert "between 1 and 500" in str(exc)
    else:
        raise AssertionError("Expected invalid batch size to be rejected")


def test_maintenance_runner_purges_expired_memory_and_keeps_content_out_of_audit():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        now = datetime.now(timezone.utc)
        with factory() as db:
            user = models.User(
                email="memory-retention@example.com",
                password_hash="test-only-hash",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            memory = create_memory(
                db,
                actor_user_id=user.id,
                scope="user",
                organization_id=None,
                memory_type="reference",
                content="Expired private content must not enter audit logs",
                expires_at=now + timedelta(seconds=30),
            )
            memory_id = memory.id
            memory.expires_at = now - timedelta(seconds=1)
            db.commit()

        result = run_maintenance_once(factory, order_limit=7, now=now)
        assert result["expired_memory_records_deleted"] == 1

        with factory() as db:
            assert db.get(models.AIMemoryRecord, memory_id) is None
            event = db.query(models.AuditEvent).filter_by(
                action="ai.memory.expired_purged",
                entity_id=memory_id,
            ).one()
            assert event.details == {"scope": "user", "reason": "expired"}
            assert "Expired private content" not in str(event.details)
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
