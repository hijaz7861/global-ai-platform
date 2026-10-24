"""End-to-end tests for email verification and login gating."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import EmailVerificationToken, User, utc_now


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "require_email_verification", True)
    delivered = {}
    monkeypatch.setattr(
        "app.api.routes.identity.send_email_verification",
        lambda recipient, token: delivered.update(recipient=recipient, token=token),
    )

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        test_client.delivered_verification = delivered
        test_client.testing_sessions = sessions
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def register(client, email="verify@example.com"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-12"},
    )


def login(client, email="verify@example.com"):
    return client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": "correct-horse-battery-12"},
    )


def test_unverified_user_cannot_login_then_single_use_token_verifies(client):
    response = register(client)
    assert response.status_code == 201
    assert response.json()["status"] == "pending_verification"
    assert client.delivered_verification["recipient"] == "verify@example.com"
    assert login(client).status_code == 403

    token = client.delivered_verification["token"]
    db = client.testing_sessions()
    try:
        record = db.scalar(select(EmailVerificationToken))
        assert record is not None
        assert token not in record.token_hash
        assert record.expires_at is not None
    finally:
        db.close()

    confirmed = client.post("/api/v1/auth/email-verification/confirm", json={"token": token})
    assert confirmed.status_code == 204
    assert login(client).status_code == 200
    assert client.post("/api/v1/auth/email-verification/confirm", json={"token": token}).status_code == 400


def test_expired_verification_token_is_rejected(client):
    assert register(client).status_code == 201
    token = client.delivered_verification["token"]
    db = client.testing_sessions()
    try:
        record = db.scalar(select(EmailVerificationToken))
        record.expires_at = utc_now() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    assert client.post("/api/v1/auth/email-verification/confirm", json={"token": token}).status_code == 400


def test_resend_response_does_not_disclose_account_existence(client):
    register(client)
    known = client.post(
        "/api/v1/auth/email-verification/request",
        json={"email": "verify@example.com"},
    )
    unknown = client.post(
        "/api/v1/auth/email-verification/request",
        json={"email": "unknown@example.com"},
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
