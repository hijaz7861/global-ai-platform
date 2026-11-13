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


def test_journal_posting_serializes_with_period_closure_and_trigger_blocks_closed_period():
    from datetime import datetime, timedelta, timezone

    suffix = uuid4().hex
    email = f"postgres-period-post-{suffix}@example.com"
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
            json={"name": f"Posting Closure Race {suffix[:8]}"},
        )
        assert organization.status_code == 201, organization.text
        organization_id = organization.json()["id"]
        cash = client.post(
            f"/api/v1/organizations/{organization_id}/accounts",
            headers=headers,
            json={"code": "1000", "name": "Cash", "account_type": "asset", "currency": "PKR"},
        )
        revenue = client.post(
            f"/api/v1/organizations/{organization_id}/accounts",
            headers=headers,
            json={"code": "4000", "name": "Revenue", "account_type": "revenue", "currency": "PKR"},
        )
        assert cash.status_code == 201 and revenue.status_code == 201
        today = datetime.now(timezone.utc).date()
        period = client.post(
            f"/api/v1/organizations/{organization_id}/accounting-periods",
            headers=headers,
            json={
                "name": "Current accounting period",
                "starts_on": (today - timedelta(days=1)).isoformat(),
                "ends_on": (today + timedelta(days=1)).isoformat(),
            },
        )
        assert period.status_code == 201, period.text
        period_id = period.json()["id"]

    def post_journal():
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/organizations/{organization_id}/journal-entries",
                headers={**headers, "Idempotency-Key": f"pg-close-race-{suffix}"},
                json={
                    "description": "Concurrent period posting",
                    "lines": [
                        {"account_id": cash.json()["id"], "debit_minor": 250, "credit_minor": 0},
                        {"account_id": revenue.json()["id"], "debit_minor": 0, "credit_minor": 250},
                    ],
                },
            )

    def close_period():
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/organizations/{organization_id}/accounting-periods/{period_id}/close",
                headers=headers,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        post_future = pool.submit(post_journal)
        close_future = pool.submit(close_period)
        posted = post_future.result()
        closed = close_future.result()

    assert closed.status_code == 200, closed.text
    assert posted.status_code in {201, 409}, posted.text
    if posted.status_code == 201:
        posted_at = datetime.fromisoformat(posted.json()["created_at"].replace("Z", "+00:00"))
        closed_at = datetime.fromisoformat(closed.json()["closed_at"].replace("Z", "+00:00"))
        assert posted_at <= closed_at, "Posting that succeeds in the race must commit before closure"

    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            user_id = connection.scalar(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": email},
            )
        assert user_id
        with pytest.raises(DBAPIError, match="no open accounting period covers journal entry date"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO journal_entries
                            (id, organization_id, created_by_user_id, idempotency_key,
                             request_fingerprint, description, currency, total_minor,
                             status, created_at)
                        VALUES
                            (:id, :org_id, :user_id, :key, :fingerprint, 'Direct SQL after close',
                             'PKR', 100, 'posted', CURRENT_TIMESTAMP)
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "org_id": organization_id,
                        "user_id": user_id,
                        "key": f"pg-direct-after-close-{suffix}",
                        "fingerprint": "a" * 64,
                    },
                )
    finally:
        engine.dispose()


