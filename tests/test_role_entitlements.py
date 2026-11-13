"""Tests for independent user role applications and approval gates."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform@example.com")

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


def register(client: TestClient, email: str) -> str:
    password = "correct-horse-battery-12"
    assert client.post("/api/v1/auth/register", json={"email": email, "password": password}).status_code == 201
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
    assert teacher.json()["fee_amount_minor"] == 20000
    roles = client.get("/api/v1/me/role-entitlements", headers=headers)
    assert roles.status_code == 200
    assert {row["role_key"] for row in roles.json()} == {"student", "teacher"}


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
    headers = {"Authorization": f"Bearer {user_token}"}
    application = client.post("/api/v1/me/role-entitlements", headers=headers, json={"role_key": "teacher"})
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
