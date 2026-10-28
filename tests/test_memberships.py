"""Membership role and tenant isolation tests."""
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


def register_login(client: TestClient, email: str) -> str:
    password = "correct-horse-battery-12"
    response = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    response = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def create_org(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Team Org"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_owner_can_add_admin_and_list_members(client):
    owner_token = register_login(client, "owner@example.com")
    org_id = create_org(client, owner_token)
    register_login(client, "admin@example.com")
    response = client.post(
        f"/api/v1/organizations/{org_id}/memberships",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "admin@example.com", "role": "admin"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "admin"

    listing = client.get(
        f"/api/v1/organizations/{org_id}/memberships",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert listing.status_code == 200
    assert {row["role"] for row in listing.json()} == {"owner", "admin"}


def test_admin_cannot_grant_admin_role(client):
    owner_token = register_login(client, "owner@example.com")
    org_id = create_org(client, owner_token)
    register_login(client, "admin@example.com")
    admin_add = client.post(
        f"/api/v1/organizations/{org_id}/memberships",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "admin@example.com", "role": "admin"},
    )
    assert admin_add.status_code == 201
    admin_token = client.post(
        "/api/v1/auth/token",
        json={"email": "admin@example.com", "password": "correct-horse-battery-12"},
    ).json()["access_token"]
    register_login(client, "new-member@example.com")
    response = client.post(
        f"/api/v1/organizations/{org_id}/memberships",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"email": "new-member@example.com", "role": "admin"},
    )
    assert response.status_code == 403


def test_member_removal_is_audited_and_owner_cannot_be_removed(client):
    owner_token = register_login(client, "owner@example.com")
    org_id = create_org(client, owner_token)
    register_login(client, "member@example.com")
    member = client.post(
        f"/api/v1/organizations/{org_id}/memberships",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "member@example.com", "role": "member"},
    )
    assert member.status_code == 201
    removed = client.delete(
        f"/api/v1/organizations/{org_id}/memberships/{member.json()['user_id']}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert removed.status_code == 204

    memberships = client.get(
        f"/api/v1/organizations/{org_id}/memberships",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    owner_user_id = next(row["user_id"] for row in memberships.json() if row["role"] == "owner")
    protected = client.delete(
        f"/api/v1/organizations/{org_id}/memberships/{owner_user_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert protected.status_code == 409
    audit = client.get(
        f"/api/v1/organizations/{org_id}/audit-events",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert "membership.created" in {event["action"] for event in audit.json()}
    assert "membership.removed" in {event["action"] for event in audit.json()}