def test_concurrent_procurement_awards_accept_exactly_one_supplier_quote(monkeypatch):
    from app.core.config import settings
    from app.api.routes import identity as identity_routes

    verification_tokens: dict[str, str] = {}
    monkeypatch.setattr(
        identity_routes,
        "send_email_verification",
        lambda email, token: verification_tokens.__setitem__(email, token),
    )
    suffix = uuid4().hex
    password = "correct-horse-battery-12"
    admin_email = f"postgres-proc-admin-{suffix}@example.com"
    buyer_email = f"postgres-proc-buyer-{suffix}@example.com"
    supplier_emails = [
        f"postgres-proc-supplier-one-{suffix}@example.com",
        f"postgres-proc-supplier-two-{suffix}@example.com",
    ]
    monkeypatch.setattr(settings, "platform_admin_emails", admin_email)

    def register_and_login(client, email):
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        assert registered.status_code == 201, registered.text
        raw_token = verification_tokens.pop(email)
        verified = client.post(
            "/api/v1/auth/email-verification/confirm",
            json={"token": raw_token},
        )
        assert verified.status_code == 204, verified.text
        login = client.post(
            "/api/v1/auth/token",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        return login.json()["access_token"]

    with TestClient(app) as client:
        admin_token = register_and_login(client, admin_email)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        configured_role = client.patch(
            "/api/v1/platform/role-definitions/supplier",
            headers=admin_headers,
            json={"fee_amount_minor": 100, "fee_currency": "PKR", "min_age": 18, "is_active": True},
        )
        assert configured_role.status_code == 200, configured_role.text
        buyer_token = register_and_login(client, buyer_email)
        buyer_org = client.post(
            "/api/v1/organizations",
            headers={"Authorization": f"Bearer {buyer_token}"},
            json={"name": f"Procurement Buyer {suffix[:8]}"},
        )
        assert buyer_org.status_code == 201, buyer_org.text
        buyer_org_id = buyer_org.json()["id"]
        buyer_headers = {"Authorization": f"Bearer {buyer_token}"}

        supplier_tokens = []
        supplier_org_ids = []
        for index, email in enumerate(supplier_emails, start=1):
            token = register_and_login(client, email)
            supplier_tokens.append(token)
            organization = client.post(
                "/api/v1/organizations",
                headers={"Authorization": f"Bearer {token}"},
                json={"name": f"Procurement Supplier {index} {suffix[:6]}"},
            )
            assert organization.status_code == 201, organization.text
            supplier_org_id = organization.json()["id"]
            supplier_org_ids.append(supplier_org_id)
            profile = client.post(
                f"/api/v1/organizations/{supplier_org_id}/supplier-profile",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "legal_name": f"Procurement Supplier {index} Ltd",
                    "business_type": "manufacturer",
                    "country_code": "PK",
                    "contact_email": f"proc-supplier-{index}-{suffix}@example.com",
                    "description": "PostgreSQL award concurrency test",
                },
            )
            assert profile.status_code == 201, profile.text
            submitted = client.post(
                f"/api/v1/organizations/{supplier_org_id}/supplier-profile/submit",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert submitted.status_code == 200, submitted.text
            reviewed = client.post(
                f"/api/v1/platform/supplier-submissions/{profile.json()['id']}/review",
                headers=admin_headers,
                json={"decision": "approved", "notes": "CI test approval"},
            )
            assert reviewed.status_code == 200, reviewed.text
            entitlement = client.post(
                "/api/v1/me/role-entitlements",
                headers={"Authorization": f"Bearer {token}"},
                json={"role_key": "supplier", "organization_id": supplier_org_id},
            )
            assert entitlement.status_code == 201, entitlement.text
            role_review = client.post(
                f"/api/v1/platform/role-entitlements/{entitlement.json()['id']}/review",
                headers=admin_headers,
                json={"decision": "approve", "payment_verified": True, "age_verified": True},
            )
            assert role_review.status_code == 200, role_review.text
            assert role_review.json()["status"] == "active"

        request = client.post(
            f"/api/v1/organizations/{buyer_org_id}/procurement-requests",
            headers=buyer_headers,
            json={
                "title": "Concurrent award request",
                "description": "Only one quote may win",
                "quantity": 10,
                "currency": "PKR",
            },
        )
        assert request.status_code == 201, request.text
        request_id = request.json()["id"]
        quote_ids = []
        for index, (supplier_org_id, token) in enumerate(zip(supplier_org_ids, supplier_tokens), start=1):
            quote = client.post(
                f"/api/v1/organizations/{supplier_org_id}/procurement-requests/{request_id}/quotes",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "unit_price_minor": 100 + index,
                    "currency": "PKR",
                    "lead_time_days": index,
                    "notes": f"Offer {index}",
                },
            )
            assert quote.status_code == 201, quote.text
            quote_ids.append(quote.json()["id"])

    def award_once(quote_id):
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/organizations/{buyer_org_id}/procurement-requests/{request_id}/award",
                headers=buyer_headers,
                json={"quote_id": quote_id},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(award_once, quote_ids))
    assert sorted(response.status_code for response in responses) == [200, 409], [
        (response.status_code, response.text) for response in responses
    ]
    with TestClient(app) as client:
        quotes = client.get(
            f"/api/v1/organizations/{buyer_org_id}/procurement-requests/{request_id}/quotes",
            headers=buyer_headers,
        )
        requests = client.get(
            f"/api/v1/organizations/{buyer_org_id}/procurement-requests",
            headers=buyer_headers,
        )
    assert quotes.status_code == 200, quotes.text
    assert requests.status_code == 200, requests.text
    statuses = [quote["status"] for quote in quotes.json()]
    assert statuses.count("accepted") == 1
    assert statuses.count("rejected") == 1
    awarded = [item for item in requests.json() if item["id"] == request_id]
    assert len(awarded) == 1 and awarded[0]["status"] == "awarded"



