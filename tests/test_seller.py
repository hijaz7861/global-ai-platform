"""Seller views and inventory adjustment permission tests."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.api.routes import identity as identity_routes
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import RoleDefinition

_EMAIL_TOKENS: dict[str, str] = {}


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform-admin@example.com")
    monkeypatch.setattr(settings, "require_email_verification", True)
    _EMAIL_TOKENS.clear()
    monkeypatch.setattr(
        identity_routes,
        "send_email_verification",
        lambda email, token: _EMAIL_TOKENS.__setitem__(email, token),
    )
    with TestingSessionLocal() as db:
        db.add(RoleDefinition(
            role_key="supplier", display_name="Supplier", description="Supplier profile",
            min_age=18, fee_amount_minor=None, fee_currency="PKR",
            requires_review=True, is_active=True,
        ))
        db.commit()

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
    _EMAIL_TOKENS.clear()


def register_login(client: TestClient, email: str) -> str:
    password = "correct-horse-battery-12"
    assert client.post("/api/v1/auth/register", json={"email": email, "password": password}).status_code == 201
    raw_token = _EMAIL_TOKENS.pop(email)
    verified = client.post("/api/v1/auth/email-verification/confirm", json={"token": raw_token})
    assert verified.status_code == 204, verified.text
    response = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def activate_supplier_role(client: TestClient, token: str, org_id: str) -> tuple[str, dict[str, str]]:
    admin_token = register_login(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    configured = client.patch(
        "/api/v1/platform/role-definitions/supplier",
        headers=admin_headers,
        json={"fee_amount_minor": 100, "fee_currency": "PKR", "min_age": 18, "is_active": True},
    )
    assert configured.status_code == 200, configured.text
    applied = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {token}"},
        json={"role_key": "supplier", "organization_id": org_id},
    )
    assert applied.status_code == 201, applied.text
    entitlement_id = applied.json()["id"]
    reviewed = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/review",
        headers=admin_headers,
        json={"decision": "approve", "payment_verified": True, "age_verified": True},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "active"
    return entitlement_id, admin_headers


def seller_setup(client: TestClient, email: str = "seller@example.com") -> tuple[str, str, str]:
    token = register_login(client, email)
    headers = {"Authorization": f"Bearer {token}"}
    org = client.post("/api/v1/organizations", headers=headers, json={"name": "Seller Org"})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    activate_supplier_role(client, token, org_id)
    listing = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers=headers,
        json={"title": "Toolbox", "description": "Inventory item", "price_minor": 1000,
              "currency": "PKR", "inventory_quantity": 2},
    )
    assert listing.status_code == 201, listing.text
    listing_id = listing.json()["id"]
    published = client.post(
        f"/api/v1/organizations/{org_id}/listings/{listing_id}/publish",
        headers=headers,
    )
    assert published.status_code == 200, published.text
    return token, org_id, listing_id


def test_seller_order_view_is_organization_scoped(client):
    seller_token, org_id, listing_id = seller_setup(client)
    buyer_token = register_login(client, "buyer@example.com")
    created = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {buyer_token}", "Idempotency-Key": "seller-order-0001"},
        json={"listing_id": listing_id, "quantity": 1},
    )
    assert created.status_code == 201, created.text
    response = client.get(
        f"/api/v1/organizations/{org_id}/orders",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert "user_id" not in response.json()[0]
    assert "email" not in response.json()[0]


def test_non_member_cannot_view_seller_orders(client):
    _, org_id, _ = seller_setup(client)
    outsider = register_login(client, "outsider@example.com")
    response = client.get(
        f"/api/v1/organizations/{org_id}/orders",
        headers={"Authorization": f"Bearer {outsider}"},
    )
    assert response.status_code == 404


def test_inventory_adjustment_is_atomic_and_audited(client):
    token, org_id, listing_id = seller_setup(client)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        f"/api/v1/organizations/{org_id}/listings/{listing_id}/inventory-adjustments",
        headers=headers,
        json={"delta": 3},
    )
    assert response.status_code == 200, response.text
    assert response.json()["inventory_quantity"] == 5

    rejected = client.post(
        f"/api/v1/organizations/{org_id}/listings/{listing_id}/inventory-adjustments",
        headers=headers,
        json={"delta": -6},
    )
    assert rejected.status_code == 409
    listing = client.get(
        f"/api/v1/organizations/{org_id}/listings", headers=headers
    ).json()[0]
    assert listing["inventory_quantity"] == 5

    events = client.get(f"/api/v1/organizations/{org_id}/audit-events", headers=headers)
    assert events.status_code == 200
    assert "inventory.adjusted" in {event["action"] for event in events.json()}



def test_seller_order_view_requires_active_supplier_role_and_exact_scope(client):
    token = register_login(client, "seller-role-guard@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    first_org = client.post("/api/v1/organizations", headers=headers, json={"name": "Supplier A"})
    assert first_org.status_code == 201, first_org.text
    first_org_id = first_org.json()["id"]
    second_org = client.post("/api/v1/organizations", headers=headers, json={"name": "Supplier B"})
    assert second_org.status_code == 201, second_org.text
    second_org_id = second_org.json()["id"]

    assert client.get(
        f"/api/v1/organizations/{first_org_id}/orders", headers=headers
    ).status_code == 403

    entitlement_id, admin_headers = activate_supplier_role(client, token, first_org_id)
    assert client.get(
        f"/api/v1/organizations/{first_org_id}/orders", headers=headers
    ).status_code == 200
    assert client.get(
        f"/api/v1/organizations/{second_org_id}/orders", headers=headers
    ).status_code == 403

    suspended = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/lifecycle",
        headers=admin_headers,
        json={"action": "suspend"},
    )
    assert suspended.status_code == 200, suspended.text
    assert client.get(
        f"/api/v1/organizations/{first_org_id}/orders", headers=headers
    ).status_code == 403
