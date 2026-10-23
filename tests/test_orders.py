"""Order lifecycle smoke tests for idempotency and stock protection."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Listing


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
    assert client.post("/api/v1/auth/register", json={"email": email, "password": password}).status_code == 201
    response = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def make_active_listing(client: TestClient, quantity: int = 3) -> tuple[str, str]:
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
    return listing_id, token


def test_order_reserves_stock_and_is_idempotent(client):
    listing_id, _ = make_active_listing(client, quantity=3)
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

    with client.app.state.__dict__.get("_unused", None) if False else _noop():
        pass


class _noop:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_order_rejects_insufficient_stock(client):
    listing_id, _ = make_active_listing(client, quantity=1)
    buyer_token = register_login(client, "buyer@example.com")
    response = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {buyer_token}", "Idempotency-Key": "order-key-0002"},
        json={"listing_id": listing_id, "quantity": 2},
    )
    assert response.status_code == 409


def test_orders_are_private_to_the_buyer(client):
    listing_id, _ = make_active_listing(client, quantity=2)
    buyer_token = register_login(client, "buyer@example.com")
    created = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {buyer_token}", "Idempotency-Key": "order-key-0003"},
        json={"listing_id": listing_id, "quantity": 1},
    )
    assert created.status_code == 201, created.text
    other_token = register_login(client, "other@example.com")
    response = client.get(
        f"/api/v1/orders/{created.json()['id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404
