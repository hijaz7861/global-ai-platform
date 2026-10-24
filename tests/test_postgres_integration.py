"""PostgreSQL integration tests for refresh-token replay under concurrent requests.

These tests run only in the dedicated PostgreSQL CI job after Alembic migrations.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

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


def test_concurrent_overlapping_accounting_periods_are_serialized_per_tenant():
    from datetime import datetime, timedelta, timezone

    suffix = uuid4().hex
    email = f"postgres-period-{suffix}@example.com"
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
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        organization = client.post(
            "/api/v1/organizations",
            headers=headers,
            json={"name": f"Period Race {suffix[:8]}"},
        )
        assert organization.status_code == 201, organization.text
        organization_id = organization.json()["id"]

    today = datetime.now(timezone.utc).date()
    period_payloads = [
        {
            "name": "Concurrent Period A",
            "starts_on": today.isoformat(),
            "ends_on": (today + timedelta(days=30)).isoformat(),
        },
        {
            "name": "Concurrent Period B",
            "starts_on": (today + timedelta(days=1)).isoformat(),
            "ends_on": (today + timedelta(days=31)).isoformat(),
        },
    ]

    def create_period(payload):
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/organizations/{organization_id}/accounting-periods",
                headers=headers,
                json=payload,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create_period, period_payloads))

    assert sorted(response.status_code for response in responses) == [201, 409], [
        (response.status_code, response.text) for response in responses
    ]
    with TestClient(app) as client:
        periods = client.get(
            f"/api/v1/organizations/{organization_id}/accounting-periods",
            headers=headers,
        )
    assert periods.status_code == 200, periods.text
    assert len(periods.json()) == 1



def test_audit_events_are_database_enforced_append_only():
    suffix = uuid4().hex
    email = f"postgres-audit-{suffix}@example.com"
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
        organization = client.post(
            "/api/v1/organizations",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            json={"name": f"Append-only audit {suffix[:8]}"},
        )
        assert organization.status_code == 201, organization.text

    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            event_id = connection.scalar(
                text(
                    "SELECT id FROM audit_events "
                    "WHERE organization_id = :org_id AND action = 'organization.created' "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"org_id": organization.json()["id"]},
            )
        assert event_id

        with pytest.raises(DBAPIError, match="audit_events are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE audit_events SET action = 'tampered' WHERE id = :event_id"),
                    {"event_id": event_id},
                )

        with pytest.raises(DBAPIError, match="audit_events are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM audit_events WHERE id = :event_id"),
                    {"event_id": event_id},
                )
    finally:
        engine.dispose()



def test_accounting_period_overlap_is_rejected_by_database_trigger():
    from datetime import datetime, timedelta, timezone

    suffix = uuid4().hex
    email = f"postgres-period-trigger-{suffix}@example.com"
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
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        organization = client.post(
            "/api/v1/organizations",
            headers=headers,
            json={"name": f"Period trigger {suffix[:8]}"},
        )
        assert organization.status_code == 201, organization.text
        organization_id = organization.json()["id"]
        today = datetime.now(timezone.utc).date()
        period = client.post(
            f"/api/v1/organizations/{organization_id}/accounting-periods",
            headers=headers,
            json={
                "name": "Original period",
                "starts_on": today.isoformat(),
                "ends_on": (today + timedelta(days=30)).isoformat(),
            },
        )
        assert period.status_code == 201, period.text

    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            user_id = connection.scalar(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": email},
            )
        assert user_id
        with pytest.raises(DBAPIError, match="accounting periods must not overlap"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO accounting_periods
                            (id, organization_id, name, starts_on, ends_on, status,
                             created_by_user_id, created_at)
                        VALUES
                            (:id, :org_id, :name, :starts_on, :ends_on, 'open',
                             :user_id, CURRENT_TIMESTAMP)
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "org_id": organization_id,
                        "name": "Direct SQL overlapping period",
                        "starts_on": today + timedelta(days=1),
                        "ends_on": today + timedelta(days=40),
                        "user_id": user_id,
                    },
                )
    finally:
        engine.dispose()
