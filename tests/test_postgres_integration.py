"""PostgreSQL integration tests for refresh-token replay under concurrent requests.

These tests run only in the dedicated PostgreSQL CI job after Alembic migrations.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="Dedicated PostgreSQL integration database is required",
)


def test_concurrent_refresh_replay_revokes_session_family():
    email = f"postgres-refresh-{uuid4().hex}@example.com"
    password = "correct-horse-battery-12"
    with TestClient(app) as client:
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
        refresh_token = login.json()["refresh_token"]

    def refresh_once():
        with TestClient(app) as client:
            return client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: refresh_once(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 401]
    successful = next(response for response in responses if response.status_code == 200)
    access = successful.json()["access_token"]
    with TestClient(app) as client:
        protected = client.get(
            "/api/v1/organizations",
            headers={"Authorization": f"Bearer {access}"},
        )
    # Concurrent reuse is treated as a suspected theft and invalidates the family.
    assert protected.status_code == 401



def test_concurrent_orders_cannot_oversell_single_inventory_unit():
    suffix = uuid4().hex
    password = "correct-horse-battery-12"
    seller_email = f"postgres-seller-{suffix}@example.com"
    buyer_one_email = f"postgres-buyer-one-{suffix}@example.com"
    buyer_two_email = f"postgres-buyer-two-{suffix}@example.com"

    def register_and_login(client, email):
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        assert registered.status_code == 201, registered.text
        logged_in = client.post(
            "/api/v1/auth/token",
            json={"email": email, "password": password},
        )
        assert logged_in.status_code == 200, logged_in.text
        return logged_in.json()["access_token"]

    with TestClient(app) as client:
        seller_token = register_and_login(client, seller_email)
        organization = client.post(
            "/api/v1/organizations",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={"name": f"Concurrent Stock {suffix[:8]}"},
        )
        assert organization.status_code == 201, organization.text
        organization_id = organization.json()["id"]
        listing = client.post(
            f"/api/v1/organizations/{organization_id}/listings",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={
                "title": "Single Inventory Unit",
                "description": "PostgreSQL concurrency test",
                "price_minor": 500,
                "currency": "PKR",
                "inventory_quantity": 1,
            },
        )
        assert listing.status_code == 201, listing.text
        listing_id = listing.json()["id"]
        published = client.post(
            f"/api/v1/organizations/{organization_id}/listings/{listing_id}/publish",
            headers={"Authorization": f"Bearer {seller_token}"},
        )
        assert published.status_code == 200, published.text
        buyer_one = register_and_login(client, buyer_one_email)
        buyer_two = register_and_login(client, buyer_two_email)

    def order_once(args):
        buyer_token, key = args
        with TestClient(app) as client:
            return client.post(
                "/api/v1/orders",
                headers={
                    "Authorization": f"Bearer {buyer_token}",
                    "Idempotency-Key": key,
                },
                json={"listing_id": listing_id, "quantity": 1},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            order_once,
            [(buyer_one, f"pg-stock-{suffix}-one"), (buyer_two, f"pg-stock-{suffix}-two")],
        ))

    assert sorted(response.status_code for response in responses) == [201, 409], [
        (response.status_code, response.text) for response in responses
    ]
    with TestClient(app) as client:
        remaining = client.get(
            f"/api/v1/organizations/{organization_id}/listings",
            headers={"Authorization": f"Bearer {seller_token}"},
        )
        assert remaining.status_code == 200, remaining.text
        assert next(item for item in remaining.json() if item["id"] == listing_id)["inventory_quantity"] == 0
        orders_one = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {buyer_one}"})
        orders_two = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {buyer_two}"})
        assert orders_one.status_code == 200
        assert orders_two.status_code == 200
        assert len(orders_one.json()) + len(orders_two.json()) == 1
