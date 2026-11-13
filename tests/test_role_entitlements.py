"""Tests for independent user role applications and approval gates."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import identity as identity_routes
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import RoleDefinition, RoleEntitlementEvent, UserRoleEntitlement, utc_now
from app.services.role_entitlement_expiry import expire_due_role_entitlements

_EMAIL_TOKENS: dict[str, str] = {}
_TEST_SESSION_FACTORY = None


@pytest.fixture
def client(monkeypatch):
    global _TEST_SESSION_FACTORY
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TRIGGER role_entitlement_events_no_update
            BEFORE UPDATE ON role_entitlement_events
            BEGIN
                SELECT RAISE(ABORT, 'role entitlement events are append-only');
            END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER role_entitlement_events_no_delete
            BEFORE DELETE ON role_entitlement_events
            BEGIN
                SELECT RAISE(ABORT, 'role entitlement events are append-only');
            END
        """)
    _TEST_SESSION_FACTORY = TestingSessionLocal
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform@example.com")
    monkeypatch.setattr(settings, "require_email_verification", True)
    _EMAIL_TOKENS.clear()
    monkeypatch.setattr(
        identity_routes,
        "send_email_verification",
        lambda email, token: _EMAIL_TOKENS.__setitem__(email, token),
    )
    with TestingSessionLocal() as db:
        db.add_all([
            RoleDefinition(role_key="student", display_name="Student", description="Student account", min_age=0,
                           fee_amount_minor=0, fee_currency="PKR", requires_review=True, is_active=True),
            RoleDefinition(role_key="teacher", display_name="Teacher", description="Teacher profile", min_age=18,
                           fee_amount_minor=20_000, fee_currency="PKR", requires_review=True, is_active=True),
            RoleDefinition(role_key="contractor", display_name="Contractor", description="Contractor profile", min_age=18,
                           fee_amount_minor=50_000, fee_currency="PKR", requires_review=True, is_active=True),
            RoleDefinition(role_key="supplier", display_name="Supplier", description="Supplier profile", min_age=18,
                           fee_amount_minor=None, fee_currency="PKR", requires_review=True, is_active=True),
        ])
        db.commit()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    _EMAIL_TOKENS.clear()
    _TEST_SESSION_FACTORY = None


def register(client: TestClient, email: str, *, verify: bool = True) -> str:
    password = "correct-horse-battery-12"
    created = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert created.status_code == 201, created.text
    if verify:
        raw_token = _EMAIL_TOKENS.pop(email)
        verified = client.post("/api/v1/auth/email-verification/confirm", json={"token": raw_token})
        assert verified.status_code == 204, verified.text
    token = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert token.status_code == 200, token.text
    return token.json()["access_token"]


def test_user_can_apply_for_multiple_independent_roles(client):
    token = register(client, "learner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    student = client.post("/api/v1/me/role-entitlements", headers=headers, json={"role_key": "student"})
    teacher = client.post("/api/v1/me/role-entitlements", headers=headers, json={"role_key": "teacher"})
    assert student.status_code == 201, student.text
    assert teacher.status_code == 201, teacher.text
    assert student.json()["status"] == "pending_review"
    assert student.json()["payment_status"] == "not_required"
    assert teacher.json()["status"] == "pending_payment"
    assert teacher.json()["fee_amount_minor"] == 20_000
    duplicate = client.post("/api/v1/me/role-entitlements", headers=headers, json={"role_key": "student"})
    assert duplicate.status_code == 409
    roles = client.get("/api/v1/me/role-entitlements", headers=headers)
    assert roles.status_code == 200
    assert {row["role_key"] for row in roles.json()} == {"student", "teacher"}


def test_unverified_email_cannot_apply_even_when_global_toggle_is_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "require_email_verification", False)
    token = register(client, "unverified@example.com", verify=False)
    response = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {token}"},
        json={"role_key": "student"},
    )
    assert response.status_code == 403
    assert "verified email" in response.json()["detail"].lower()


def test_role_with_unconfigured_fee_cannot_be_requested(client):
    token = register(client, "supplier@example.com")
    response = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {token}"},
        json={"role_key": "supplier"},
    )
    assert response.status_code == 409


def test_paid_role_cannot_be_activated_without_verified_payment(client):
    user_token = register(client, "teacher@example.com")
    admin_token = register(client, "platform@example.com")
    application = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"role_key": "teacher"},
    )
    entitlement_id = application.json()["id"]
    review = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"decision": "approve", "age_verified": True},
    )
    assert review.status_code == 409
    assert "payment" in review.json()["detail"].lower()


