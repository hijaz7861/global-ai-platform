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


def test_concurrent_order_cancellation_releases_inventory_exactly_once():
    suffix = uuid4().hex
    password = "correct-horse-battery-12"
    seller_email = f"postgres-cancel-seller-{suffix}@example.com"
    buyer_email = f"postgres-cancel-buyer-{suffix}@example.com"

    def register_and_login(client, email):
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

    with TestClient(app) as client:
        seller_token = register_and_login(client, seller_email)
        organization = client.post(
            "/api/v1/organizations",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={"name": f"Concurrent Cancel {suffix[:8]}"},
        )
        assert organization.status_code == 201, organization.text
        organization_id = organization.json()["id"]
        listing = client.post(
            f"/api/v1/organizations/{organization_id}/listings",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={
                "title": "Cancellation Race Inventory",
                "description": "Stock must only be released once",
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
        buyer_token = register_and_login(client, buyer_email)
        created = client.post(
            "/api/v1/orders",
            headers={
                "Authorization": f"Bearer {buyer_token}",
                "Idempotency-Key": f"pg-cancel-order-{suffix}",
            },
            json={"listing_id": listing_id, "quantity": 1},
        )
        assert created.status_code == 201, created.text
        order_id = created.json()["id"]

    def cancel_once():
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/orders/{order_id}/cancel",
                headers={"Authorization": f"Bearer {buyer_token}"},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: cancel_once(), range(2)))

    assert [response.status_code for response in responses] == [200, 200], [
        (response.status_code, response.text) for response in responses
    ]
    assert all(response.json()["status"] == "cancelled" for response in responses)
    with TestClient(app) as client:
        listing_response = client.get(
            f"/api/v1/organizations/{organization_id}/listings",
            headers={"Authorization": f"Bearer {seller_token}"},
        )
    assert listing_response.status_code == 200, listing_response.text
    inventory = next(item for item in listing_response.json() if item["id"] == listing_id)
    assert inventory["inventory_quantity"] == 1, "Concurrent cancellation must not double-release inventory"


def test_concurrent_cancel_and_expiry_release_inventory_exactly_once(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.core.config import settings

    suffix = uuid4().hex
    password = "correct-horse-battery-12"
    admin_email = f"postgres-expiry-admin-{suffix}@example.com"
    seller_email = f"postgres-expiry-seller-{suffix}@example.com"
    buyer_email = f"postgres-expiry-buyer-{suffix}@example.com"
    monkeypatch.setattr(settings, "platform_admin_emails", admin_email)

    def register_and_login(client, email):
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

    with TestClient(app) as client:
        admin_token = register_and_login(client, admin_email)
        seller_token = register_and_login(client, seller_email)
        organization = client.post(
            "/api/v1/organizations",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={"name": f"Expiry Race {suffix[:8]}"},
        )
        assert organization.status_code == 201, organization.text
        organization_id = organization.json()["id"]
        listing = client.post(
            f"/api/v1/organizations/{organization_id}/listings",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={
                "title": "Expiry Race Stock",
                "description": "Cancel and expiry must release stock once",
                "price_minor": 700,
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
        buyer_token = register_and_login(client, buyer_email)
        created = client.post(
            "/api/v1/orders",
            headers={
                "Authorization": f"Bearer {buyer_token}",
                "Idempotency-Key": f"pg-expiry-race-{suffix}",
            },
            json={"listing_id": listing_id, "quantity": 1},
        )
        assert created.status_code == 201, created.text
        order_id = created.json()["id"]

    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE orders SET expires_at = :expiry WHERE id = :order_id"),
                {"expiry": datetime.now(timezone.utc) - timedelta(minutes=1), "order_id": order_id},
            )
    finally:
        engine.dispose()

    def cancel_order():
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/orders/{order_id}/cancel",
                headers={"Authorization": f"Bearer {buyer_token}"},
            )

    def expire_orders():
        with TestClient(app) as client:
            return client.post(
                "/api/v1/platform/orders/expire-pending",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel_future = pool.submit(cancel_order)
        expiry_future = pool.submit(expire_orders)
        cancelled = cancel_future.result()
        expired = expiry_future.result()

    assert cancelled.status_code in {200, 409}, cancelled.text
    assert expired.status_code == 200, expired.text
    with TestClient(app) as client:
        order = client.get(
            f"/api/v1/orders/{order_id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        seller_listings = client.get(
            f"/api/v1/organizations/{organization_id}/listings",
            headers={"Authorization": f"Bearer {seller_token}"},
        )
    assert order.status_code == 200, order.text
    assert order.json()["status"] in {"cancelled", "expired"}
    assert seller_listings.status_code == 200, seller_listings.text
    inventory = next(item for item in seller_listings.json() if item["id"] == listing_id)
    assert inventory["inventory_quantity"] == 1, "Cancellation and expiry race must not double-release stock"


def test_concurrent_reconciliation_windows_and_database_overlap_trigger():
    from datetime import datetime, timedelta, timezone

    suffix = uuid4().hex
    email = f"postgres-recon-{suffix}@example.com"
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
            json={"name": f"Reconciliation Race {suffix[:8]}"},
        )
        assert organization.status_code == 201, organization.text
        organization_id = organization.json()["id"]
        account = client.post(
            f"/api/v1/organizations/{organization_id}/accounts",
            headers=headers,
            json={"code": "1000", "name": "Bank Cash", "account_type": "asset", "currency": "PKR"},
        )
        assert account.status_code == 201, account.text
        account_id = account.json()["id"]

    today = datetime.now(timezone.utc).date()
    windows = [
        {"start": today, "end": today + timedelta(days=30)},
        {"start": today + timedelta(days=1), "end": today + timedelta(days=31)},
    ]

    def create_window(window):
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/organizations/{organization_id}/reconciliations",
                headers=headers,
                json={
                    "account_id": account_id,
                    "statement_start_date": window["start"].isoformat(),
                    "statement_end_date": window["end"].isoformat(),
                    "opening_balance_minor": 0,
                    "closing_balance_minor": 0,
                },
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create_window, windows))
    assert sorted(response.status_code for response in responses) == [201, 409], [
        (response.status_code, response.text) for response in responses
    ]
    created = next(response for response in responses if response.status_code == 201).json()
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            user_id = connection.scalar(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": email},
            )
        assert user_id
        with pytest.raises(DBAPIError, match="bank reconciliation statement windows must not overlap"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO bank_reconciliation_sessions
                            (id, organization_id, account_id, created_by_user_id,
                             statement_start_date, statement_end_date,
                             opening_balance_minor, closing_balance_minor, status, created_at)
                        VALUES
                            (:id, :org_id, :account_id, :user_id, :start_date, :end_date,
                             0, 0, 'open', CURRENT_TIMESTAMP)
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "org_id": organization_id,
                        "account_id": account_id,
                        "user_id": user_id,
                        "start_date": created["statement_start_date"],
                        "end_date": created["statement_end_date"],
                    },
                )
    finally:
        engine.dispose()


