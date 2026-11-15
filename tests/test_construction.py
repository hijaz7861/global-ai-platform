"""Construction entitlement, tenant isolation, lifecycle, and BOQ regression tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.api.routes import identity as identity_routes
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import (AuditEvent, ConstructionBoqItem, ConstructionQuote, ConstructionQuoteLine,
                        Membership, Organization, RoleDefinition, User, UserRoleEntitlement, utc_now)

_EMAIL_TOKENS: dict[str, str] = {}
_TEST_SESSION_FACTORY = None


@pytest.fixture
def client(monkeypatch):
    global _TEST_SESSION_FACTORY
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(engine, "connect", enable_foreign_keys)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    _TEST_SESSION_FACTORY = sessions
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform-admin@example.com")
    monkeypatch.setattr(settings, "require_email_verification", True)
    _EMAIL_TOKENS.clear()
    monkeypatch.setattr(identity_routes, "send_email_verification",
                        lambda email, token: _EMAIL_TOKENS.__setitem__(email, token))
    with sessions() as db:
        db.add(RoleDefinition(role_key="construction_provider", display_name="Construction Service Provider",
            description="Construction projects and BOQ", min_age=18, fee_amount_minor=None,
            fee_currency="PKR", requires_review=True, is_active=True))
        db.commit()

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    _EMAIL_TOKENS.clear()
    _TEST_SESSION_FACTORY = None


def register(client: TestClient, email: str, *, verify: bool = True) -> str:
    password = "correct-horse-battery-12"
    created = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert created.status_code == 201, created.text
    if verify:
        raw = _EMAIL_TOKENS.pop(email)
        assert client.post("/api/v1/auth/email-verification/confirm", json={"token": raw}).status_code == 204
    login = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def organization(client: TestClient, token: str, name: str) -> str:
    response = client.post("/api/v1/organizations", headers={"Authorization": f"Bearer {token}"},
                           json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def activate_role(client: TestClient, token: str, organization_id: str, admin_headers: dict) -> None:
    configured = client.patch("/api/v1/platform/role-definitions/construction_provider",
        headers=admin_headers, json={"fee_amount_minor": 1000, "fee_currency": "PKR",
                                     "min_age": 18, "is_active": True})
    assert configured.status_code == 200, configured.text
    application = client.post("/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {token}"},
        json={"role_key": "construction_provider", "organization_id": organization_id})
    assert application.status_code == 201, application.text
    reviewed = client.post(f"/api/v1/platform/role-entitlements/{application.json()['id']}/review",
        headers=admin_headers, json={"decision": "approve", "payment_verified": True,
                                     "age_verified": True, "notes": "Manual construction role review"})
    assert reviewed.status_code == 200, reviewed.text


def create_client_record(client: TestClient, token: str, organization_id: str, name: str = "North Build Client"):
    return client.post(f"/api/v1/organizations/{organization_id}/construction/clients",
        headers={"Authorization": f"Bearer {token}"}, json={"display_name": name, "city": "Lahore"})


def create_project(client: TestClient, token: str, organization_id: str, client_id: str):
    return client.post(f"/api/v1/organizations/{organization_id}/construction/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"client_id": client_id, "title": "Grey structure project", "work_type": "grey_structure",
              "contract_type": "labour_material", "contract_amount_minor": 2500000, "currency": "PKR",
              "location_label": "Lahore", "start_date": "2026-11-01", "target_date": "2027-03-01"})


def test_construction_project_boq_decimal_totals_and_audit(client):
    token = register(client, "construction-owner@example.com")
    org_id = organization(client, token, "Construction Org")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, token, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, token, org_id)
    assert client_row.status_code == 201, client_row.text
    project = create_project(client, token, org_id, client_row.json()["id"])
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    boq = client.post(f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/boq-items",
        headers={"Authorization": f"Bearer {token}"},
        json={"line_number": 1, "category": "Concrete", "item_name": "Cement", "unit": "bag",
              "quantity": "0.001", "unit_rate_minor": 500})
    assert boq.status_code == 201, boq.text
    assert boq.json()["line_total_minor"] == 1
    detail = client.get(f"/api/v1/organizations/{org_id}/construction/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"})
    assert detail.status_code == 200 and detail.json()["boq_total_minor"] == 1
    assert detail.json()["project"]["currency"] == "PKR"
    with _TEST_SESSION_FACTORY() as db:
        events = db.scalars(select(AuditEvent).where(
            AuditEvent.organization_id == org_id,
            AuditEvent.entity_type.in_(["construction_client", "construction_project", "construction_boq_item"])
        )).all()
        assert len(events) == 3


def test_construction_missing_role_and_unverified_email_are_denied(client, monkeypatch):
    token = register(client, "no-role@example.com")
    org_id = organization(client, token, "No Role Org")
    assert create_client_record(client, token, org_id).status_code == 403

    # Permit token issuance only for this test; require_active_role must still reject the unverified identity.
    monkeypatch.setattr(settings, "require_email_verification", False)
    unverified = register(client, "unverified-construction@example.com", verify=False)
    with _TEST_SESSION_FACTORY() as db:
        user = db.scalar(select(User).where(User.email == "unverified-construction@example.com"))
        org = Organization(name="Unverified Org")
        db.add(org)
        db.flush()
        db.add(Membership(user_id=user.id, organization_id=org.id, role="owner"))
        db.add(UserRoleEntitlement(user_id=user.id, role_key="construction_provider",
            organization_id=org.id, status="active", payment_status="verified",
            fee_amount_minor=1000, fee_currency="PKR", age_verified=True, activated_at=utc_now()))
        db.commit()
        unverified_org = org.id
    denied = create_client_record(client, unverified, unverified_org)
    assert denied.status_code == 403
    assert "verified email" in denied.json()["detail"].lower()


def test_construction_cross_tenant_client_and_nonmember_access_are_denied(client):
    token = register(client, "construction-multi-org@example.com")
    org_a = organization(client, token, "Construction A")
    org_b = organization(client, token, "Construction B")
    admin = register(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin}"}
    activate_role(client, token, org_a, admin_headers)
    activate_role(client, token, org_b, admin_headers)
    client_a = create_client_record(client, token, org_a, "Tenant A Client")
    assert client_a.status_code == 201, client_a.text
    assert create_project(client, token, org_b, client_a.json()["id"]).status_code == 404
    outsider = register(client, "construction-outsider@example.com")
    hidden = client.get(f"/api/v1/organizations/{org_a}/construction/projects",
        headers={"Authorization": f"Bearer {outsider}"})
    assert hidden.status_code == 404


def test_construction_boq_rejects_zero_quantity_and_duplicate_line(client):
    token = register(client, "construction-boq@example.com")
    org_id = organization(client, token, "Construction BOQ Org")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, token, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, token, org_id)
    project = create_project(client, token, org_id, client_row.json()["id"])
    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project.json()['id']}/boq-items"
    headers = {"Authorization": f"Bearer {token}"}
    invalid = client.post(endpoint, headers=headers,
        json={"line_number": 1, "item_name": "Sand", "unit": "cft", "quantity": "0", "unit_rate_minor": 10})
    assert invalid.status_code == 422
    valid = {"line_number": 1, "item_name": "Sand", "unit": "cft", "quantity": "2.500", "unit_rate_minor": 100}
    assert client.post(endpoint, headers=headers, json=valid).status_code == 201
    assert client.post(endpoint, headers=headers, json=valid).status_code == 409


def test_construction_lifecycle_is_bounded_and_completion_requires_100_percent(client):
    token = register(client, "construction-lifecycle@example.com")
    org_id = organization(client, token, "Construction Lifecycle Org")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, token, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, token, org_id)
    project = create_project(client, token, org_id, client_row.json()["id"])
    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project.json()['id']}"
    headers = {"Authorization": f"Bearer {token}"}
    assert client.patch(endpoint, headers=headers, json={"status": "in_progress"}).status_code == 409
    assert client.patch(endpoint, headers=headers, json={"status": "quoted"}).status_code == 200
    assert client.patch(endpoint, headers=headers, json={"status": "approved"}).status_code == 200
    assert client.patch(endpoint, headers=headers, json={"status": "in_progress"}).status_code == 200
    assert client.patch(endpoint, headers=headers, json={"status": "completed", "progress_percent": 99}).status_code == 409
    assert client.patch(endpoint, headers=headers, json={"status": "completed", "progress_percent": 100}).status_code == 200
    assert client.patch(endpoint, headers=headers, json={"status": "in_progress"}).status_code == 409


def test_construction_project_updates_are_creator_or_admin_only(client):
    owner = register(client, "construction-owner-two@example.com")
    org_id = organization(client, owner, "Construction Ownership Org")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, owner, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, owner, org_id)
    project = create_project(client, owner, org_id, client_row.json()["id"])
    member_token = register(client, "construction-member@example.com")
    with _TEST_SESSION_FACTORY() as db:
        member = db.scalar(select(User).where(User.email == "construction-member@example.com"))
        db.add(Membership(user_id=member.id, organization_id=org_id, role="member"))
        db.add(UserRoleEntitlement(user_id=member.id, role_key="construction_provider",
            organization_id=org_id, status="active", payment_status="verified",
            fee_amount_minor=1000, fee_currency="PKR", age_verified=True, activated_at=utc_now()))
        db.commit()
    denied = client.patch(f"/api/v1/organizations/{org_id}/construction/projects/{project.json()['id']}",
        headers={"Authorization": f"Bearer {member_token}"}, json={"progress_percent": 10})
    assert denied.status_code == 403


def test_construction_project_boq_total_includes_lines_beyond_detail_limit(client):
    token = register(client, "construction-large-boq@example.com")
    org_id = organization(client, token, "Construction Large BOQ Org")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, token, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, token, org_id)
    project = create_project(client, token, org_id, client_row.json()["id"])
    project_id = project.json()["id"]
    with _TEST_SESSION_FACTORY() as db:
        user = db.scalar(select(User).where(User.email == "construction-large-boq@example.com"))
        db.add_all([
            ConstructionBoqItem(
                organization_id=org_id,
                project_id=project_id,
                created_by_user_id=user.id,
                line_number=index,
                item_name=f"BOQ line {index}",
                unit="unit",
                quantity=Decimal("1.000"),
                unit_rate_minor=100,
                line_total_minor=100,
            )
            for index in range(1, 502)
        ])
        db.commit()
    detail = client.get(
        f"/api/v1/organizations/{org_id}/construction/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["boq_items"]) == 500
    assert detail.json()["boq_total_minor"] == 50100



def test_construction_quote_uses_decimal_minor_units_and_immutable_boq_snapshot(client):
    token = register(client, "construction-quote-owner@example.com")
    org_id = organization(client, token, "Construction Quote Org")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, token, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, token, org_id)
    project = create_project(client, token, org_id, client_row.json()["id"])
    project_id = project.json()["id"]
    headers = {"Authorization": f"Bearer {token}"}
    boq_endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/boq-items"
    first = client.post(boq_endpoint, headers=headers, json={
        "line_number": 1, "category": "Concrete", "item_name": "Cement", "unit": "bag",
        "quantity": "1.500", "unit_rate_minor": 1001,
    })
    second = client.post(boq_endpoint, headers=headers, json={
        "line_number": 2, "category": "Steel", "item_name": "Rebar", "unit": "kg",
        "quantity": "2.000", "unit_rate_minor": 500,
    })
    assert first.status_code == second.status_code == 201

    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/quotes"
    created = client.post(endpoint, headers=headers, json={
        "overhead_pct": "10.00", "profit_pct": "10.00", "discount_pct": "5.00",
        "tax_pct": "17.00", "validity_days": 15, "terms": "Estimate only; subject to signed contract.",
    })
    assert created.status_code == 201, created.text
    body = created.json()
    quote = body["quote"]
    assert quote["status"] == "draft"
    assert quote["currency"] == "PKR"
    assert quote["boq_subtotal_minor"] == 2502
    assert quote["overhead_amount_minor"] == 250
    assert quote["profit_amount_minor"] == 275
    assert quote["discount_amount_minor"] == 151
    assert quote["tax_amount_minor"] == 489
    assert quote["total_minor"] == 3365
    assert len(body["lines"]) == 2
    assert body["lines"][0]["line_total_minor"] == 1502

    with _TEST_SESSION_FACTORY() as db:
        boq_line = db.scalar(select(ConstructionBoqItem).where(
            ConstructionBoqItem.project_id == project_id, ConstructionBoqItem.line_number == 1))
        boq_line.unit_rate_minor = 9999
        boq_line.line_total_minor = 14999
        db.commit()
    detail = client.get(f"{endpoint}/{quote['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["lines"][0]["unit_rate_minor"] == 1001
    assert detail.json()["lines"][0]["line_total_minor"] == 1502
    with _TEST_SESSION_FACTORY() as db:
        snapshot = db.scalars(select(ConstructionQuoteLine).where(
            ConstructionQuoteLine.quote_id == quote["id"])).all()
        assert len(snapshot) == 2
        event_row = db.scalar(select(AuditEvent).where(
            AuditEvent.entity_type == "construction_quote", AuditEvent.entity_id == quote["id"]))
        assert event_row is not None and event_row.action == "construction_quote.created"


def test_construction_quote_send_accept_and_expiry_lifecycle(client):
    token = register(client, "construction-quote-lifecycle@example.com")
    org_id = organization(client, token, "Construction Quote Lifecycle Org")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, token, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, token, org_id)
    project = create_project(client, token, org_id, client_row.json()["id"])
    project_id = project.json()["id"]
    headers = {"Authorization": f"Bearer {token}"}
    boq_endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/boq-items"
    assert client.post(boq_endpoint, headers=headers, json={
        "line_number": 1, "item_name": "Sand", "unit": "cft", "quantity": "2.000", "unit_rate_minor": 100,
    }).status_code == 201
    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/quotes"
    first = client.post(endpoint, headers=headers, json={})
    assert first.status_code == 201, first.text
    quote_id = first.json()["quote"]["id"]
    sent = client.post(f"{endpoint}/{quote_id}/send", headers=headers)
    assert sent.status_code == 200, sent.text
    assert sent.json()["quote"]["status"] == "sent"
    with _TEST_SESSION_FACTORY() as db:
        project_row = db.get(models.ConstructionProject, project_id)
        assert project_row.status == "quoted"
    accepted = client.post(f"{endpoint}/{quote_id}/decision", headers=headers, json={"decision": "accept"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["quote"]["status"] == "accepted"
    repeated = client.post(f"{endpoint}/{quote_id}/decision", headers=headers, json={"decision": "reject"})
    assert repeated.status_code == 409

    second = client.post(endpoint, headers=headers, json={})
    assert second.status_code == 201, second.text
    second_id = second.json()["quote"]["id"]
    assert client.post(f"{endpoint}/{second_id}/send", headers=headers).status_code == 200
    with _TEST_SESSION_FACTORY() as db:
        quote_row = db.get(ConstructionQuote, second_id)
        quote_row.valid_until = datetime.now(timezone.utc).date() - timedelta(days=1)
        db.commit()
    expired = client.post(f"{endpoint}/{second_id}/decision", headers=headers, json={"decision": "reject"})
    assert expired.status_code == 409
    with _TEST_SESSION_FACTORY() as db:
        assert db.get(ConstructionQuote, second_id).status == "expired"


def test_construction_quote_requires_boq_and_rejects_invalid_percentages(client):
    token = register(client, "construction-quote-validation@example.com")
    org_id = organization(client, token, "Construction Quote Validation Org")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, token, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, token, org_id)
    project = create_project(client, token, org_id, client_row.json()["id"])
    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project.json()['id']}/quotes"
    headers = {"Authorization": f"Bearer {token}"}
    missing_boq = client.post(endpoint, headers=headers, json={})
    assert missing_boq.status_code == 409
    invalid_pct = client.post(endpoint, headers=headers, json={"tax_pct": "100.01"})
    assert invalid_pct.status_code == 422


def test_construction_quote_decision_is_owner_admin_only(client):
    owner_token = register(client, "construction-quote-owner-only@example.com")
    org_id = organization(client, owner_token, "Construction Quote Decision Org")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, owner_token, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, owner_token, org_id)
    project = create_project(client, owner_token, org_id, client_row.json()["id"])
    project_id = project.json()["id"]
    headers = {"Authorization": f"Bearer {owner_token}"}
    boq_endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/boq-items"
    assert client.post(boq_endpoint, headers=headers, json={
        "line_number": 1, "item_name": "Blocks", "unit": "piece", "quantity": "10.000", "unit_rate_minor": 25,
    }).status_code == 201
    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/quotes"
    created = client.post(endpoint, headers=headers, json={})
    quote_id = created.json()["quote"]["id"]
    assert client.post(f"{endpoint}/{quote_id}/send", headers=headers).status_code == 200

    member_token = register(client, "construction-quote-member@example.com")
    with _TEST_SESSION_FACTORY() as db:
        member = db.scalar(select(User).where(User.email == "construction-quote-member@example.com"))
        db.add(Membership(user_id=member.id, organization_id=org_id, role="member"))
        db.add(UserRoleEntitlement(user_id=member.id, role_key="construction_provider", organization_id=org_id,
            status="active", payment_status="verified", fee_amount_minor=1000, fee_currency="PKR",
            age_verified=True, activated_at=utc_now()))
        db.commit()
    denied = client.post(f"{endpoint}/{quote_id}/decision",
        headers={"Authorization": f"Bearer {member_token}"}, json={"decision": "accept"})
    assert denied.status_code == 403
    assert client.get(f"{endpoint}/{quote_id}", headers={"Authorization": f"Bearer {member_token}"}).status_code == 200



def _create_cashbook_fixture(client, email: str):
    token = register(client, email)
    org_id = organization(client, token, f"Cashbook Org {email.split('@')[0]}")
    admin = register(client, "platform-admin@example.com")
    activate_role(client, token, org_id, {"Authorization": f"Bearer {admin}"})
    client_row = create_client_record(client, token, org_id)
    project = create_project(client, token, org_id, client_row.json()["id"])
    project_id = project.json()["id"]
    with _TEST_SESSION_FACTORY() as db:
        project_row = db.get(models.ConstructionProject, project_id)
        project_row.status = "approved"
        cash = models.Account(
            organization_id=org_id, code="1000", name="Project Cash",
            account_type="asset", currency="PKR", is_active=True,
        )
        revenue = models.Account(
            organization_id=org_id, code="4000", name="Construction Revenue",
            account_type="revenue", currency="PKR", is_active=True,
        )
        expense = models.Account(
            organization_id=org_id, code="5000", name="Construction Expense",
            account_type="expense", currency="PKR", is_active=True,
        )
        db.add_all([cash, revenue, expense])
        db.flush()
        account_ids = {"cash": cash.id, "revenue": revenue.id, "expense": expense.id}
        db.commit()
    return token, org_id, project_id, account_ids, email


def test_construction_cashbook_posts_balanced_income_expense_and_is_idempotent(client):
    token, org_id, project_id, accounts, _ = _create_cashbook_fixture(
        client, "construction-cashbook-owner@example.com"
    )
    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/cashbook"
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "cashbook-income-0001"}
    income_payload = {
        "direction": "income", "cash_account_id": accounts["cash"],
        "category_account_id": accounts["revenue"], "category": "Client advance",
        "amount_minor": 12500, "description": "Approved project advance",
    }
    income = client.post(endpoint, headers=headers, json=income_payload)
    assert income.status_code == 201, income.text
    repeated = client.post(endpoint, headers=headers, json=income_payload)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == income.json()["id"]

    changed_payload = {**income_payload, "amount_minor": 13000}
    conflict = client.post(endpoint, headers=headers, json=changed_payload)
    assert conflict.status_code == 409

    expense = client.post(
        endpoint,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "cashbook-expense-0001"},
        json={
            "direction": "expense", "cash_account_id": accounts["cash"],
            "category_account_id": accounts["expense"], "category": "Cement purchase",
            "amount_minor": 4300, "description": "Project materials",
        },
    )
    assert expense.status_code == 201, expense.text

    with _TEST_SESSION_FACTORY() as db:
        cashbook_rows = db.scalars(select(models.ConstructionCashbookEntry).where(
            models.ConstructionCashbookEntry.project_id == project_id
        )).all()
        assert len(cashbook_rows) == 2
        journal_ids = {row.journal_entry_id for row in cashbook_rows}
        journal_entries = db.scalars(select(models.JournalEntry).where(
            models.JournalEntry.id.in_(journal_ids)
        )).all()
        assert len(journal_entries) == 2
        for entry in journal_entries:
            lines = db.scalars(select(models.JournalLine).where(
                models.JournalLine.journal_entry_id == entry.id
            )).all()
            assert sum(line.debit_minor for line in lines) == entry.total_minor
            assert sum(line.credit_minor for line in lines) == entry.total_minor
        income_entry = next(item for item in journal_entries if item.id == income.json()["journal_entry_id"])
        income_lines = db.scalars(select(models.JournalLine).where(
            models.JournalLine.journal_entry_id == income_entry.id
        )).all()
        assert {line.account_id: (line.debit_minor, line.credit_minor) for line in income_lines} == {
            accounts["cash"]: (12500, 0), accounts["revenue"]: (0, 12500)
        }
        expense_entry = next(item for item in journal_entries if item.id == expense.json()["journal_entry_id"])
        expense_lines = db.scalars(select(models.JournalLine).where(
            models.JournalLine.journal_entry_id == expense_entry.id
        )).all()
        assert {line.account_id: (line.debit_minor, line.credit_minor) for line in expense_lines} == {
            accounts["expense"]: (4300, 0), accounts["cash"]: (0, 4300)
        }
        events = db.scalars(select(AuditEvent).where(
            AuditEvent.organization_id == org_id,
            AuditEvent.action == "construction_cashbook.posted",
        )).all()
        assert len(events) == 2

    listed = client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert len(listed.json()) == 2


def test_construction_cashbook_rejects_cross_tenant_wrong_type_currency_and_invalid_amount(client):
    token, org_id, project_id, accounts, _ = _create_cashbook_fixture(
        client, "construction-cashbook-validation@example.com"
    )
    with _TEST_SESSION_FACTORY() as db:
        foreign_org = Organization(name="Foreign Cashbook Organization")
        db.add(foreign_org)
        db.flush()
        foreign_cash = models.Account(
            organization_id=foreign_org.id, code="1000", name="Foreign Cash",
            account_type="asset", currency="PKR", is_active=True,
        )
        usd_revenue = models.Account(
            organization_id=org_id, code="4001", name="USD Revenue",
            account_type="revenue", currency="USD", is_active=True,
        )
        db.add_all([foreign_cash, usd_revenue])
        db.commit()
        foreign_cash_id, usd_revenue_id = foreign_cash.id, usd_revenue.id

    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/cashbook"
    auth = {"Authorization": f"Bearer {token}"}
    base = {
        "direction": "income", "cash_account_id": accounts["cash"],
        "category_account_id": accounts["revenue"], "category": "Advance", "amount_minor": 1000,
    }
    foreign = client.post(endpoint, headers={**auth, "Idempotency-Key": "cashbook-foreign-01"},
        json={**base, "cash_account_id": foreign_cash_id})
    assert foreign.status_code == 422
    wrong_type = client.post(endpoint, headers={**auth, "Idempotency-Key": "cashbook-wrongtype-01"},
        json={**base, "category_account_id": accounts["expense"]})
    assert wrong_type.status_code == 422
    wrong_currency = client.post(endpoint, headers={**auth, "Idempotency-Key": "cashbook-currency-01"},
        json={**base, "category_account_id": usd_revenue_id})
    assert wrong_currency.status_code == 422
    same_account = client.post(endpoint, headers={**auth, "Idempotency-Key": "cashbook-sameacct-01"},
        json={**base, "category_account_id": accounts["cash"]})
    assert same_account.status_code == 422
    zero_amount = client.post(endpoint, headers={**auth, "Idempotency-Key": "cashbook-zero-amount"},
        json={**base, "amount_minor": 0})
    assert zero_amount.status_code == 422
    with _TEST_SESSION_FACTORY() as db:
        assert db.scalar(select(models.JournalEntry.id).where(
            models.JournalEntry.organization_id == org_id
        )) is None


def test_construction_cashbook_denies_non_creator_member_but_allows_tenant_read(client):
    token, org_id, project_id, accounts, _ = _create_cashbook_fixture(
        client, "construction-cashbook-owner-access@example.com"
    )
    member_token = register(client, "construction-cashbook-member@example.com")
    with _TEST_SESSION_FACTORY() as db:
        member = db.scalar(select(User).where(User.email == "construction-cashbook-member@example.com"))
        db.add(Membership(user_id=member.id, organization_id=org_id, role="member"))
        db.add(UserRoleEntitlement(
            user_id=member.id, role_key="construction_provider", organization_id=org_id,
            status="active", payment_status="verified", fee_amount_minor=1000,
            fee_currency="PKR", age_verified=True, activated_at=utc_now(),
        ))
        db.commit()
    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/cashbook"
    member_headers = {"Authorization": f"Bearer {member_token}"}
    denied = client.post(endpoint, headers={**member_headers, "Idempotency-Key": "cashbook-member-01"},
        json={
            "direction": "income", "cash_account_id": accounts["cash"],
            "category_account_id": accounts["revenue"], "category": "Advance", "amount_minor": 1000,
        })
    assert denied.status_code == 403
    listed = client.get(endpoint, headers=member_headers)
    assert listed.status_code == 200
    assert listed.json() == []


def test_construction_cashbook_rejects_closed_projects_and_closed_accounting_periods(client):
    token, org_id, project_id, accounts, owner_email = _create_cashbook_fixture(
        client, "construction-cashbook-period@example.com"
    )
    endpoint = f"/api/v1/organizations/{org_id}/construction/projects/{project_id}/cashbook"
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "cashbook-period-guard-01"}
    payload = {
        "direction": "income", "cash_account_id": accounts["cash"],
        "category_account_id": accounts["revenue"], "category": "Advance", "amount_minor": 1000,
    }
    with _TEST_SESSION_FACTORY() as db:
        project = db.get(models.ConstructionProject, project_id)
        project.status = "completed"
        db.commit()
    closed_project = client.post(endpoint, headers=headers, json=payload)
    assert closed_project.status_code == 409

    with _TEST_SESSION_FACTORY() as db:
        project = db.get(models.ConstructionProject, project_id)
        project.status = "approved"
        owner = db.scalar(select(User).where(User.email == owner_email))
        today = utc_now().date()
        db.add(models.AccountingPeriod(
            organization_id=org_id, name="Closed cashbook period",
            starts_on=today, ends_on=today, status="closed", created_by_user_id=owner.id,
        ))
        db.commit()
    closed_period = client.post(endpoint, headers=headers, json=payload)
    assert closed_period.status_code == 409
    with _TEST_SESSION_FACTORY() as db:
        assert db.scalar(select(models.JournalEntry.id).where(
            models.JournalEntry.organization_id == org_id
        )) is None
