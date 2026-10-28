"""Privacy export endpoint tests: user-scoped records and credential exclusion."""
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
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "require_email_verification", False)

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


def register_and_login(client: TestClient, email: str) -> str:
    password = "correct-horse-battery-12"
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert registered.status_code == 201, registered.text
    login = client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def create_organization(client: TestClient, token: str, name: str) -> str:
    response = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_self_service_export_includes_own_profile_and_memberships_only(client):
    first_token = register_and_login(client, "privacy-first@example.com")
    first_org_id = create_organization(client, first_token, "First Private Org")

    second_token = register_and_login(client, "privacy-second@example.com")
    second_org_id = create_organization(client, second_token, "Second Private Org")

    response = client.get(
        "/api/v1/auth/me/export",
        headers={"Authorization": f"Bearer {first_token}"},
    )

    assert response.status_code == 200, response.text
    export = response.json()
    assert export["schema_version"] == 1
    assert export["user"]["email"] == "privacy-first@example.com"
    assert [item["organization_id"] for item in export["memberships"]] == [first_org_id]
    assert second_org_id not in str(export)
    assert export["buyer_orders"] == []


def test_self_service_export_never_includes_credential_material(client):
    token = register_and_login(client, "privacy-credentials@example.com")
    response = client.get(
        "/api/v1/auth/me/export",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    export = response.json()
    serialized = str(export).lower()
    assert "password_hash" not in serialized
    assert "mfa_secret_encrypted" not in serialized
    assert "mfa_pending_secret_encrypted" not in serialized
    assert "refresh_token" not in serialized
    assert "token_hash" not in serialized


def test_self_service_export_requires_authentication(client):
    response = client.get("/api/v1/auth/me/export")
    assert response.status_code in {401, 403}