def test_concurrent_ai_task_claim_is_granted_to_exactly_one_worker():
    from sqlalchemy.orm import sessionmaker

    from app.ai.runtime.tasks import claim_next_task, create_task
    from app.models import AITask, AITaskEvent, Organization, User

    suffix = uuid4().hex
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with factory() as db:
            user = User(email=f"postgres-ai-task-{suffix}@example.com", password_hash="test-hash")
            organization = Organization(name=f"AI Task Claim {suffix[:8]}")
            db.add_all([user, organization])
            db.commit()
            db.refresh(user)
            db.refresh(organization)
            task, created = create_task(
                db,
                organization_id=organization.id,
                actor_user_id=user.id,
                task_type="agent.inspect",
                title="Concurrent worker claim",
                input_payload={"scope": "read-only"},
                idempotency_key=f"pg-ai-task-{suffix}",
            )
            assert created is True
            task_id = task.id

        def claim(worker_id: str) -> str | None:
            with factory() as db:
                claimed = claim_next_task(db, worker_id=worker_id, lease_seconds=30)
                return claimed.id if claimed is not None else None

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim, ["worker-a", "worker-b"]))

        assert claims.count(task_id) == 1
        assert claims.count(None) == 1
        with factory() as db:
            stored = db.get(AITask, task_id)
            assert stored is not None
            assert stored.status == "running"
            assert stored.attempt_count == 1
            claim_events = db.query(AITaskEvent).filter(
                AITaskEvent.task_id == task_id,
                AITaskEvent.event_type == "claimed",
            ).count()
            assert claim_events == 1
    finally:
        engine.dispose()



def test_postgres_memory_isolation_separates_users_and_organizations():
    from sqlalchemy.orm import sessionmaker

    from app.ai.memory.service import create_memory, search_memories
    from app.models import Organization, User

    suffix = uuid4().hex
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with factory() as db:
            user_a = User(email=f"pg-memory-a-{suffix}@example.com", password_hash="test-hash")
            user_b = User(email=f"pg-memory-b-{suffix}@example.com", password_hash="test-hash")
            org_a = Organization(name=f"Memory Tenant A {suffix[:8]}")
            org_b = Organization(name=f"Memory Tenant B {suffix[:8]}")
            db.add_all([user_a, user_b, org_a, org_b])
            db.commit()
            db.refresh(user_a)
            db.refresh(user_b)
            db.refresh(org_a)
            db.refresh(org_b)
            private = create_memory(
                db, actor_user_id=user_a.id, scope="user", organization_id=None,
                memory_type="preference", content="Private user memory isolation sentinel",
            )
            shared_a = create_memory(
                db, actor_user_id=user_a.id, scope="organization", organization_id=org_a.id,
                memory_type="project", content="Tenant A shared memory isolation sentinel",
            )
            restricted_a = create_memory(
                db, actor_user_id=user_a.id, actor_role="owner", scope="organization",
                organization_id=org_a.id, memory_type="reference",
                content="Tenant A restricted memory sentinel", sensitivity="restricted",
            )
            shared_b = create_memory(
                db, actor_user_id=user_b.id, scope="organization", organization_id=org_b.id,
                memory_type="project", content="Tenant B shared memory isolation sentinel",
            )
            ids = {
                "user_a": user_a.id, "user_b": user_b.id,
                "org_a": org_a.id, "org_b": org_b.id,
                "private": private.id, "shared_a": shared_a.id,
                "restricted_a": restricted_a.id, "shared_b": shared_b.id,
            }

        def visible(actor: str, organization: str, role: str) -> set[str]:
            with factory() as db:
                return {
                    item.record.id for item in search_memories(
                        db, actor_user_id=actor, organization_id=organization, role=role
                    )
                }

        a_owner = visible(ids["user_a"], ids["org_a"], "owner")
        b_member_a = visible(ids["user_b"], ids["org_a"], "member")
        b_admin_a = visible(ids["user_b"], ids["org_a"], "admin")
        a_owner_b = visible(ids["user_a"], ids["org_b"], "owner")

        assert ids["private"] in a_owner
        assert ids["shared_a"] in a_owner
        assert ids["restricted_a"] in a_owner
        assert ids["private"] not in b_member_a
        assert ids["restricted_a"] not in b_member_a
        assert ids["restricted_a"] not in b_admin_a
        assert ids["shared_a"] not in a_owner_b
        assert ids["shared_b"] in a_owner_b
        assert ids["private"] in a_owner_b
    finally:
        engine.dispose()