def test_paid_role_requires_admin_payment_and_age_verification(client):
    user_token = register(client, "contractor@example.com")
    admin_token = register(client, "platform@example.com")
    application = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"role_key": "contractor", "payment_reference": "MANUAL-REF-001"},
    )
    entitlement_id = application.json()["id"]
    missing_age = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"decision": "approve", "payment_verified": True},
    )
    assert missing_age.status_code == 409
    assert "age" in missing_age.json()["detail"].lower()
    response = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"decision": "approve", "payment_verified": True, "age_verified": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert response.json()["payment_status"] == "verified"
    assert response.json()["age_verified"] is True


def test_non_admin_cannot_review_role_applications(client):
    user_token = register(client, "ordinary@example.com")
    application = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"role_key": "student"},
    )
    response = client.post(
        f"/api/v1/platform/role-entitlements/{application.json()['id']}/review",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"decision": "approve"},
    )
    assert response.status_code == 403


def test_rejected_application_can_be_reapplied_for(client):
    user_token = register(client, "reapply@example.com")
    admin_token = register(client, "platform@example.com")
    headers = {"Authorization": f"Bearer {user_token}"}
    first = client.post("/api/v1/me/role-entitlements", headers=headers, json={"role_key": "student"})
    assert first.status_code == 201
    rejected = client.post(
        f"/api/v1/platform/role-entitlements/{first.json()['id']}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"decision": "reject", "notes": "Test rejection"},
    )
    assert rejected.status_code == 200
    second = client.post("/api/v1/me/role-entitlements", headers=headers, json={"role_key": "student"})
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]


def test_admin_can_suspend_reinstate_and_revoke_role(client):
    user_token = register(client, "lifecycle@example.com")
    admin_token = register(client, "platform@example.com")
    application = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"role_key": "student"},
    )
    entitlement_id = application.json()["id"]
    approved = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"decision": "approve"},
    )
    assert approved.status_code == 200
    suspended = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/lifecycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "suspend"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"
    reinstated = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/lifecycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "reinstate"},
    )
    assert reinstated.status_code == 200
    assert reinstated.json()["status"] == "active"
    revoked = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/lifecycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "revoke"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    terminal_reinstate = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/lifecycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "reinstate"},
    )
    assert terminal_reinstate.status_code == 409



def test_role_entitlement_events_are_immutable_and_survive_entitlement_deletion(client):
    token = register(client, "audit-history@example.com")
    application = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {token}"},
        json={"role_key": "student"},
    )
    assert application.status_code == 201, application.text
    entitlement_id = application.json()["id"]

    with _TEST_SESSION_FACTORY() as db:
        event = db.scalar(
            select(RoleEntitlementEvent)
            .where(RoleEntitlementEvent.entitlement_id == entitlement_id)
            .order_by(RoleEntitlementEvent.created_at.desc())
        )
        assert event is not None
        event_id = event.id
        db.delete(db.get(UserRoleEntitlement, entitlement_id))
        db.commit()

    with _TEST_SESSION_FACTORY() as check_db:
        assert check_db.get(RoleEntitlementEvent, event_id) is not None
        with pytest.raises(IntegrityError):
            check_db.execute(
                update(RoleEntitlementEvent)
                .where(RoleEntitlementEvent.id == event_id)
                .values(event_type="tampered")
            )
            check_db.commit()
        check_db.rollback()

        with pytest.raises(IntegrityError):
            check_db.execute(delete(RoleEntitlementEvent).where(RoleEntitlementEvent.id == event_id))
            check_db.commit()
        check_db.rollback()


def test_expired_role_entitlement_can_be_reapplied(client):
    user_token = register(client, "expired-reapply@example.com")
    admin_token = register(client, "platform@example.com")
    headers = {"Authorization": f"Bearer {user_token}"}
    application = client.post(
        "/api/v1/me/role-entitlements",
        headers=headers,
        json={"role_key": "student"},
    )
    assert application.status_code == 201, application.text
    entitlement_id = application.json()["id"]

    approved = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"decision": "approve", "validity_days": 1},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"

    with _TEST_SESSION_FACTORY() as db:
        now = utc_now()
        entitlement = db.get(UserRoleEntitlement, entitlement_id)
        entitlement.expires_at = now - timedelta(seconds=1)
        db.commit()
        expired_ids = expire_due_role_entitlements(db, now=now)
        db.commit()
    assert expired_ids == [entitlement_id]

    reapplied = client.post(
        "/api/v1/me/role-entitlements",
        headers=headers,
        json={"role_key": "student"},
    )
    assert reapplied.status_code == 201, reapplied.text
    assert reapplied.json()["id"] != entitlement_id
