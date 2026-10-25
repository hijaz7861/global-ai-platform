"""AI route safety tests; live provider calls remain opt-in."""
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
    monkeypatch.setattr(settings, "ai_live_calls_enabled", False)

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


def owner(client: TestClient) -> tuple[str, str]:
    password = "correct-horse-battery-12"
    assert client.post(
        "/api/v1/auth/register",
        json={"email": "ai-owner@example.com", "password": password},
    ).status_code == 201
    login = client.post(
        "/api/v1/auth/token",
        json={"email": "ai-owner@example.com", "password": password},
    )
    token = login.json()["access_token"]
    org = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "AI Organization"},
    )
    assert org.status_code == 201, org.text
    return token, org.json()["id"]


def test_live_ai_is_disabled_by_default(client):
    token, org_id = owner(client)
    response = client.post(
        f"/api/v1/organizations/{org_id}/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "Do not call a provider", "max_output_tokens": 8},
    )
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"].lower()


def test_ai_output_cap_is_enforced_before_provider_call(client, monkeypatch):
    token, org_id = owner(client)
    monkeypatch.setattr(settings, "ai_live_calls_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_request_limit", 3)
    monkeypatch.setattr(settings, "ai_max_output_tokens", 8)
    response = client.post(
        f"/api/v1/organizations/{org_id}/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "Must be rejected before any network request", "max_output_tokens": 9},
    )
    assert response.status_code == 422
    assert "cannot exceed configured limit" in response.json()["detail"]



def test_ai_fallback_requires_complete_configuration(client, monkeypatch):
    token, org_id = owner(client)
    monkeypatch.setattr(settings, "ai_live_calls_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_request_limit", 3)
    monkeypatch.setattr(settings, "ai_max_output_tokens", 16)
    monkeypatch.setattr(settings, "ai_api_key", "primary-test-key")
    monkeypatch.setattr(settings, "ai_base_url", "https://primary.example/v1")
    monkeypatch.setattr(settings, "ai_model", "primary-model")
    monkeypatch.setattr(settings, "ai_fallback_api_key", "fallback-test-key")
    monkeypatch.setattr(settings, "ai_fallback_base_url", None)
    monkeypatch.setattr(settings, "ai_fallback_model", "fallback-model")
    response = client.post(
        f"/api/v1/organizations/{org_id}/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "hello", "provider": "fallback"},
    )
    assert response.status_code == 503
    assert "fallback provider configuration is incomplete" in response.json()["detail"].lower()
