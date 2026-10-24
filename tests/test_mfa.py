"""TOTP MFA enrollment, login, replay, disable, and secret-at-rest tests."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.core.mfa import decrypt_totp_secret, encrypt_totp_secret, generate_totp_code
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
        consumed_step = user.mfa_last_verified_step
        assert consumed_step is not None
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
            "totp_code": generate_totp_code(secret, consumed_step),
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



def test_mfa_recovery_codes_are_hash_only_single_use_and_regenerable(client, monkeypatch):
    password, access_token, secret = enroll_and_confirm(client, "mfa-recovery@example.com")
    headers = {"Authorization": f"Bearer {access_token}"}
    fresh_code = generate_totp_code(secret, int(time.time() // 30) + 1)
    generated = client.post(
        "/api/v1/auth/mfa/recovery-codes",
        headers=headers,
        json={"password": password, "code": fresh_code},
    )
    assert generated.status_code == 200, generated.text
    assert generated.headers["cache-control"] == "no-store"
    assert generated.headers["pragma"] == "no-cache"
    codes = generated.json()["codes"]
    assert len(codes) == 10
    assert len(set(codes)) == 10

    db = client.testing_sessions()
    try:
        rows = db.scalars(select(models.MFARecoveryCode)).all()
        assert len(rows) == 10
        assert all(row.code_hash not in codes for row in rows)
        assert all(len(row.code_hash) == 64 for row in rows)
    finally:
        db.close()

    recovery_login = client.post(
        "/api/v1/auth/token",
        json={"email": "mfa-recovery@example.com", "password": password, "recovery_code": codes[0]},
    )
    assert recovery_login.status_code == 200, recovery_login.text
    replay = client.post(
        "/api/v1/auth/token",
        json={"email": "mfa-recovery@example.com", "password": password, "recovery_code": codes[0]},
    )
    assert replay.status_code == 401

    # Simulate a newly accepted TOTP step to isolate recovery-code rotation without sleeping for a 30-second window.
    monkeypatch.setattr(
        "app.api.routes.identity.verify_totp",
        lambda _secret, _code, last_step=None: max(int(time.time() // 30) + 1, (last_step or -1) + 1),
    )
    new_fresh_code = "123456"
    regenerated = client.post(
        "/api/v1/auth/mfa/recovery-codes",
        headers=headers,
        json={"password": password, "code": new_fresh_code},
    )
    assert regenerated.status_code == 200, regenerated.text
    replacement_codes = regenerated.json()["codes"]
    assert codes[1] not in replacement_codes
    invalidated = client.post(
        "/api/v1/auth/token",
        json={"email": "mfa-recovery@example.com", "password": password, "recovery_code": codes[1]},
    )
    assert invalidated.status_code == 401



def test_mfa_can_be_disabled_with_single_use_recovery_code(client):
    password, access_token, secret = enroll_and_confirm(client, "mfa-recovery-disable@example.com")
    headers = {"Authorization": f"Bearer {access_token}"}
    generated = client.post(
        "/api/v1/auth/mfa/recovery-codes",
        headers=headers,
        json={"password": password, "code": generate_totp_code(secret, int(time.time() // 30) + 1)},
    )
    assert generated.status_code == 200, generated.text
    recovery_code = generated.json()["codes"][0]

    disabled = client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"password": password, "recovery_code": recovery_code},
    )
    assert disabled.status_code == 204, disabled.text
    assert client.get("/api/v1/organizations", headers=headers).status_code == 401

    db = client.testing_sessions()
    try:
        assert db.scalars(
            select(models.MFARecoveryCode).where(models.MFARecoveryCode.user_id.is_not(None))
        ).all() == []
    finally:
        db.close()

    # Disabling MFA restores password-only login.
    login = client.post(
        "/api/v1/auth/token",
        json={"email": "mfa-recovery-disable@example.com", "password": password},
    )
    assert login.status_code == 200, login.text



def test_mfa_secret_remains_decryptable_during_configured_key_rotation(monkeypatch):
    old_key = "old-signing-key-that-is-long-enough-012345"
    new_key = "new-signing-key-that-is-long-enough-678901"
    secret = "JBSWY3DPEHPK3PXP"
    monkeypatch.setattr(settings, "secret_key", old_key)
    monkeypatch.setattr(settings, "previous_secret_keys", "")
    encrypted = encrypt_totp_secret(secret)

    monkeypatch.setattr(settings, "secret_key", new_key)
    with pytest.raises(ValueError, match="current/previous SECRET_KEY"):
        decrypt_totp_secret(encrypted)

    monkeypatch.setattr(settings, "previous_secret_keys", old_key)
    assert decrypt_totp_secret(encrypted) == secret

    newly_encrypted = encrypt_totp_secret(secret)
    assert decrypt_totp_secret(newly_encrypted) == secret


def test_mfa_decryption_rejects_invalid_previous_key_configuration(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "current-signing-key-that-is-long-enough-012")
    monkeypatch.setattr(settings, "previous_secret_keys", "short")
    with pytest.raises(ValueError, match="current/previous SECRET_KEY"):
        decrypt_totp_secret("bm90LWEtc2VjcmV0")