def test_concurrent_reconciliation_match_cannot_reuse_journal_line():
    from datetime import datetime, timezone

    suffix = uuid4().hex
    email = f"postgres-recon-match-{suffix}@example.com"
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
            json={"name": f"Reconciliation Match Race {suffix[:8]}"},
        )
        assert organization.status_code == 201, organization.text
        organization_id = organization.json()["id"]
        cash = client.post(
            f"/api/v1/organizations/{organization_id}/accounts",
            headers=headers,
            json={"code": "1000", "name": "Bank Cash", "account_type": "asset", "currency": "PKR"},
        )
        revenue = client.post(
            f"/api/v1/organizations/{organization_id}/accounts",
            headers=headers,
            json={"code": "4000", "name": "Revenue", "account_type": "revenue", "currency": "PKR"},
        )
        assert cash.status_code == 201 and revenue.status_code == 201
        entry = client.post(
            f"/api/v1/organizations/{organization_id}/journal-entries",
            headers={**headers, "Idempotency-Key": f"pg-recon-entry-{suffix}"},
            json={
                "description": "Bank receipt",
                "lines": [
                    {"account_id": cash.json()["id"], "debit_minor": 500, "credit_minor": 0},
                    {"account_id": revenue.json()["id"], "debit_minor": 0, "credit_minor": 500},
                ],
            },
        )
        assert entry.status_code == 201, entry.text
        cash_line_id = next(
            line["id"] for line in entry.json()["lines"] if line["account_id"] == cash.json()["id"]
        )
        today = datetime.now(timezone.utc).date().isoformat()
        session = client.post(
            f"/api/v1/organizations/{organization_id}/reconciliations",
            headers=headers,
            json={
                "account_id": cash.json()["id"],
                "statement_start_date": today,
                "statement_end_date": today,
                "opening_balance_minor": 0,
                "closing_balance_minor": 1000,
            },
        )
        assert session.status_code == 201, session.text
        session_id = session.json()["id"]
        line_ids = []
        for reference in ("MATCH-RACE-1", "MATCH-RACE-2"):
            line = client.post(
                f"/api/v1/organizations/{organization_id}/reconciliations/{session_id}/lines",
                headers=headers,
                json={
                    "statement_date": today,
                    "description": f"Bank receipt {reference}",
                    "amount_minor": 500,
                    "external_reference": reference,
                },
            )
            assert line.status_code == 201, line.text
            line_ids.append(line.json()["id"])

    def match_once(line_id):
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/organizations/{organization_id}/reconciliations/{session_id}/lines/{line_id}/match",
                headers=headers,
                json={"journal_line_id": cash_line_id},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(match_once, line_ids))
    assert sorted(response.status_code for response in responses) == [200, 409], [
        (response.status_code, response.text) for response in responses
    ]
    with TestClient(app) as client:
        lines = client.get(
            f"/api/v1/organizations/{organization_id}/reconciliations/{session_id}/lines",
            headers=headers,
        )
    assert lines.status_code == 200, lines.text
    assert sum(line["matched_journal_line_id"] is not None for line in lines.json()) == 1
