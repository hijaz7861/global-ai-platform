"""Persistent rate-limit regression tests for authentication endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.api.routes.identity import _client_ip_for_rate_limit


@pytest.fixture
def test_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "auth_login_attempt_limit", 3)
    monkeypatch.setattr(settings, "auth_login_window_minutes", 15)
    monkeypatch.setattr(settings, "password_reset_request_limit", 2)
    monkeypatch.setattr(settings, "password_reset_window_minutes", 15)

    def override_get_db():
        db = sessions()
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


def test_login_attempts_are_limited_by_email_and_peer(test_client):
    payload = {"email": "missing@example.com", "password": "wrong-password"}
    assert test_client.post("/api/v1/auth/token", json=payload).status_code == 401
    assert test_client.post("/api/v1/auth/token", json=payload).status_code == 401
    assert test_client.post("/api/v1/auth/token", json=payload).status_code == 401
    limited = test_client.post("/api/v1/auth/token", json=payload)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "900"


def test_password_reset_requests_are_limited_without_disclosing_account_state(test_client):
    payload = {"email": "unknown@example.com"}
    first = test_client.post("/api/v1/auth/password-reset/request", json=payload)
    second = test_client.post("/api/v1/auth/password-reset/request", json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    limited = test_client.post("/api/v1/auth/password-reset/request", json=payload)
    assert limited.status_code == 429
    assert "retry-after" in limited.headers


def test_rate_limit_subject_stores_only_fixed_length_digest():
    assert models.AuthAttempt.__table__.c.subject_hash.type.length == 64



def test_forwarded_for_is_ignored_for_untrusted_direct_peer(test_client, monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.0/8")
    request = type("Request", (), {
        "client": type("Client", (), {"host": "198.51.100.20"})(),
        "headers": {"x-forwarded-for": "203.0.113.99"},
    })()
    assert _client_ip_for_rate_limit(request) == "198.51.100.20"


def test_trusted_proxy_uses_first_untrusted_address_from_right(test_client, monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.0/8, 192.0.2.10")
    request = type("Request", (), {
        "client": type("Client", (), {"host": "10.0.0.4"})(),
        "headers": {"x-forwarded-for": "203.0.113.8, 192.0.2.10"},
    })()
    assert _client_ip_for_rate_limit(request) == "203.0.113.8"


def test_malformed_forwarded_chain_falls_back_to_trusted_peer(test_client, monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.0/8")
    request = type("Request", (), {
        "client": type("Client", (), {"host": "10.0.0.4"})(),
        "headers": {"x-forwarded-for": "spoofed, 203.0.113.8"},
    })()
    assert _client_ip_for_rate_limit(request) == "10.0.0.4"
