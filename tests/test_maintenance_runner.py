"""Tests for the one-shot scheduled maintenance runner."""
from datetime import datetime, timedelta, timezone

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
        assert result["expired_role_entitlements"] == 0
        assert result["expired_role_entitlement_ids"] == []
        assert result["security_cleanup"]["email_verification_tokens_deleted"] == 0
        with factory() as db:
            event = db.query(models.AuditEvent).filter_by(action="platform.maintenance_completed").one()
            assert event.details == {"status": "completed"}
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_maintenance_expires_due_active_and_suspended_role_entitlements_atomically():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    now = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)
    try:
        with factory() as db:
            role = models.RoleDefinition(
                role_key="student",
                display_name="Student",
                description="Student role",
                min_age=0,
                fee_amount_minor=0,
                fee_currency="PKR",
                requires_review=True,
                is_active=True,
            )
            users = [
                models.User(email=f"role-expiry-{index}@example.com", password_hash="test-hash")
                for index in range(5)
            ]
            db.add(role)
            db.add_all(users)
            db.flush()

            entitlements = [
                models.UserRoleEntitlement(
                    user_id=users[0].id, role_key="student", status="active",
                    payment_status="not_required", fee_amount_minor=0, fee_currency="PKR",
                    activated_at=now - timedelta(days=30), expires_at=now - timedelta(seconds=1),
                ),
                models.UserRoleEntitlement(
                    user_id=users[1].id, role_key="student", status="suspended",
                    payment_status="not_required", fee_amount_minor=0, fee_currency="PKR",
                    activated_at=now - timedelta(days=30), expires_at=now - timedelta(seconds=1),
                ),
                models.UserRoleEntitlement(
                    user_id=users[2].id, role_key="student", status="active",
                    payment_status="not_required", fee_amount_minor=0, fee_currency="PKR",
                    activated_at=now - timedelta(days=1), expires_at=now + timedelta(days=1),
                ),
                models.UserRoleEntitlement(
                    user_id=users[3].id, role_key="student", status="active",
                    payment_status="not_required", fee_amount_minor=0, fee_currency="PKR",
                    activated_at=now - timedelta(days=1), expires_at=None,
                ),
                models.UserRoleEntitlement(
                    user_id=users[4].id, role_key="student", status="pending_review",
                    payment_status="not_required", fee_amount_minor=0, fee_currency="PKR",
                    expires_at=now - timedelta(days=1),
                ),
            ]
            db.add_all(entitlements)
            db.commit()
            expired_ids = {entitlements[0].id, entitlements[1].id}
            future_id, no_expiry_id, pending_id = entitlements[2].id, entitlements[3].id, entitlements[4].id

        result = run_maintenance_once(factory, role_entitlement_limit=10, now=now)
        assert result["expired_role_entitlements"] == 2
        assert set(result["expired_role_entitlement_ids"]) == expired_ids

        with factory() as db:
            statuses = {
                item.id: item.status
                for item in db.query(models.UserRoleEntitlement).all()
            }
            assert statuses == {
                entitlements[0].id: "expired",
                entitlements[1].id: "expired",
                future_id: "active",
                no_expiry_id: "active",
                pending_id: "pending_review",
            }
            events = db.query(models.RoleEntitlementEvent).filter(
                models.RoleEntitlementEvent.event_type == "expired"
            ).all()
            assert {event.entitlement_id for event in events} == expired_ids
            assert {event.details["previous_status"] for event in events} == {"active", "suspended"}
            assert db.query(models.AuditEvent).filter_by(action="role_entitlement.expired").count() == 2
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

def test_maintenance_runner_rejects_unbounded_role_expiry_batch_size():
    try:
        run_maintenance_once(role_entitlement_limit=501)
    except ValueError as exc:
        assert "role_entitlement_limit must be between 1 and 500" in str(exc)
    else:
        raise AssertionError("Expected invalid role entitlement batch size to be rejected")
