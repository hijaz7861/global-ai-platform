"""Bank reconciliation tests for tenant isolation, exact matching, and completion rules."""
from datetime import datetime, timezone

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
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")

    def override_get_db():
        db = sessions()
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
    assert client.post("/api/v1/auth/register", json={"email": email, "password": password}).status_code == 201
    login = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    org = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"{email.split('@')[0]} org"},
    )
    assert org.status_code == 201, org.text
    return token, org.json()["id"]


def create_account(client: TestClient, token: str, org_id: str, code: str, account_type: str) -> str:
    response = client.post(
        f"/api/v1/organizations/{org_id}/accounts",
        headers={"Authorization": f"Bearer {token}"},
        json={"code": code, "name": code.title(), "account_type": account_type, "currency": "PKR"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def post_entry(client, token, org_id, key, description, cash_account, other_account, cash_debit, cash_credit):
    response = client.post(
        f"/api/v1/organizations/{org_id}/journal-entries",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
        json={
            "description": description,
            "lines": [
                {"account_id": cash_account, "debit_minor": cash_debit, "credit_minor": cash_credit},
                {
                    "account_id": other_account,
                    "debit_minor": cash_credit,
                    "credit_minor": cash_debit,
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return next(line["id"] for line in response.json()["lines"] if line["account_id"] == cash_account)


def test_reconciliation_matches_statement_lines_and_completes(client):
    token, org_id = owner_and_org(client, "reconcile-owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    cash = create_account(client, token, org_id, "1000", "asset")
    revenue = create_account(client, token, org_id, "4000", "revenue")
    expense = create_account(client, token, org_id, "5000", "expense")
    deposit_line_id = post_entry(
        client, token, org_id, "recon-deposit-0001", "Bank deposit", cash, revenue, 500, 0
    )
    withdrawal_line_id = post_entry(
        client, token, org_id, "recon-withdraw-0001", "Bank withdrawal", cash, expense, 0, 200
    )
    today = datetime.now(timezone.utc).date().isoformat()
    created = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations",
        headers=headers,
        json={
            "account_id": cash,
            "statement_start_date": today,
            "statement_end_date": today,
            "opening_balance_minor": 1000,
            "closing_balance_minor": 1300,
        },
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    assert created.json()["status"] == "open"

    deposit = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines",
        headers=headers,
        json={"statement_date": today, "description": "Customer deposit", "amount_minor": 500, "external_reference": "BANK-001"},
    )
    withdrawal = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines",
        headers=headers,
        json={"statement_date": today, "description": "Supplier payment", "amount_minor": -200, "external_reference": "BANK-002"},
    )
    assert deposit.status_code == 201, deposit.text
    assert withdrawal.status_code == 201, withdrawal.text

    premature = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/complete",
        headers=headers,
    )
    assert premature.status_code == 409

    mismatch = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/{withdrawal.json()['id']}/match",
        headers=headers,
        json={"journal_line_id": deposit_line_id},
    )
    assert mismatch.status_code == 422

    matched_deposit = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/{deposit.json()['id']}/match",
        headers=headers,
        json={"journal_line_id": deposit_line_id},
    )
    matched_withdrawal = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/{withdrawal.json()['id']}/match",
        headers=headers,
        json={"journal_line_id": withdrawal_line_id},
    )
    assert matched_deposit.status_code == 200, matched_deposit.text
    assert matched_withdrawal.status_code == 200, matched_withdrawal.text

    completed = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/complete",
        headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["line_count"] == 2
    assert completed.json()["matched_line_count"] == 2
    assert completed.json()["unmatched_line_count"] == 0

    immutable = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines",
        headers=headers,
        json={"statement_date": today, "description": "Late line", "amount_minor": 100},
    )
    assert immutable.status_code == 409
    replay_complete = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/complete",
        headers=headers,
    )
    assert replay_complete.status_code == 200
    assert replay_complete.json()["status"] == "completed"


def test_reconciliation_rejects_cross_tenant_accounts_and_duplicate_matches(client):
    token_a, org_a = owner_and_org(client, "reconcile-a@example.com")
    token_b, org_b = owner_and_org(client, "reconcile-b@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}
    cash_a = create_account(client, token_a, org_a, "1000", "asset")
    revenue_a = create_account(client, token_a, org_a, "4000", "revenue")

    today = datetime.now(timezone.utc).date().isoformat()
    cross_tenant = client.post(
        f"/api/v1/organizations/{org_b}/reconciliations",
        headers=headers_b,
        json={
            "account_id": cash_a,
            "statement_start_date": today,
            "statement_end_date": today,
            "opening_balance_minor": 0,
            "closing_balance_minor": 500,
        },
    )
    assert cross_tenant.status_code == 404

    line_a = post_entry(
        client, token_a, org_a, "recon-unique-0001", "First deposit", cash_a, revenue_a, 500, 0
    )
    line_b = post_entry(
        client, token_a, org_a, "recon-unique-0002", "Second deposit", cash_a, revenue_a, 500, 0
    )
    created = client.post(
        f"/api/v1/organizations/{org_a}/reconciliations",
        headers=headers_a,
        json={
            "account_id": cash_a,
            "statement_start_date": today,
            "statement_end_date": today,
            "opening_balance_minor": 0,
            "closing_balance_minor": 1000,
        },
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    first = client.post(
        f"/api/v1/organizations/{org_a}/reconciliations/{session_id}/lines",
        headers=headers_a,
        json={"statement_date": today, "description": "Deposit 1", "amount_minor": 500},
    )
    second = client.post(
        f"/api/v1/organizations/{org_a}/reconciliations/{session_id}/lines",
        headers=headers_a,
        json={"statement_date": today, "description": "Deposit 2", "amount_minor": 500},
    )
    assert first.status_code == 201 and second.status_code == 201

    first_match = client.post(
        f"/api/v1/organizations/{org_a}/reconciliations/{session_id}/lines/{first.json()['id']}/match",
        headers=headers_a,
        json={"journal_line_id": line_a},
    )
    duplicate_match = client.post(
        f"/api/v1/organizations/{org_a}/reconciliations/{session_id}/lines/{second.json()['id']}/match",
        headers=headers_a,
        json={"journal_line_id": line_a},
    )
    second_match = client.post(
        f"/api/v1/organizations/{org_a}/reconciliations/{session_id}/lines/{second.json()['id']}/match",
        headers=headers_a,
        json={"journal_line_id": line_b},
    )
    assert first_match.status_code == 200, first_match.text
    assert duplicate_match.status_code == 409
    assert second_match.status_code == 200, second_match.text

    other_org_read = client.get(
        f"/api/v1/organizations/{org_a}/reconciliations/{session_id}/lines",
        headers=headers_b,
    )
    assert other_org_read.status_code == 404
