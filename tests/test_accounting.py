"""Accounting tests for balance rules, tenant isolation, and idempotency."""
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


def owner_and_org(client: TestClient, email: str) -> tuple[str, str]:
    password = "correct-horse-battery-12"
    response = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    response = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    response = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"{email.split('@')[0]} org"},
    )
    assert response.status_code == 201, response.text
    return token, response.json()["id"]


def create_account(client: TestClient, token: str, org_id: str, code: str, account_type: str) -> str:
    response = client.post(
        f"/api/v1/organizations/{org_id}/accounts",
        headers={"Authorization": f"Bearer {token}"},
        json={"code": code, "name": code.title(), "account_type": account_type, "currency": "PKR"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_balanced_journal_posts_and_is_idempotent(client):
    token, org_id = owner_and_org(client, "accounting@example.com")
    cash = create_account(client, token, org_id, "1000", "asset")
    revenue = create_account(client, token, org_id, "4000", "revenue")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "journal-key-0001"}
    payload = {
        "description": "Record sample sale",
        "lines": [
            {"account_id": cash, "debit_minor": 2500, "credit_minor": 0},
            {"account_id": revenue, "debit_minor": 0, "credit_minor": 2500},
        ],
    }
    first = client.post(f"/api/v1/organizations/{org_id}/journal-entries", headers=headers, json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["total_minor"] == 2500
    assert first.json()["status"] == "posted"

    second = client.post(f"/api/v1/organizations/{org_id}/journal-entries", headers=headers, json=payload)
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert len(client.get(
        f"/api/v1/organizations/{org_id}/journal-entries", headers=headers
    ).json()) == 1
    trial = client.get(
        f"/api/v1/organizations/{org_id}/trial-balance", headers=headers
    )
    assert trial.status_code == 200, trial.text
    by_code = {row["code"]: row for row in trial.json()}
    assert by_code["1000"]["normal_balance_minor"] == 2500
    assert by_code["4000"]["normal_balance_minor"] == 2500
    assert sum(row["debit_total_minor"] for row in trial.json()) == sum(
        row["credit_total_minor"] for row in trial.json()
    )


def test_unbalanced_journal_is_rejected(client):
    token, org_id = owner_and_org(client, "unbalanced@example.com")
    cash = create_account(client, token, org_id, "1000", "asset")
    revenue = create_account(client, token, org_id, "4000", "revenue")
    response = client.post(
        f"/api/v1/organizations/{org_id}/journal-entries",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "journal-key-0002"},
        json={"description": "Invalid entry", "lines": [
            {"account_id": cash, "debit_minor": 1000, "credit_minor": 0},
            {"account_id": revenue, "debit_minor": 0, "credit_minor": 900},
        ]},
    )
    assert response.status_code == 422


def test_cross_tenant_account_cannot_be_used(client):
    token_a, org_a = owner_and_org(client, "ledger-a@example.com")
    token_b, org_b = owner_and_org(client, "ledger-b@example.com")
    foreign_account = create_account(client, token_a, org_a, "1000", "asset")
    local_account = create_account(client, token_b, org_b, "4000", "revenue")
    response = client.post(
        f"/api/v1/organizations/{org_b}/journal-entries",
        headers={"Authorization": f"Bearer {token_b}", "Idempotency-Key": "journal-key-0003"},
        json={"description": "Cross tenant attempt", "lines": [
            {"account_id": foreign_account, "debit_minor": 100, "credit_minor": 0},
            {"account_id": local_account, "debit_minor": 0, "credit_minor": 100},
        ]},
    )
    assert response.status_code == 422


def test_idempotency_key_mismatch_is_rejected(client):
    token, org_id = owner_and_org(client, "ledger-key@example.com")
    cash = create_account(client, token, org_id, "1000", "asset")
    revenue = create_account(client, token, org_id, "4000", "revenue")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "journal-key-0004"}
    base = {"description": "Sale", "lines": [
        {"account_id": cash, "debit_minor": 100, "credit_minor": 0},
        {"account_id": revenue, "debit_minor": 0, "credit_minor": 100},
    ]}
    assert client.post(f"/api/v1/organizations/{org_id}/journal-entries", headers=headers, json=base).status_code == 201
    changed = {"description": "Sale", "lines": [
        {"account_id": cash, "debit_minor": 200, "credit_minor": 0},
        {"account_id": revenue, "debit_minor": 0, "credit_minor": 200},
    ]}
    response = client.post(f"/api/v1/organizations/{org_id}/journal-entries", headers=headers, json=changed)
    assert response.status_code == 409


