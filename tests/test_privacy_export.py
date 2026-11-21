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



def test_self_service_export_includes_only_the_buyers_own_orders(client):
    seller_token = register_and_login(client, "privacy-export-seller@example.com")
    seller_org_id = create_organization(client, seller_token, "Export Seller Org")
    seller_headers = {"Authorization": f"Bearer {seller_token}"}
    listing = client.post(
        f"/api/v1/organizations/{seller_org_id}/listings",
        headers=seller_headers,
        json={
            "title": "Export Test Item",
            "description": "Privacy export regression fixture",
            "price_minor": 725,
            "currency": "PKR",
            "inventory_quantity": 2,
        },
    )
    assert listing.status_code == 201, listing.text
    listing_id = listing.json()["id"]
    published = client.post(
        f"/api/v1/organizations/{seller_org_id}/listings/{listing_id}/publish",
        headers=seller_headers,
    )
    assert published.status_code == 200, published.text

    buyer_token = register_and_login(client, "privacy-export-buyer@example.com")
    order = client.post(
        "/api/v1/orders",
        headers={
            "Authorization": f"Bearer {buyer_token}",
            "Idempotency-Key": "privacy-export-order-001",
        },
        json={"listing_id": listing_id, "quantity": 1},
    )
    assert order.status_code == 201, order.text

    buyer_export = client.get(
        "/api/v1/auth/me/export",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert buyer_export.status_code == 200, buyer_export.text
    buyer_orders = buyer_export.json()["buyer_orders"]
    assert len(buyer_orders) == 1
    assert buyer_orders[0]["id"] == order.json()["id"]
    assert buyer_orders[0]["items"][0]["listing_id"] == listing_id
    assert buyer_orders[0]["items"][0]["title"] == "Export Test Item"

    seller_export = client.get("/api/v1/auth/me/export", headers=seller_headers)
    assert seller_export.status_code == 200, seller_export.text
    assert seller_export.json()["buyer_orders"] == []

def test_self_service_export_requires_authentication(client):
    response = client.get("/api/v1/auth/me/export")
    assert response.status_code in {401, 403}
