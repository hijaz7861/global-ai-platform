"""Procurement request, supplier quote, award, and tenant-isolation tests."""
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
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform-admin@example.com")

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


def register(client: TestClient, email: str) -> str:
    password = "correct-horse-battery-12"
    created = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert created.status_code == 201, created.text
    login = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def create_org(client: TestClient, token: str, name: str) -> str:
    response = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def approve_supplier(client: TestClient, token: str, org_id: str, legal_name: str, admin_headers: dict[str, str]):
    headers = {"Authorization": f"Bearer {token}"}
    profile = client.post(
        f"/api/v1/organizations/{org_id}/supplier-profile",
        headers=headers,
        json={
            "legal_name": legal_name,
            "business_type": "manufacturer",
            "country_code": "PK",
            "contact_email": f"{legal_name.split()[0].lower()}@example.com",
            "description": "Supplier approved for procurement tests",
        },
    )
    assert profile.status_code == 201, profile.text
    submitted = client.post(
        f"/api/v1/organizations/{org_id}/supplier-profile/submit",
        headers=headers,
    )
    assert submitted.status_code == 200, submitted.text
    reviewed = client.post(
        f"/api/v1/platform/supplier-submissions/{profile.json()['id']}/review",
        headers=admin_headers,
        json={"decision": "approved", "notes": "Verification completed"},
    )
    assert reviewed.status_code == 200, reviewed.text


