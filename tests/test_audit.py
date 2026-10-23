"""Audit trail tests for scope and secret filtering."""
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


def test_audit_events_are_scoped_and_details_are_allowlisted(client):
    password = "correct-horse-battery-12"
    assert client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": password}).status_code == 201
    login = client.post("/api/v1/auth/token", json={"email": "owner@example.com", "password": password})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    org = client.post("/api/v1/organizations", headers=headers, json={"name": "Audit Org"})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]

    created = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers=headers,
        json={"title": "Audit item", "description": "Test", "price_minor": 250,
              "currency": "PKR", "inventory_quantity": 4},
    )
    assert created.status_code == 201, created.text
    events = client.get(f"/api/v1/organizations/{org_id}/audit-events", headers=headers)
    assert events.status_code == 200, events.text
    actions = {event["action"] for event in events.json()}
    assert "organization.created" in actions
    assert "listing.created" in actions
    for event in events.json():
        assert "password" not in event["details"]
        assert "token" not in event["details"]
        assert "secret" not in event["details"]


def test_non_member_cannot_read_tenant_audit_events(client):
    password = "correct-horse-battery-12"
    for email in ("owner@example.com", "outsider@example.com"):
        assert client.post("/api/v1/auth/register", json={"email": email, "password": password}).status_code == 201
    owner = client.post("/api/v1/auth/token", json={"email": "owner@example.com", "password": password}).json()["access_token"]
    outsider = client.post("/api/v1/auth/token", json={"email": "outsider@example.com", "password": password}).json()["access_token"]
    org = client.post("/api/v1/organizations", headers={"Authorization": f"Bearer {owner}"}, json={"name": "Private Audit"}).json()
    response = client.get(
        f"/api/v1/organizations/{org['id']}/audit-events",
        headers={"Authorization": f"Bearer {outsider}"},
    )
    assert response.status_code == 404
