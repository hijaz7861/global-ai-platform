"""Access and refresh-token lifecycle tests."""
import hashlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import RefreshToken, utc_now


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        test_client.testing_sessions = TestingSessionLocal
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_logout_revokes_access_token(client):
    password = "correct-horse-battery-12"
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": "logout@example.com", "password": password},
    )
    assert registered.status_code == 201, registered.text
    login = client.post(
        "/api/v1/auth/token",
        json={"email": "logout@example.com", "password": password},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    refresh_token = login.json()["refresh_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/organizations", headers=headers).status_code == 200

    logout = client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 204
    rejected = client.get("/api/v1/organizations", headers=headers)
    assert rejected.status_code == 401
    refresh_after_logout = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_after_logout.status_code == 401


def test_refresh_token_rotates_and_replay_revokes_entire_session_family(client):
    password = "correct-horse-battery-12"
    assert client.post(
        "/api/v1/auth/register",
        json={"email": "refresh@example.com", "password": password},
    ).status_code == 201
    login = client.post(
        "/api/v1/auth/token",
        json={"email": "refresh@example.com", "password": password},
    )
    assert login.status_code == 200, login.text
    first = login.json()
    assert first["refresh_token"]
    assert first["refresh_expires_in"] > first["expires_in"]

    rotated = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first["refresh_token"]},
    )
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]

    replay = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first["refresh_token"]},
    )
    assert replay.status_code == 401
    assert "reuse detected" in replay.json()["detail"].lower()

    first_access = client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )
    second_access = client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert first_access.status_code == 401
    assert second_access.status_code == 401

    revoked_refresh = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": second["refresh_token"]},
    )
    assert revoked_refresh.status_code == 401


def test_unknown_refresh_token_is_rejected(client):
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "x" * 48},
    )
    assert response.status_code == 401



def test_expired_consumed_refresh_token_replay_still_revokes_family(client):
    password = "correct-horse-battery-12"
    assert client.post(
        "/api/v1/auth/register",
        json={"email": "expired-replay@example.com", "password": password},
    ).status_code == 201
    login = client.post(
        "/api/v1/auth/token",
        json={"email": "expired-replay@example.com", "password": password},
    )
    assert login.status_code == 200, login.text
    first = login.json()
    rotated = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first["refresh_token"]},
    )
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()

    db = client.testing_sessions()
    try:
        old_hash = hashlib.sha256(first["refresh_token"].encode("utf-8")).hexdigest()
        old_record = db.get(RefreshToken, old_hash)
        assert old_record is not None and old_record.used_at is not None
        old_record.expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    replay = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first["refresh_token"]},
    )
    assert replay.status_code == 401
    assert "reuse detected" in replay.json()["detail"].lower()
    second_access = client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert second_access.status_code == 401
