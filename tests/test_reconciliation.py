"""Bank reconciliation tests for tenant isolation, exact matching, and completion rules."""
import pytest
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

    temporary = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines",
        headers=headers,
        json={"statement_date": today, "description": "Temporary import", "amount_minor": 100, "external_reference": "BANK-TEMP"},
    )
    assert temporary.status_code == 201, temporary.text
    deleted = client.delete(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/{temporary.json()['id']}",
        headers=headers,
    )
    assert deleted.status_code == 204

    corrected_balances = client.put(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/balances",
        headers=headers,
        json={"opening_balance_minor": 999, "closing_balance_minor": 1299},
    )
    assert corrected_balances.status_code == 200, corrected_balances.text
    restored_balances = client.put(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/balances",
        headers=headers,
        json={"opening_balance_minor": 1000, "closing_balance_minor": 1300},
    )
    assert restored_balances.status_code == 200, restored_balances.text

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

    cannot_edit_matched = client.put(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/{deposit.json()['id']}",
        headers=headers,
        json={"statement_date": today, "description": "Edited while matched", "amount_minor": 500, "external_reference": "BANK-001"},
    )
    assert cannot_edit_matched.status_code == 409
    unmatched = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/{deposit.json()['id']}/unmatch",
        headers=headers,
    )
    assert unmatched.status_code == 200, unmatched.text
    corrected_line = client.put(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/{deposit.json()['id']}",
        headers=headers,
        json={"statement_date": today, "description": "Corrected customer deposit", "amount_minor": 500, "external_reference": "BANK-001"},
    )
    assert corrected_line.status_code == 200, corrected_line.text
    rematched = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/{deposit.json()['id']}/match",
        headers=headers,
        json={"journal_line_id": deposit_line_id},
    )
    assert rematched.status_code == 200, rematched.text

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



def test_csv_import_is_atomic_provenanced_and_idempotent(client):
    token, org_id = owner_and_org(client, "reconcile-csv@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    cash = create_account(client, token, org_id, "1000", "asset")
    today = datetime.now(timezone.utc).date().isoformat()
    created = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations",
        headers=headers,
        json={
            "account_id": cash,
            "statement_start_date": today,
            "statement_end_date": today,
            "opening_balance_minor": 0,
            "closing_balance_minor": 300,
        },
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    csv_bytes = (
        "statement_date,description,amount_minor,external_reference\\n"
        f"{today},Deposit,500,BANK-CSV-001\\n"
        f"{today},Withdrawal,-200,BANK-CSV-002\\n"
    ).encode()
    import_url = f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/import-csv"
    csv_headers = {**headers, "Content-Type": "text/csv", "X-Import-Filename": "../bank-statement.csv"}

    imported = client.post(import_url, headers=csv_headers, content=csv_bytes)
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["row_count"] == 2
    assert body["source_filename"] == "bank-statement.csv"
    assert len(body["content_sha256"]) == 64
    assert body["replayed"] is False

    lines = client.get(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines",
        headers=headers,
    )
    assert lines.status_code == 200
    assert len(lines.json()) == 2
    assert {line["import_batch_id"] for line in lines.json()} == {body["import_batch_id"]}

    replay = client.post(import_url, headers=csv_headers, content=csv_bytes)
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["import_batch_id"] == body["import_batch_id"]
    assert len(client.get(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines",
        headers=headers,
    ).json()) == 2

    bad_csv = (
        "statement_date,description,amount_minor,external_reference\\n"
        f"{today},Valid row,100,BANK-CSV-003\\n"
        f"{today},Invalid zero,0,BANK-CSV-004\\n"
    ).encode()
    rejected = client.post(import_url, headers=csv_headers, content=bad_csv)
    assert rejected.status_code == 422
    after_rejection = client.get(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines",
        headers=headers,
    )
    assert len(after_rejection.json()) == 2


def test_csv_import_rejects_duplicate_references_and_malformed_headers(client):
    token, org_id = owner_and_org(client, "reconcile-csv-invalid@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    cash = create_account(client, token, org_id, "1000", "asset")
    today = datetime.now(timezone.utc).date().isoformat()
    created = client.post(
        f"/api/v1/organizations/{org_id}/reconciliations",
        headers=headers,
        json={
            "account_id": cash,
            "statement_start_date": today,
            "statement_end_date": today,
            "opening_balance_minor": 0,
            "closing_balance_minor": 100,
        },
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    url = f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines/import-csv"
    csv_headers = {**headers, "Content-Type": "text/csv"}
    duplicate_refs = (
        "statement_date,description,amount_minor,external_reference\\n"
        f"{today},First,50,DUPLICATE\\n"
        f"{today},Second,50,DUPLICATE\\n"
    ).encode()
    rejected = client.post(url, headers=csv_headers, content=duplicate_refs)
    assert rejected.status_code == 422
    wrong_header = client.post(
        url,
        headers=csv_headers,
        content=b"date,description,amount_minor,external_reference\\n2026-01-01,Wrong,100,REF-1\\n",
    )
    assert wrong_header.status_code == 422
    lines = client.get(
        f"/api/v1/organizations/{org_id}/reconciliations/{session_id}/lines",
        headers=headers,
    )
    assert lines.status_code == 200
    assert lines.json() == []
