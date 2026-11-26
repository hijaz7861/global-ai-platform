"""Catalog tests for tenant isolation and publication boundaries."""
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
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


def account(client: TestClient, email: str) -> tuple[str, str]:
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-12"},
    )
    assert registered.status_code == 201, registered.text
    raw_token = _EMAIL_TOKENS.pop(email)
    verified = client.post("/api/v1/auth/email-verification/confirm", json={"token": raw_token})
    assert verified.status_code == 204, verified.text
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


def activate_supplier_role(client: TestClient, token: str, org_id: str) -> tuple[str, dict[str, str]]:
    admin_token, _ = account(client, "platform-admin@example.com")
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


def test_listing_is_draft_until_owner_publishes(client):
    token, org_id = account(client, "seller@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    activate_supplier_role(client, token, org_id)
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
    activate_supplier_role(client, token, org_id)
    response = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Invalid", "description": "", "price_minor": 0, "currency": "PKR"},
    )
    assert response.status_code == 422

def test_public_marketplace_search_and_price_filters(client):
    token, org_id = account(client, "searchseller@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    activate_supplier_role(client, token, org_id)
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


def test_inventory_adjustments_are_recorded_atomically_and_tenant_scoped(client):
    token, org_id = account(client, "inventory-history@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    activate_supplier_role(client, token, org_id)
    created = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers=headers,
        json={
            "title": "Stock tracked item",
            "description": "Inventory audit sample",
            "price_minor": 1000,
            "currency": "PKR",
            "inventory_quantity": 5,
        },
    )
    assert created.status_code == 201, created.text
    listing_id = created.json()["id"]
    base_url = f"/api/v1/organizations/{org_id}/listings/{listing_id}"

    first = client.post(
        f"{base_url}/inventory-adjustments",
        headers=headers,
        json={"delta": 3, "reason": "Received shipment"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["inventory_quantity"] == 8

    second = client.post(
        f"{base_url}/inventory-adjustments",
        headers=headers,
        json={"delta": -2, "reason": "Damaged units"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["inventory_quantity"] == 6

    history = client.get(f"{base_url}/inventory-adjustments", headers=headers)
    assert history.status_code == 200, history.text
    assert len(history.json()) == 2
    newest, oldest = history.json()
    assert (newest["delta"], newest["quantity_before"], newest["quantity_after"]) == (-2, 8, 6)
    assert newest["reason"] == "Damaged units"
    assert (oldest["delta"], oldest["quantity_before"], oldest["quantity_after"]) == (3, 5, 8)
    assert oldest["reason"] == "Received shipment"

    rejected = client.post(
        f"{base_url}/inventory-adjustments",
        headers=headers,
        json={"delta": -7, "reason": "Would underflow stock"},
    )
    assert rejected.status_code == 409
    after_rejection = client.get(f"{base_url}/inventory-adjustments", headers=headers)
    assert len(after_rejection.json()) == 2
    assert client.get(f"{base_url}/inventory-adjustments").status_code == 401


def test_inventory_adjustment_history_does_not_cross_tenants(client):
    owner_token, org_id = account(client, "inventory-owner@example.com")
    outsider_token, _ = account(client, "inventory-outsider@example.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    activate_supplier_role(client, owner_token, org_id)
    created = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers=headers,
        json={"title": "Private stock", "price_minor": 500, "currency": "PKR", "inventory_quantity": 2},
    )
    assert created.status_code == 201, created.text
    listing_id = created.json()["id"]
    response = client.get(
        f"/api/v1/organizations/{org_id}/listings/{listing_id}/inventory-adjustments",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert response.status_code == 404



def test_catalog_routes_fail_closed_without_active_supplier_role_and_exact_scope(client):
    token, org_id = account(client, "role-guard@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    denied_create = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers=headers,
        json={"title": "Denied item", "description": "No supplier entitlement",
              "price_minor": 100, "currency": "PKR"},
    )
    assert denied_create.status_code == 403
    denied_read = client.get(f"/api/v1/organizations/{org_id}/listings", headers=headers)
    assert denied_read.status_code == 403

    second_org = client.post(
        "/api/v1/organizations",
        headers=headers,
        json={"name": "Second supplier organization"},
    )
    assert second_org.status_code == 201, second_org.text
    second_org_id = second_org.json()["id"]

    entitlement_id, admin_headers = activate_supplier_role(client, token, org_id)
    wrong_scope = client.post(
        f"/api/v1/organizations/{second_org_id}/listings",
        headers=headers,
        json={"title": "Wrong scope", "description": "Entitlement belongs to another org",
              "price_minor": 100, "currency": "PKR"},
    )
    assert wrong_scope.status_code == 403

    created = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers=headers,
        json={"title": "Authorized item", "description": "Active supplier",
              "price_minor": 100, "currency": "PKR"},
    )
    assert created.status_code == 201, created.text
    listing_id = created.json()["id"]
    assert client.get(f"/api/v1/organizations/{org_id}/listings", headers=headers).status_code == 200

    suspended = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/lifecycle",
        headers=admin_headers,
        json={"action": "suspend"},
    )
    assert suspended.status_code == 200, suspended.text

    assert client.get(f"/api/v1/organizations/{org_id}/listings", headers=headers).status_code == 403
    assert client.post(
        f"/api/v1/organizations/{org_id}/listings/{listing_id}/publish",
        headers=headers,
    ).status_code == 403
    assert client.post(
        f"/api/v1/organizations/{org_id}/listings/{listing_id}/inventory-adjustments",
        headers=headers,
        json={"delta": 1},
    ).status_code == 403
    assert client.get(
        f"/api/v1/organizations/{org_id}/listings/{listing_id}/inventory-adjustments",
        headers=headers,
    ).status_code == 403
