"""TOTP MFA enrollment, login, replay, disable, and secret-at-rest tests."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.core.mfa import generate_totp_code
from app.db import Base, get_db
from app.main import app
from app.models import User


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "mfa_enrollment_expire_minutes", 10)

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        test_client.testing_sessions = sessions
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def register_and_login(client: TestClient, email: str) -> tuple[str, str]:
    password = "correct-horse-battery-12"
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": password},
    )
    assert logged_in.status_code == 200, logged_in.text
    return password, logged_in.json()["access_token"]


def enroll_and_confirm(client: TestClient, email: str):
    password, access_token = register_and_login(client, email)
    headers = {"Authorization": f"Bearer {access_token}"}
    enrolled = client.post(
        "/api/v1/auth/mfa/enroll",
        headers=headers,
        json={"password": password},
    )
    assert enrolled.status_code == 200, enrolled.text
    secret = enrolled.json()["secret"]
    assert enrolled.json()["otpauth_uri"].startswith("otpauth://totp/")
    assert enrolled.json()["expires_in_seconds"] == 600
    confirmed = client.post(
        "/api/v1/auth/mfa/confirm",
        headers=headers,
        json={"code": generate_totp_code(secret)},
    )
    assert confirmed.status_code == 204
    return password, access_token, secret


def test_mfa_enrollment_encrypts_secret_and_login_rejects_missing_or_replayed_code(client):
    password, _, secret = enroll_and_confirm(client, "mfa-login@example.com")
    db = client.testing_sessions()
    try:
        user = db.scalar(select(User).where(User.email == "mfa-login@example.com"))
        assert user is not None and user.mfa_enabled
        assert user.mfa_secret_encrypted != secret
        assert secret not in user.mfa_secret_encrypted
    finally:
        db.close()

    missing = client.post(
        "/api/v1/auth/token",
        json={"email": "mfa-login@example.com", "password": password},
    )
    assert missing.status_code == 401

    # Enrollment confirmation consumed this time step, so the same TOTP cannot authenticate.
    same_step = client.post(
        "/api/v1/auth/token",
        json={
            "email": "mfa-login@example.com",
            "password": password,
            "totp_code": generate_totp_code(secret),
        },
    )
    assert same_step.status_code == 401

    future_step = int(time.time() // 30) + 1
    code = generate_totp_code(secret, future_step)
    valid = client.post(
        "/api/v1/auth/token",
        json={"email": "mfa-login@example.com", "password": password, "totp_code": code},
    )
    assert valid.status_code == 200, valid.text
    replay = client.post(
        "/api/v1/auth/token",
        json={"email": "mfa-login@example.com", "password": password, "totp_code": code},
    )
    assert replay.status_code == 401


def test_mfa_disable_requires_password_and_fresh_code_and_revokes_sessions(client):
    password, access_token, secret = enroll_and_confirm(client, "mfa-disable@example.com")
    headers = {"Authorization": f"Bearer {access_token}"}
    wrong_password = client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"password": "not-the-password", "code": generate_totp_code(secret, int(time.time() // 30) + 1)},
    )
    assert wrong_password.status_code == 401

    code = generate_totp_code(secret, int(time.time() // 30) + 1)
    disabled = client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"password": password, "code": code},
    )
    assert disabled.status_code == 204
    # MFA disable increments auth_version and revokes refresh families, so old access is invalid.
    assert client.get("/api/v1/organizations", headers=headers).status_code == 401
    login = client.post(
        "/api/v1/auth/token",
        json={"email": "mfa-disable@example.com", "password": password},
    )
    assert login.status_code == 200, login.text
