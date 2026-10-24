"""Platform-admin cleanup tests for expired auth and throttle records."""
import hashlib
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
from app.models import (
    AuthAttempt,
    AuthRateLimitLock,
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
    TokenRevocation,
    utc_now,
)


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform-admin@example.com")

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


def register_login(client: TestClient, email: str) -> tuple[str, dict[str, str]]:
    password = "correct-horse-battery-12"
    registered = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert registered.status_code == 201, registered.text
    login = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return password, {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "refresh_token": login.json()["refresh_token"],
    }


def test_cleanup_removes_expired_records_but_preserves_live_refresh_families(client):
    _, admin = register_login(client, "platform-admin@example.com")
    _, target = register_login(client, "cleanup-target@example.com")
    _, regular = register_login(client, "regular-user@example.com")

    # A non-admin must not be able to invoke security maintenance.
    forbidden = client.post(
        "/api/v1/platform/security/cleanup-expired-tokens",
        headers={"Authorization": regular["Authorization"]},
    )
    assert forbidden.status_code == 403

    logged_out = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": target["Authorization"]},
    )
    assert logged_out.status_code == 204

    reset = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "cleanup-target@example.com"},
    )
    assert reset.status_code == 202

    now = utc_now()
    cutoff = now - timedelta(days=2)
    db = client.testing_sessions()
    try:
        for token in db.scalars(
            select(EmailVerificationToken).where(EmailVerificationToken.user_id.is_not(None))
        ).all():
            if token.user_id:
                token.used_at = cutoff
                token.expires_at = cutoff
        for token in db.scalars(
            select(PasswordResetToken).where(PasswordResetToken.used_at.is_not(None))
        ).all():
            token.used_at = cutoff
            token.expires_at = cutoff

        revocation = db.scalar(select(TokenRevocation))
        assert revocation is not None
        revocation.expires_at = now - timedelta(hours=1)

        target_hash = hashlib.sha256(target["refresh_token"].encode("utf-8")).hexdigest()
        target_refresh = db.get(RefreshToken, target_hash)
        assert target_refresh is not None
        target_family = target_refresh.family_id
        db.query(RefreshToken).filter(RefreshToken.family_id == target_family).update(
            {RefreshToken.expires_at: now - timedelta(hours=1)},
            synchronize_session=False,
        )

        db.query(AuthAttempt).update(
            {AuthAttempt.created_at: cutoff},
            synchronize_session=False,
        )
        db.query(AuthRateLimitLock).update(
            {AuthRateLimitLock.touched_at: cutoff},
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/v1/platform/security/cleanup-expired-tokens",
        headers={"Authorization": admin["Authorization"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["email_verification_tokens_deleted"] >= 1
    assert result["password_reset_tokens_deleted"] >= 1
    assert result["expired_access_revocations_deleted"] >= 1
    assert result["expired_refresh_families_deleted"] == 1
    assert result["auth_attempts_deleted"] > 0
    assert result["rate_limit_locks_deleted"] > 0

    db = client.testing_sessions()
    try:
        assert db.get(RefreshToken, target_hash) is None
        admin_hash = hashlib.sha256(admin["refresh_token"].encode("utf-8")).hexdigest()
        assert db.get(RefreshToken, admin_hash) is not None
    finally:
        db.close()
