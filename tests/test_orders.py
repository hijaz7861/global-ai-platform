"""
import pytestOrder lifecycle tests for idempotency, stock protection, and cancellation."""
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
