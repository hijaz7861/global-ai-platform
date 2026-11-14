"""Construction entitlement, tenant isolation, lifecycle, and BOQ regression tests."""

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
from app.models import AuditEvent, Membership, Organization, RoleDefinition, User, UserRoleEntitlement, utc_now

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