def test_postgres_concurrent_memory_supersession_has_one_winner():
    from sqlalchemy.orm import sessionmaker

    from app.ai.memory.service import MemoryConflict, MemoryNotFound, create_memory
    from app.models import AIMemoryRecord, Organization, User

    suffix = uuid4().hex
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with factory() as db:
            user = User(email=f"pg-memory-race-{suffix}@example.com", password_hash="test-hash")
            organization = Organization(name=f"Memory Supersession {suffix[:8]}")
            db.add_all([user, organization])
            db.commit()
            db.refresh(user)
            db.refresh(organization)
            original = create_memory(
                db, actor_user_id=user.id, scope="user", organization_id=None,
                memory_type="project", content="Original version before concurrent correction",
            )
            user_id, original_id = user.id, original.id

        def supersede(index: int) -> str:
            with factory() as db:
                try:
                    replacement = create_memory(
                        db, actor_user_id=user_id, scope="user", organization_id=None,
                        memory_type="project", content=f"Concurrent corrected memory version {index}",
                        supersedes_id=original_id,
                    )
                    return replacement.id
                except (MemoryConflict, MemoryNotFound):
                    db.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(supersede, [1, 2]))

        assert sum(outcome != "conflict" for outcome in outcomes) == 1
        with factory() as db:
            original_row = db.get(AIMemoryRecord, original_id)
            active_count = db.query(AIMemoryRecord).filter(
                AIMemoryRecord.owner_user_id == user_id,
                AIMemoryRecord.scope == "user",
                AIMemoryRecord.status == "active",
            ).count()
            assert original_row is not None
            assert original_row.status == "superseded"
            assert active_count == 1
    finally:
        engine.dispose()


def test_concurrent_approval_nonce_consumption_is_single_use():
    from app.ai.postgres_nonce_store import PostgresNonceStore

    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    store = PostgresNonceStore(engine)
    nonce = f"concurrent-approval-{uuid4().hex}"

    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: store.consume_nonce(nonce), range(24)))
        assert results.count(True) == 1
        assert results.count(False) == 23
        # A later attempt from another connection/process remains a replay.
        assert store.consume_nonce(nonce) is False
    finally:
        engine.dispose()



def test_role_entitlement_events_are_immutable_in_postgresql(monkeypatch):
    from app.api.routes import identity as identity_routes

    verification_tokens: dict[str, str] = {}
    monkeypatch.setattr(
        identity_routes,
        "send_email_verification",
        lambda email, token: verification_tokens.__setitem__(email, token),
    )
    email = f"postgres-role-audit-{uuid4().hex}@example.com"
    password = "correct-horse-battery-12"
    with TestClient(app) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        assert registered.status_code == 201, registered.text
        verified = client.post(
            "/api/v1/auth/email-verification/confirm",
            json={"token": verification_tokens.pop(email)},
        )
        assert verified.status_code == 204, verified.text
        login = client.post(
            "/api/v1/auth/token",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        application = client.post(
            "/api/v1/me/role-entitlements",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            json={"role_key": "student"},
        )
        assert application.status_code == 201, application.text
        entitlement_id = application.json()["id"]

    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            event_id = connection.execute(
                text(
                    "SELECT id FROM role_entitlement_events "
                    "WHERE entitlement_id = :entitlement_id ORDER BY created_at DESC LIMIT 1"
                ),
                {"entitlement_id": entitlement_id},
            ).scalar_one()

        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE role_entitlement_events SET event_type = 'tampered' WHERE id = :event_id"),
                    {"event_id": event_id},
                )

        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM role_entitlement_events WHERE id = :event_id"),
                    {"event_id": event_id},
                )
    finally:
        engine.dispose()
