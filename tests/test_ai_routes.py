"""AI route safety tests; live provider calls remain opt-in."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import AuditEvent


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    app.state.testing_sessionmaker = TestingSessionLocal
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
    del app.state.testing_sessionmaker
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
        json={"prompt": "hello", "provider": "fallback", "max_output_tokens": 8},
    )
    assert response.status_code == 503
    assert "fallback provider configuration is incomplete" in response.json()["detail"].lower()



def test_ai_chat_uses_model_router_and_preserves_task_classification(client, monkeypatch):
    token, org_id = owner(client)
    monkeypatch.setattr(settings, "ai_live_calls_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_request_limit", 3)
    monkeypatch.setattr(settings, "ai_max_output_tokens", 32)
    monkeypatch.setattr(settings, "database_url", "postgresql://configured-for-route-test")
    calls = {}

    class FakeRouter:
        def select(self, **kwargs):
            calls["selected"] = kwargs
            return object()

        async def complete(self, **kwargs):
            calls["completed"] = kwargs
            return {
                "provider": "stub",
                "model": "stub-model",
                "content": "safe answer",
                "input_tokens": 5,
                "output_tokens": 2,
            }

    monkeypatch.setattr(
        "app.api.routes.ai.create_model_router",
        lambda config: FakeRouter(),
    )
    response = client.post(
        f"/api/v1/organizations/{org_id}/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "prompt": "Summarize the public release note",
            "task_type": "summarize",
            "data_class": "public",
            "max_output_tokens": 8,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "stub"
    assert calls["selected"]["task_type"] == "summarize"
    assert calls["selected"]["data_class"] == "public"
    assert calls["completed"]["route"] == "primary"


def test_ai_chat_rejects_confidential_data_before_provider_call(client, monkeypatch):
    token, org_id = owner(client)
    monkeypatch.setattr(settings, "ai_live_calls_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_request_limit", 3)
    monkeypatch.setattr(settings, "ai_max_output_tokens", 32)
    monkeypatch.setattr(settings, "ai_api_key", "primary-test-key")
    monkeypatch.setattr(settings, "ai_base_url", "https://provider.example/v1")
    monkeypatch.setattr(settings, "ai_model", "primary-model")
    response = client.post(
        f"/api/v1/organizations/{org_id}/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "prompt": "This should never be sent to an external model",
            "data_class": "confidential",
            "max_output_tokens": 8,
        },
    )
    assert response.status_code == 422
    assert "data classification" in response.json()["detail"]



def test_ai_chat_blocks_secret_patterns_before_quota_or_provider_call(client, monkeypatch):
    token, org_id = owner(client)
    monkeypatch.setattr(settings, "ai_live_calls_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_request_limit", 3)
    monkeypatch.setattr(settings, "ai_max_output_tokens", 32)
    calls = {}

    class FakeRouter:
        def select(self, **kwargs):
            calls["selected"] = kwargs
            return object()

        async def complete(self, **kwargs):
            calls["completed"] = kwargs
            pytest.fail("credential-bearing chat prompt must not reach a provider")

    monkeypatch.setattr("app.api.routes.ai.create_model_router", lambda config: FakeRouter())
    monkeypatch.setattr(
        "app.api.routes.ai.record_audit_event",
        lambda *args, **kwargs: pytest.fail("blocked prompt must not reserve or audit a provider request"),
    )
    secret = "ghp_" + "C" * 36
    response = client.post(
        f"/api/v1/organizations/{org_id}/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "Summarize this token: " + secret, "max_output_tokens": 8},
    )

    assert response.status_code == 422
    assert "credential-like" in response.json()["detail"]
    assert secret not in response.text
    assert "completed" not in calls
    assert "selected" not in calls



def test_ai_chat_enforces_rolling_token_budget_before_provider_or_audit(client, monkeypatch):
    token, org_id = owner(client)
    monkeypatch.setattr(settings, "ai_live_calls_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_request_limit", 10)
    monkeypatch.setattr(settings, "ai_daily_token_limit", 1_000)
    monkeypatch.setattr(settings, "ai_max_output_tokens", 32)
    monkeypatch.setattr(settings, "database_url", "postgresql://configured-for-route-test")

    session = client.app.state.testing_sessionmaker()
    session.add(
        AuditEvent(
            actor_user_id=None,
            organization_id=org_id,
            action="ai.request.started",
            entity_type="ai_request",
            entity_id="prior-token-budget-reservation",
            details={"estimated_tokens": 995, "token_estimation": "utf8_bytes_plus_max_output"},
        )
    )
    session.commit()
    session.close()

    class FakeRouter:
        def select(self, **kwargs):
            return object()

        async def complete(self, **kwargs):
            pytest.fail("token-budget rejection must happen before provider completion")

    monkeypatch.setattr("app.api.routes.ai.create_model_router", lambda config: FakeRouter())
    monkeypatch.setattr(
        "app.api.routes.ai.record_audit_event",
        lambda *args, **kwargs: pytest.fail("rejected token reservation must not create a request audit event"),
    )

    response = client.post(
        f"/api/v1/organizations/{org_id}/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "hi", "max_output_tokens": 8},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "Organization AI token budget reached"


def test_ai_chat_records_estimated_tokens_for_shared_rolling_budget(client, monkeypatch):
    token, org_id = owner(client)
    monkeypatch.setattr(settings, "ai_live_calls_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_request_limit", 10)
    monkeypatch.setattr(settings, "ai_daily_token_limit", 100)
    monkeypatch.setattr(settings, "ai_max_output_tokens", 32)
    monkeypatch.setattr(settings, "database_url", "postgresql://configured-for-route-test")

    class FakeRouter:
        def select(self, **kwargs):
            return object()

        async def complete(self, **kwargs):
            return {
                "provider": "stub",
                "model": "stub-model",
                "content": "safe answer",
                "input_tokens": 2,
                "output_tokens": 2,
            }

    monkeypatch.setattr("app.api.routes.ai.create_model_router", lambda config: FakeRouter())
    response = client.post(
        f"/api/v1/organizations/{org_id}/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "hi", "max_output_tokens": 8},
    )
    assert response.status_code == 200, response.text

    session = client.app.state.testing_sessionmaker()
    events = session.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == org_id,
            AuditEvent.action == "ai.request.started",
        )
    ).all()
    session.close()
    assert len(events) == 1
    assert events[0].details["estimated_tokens"] == len("hi".encode("utf-8")) + 8
    assert events[0].details["token_estimation"] == "utf8_bytes_plus_max_output"
