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
