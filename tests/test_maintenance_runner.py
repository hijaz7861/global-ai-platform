"""Tests for the one-shot scheduled maintenance runner."""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base
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