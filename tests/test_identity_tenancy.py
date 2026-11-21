"""Integration tests for identity and tenant isolation."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app import models  # noqa: F401 - registers ORM tables


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def register_and_login(client, email: str) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-12"},
    )
    assert response.status_code == 201, response.text
    response = client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": "correct-horse-battery-12"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_register_login_and_create_organization(client):
    token = register_and_login(client, "owner@example.com")
    response = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Owner Org"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "owner"
    assert response.json()["name"] == "Owner Org"


def test_organization_data_is_not_visible_to_non_member(client):
    owner_token = register_and_login(client, "owner@example.com")
    org = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"name": "Private Org"},
    ).json()

    outsider_token = register_and_login(client, "outsider@example.com")
    response = client.get(
        f"/api/v1/organizations/{org['id']}",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert response.status_code == 404


def test_organization_list_contains_only_memberships(client):
    owner_token = register_and_login(client, "owner@example.com")
    client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"name": "Visible Org"},
    )
    outsider_token = register_and_login(client, "outsider@example.com")
    response = client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert response.status_code == 200
    assert response.json() == []


def test_login_rejects_wrong_password(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "correct-horse-battery-12"},
    )
    response = client.post(
        "/api/v1/auth/token",
        json={"email": "user@example.com", "password": "incorrect-password"},
    )
    assert response.status_code == 401


def test_auth_endpoints_fail_closed_without_signing_key(client, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", None)
    client.post(
        "/api/v1/auth/register",
        json={"email": "nokey@example.com", "password": "correct-horse-battery-12"},
    )
    response = client.post(
        "/api/v1/auth/token",
        json={"email": "nokey@example.com", "password": "correct-horse-battery-12"},
    )
    assert response.status_code == 503