def test_procurement_quote_award_is_tenant_scoped_and_single_winner(client):
    admin_token = register(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    buyer_token = register(client, "buyer-procurement@example.com")
    buyer_org = create_org(client, buyer_token, "Buyer Organization")
    buyer_headers = {"Authorization": f"Bearer {buyer_token}"}

    supplier_one_token = register(client, "supplier-one@example.com")
    supplier_one_org = create_org(client, supplier_one_token, "Supplier One")
    approve_supplier(client, supplier_one_token, supplier_one_org, "Supplier One Ltd", admin_headers)

    supplier_two_token = register(client, "supplier-two@example.com")
    supplier_two_org = create_org(client, supplier_two_token, "Supplier Two")
    approve_supplier(client, supplier_two_token, supplier_two_org, "Supplier Two Ltd", admin_headers)

    created = client.post(
        f"/api/v1/organizations/{buyer_org}/procurement-requests",
        headers=buyer_headers,
        json={
            "title": "Bulk notebooks",
            "description": "Supply notebooks for the new term",
            "quantity": 100,
            "currency": "pkr",
        },
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json()["currency"] == "PKR"
    assert created.json()["status"] == "open"

    quote_one = client.post(
        f"/api/v1/organizations/{supplier_one_org}/procurement-requests/{request_id}/quotes",
        headers={"Authorization": f"Bearer {supplier_one_token}"},
        json={"unit_price_minor": 100, "currency": "PKR", "lead_time_days": 5, "notes": "First offer"},
    )
    assert quote_one.status_code == 201, quote_one.text
    quote_two = client.post(
        f"/api/v1/organizations/{supplier_two_org}/procurement-requests/{request_id}/quotes",
        headers={"Authorization": f"Bearer {supplier_two_token}"},
        json={"unit_price_minor": 120, "currency": "PKR", "lead_time_days": 2, "notes": "Faster delivery"},
    )
    assert quote_two.status_code == 201, quote_two.text

    duplicate = client.post(
        f"/api/v1/organizations/{supplier_one_org}/procurement-requests/{request_id}/quotes",
        headers={"Authorization": f"Bearer {supplier_one_token}"},
        json={"unit_price_minor": 90, "currency": "PKR", "lead_time_days": 6},
    )
    assert duplicate.status_code == 409

    quotes_url = f"/api/v1/organizations/{buyer_org}/procurement-requests/{request_id}/quotes"
    quotes = client.get(quotes_url, headers=buyer_headers)
    assert quotes.status_code == 200, quotes.text
    assert [quote["unit_price_minor"] for quote in quotes.json()] == [100, 120]

    hidden = client.get(
        f"/api/v1/organizations/{supplier_one_org}/procurement-requests/{request_id}/quotes",
        headers={"Authorization": f"Bearer {supplier_one_token}"},
    )
    assert hidden.status_code == 404

    awarded = client.post(
        f"/api/v1/organizations/{buyer_org}/procurement-requests/{request_id}/award",
        headers=buyer_headers,
        json={"quote_id": quote_one.json()["id"]},
    )
    assert awarded.status_code == 200, awarded.text
    assert awarded.json()["status"] == "awarded"
    assert awarded.json()["awarded_quote_id"] == quote_one.json()["id"]

    after_award = client.get(quotes_url, headers=buyer_headers)
    statuses = {quote["id"]: quote["status"] for quote in after_award.json()}
    assert statuses[quote_one.json()["id"]] == "accepted"
    assert statuses[quote_two.json()["id"]] == "rejected"

    issued = client.post(
        f"/api/v1/organizations/{buyer_org}/procurement-requests/{request_id}/purchase-order",
        headers=buyer_headers,
    )
    assert issued.status_code == 201, issued.text
    purchase_order = issued.json()
    assert purchase_order["status"] == "issued"
    assert purchase_order["quantity"] == 100
    assert purchase_order["unit_price_minor"] == 100
    assert purchase_order["total_minor"] == 10_000
    assert purchase_order["currency"] == "PKR"

    replay_order = client.post(
        f"/api/v1/organizations/{buyer_org}/procurement-requests/{request_id}/purchase-order",
        headers=buyer_headers,
    )
    assert replay_order.status_code == 200
    assert replay_order.json()["id"] == purchase_order["id"]

    buyer_orders = client.get(
        f"/api/v1/organizations/{buyer_org}/purchase-orders",
        headers=buyer_headers,
    )
    supplier_headers = {"Authorization": f"Bearer {supplier_one_token}"}
    supplier_orders = client.get(
        f"/api/v1/organizations/{supplier_one_org}/purchase-orders",
        headers=supplier_headers,
    )
    assert buyer_orders.status_code == 200 and len(buyer_orders.json()) == 1
    assert supplier_orders.status_code == 200 and supplier_orders.json()[0]["id"] == purchase_order["id"]

    fulfilled = client.post(
        f"/api/v1/organizations/{supplier_one_org}/purchase-orders/{purchase_order['id']}/fulfill",
        headers=supplier_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text
    assert fulfilled.json()["status"] == "fulfilled"
    fulfilled_replay = client.post(
        f"/api/v1/organizations/{supplier_one_org}/purchase-orders/{purchase_order['id']}/fulfill",
        headers=supplier_headers,
    )
    assert fulfilled_replay.status_code == 200
    assert fulfilled_replay.json()["status"] == "fulfilled"
    cannot_cancel_fulfilled = client.post(
        f"/api/v1/organizations/{buyer_org}/purchase-orders/{purchase_order['id']}/cancel",
        headers=buyer_headers,
    )
    assert cannot_cancel_fulfilled.status_code == 409

    cancel_request = client.post(
        f"/api/v1/organizations/{buyer_org}/procurement-requests",
        headers=buyer_headers,
        json={"title": "Bulk pens", "quantity": 20, "currency": "PKR"},
    )
    assert cancel_request.status_code == 201, cancel_request.text
    cancel_request_id = cancel_request.json()["id"]
    cancel_quote = client.post(
        f"/api/v1/organizations/{supplier_two_org}/procurement-requests/{cancel_request_id}/quotes",
        headers={"Authorization": f"Bearer {supplier_two_token}"},
        json={"unit_price_minor": 50, "currency": "PKR", "lead_time_days": 3},
    )
    assert cancel_quote.status_code == 201, cancel_quote.text
    cancel_award = client.post(
        f"/api/v1/organizations/{buyer_org}/procurement-requests/{cancel_request_id}/award",
        headers=buyer_headers,
        json={"quote_id": cancel_quote.json()["id"]},
    )
    assert cancel_award.status_code == 200, cancel_award.text
    cancel_order = client.post(
        f"/api/v1/organizations/{buyer_org}/procurement-requests/{cancel_request_id}/purchase-order",
        headers=buyer_headers,
    )
    assert cancel_order.status_code == 201, cancel_order.text
    cancelled = client.post(
        f"/api/v1/organizations/{buyer_org}/purchase-orders/{cancel_order.json()['id']}/cancel",
        headers=buyer_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    cancelled_replay = client.post(
        f"/api/v1/organizations/{buyer_org}/purchase-orders/{cancel_order.json()['id']}/cancel",
        headers=buyer_headers,
    )
    assert cancelled_replay.status_code == 200
    cannot_fulfil_cancelled = client.post(
        f"/api/v1/organizations/{supplier_two_org}/purchase-orders/{cancel_order.json()['id']}/fulfill",
        headers={"Authorization": f"Bearer {supplier_two_token}"},
    )
    assert cannot_fulfil_cancelled.status_code == 409

    second_award = client.post(
        f"/api/v1/organizations/{buyer_org}/procurement-requests/{request_id}/award",
        headers=buyer_headers,
        json={"quote_id": quote_two.json()["id"]},
    )
    assert second_award.status_code == 409

    late_quote = client.post(
        f"/api/v1/organizations/{supplier_two_org}/procurement-requests/{request_id}/quotes",
        headers={"Authorization": f"Bearer {supplier_two_token}"},
        json={"unit_price_minor": 110, "currency": "PKR", "lead_time_days": 3},
    )
    assert late_quote.status_code == 404


def test_procurement_requires_approved_supplier_and_matching_currency(client):
    buyer_token = register(client, "buyer-second@example.com")
    buyer_org = create_org(client, buyer_token, "Buyer Two")
    buyer_headers = {"Authorization": f"Bearer {buyer_token}"}
    request = client.post(
        f"/api/v1/organizations/{buyer_org}/procurement-requests",
        headers=buyer_headers,
        json={"title": "Raw materials", "quantity": 20, "currency": "PKR"},
    )
    assert request.status_code == 201, request.text

    supplier_token = register(client, "unapproved-supplier@example.com")
    supplier_org = create_org(client, supplier_token, "Unapproved Supplier")
    quote_url = f"/api/v1/organizations/{supplier_org}/procurement-requests/{request.json()['id']}/quotes"
    unapproved = client.post(
        quote_url,
        headers={"Authorization": f"Bearer {supplier_token}"},
        json={"unit_price_minor": 500, "currency": "PKR", "lead_time_days": 4},
    )
    assert unapproved.status_code == 403

    admin_token = register(client, "platform-admin@example.com")
    approve_supplier(
        client,
        supplier_token,
        supplier_org,
        "Unapproved Supplier Ltd",
        {"Authorization": f"Bearer {admin_token}"},
    )
    wrong_currency = client.post(
        quote_url,
        headers={"Authorization": f"Bearer {supplier_token}"},
        json={"unit_price_minor": 500, "currency": "USD", "lead_time_days": 4},
    )
    assert wrong_currency.status_code == 422
