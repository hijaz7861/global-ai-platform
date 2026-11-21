"""Order lifecycle tests for idempotency, stock protection, cancellation, and expiry."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Order, utc_now


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
        test_client.testing_sessions = TestingSessionLocal
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


def make_active_listing(client: TestClient, quantity: int = 3) -> tuple[str, str, str]:
    token = register_login(client, "seller@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org = client.post("/api/v1/organizations", headers=headers, json={"name": "Seller Org"})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    created = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers=headers,
        json={"title": "Notebook", "description": "Stock-controlled item",
              "price_minor": 500, "currency": "PKR", "inventory_quantity": quantity},
    )
    assert created.status_code == 201, created.text
    listing_id = created.json()["id"]
    published = client.post(
        f"/api/v1/organizations/{org_id}/listings/{listing_id}/publish", headers=headers
    )
    assert published.status_code == 200, published.text
    return listing_id, token, org_id


def create_order(client: TestClient, listing_id: str, buyer_token: str, key: str, quantity: int = 1):
    return client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {buyer_token}", "Idempotency-Key": key},
        json={"listing_id": listing_id, "quantity": quantity},
    )


def listing_stock(client: TestClient, org_id: str, seller_token: str) -> int:
    response = client.get(
        f"/api/v1/organizations/{org_id}/listings",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()[0]["inventory_quantity"]


def test_order_reserves_stock_and_is_idempotent(client):
    listing_id, seller_token, org_id = make_active_listing(client, quantity=3)
    buyer_token = register_login(client, "buyer@example.com")
    headers = {"Authorization": f"Bearer {buyer_token}", "Idempotency-Key": "order-key-0001"}
    payload = {"listing_id": listing_id, "quantity": 2}
    first = client.post("/api/v1/orders", headers=headers, json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["total_minor"] == 1000
    assert first.json()["status"] == "pending_payment"

    repeated = client.post("/api/v1/orders", headers=headers, json=payload)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == first.json()["id"]
    assert listing_stock(client, org_id, seller_token) == 1


def test_idempotency_key_cannot_be_reused_for_different_order(client):
    listing_id, _, _ = make_active_listing(client, quantity=3)
    buyer_token = register_login(client, "buyer@example.com")
    first = create_order(client, listing_id, buyer_token, "order-key-0004", 1)
    assert first.status_code == 201, first.text
    second = create_order(client, listing_id, buyer_token, "order-key-0004", 2)
    assert second.status_code == 409


def test_order_rejects_insufficient_stock(client):
    listing_id, _, _ = make_active_listing(client, quantity=1)
    buyer_token = register_login(client, "buyer@example.com")
    response = create_order(client, listing_id, buyer_token, "order-key-0002", 2)
    assert response.status_code == 409


def test_orders_are_private_to_the_buyer(client):
    listing_id, _, _ = make_active_listing(client, quantity=2)
    buyer_token = register_login(client, "buyer@example.com")
    created = create_order(client, listing_id, buyer_token, "order-key-0003", 1)
    assert created.status_code == 201, created.text
    other_token = register_login(client, "other@example.com")
    response = client.get(
        f"/api/v1/orders/{created.json()['id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404


def test_cancellation_releases_stock_exactly_once_and_is_audited(client):
    listing_id, seller_token, org_id = make_active_listing(client, quantity=4)
    buyer_token = register_login(client, "buyer@example.com")
    created = create_order(client, listing_id, buyer_token, "order-key-0005", 3)
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    assert listing_stock(client, org_id, seller_token) == 1

    headers = {"Authorization": f"Bearer {buyer_token}"}
    cancelled = client.post(f"/api/v1/orders/{order_id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert listing_stock(client, org_id, seller_token) == 4

    repeated = client.post(f"/api/v1/orders/{order_id}/cancel", headers=headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["status"] == "cancelled"
    assert listing_stock(client, org_id, seller_token) == 4

    audit = client.get(
        f"/api/v1/organizations/{org_id}/audit-events",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert audit.status_code == 200, audit.text
    actions = {event["action"] for event in audit.json()}
    assert "order.created" in actions
    assert "order.cancelled" in actions


def test_non_owner_cannot_cancel_order(client):
    listing_id, _, _ = make_active_listing(client, quantity=2)
    buyer_token = register_login(client, "buyer@example.com")
    created = create_order(client, listing_id, buyer_token, "order-key-0006", 1)
    assert created.status_code == 201, created.text
    other_token = register_login(client, "other@example.com")
    response = client.post(
        f"/api/v1/orders/{created.json()['id']}/cancel",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404



def test_expiry_sweep_releases_stock_once_and_requires_platform_admin(client, monkeypatch):
    listing_id, seller_token, org_id = make_active_listing(client, quantity=4)
    buyer_token = register_login(client, "expiry-buyer@example.com")
    created = create_order(client, listing_id, buyer_token, "order-expiry-0001", quantity=3)
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    assert created.json()["expires_at"]
    assert listing_stock(client, org_id, seller_token) == 1

    # A normal buyer cannot invoke the maintenance operation.
    forbidden = client.post(
        "/api/v1/platform/orders/expire-pending",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert forbidden.status_code == 403

    db = client.testing_sessions()
    try:
        order = db.get(Order, order_id)
        assert order is not None
        order.expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, "platform_admin_emails", "platform-ops@example.com")
    admin_token = register_login(client, "platform-ops@example.com")
    headers = {"Authorization": f"Bearer {admin_token}"}
    swept = client.post("/api/v1/platform/orders/expire-pending?limit=10", headers=headers)
    assert swept.status_code == 200, swept.text
    assert swept.json() == {"expired_count": 1, "order_ids": [order_id]}
    assert listing_stock(client, org_id, seller_token) == 4

    repeated = client.post("/api/v1/platform/orders/expire-pending?limit=10", headers=headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == {"expired_count": 0, "order_ids": []}
    buyer_headers = {"Authorization": f"Bearer {buyer_token}"}
    assert client.get(f"/api/v1/orders/{order_id}", headers=buyer_headers).json()["status"] == "expired"
    assert client.post(f"/api/v1/orders/{order_id}/cancel", headers=buyer_headers).status_code == 409

    audit = client.get(
        f"/api/v1/organizations/{org_id}/audit-events",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert audit.status_code == 200, audit.text
    assert "order.expired" in {event["action"] for event in audit.json()}



def test_multi_item_order_reserves_each_listing_and_is_idempotent(client):
    first_listing, seller_token, org_id = make_active_listing(client, quantity=3)
    second = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers={"Authorization": f"Bearer {seller_token}"},
        json={"title": "Pen Set", "description": "Second cart item", "price_minor": 700,
              "currency": "PKR", "inventory_quantity": 2},
    )
    assert second.status_code == 201, second.text
    second_listing = second.json()["id"]
    assert client.post(
        f"/api/v1/organizations/{org_id}/listings/{second_listing}/publish",
        headers={"Authorization": f"Bearer {seller_token}"},
    ).status_code == 200

    buyer_token = register_login(client, "multi-cart-buyer@example.com")
    headers = {"Authorization": f"Bearer {buyer_token}", "Idempotency-Key": "multi-cart-key-001"}
    payload = {"items": [
        {"listing_id": first_listing, "quantity": 2},
        {"listing_id": second_listing, "quantity": 1},
    ]}
    first = client.post("/api/v1/orders", headers=headers, json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["total_minor"] == 1700
    assert len(first.json()["items"]) == 2
    assert {item["listing_id"] for item in first.json()["items"]} == {first_listing, second_listing}

    replay = client.post("/api/v1/orders", headers=headers, json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    mismatch = client.post(
        "/api/v1/orders",
        headers=headers,
        json={"items": [
            {"listing_id": first_listing, "quantity": 1},
            {"listing_id": second_listing, "quantity": 1},
        ]},
    )
    assert mismatch.status_code == 409

    listings = client.get(
        f"/api/v1/organizations/{org_id}/listings",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    assert listings.status_code == 200
    stock = {item["id"]: item["inventory_quantity"] for item in listings.json()}
    assert stock[first_listing] == 1
    assert stock[second_listing] == 1


def test_multi_item_order_rolls_back_all_stock_if_any_line_lacks_inventory(client):
    first_listing, seller_token, org_id = make_active_listing(client, quantity=2)
    second = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers={"Authorization": f"Bearer {seller_token}"},
        json={"title": "Unavailable Item", "description": "No stock", "price_minor": 700,
              "currency": "PKR", "inventory_quantity": 0},
    )
    assert second.status_code == 201, second.text
    second_listing = second.json()["id"]
    assert client.post(
        f"/api/v1/organizations/{org_id}/listings/{second_listing}/publish",
        headers={"Authorization": f"Bearer {seller_token}"},
    ).status_code == 200

    buyer_token = register_login(client, "multi-cart-fail@example.com")
    response = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {buyer_token}", "Idempotency-Key": "multi-cart-key-002"},
        json={"items": [
            {"listing_id": first_listing, "quantity": 1},
            {"listing_id": second_listing, "quantity": 1},
        ]},
    )
    assert response.status_code == 409
    listings = client.get(
        f"/api/v1/organizations/{org_id}/listings",
        headers={"Authorization": f"Bearer {seller_token}"},
    )
    stock = {item["id"]: item["inventory_quantity"] for item in listings.json()}
    assert stock[first_listing] == 2
    assert stock[second_listing] == 0
    assert client.get("/api/v1/orders", headers={"Authorization": f"Bearer {buyer_token}"}).json() == []


def test_multi_item_order_rejects_mixed_currencies_and_duplicate_listings(client):
    first_listing, seller_token, org_id = make_active_listing(client, quantity=3)
    second = client.post(
        f"/api/v1/organizations/{org_id}/listings",
        headers={"Authorization": f"Bearer {seller_token}"},
        json={"title": "Foreign Currency Item", "price_minor": 700, "currency": "USD",
              "inventory_quantity": 2},
    )
    assert second.status_code == 201, second.text
    second_listing = second.json()["id"]
    assert client.post(
        f"/api/v1/organizations/{org_id}/listings/{second_listing}/publish",
        headers={"Authorization": f"Bearer {seller_token}"},
    ).status_code == 200
    buyer_token = register_login(client, "multi-cart-currency@example.com")
    headers = {"Authorization": f"Bearer {buyer_token}", "Idempotency-Key": "multi-cart-key-003"}
    mixed = client.post(
        "/api/v1/orders",
        headers=headers,
        json={"items": [
            {"listing_id": first_listing, "quantity": 1},
            {"listing_id": second_listing, "quantity": 1},
        ]},
    )
    assert mixed.status_code == 422
    duplicate = client.post(
        "/api/v1/orders",
        headers={**headers, "Idempotency-Key": "multi-cart-key-004"},
        json={"items": [
            {"listing_id": first_listing, "quantity": 1},
            {"listing_id": first_listing, "quantity": 2},
        ]},
    )
    assert duplicate.status_code == 422