def test_journal_reversal_is_immutable_idempotent_and_offsets_trial_balance(client):
    token, org_id = owner_and_org(client, "reversal@example.com")
    cash = create_account(client, token, org_id, "1000", "asset")
    revenue = create_account(client, token, org_id, "4000", "revenue")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "journal-original-01"}
    payload = {
        "description": "Sale to reverse",
        "lines": [
            {"account_id": cash, "debit_minor": 1250, "credit_minor": 0},
            {"account_id": revenue, "debit_minor": 0, "credit_minor": 1250},
        ],
    }
    original = client.post(
        f"/api/v1/organizations/{org_id}/journal-entries",
        headers=headers,
        json=payload,
    )
    assert original.status_code == 201, original.text

    reverse_headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "journal-reversal-01",
    }
    url = f"/api/v1/organizations/{org_id}/journal-entries/{original.json()['id']}/reverse"
    reversal = client.post(url, headers=reverse_headers)
    assert reversal.status_code == 201, reversal.text
    assert reversal.json()["total_minor"] == 1250
    assert {line["account_id"]: (line["debit_minor"], line["credit_minor"]) for line in reversal.json()["lines"]} == {
        cash: (0, 1250),
        revenue: (1250, 0),
    }
    replay = client.post(url, headers=reverse_headers)
    assert replay.status_code == 201
    assert replay.json()["id"] == reversal.json()["id"]

    second_reversal = client.post(
        url,
        headers={**reverse_headers, "Idempotency-Key": "journal-reversal-02"},
    )
    assert second_reversal.status_code == 409
    reverse_a_reversal = client.post(
        f"/api/v1/organizations/{org_id}/journal-entries/{reversal.json()['id']}/reverse",
        headers={**reverse_headers, "Idempotency-Key": "journal-reversal-03"},
    )
    assert reverse_a_reversal.status_code == 409

    entries = client.get(
        f"/api/v1/organizations/{org_id}/journal-entries",
        headers=reverse_headers,
    )
    assert entries.status_code == 200
    assert len(entries.json()) == 2
    trial = client.get(
        f"/api/v1/organizations/{org_id}/trial-balance",
        headers=reverse_headers,
    )
    assert trial.status_code == 200
    assert all(row["normal_balance_minor"] == 0 for row in trial.json())

    first_page = client.get(
        f"/api/v1/organizations/{org_id}/accounts/{cash}/ledger",
        headers=reverse_headers,
        params={"limit": 1},
    )
    assert first_page.status_code == 200, first_page.text
    first_ledger = first_page.json()
    assert len(first_ledger["entries"]) == 1
    assert first_ledger["has_more"] is True
    assert first_ledger["opening_balance_minor"] == 0

    second_page = client.get(
        f"/api/v1/organizations/{org_id}/accounts/{cash}/ledger",
        headers=reverse_headers,
        params={
            "limit": 1,
            "after_created_at": first_ledger["next_cursor_created_at"],
            "after_entry_id": first_ledger["next_cursor_entry_id"],
        },
    )
    assert second_page.status_code == 200, second_page.text
    second_ledger = second_page.json()
    assert len(second_ledger["entries"]) == 1
    assert second_ledger["opening_balance_minor"] == first_ledger["closing_balance_minor"]
    assert second_ledger["closing_balance_minor"] == 0
    assert second_ledger["has_more"] is False


def test_accounting_period_overlap_close_and_posting_guard(client):
    from datetime import datetime, timedelta, timezone

    token, org_id = owner_and_org(client, "period-control@example.com")
    today = datetime.now(timezone.utc).date()
    period_payload = {
        "name": "Current Period",
        "starts_on": (today - timedelta(days=1)).isoformat(),
        "ends_on": (today + timedelta(days=1)).isoformat(),
    }
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        f"/api/v1/organizations/{org_id}/accounting-periods",
        headers=headers,
        json=period_payload,
    )
    assert created.status_code == 201, created.text
    period = created.json()
    assert period["status"] == "open"

    overlap = client.post(
        f"/api/v1/organizations/{org_id}/accounting-periods",
        headers=headers,
        json={
            "name": "Overlapping Period",
            "starts_on": today.isoformat(),
            "ends_on": (today + timedelta(days=10)).isoformat(),
        },
    )
    assert overlap.status_code == 409

    cash = create_account(client, token, org_id, "1000", "asset")
    revenue = create_account(client, token, org_id, "4000", "revenue")
    journal_headers = {**headers, "Idempotency-Key": "period-posting-001"}
    payload = {
        "description": "Sale in open period",
        "lines": [
            {"account_id": cash, "debit_minor": 500, "credit_minor": 0},
            {"account_id": revenue, "debit_minor": 0, "credit_minor": 500},
        ],
    }
    posted = client.post(
        f"/api/v1/organizations/{org_id}/journal-entries",
        headers=journal_headers,
        json=payload,
    )
    assert posted.status_code == 201, posted.text

    period_trial = client.get(
        f"/api/v1/organizations/{org_id}/accounting-periods/{period['id']}/trial-balance",
        headers=headers,
    )
    assert period_trial.status_code == 200, period_trial.text
    period_balances = {row["account_id"]: row for row in period_trial.json()}
    assert period_balances[cash]["debit_total_minor"] == 500
    assert period_balances[cash]["credit_total_minor"] == 0
    assert period_balances[revenue]["debit_total_minor"] == 0
    assert period_balances[revenue]["credit_total_minor"] == 500
    assert all(row["normal_balance_minor"] == 0 for row in period_trial.json() if row["account_type"] not in {"asset", "revenue"})

    closed = client.post(
        f"/api/v1/organizations/{org_id}/accounting-periods/{period['id']}/close",
        headers=headers,
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert closed.json()["closed_at"] is not None

    closed_again = client.post(
        f"/api/v1/organizations/{org_id}/accounting-periods/{period['id']}/close",
        headers=headers,
    )
    assert closed_again.status_code == 200
    assert closed_again.json()["status"] == "closed"

    new_entry = client.post(
        f"/api/v1/organizations/{org_id}/journal-entries",
        headers={**headers, "Idempotency-Key": "period-posting-002"},
        json=payload,
    )
    assert new_entry.status_code == 409
    assert "accounting period" in new_entry.json()["detail"].lower()

    # A replay of a successfully posted request remains idempotent after closure.
    replay = client.post(
        f"/api/v1/organizations/{org_id}/journal-entries",
        headers=journal_headers,
        json=payload,
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == posted.json()["id"]


def test_accounting_period_rejects_invalid_date_range(client):
    token, org_id = owner_and_org(client, "period-invalid@example.com")
    response = client.post(
        f"/api/v1/organizations/{org_id}/accounting-periods",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Invalid Period", "starts_on": "2026-12-31", "ends_on": "2026-01-01"},
    )
    assert response.status_code == 422
