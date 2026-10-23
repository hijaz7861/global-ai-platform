"""Supplier profile lifecycle and platform-admin approval tests."""
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


def register(client, email):
    result = client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-battery-12"})
    assert result.status_code == 201, result.text
    login = client.post("/api/v1/auth/token", json={"email": email, "password": "correct-horse-battery-12"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def test_supplier_profile_requires_submission_and_platform_admin_review(client):
    owner_token = register(client, "supplier-owner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    org = client.post("/api/v1/organizations", headers=owner_headers, json={"name": "Supplier Org"})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    profile_payload = {
        "legal_name": "Example Manufacturing Ltd",
        "business_type": "manufacturer",
        "country_code": "pk",
        "contact_email": "contact@example.com",
        "website_url": "https://example.com",
        "description": "Produces consumer goods",
    }
    created = client.post(f"/api/v1/organizations/{org_id}/supplier-profile", headers=owner_headers, json=profile_payload)
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["status"] == "draft"
    assert profile["country_code"] == "PK"

    submitted = client.post(f"/api/v1/organizations/{org_id}/supplier-profile/submit", headers=owner_headers)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"
    assert client.post(f"/api/v1/organizations/{org_id}/supplier-profile/submit", headers=owner_headers).status_code == 409
    assert client.get("/api/v1/platform/supplier-submissions", headers=owner_headers).status_code == 403

    admin_token = register(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    queue = client.get("/api/v1/platform/supplier-submissions", headers=admin_headers, params={"status": "submitted"})
    assert queue.status_code == 200, queue.text
    assert [item["id"] for item in queue.json()] == [profile["id"]]
    reviewed = client.post(
        f"/api/v1/platform/supplier-submissions/{profile['id']}/review",
        headers=admin_headers,
        json={"decision": "approved", "notes": "Business details reviewed"},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "approved"
    assert reviewed.json()["reviewed_by_user_id"] is not None
    assert client.put(f"/api/v1/organizations/{org_id}/supplier-profile", headers=owner_headers, json=profile_payload).status_code == 409


def test_rejected_supplier_requires_reason_and_can_be_resubmitted(client):
    owner_token = register(client, "resubmit-owner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    org = client.post("/api/v1/organizations", headers=owner_headers, json={"name": "Resubmit Org"}).json()
    org_id = org["id"]
    payload = {
        "legal_name": "Resubmit Trading Co",
        "business_type": "distributor",
        "country_code": "US",
        "contact_email": "trading@example.com",
        "website_url": None,
        "description": "",
    }
    created = client.post(f"/api/v1/organizations/{org_id}/supplier-profile", headers=owner_headers, json=payload)
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]
    client.post(f"/api/v1/organizations/{org_id}/supplier-profile/submit", headers=owner_headers)
    admin_token = register(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    bad_review = client.post(f"/api/v1/platform/supplier-submissions/{profile_id}/review", headers=admin_headers, json={"decision": "rejected", "notes": " "})
    assert bad_review.status_code == 422
    rejected = client.post(f"/api/v1/platform/supplier-submissions/{profile_id}/review", headers=admin_headers, json={"decision": "rejected", "notes": "Missing company details"})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    updated = client.put(f"/api/v1/organizations/{org_id}/supplier-profile", headers=owner_headers, json={**payload, "description": "Added the requested details"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "draft"
    resubmitted = client.post(f"/api/v1/organizations/{org_id}/supplier-profile/submit", headers=owner_headers)
    assert resubmitted.status_code == 200
    assert resubmitted.json()["status"] == "submitted"
