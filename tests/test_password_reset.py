"""Tests for one-time password-reset tokens and generic request responses."""
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import PasswordResetToken
from app import models  # noqa: F401

@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    def override_get_db():
        db = testing_session_local()
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

def register(client, email="reset@example.com"):
    response = client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-battery-12"})
    assert response.status_code == 201, response.text

def test_password_reset_is_single_use_and_revokes_existing_sessions(client, monkeypatch):
    delivered = {}
    monkeypatch.setattr("app.api.routes.identity.send_password_reset_email", lambda recipient, token: delivered.update(recipient=recipient, token=token))
    register(client)
    login = client.post("/api/v1/auth/token", json={"email": "reset@example.com", "password": "correct-horse-battery-12"})
    old_token = login.json()["access_token"]
    response = client.post("/api/v1/auth/password-reset/request", json={"email": "RESET@example.com"})
    assert response.status_code == 202
    assert response.json() == {"message": "If the account exists, reset instructions will be sent."}
    assert delivered["recipient"] == "reset@example.com"
    generator = app.dependency_overrides[get_db]()
    db = next(generator)
    try:
        record = db.scalar(select(PasswordResetToken))
        assert record is not None and delivered["token"] not in record.token_hash
        expiry = record.expires_at
        assert expiry > (datetime.now(timezone.utc).replace(tzinfo=None) if expiry.tzinfo is None else datetime.now(timezone.utc))
    finally:
        generator.close()
    reset = client.post("/api/v1/auth/password-reset/confirm", json={"token": delivered["token"], "new_password": "brand-new-secure-password-42"})
    assert reset.status_code == 204, reset.text
    assert client.post("/api/v1/auth/password-reset/confirm", json={"token": delivered["token"], "new_password": "another-secure-password-43"}).status_code == 400
    assert client.get("/api/v1/organizations", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert client.post("/api/v1/auth/token", json={"email": "reset@example.com", "password": "brand-new-secure-password-42"}).status_code == 200
    assert client.post("/api/v1/auth/token", json={"email": "reset@example.com", "password": "correct-horse-battery-12"}).status_code == 401

def test_reset_request_does_not_reveal_unknown_accounts(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.api.routes.identity.send_password_reset_email", lambda recipient, token: sent.append(recipient))
    known = client.post("/api/v1/auth/password-reset/request", json={"email": "known@example.com"})
    unknown = client.post("/api/v1/auth/password-reset/request", json={"email": "unknown@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert sent == []
