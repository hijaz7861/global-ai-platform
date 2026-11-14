"""Transport role, tenant, moderation, and public-discovery regression tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.api.routes import identity as identity_routes
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import RoleDefinition

_EMAIL_TOKENS: dict[str, str] = {}


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform-admin@example.com")
    monkeypatch.setattr(settings, "require_email_verification", True)
    _EMAIL_TOKENS.clear()
    monkeypatch.setattr(identity_routes, "send_email_verification",
                        lambda email, token: _EMAIL_TOKENS.__setitem__(email, token))
    with sessions() as db:
        db.add(RoleDefinition(role_key="transporter", display_name="Transporter",
            description="Transport provider", min_age=18, fee_amount_minor=None,
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


def account(client, email):
    password = "correct-horse-battery-12"
    created = client.post("/api/v1/auth/register",
                          json={"email": email, "password": password})
    assert created.status_code == 201, created.text
    raw = _EMAIL_TOKENS.pop(email)
    verified = client.post("/api/v1/auth/email-verification/confirm", json={"token": raw})
    assert verified.status_code == 204, verified.text
    logged = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert logged.status_code == 200, logged.text
    return logged.json()["access_token"]


def organization(client, token, name):
    response = client.post("/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"}, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def activate_role(client, token, org_id, admin_headers):
    configured = client.patch("/api/v1/platform/role-definitions/transporter",
        headers=admin_headers, json={"fee_amount_minor": 1000, "fee_currency": "PKR",
                                     "min_age": 18, "is_active": True})
    assert configured.status_code == 200, configured.text
    applied = client.post("/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {token}"},
        json={"role_key": "transporter", "organization_id": org_id})
    assert applied.status_code == 201, applied.text
    reviewed = client.post(f"/api/v1/platform/role-entitlements/{applied.json()['id']}/review",
        headers=admin_headers, json={"decision": "approve", "payment_verified": True,
                                     "age_verified": True, "notes": "Manual test approval"})
    assert reviewed.status_code == 200, reviewed.text
    return applied.json()["id"]


def approve_profile(client, token, org_id, admin_headers):
    created = client.post(f"/api/v1/organizations/{org_id}/transport-profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"business_name": "North Haulage", "city": "Lahore",
              "state_region": "Punjab", "country_code": "PK",
              "service_description": "Local machinery transport"})
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]
    submitted = client.post(f"/api/v1/organizations/{org_id}/transport-profile/submit",
        headers={"Authorization": f"Bearer {token}"})
    assert submitted.status_code == 200, submitted.text
    approved = client.post(f"/api/v1/platform/transport-profiles/{profile_id}/review",
        headers=admin_headers, json={"decision": "approve", "evidence_checked": True,
                                     "notes": "Business and adult-role evidence reviewed"})
    assert approved.status_code == 200, approved.text


def create_vehicle(client, token, org_id):
    response = client.post(f"/api/v1/organizations/{org_id}/transport-vehicles",
        headers={"Authorization": f"Bearer {token}"},
        json={"vehicle_type": "dumper", "title": "12-wheel dumper",
              "make_model": "Heavy duty", "capacity_description": "20 cubic yards",
              "operator_included": True, "city": "Lahore", "country_code": "PK",
              "pricing_basis": "per_trip", "rate_minor": 125000, "currency": "PKR",
              "minimum_charge_minor": 50000, "service_radius_km": 50,
              "description": "Local dumper"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def verify_vehicle(client, vehicle_id, admin_headers):
    response = client.post(f"/api/v1/platform/transport-vehicles/{vehicle_id}/review",
        headers=admin_headers, json={"decision": "verify", "evidence_checked": True,
                                     "notes": "Vehicle evidence manually checked"})
    assert response.status_code == 200, response.text


def test_transport_role_gate_moderation_and_public_redaction(client):
    token = account(client, "transporter@example.com")
    org_id = organization(client, token, "Transport Org")
    admin = account(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin}"}
    denied = client.post(f"/api/v1/organizations/{org_id}/transport-profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"city": "Lahore", "country_code": "PK"})
    assert denied.status_code == 403
    activate_role(client, token, org_id, admin_headers)
    approve_profile(client, token, org_id, admin_headers)
    vehicle_id = create_vehicle(client, token, org_id)
    assert client.get("/api/v1/transport/vehicles").json() == []
    no_attestation = client.post(f"/api/v1/platform/transport-vehicles/{vehicle_id}/review",
        headers=admin_headers, json={"decision": "verify", "notes": "Checked"})
    assert no_attestation.status_code == 422
    verify_vehicle(client, vehicle_id, admin_headers)
    public = client.get("/api/v1/transport/vehicles")
    assert public.status_code == 200 and len(public.json()) == 1
    item = public.json()[0]
    assert item["rate_minor"] == 125000 and item["currency"] == "PKR"
    assert "organization_id" not in item and "review_notes" not in item
    assert "owner_user_id" not in item and "contact_phone" not in item
    changed = client.patch(f"/api/v1/organizations/{org_id}/transport-vehicles/{vehicle_id}",
        headers={"Authorization": f"Bearer {token}"}, json={"title": "Updated dumper"})
    assert changed.status_code == 200
    assert changed.json()["verification_status"] == "pending"
    assert client.get("/api/v1/transport/vehicles").json() == []


def test_transport_role_scope_and_membership_isolation(client):
    token = account(client, "multi-org@example.com")
    org_a = organization(client, token, "Transport A")
    org_b = organization(client, token, "Transport B")
    admin = account(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin}"}
    activate_role(client, token, org_a, admin_headers)
    wrong_scope = client.post(f"/api/v1/organizations/{org_b}/transport-profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"city": "Lahore", "country_code": "PK"})
    assert wrong_scope.status_code == 403
    outsider = account(client, "transport-outsider@example.com")
    hidden = client.get(f"/api/v1/organizations/{org_a}/transport-profile",
        headers={"Authorization": f"Bearer {outsider}"})
    assert hidden.status_code == 404
    forbidden = client.get("/api/v1/platform/transport-profiles",
        headers={"Authorization": f"Bearer {token}"})
    assert forbidden.status_code == 403


def test_suspended_transporter_role_hides_vehicles_and_blocks_reads(client):
    token = account(client, "suspended-transporter@example.com")
    org_id = organization(client, token, "Suspended Transport")
    admin = account(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin}"}
    entitlement_id = activate_role(client, token, org_id, admin_headers)
    approve_profile(client, token, org_id, admin_headers)
    vehicle_id = create_vehicle(client, token, org_id)
    verify_vehicle(client, vehicle_id, admin_headers)
    assert len(client.get("/api/v1/transport/vehicles").json()) == 1
    suspended = client.post(f"/api/v1/platform/role-entitlements/{entitlement_id}/lifecycle",
        headers=admin_headers, json={"action": "suspend"})
    assert suspended.status_code == 200
    assert client.get("/api/v1/transport/vehicles").json() == []
    tenant = client.get(f"/api/v1/organizations/{org_id}/transport-vehicles",
        headers={"Authorization": f"Bearer {token}"})
    assert tenant.status_code == 403
