"""Catalog tests for tenant isolation and publication boundaries."""
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


def account(client: TestClient, email: str) -> tuple[str, str]:
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-12"},
    )
    assert registered.status_code == 201, registered.text
    login = client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": "correct-horse-battery-12"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    org = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"{email.split('@')[0]} org"},
    )
    assert org.status_code == 201, org.text
    return token, org.json()["id"]


def test_listing_is_draft_until_owner_publishes(client):
    token, org_id = account(client, "seller@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers=headers,
        json={"title": "Recycled notebook", "description": "A sample catalog item",
              "price_minor": 1250, "currency": "PKR"},
    )
    assert created.status_code == 201, created.text
    listing = created.json()
    assert listing["status"] == "draft"
    assert listing["price_minor"] == 1250
    assert client.get("/api/v1/listings").json() == []

    published = client.post(
        f"/api/v1/organizations/{org_id}/listings/{listing['id']}/publish",
        headers=headers,
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "active"
    public_items = client.get("/api/v1/listings").json()
    assert len(public_items) == 1
    assert public_items[0]["id"] == listing["id"]


def test_non_member_cannot_read_or_create_tenant_listings(client):
    owner_token, org_id = account(client, "owner@example.com")
    outsider_token, _ = account(client, "outsider@example.com")
    outsider_headers = {"Authorization": f"Bearer {outsider_token}"}
    response = client.get(
        f"/api/v1/organizations/{org_id}/listings",
        headers=outsider_headers,
    )
    assert response.status_code == 404
    response = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers=outsider_headers,
        json={"title": "Hidden", "description": "Not allowed",
              "price_minor": 100, "currency": "PKR"},
    )
    assert response.status_code == 404


def test_invalid_price_and_currency_are_rejected(client):
    token, org_id = account(client, "validator@example.com")
    response = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Invalid", "description": "", "price_minor": 0, "currency": "PKR"},
    )
    assert response.status_code == 422

def test_public_marketplace_search_and_price_filters(client):
    token, org_id = account(client, "searchseller@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    items = [
        {"title": "Organic cotton shirt", "description": "Soft fabric", "price_minor": 1800, "currency": "PKR"},
        {"title": "Notebook set", "description": "Recycled paper", "price_minor": 450, "currency": "PKR"},
        {"title": "Organic tea", "description": "Loose leaf", "price_minor": 1200, "currency": "USD"},
    ]
    for item in items:
        created = client.post(f"/api/v1/organizations/{org_id}/listings", headers=headers, json=item)
        assert created.status_code == 201, created.text
        published = client.post(
            f"/api/v1/organizations/{org_id}/listings/{created.json()['id']}/publish",
            headers=headers,
        )
        assert published.status_code == 200, published.text

    filtered = client.get(
        "/api/v1/listings",
        params={"q": "organic", "currency": "pkr", "min_price_minor": 1000, "max_price_minor": 2000},
    )
    assert filtered.status_code == 200, filtered.text
    assert len(filtered.json()) == 1
    assert filtered.json()[0]["title"] == "Organic cotton shirt"

    invalid_range = client.get("/api/v1/listings", params={"min_price_minor": 2000, "max_price_minor": 1000})
    assert invalid_range.status_code == 422
