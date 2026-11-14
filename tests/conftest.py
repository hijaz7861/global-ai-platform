"""Shared test compatibility settings; security behavior has dedicated tests."""
import pytest

from app.api.routes import identity as identity_routes
from app.core.config import settings
from app.models import RoleDefinition


@pytest.fixture
def verified_user_factory(monkeypatch):
    """Register, verify and authenticate users through the real test API."""
    verification_tokens: dict[str, str] = {}
    monkeypatch.setattr(settings, "require_email_verification", True)
    monkeypatch.setattr(
        identity_routes,
        "send_email_verification",
        lambda email, token: verification_tokens.__setitem__(email, token),
    )

    def register_and_login(client, email: str) -> str:
        password = "correct-horse-battery-12"
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        assert registered.status_code == 201, registered.text
        raw_token = verification_tokens.pop(email, None)
        assert raw_token, f"No verification token captured for {email}"
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

    return register_and_login


@pytest.fixture
def active_supplier_role_factory(monkeypatch, verified_user_factory):
    """Grant a supplier entitlement through the platform-admin review API."""
    def activate(client, supplier_token: str, organization_id: str, *,
                 admin_email: str = "platform-admin@example.com"):
        sessions = getattr(client, "testing_sessions", None)
        if sessions is not None:
            with sessions() as db:
                if db.get(RoleDefinition, "supplier") is None:
                    db.add(RoleDefinition(
                        role_key="supplier",
                        display_name="Supplier",
                        description="Supplier test role",
                        min_age=18,
                        fee_amount_minor=None,
                        fee_currency="PKR",
                        requires_review=True,
                        is_active=True,
                    ))
                    db.commit()

        monkeypatch.setattr(settings, "platform_admin_emails", admin_email)
        admin_token = verified_user_factory(client, admin_email)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        configured = client.patch(
            "/api/v1/platform/role-definitions/supplier",
            headers=admin_headers,
            json={
                "fee_amount_minor": 100,
                "fee_currency": "PKR",
                "min_age": 18,
                "is_active": True,
            },
        )
        assert configured.status_code == 200, configured.text
        application = client.post(
            "/api/v1/me/role-entitlements",
            headers={"Authorization": f"Bearer {supplier_token}"},
            json={"role_key": "supplier", "organization_id": organization_id},
        )
        assert application.status_code == 201, application.text
        entitlement_id = application.json()["id"]
        reviewed = client.post(
            f"/api/v1/platform/role-entitlements/{entitlement_id}/review",
            headers=admin_headers,
            json={
                "decision": "approve",
                "payment_verified": True,
                "age_verified": True,
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "active"
        return entitlement_id, admin_headers

    return activate


@pytest.fixture(autouse=True)
def disable_email_verification_for_legacy_domain_tests(monkeypatch):
    # Legacy domain tests focus on tenancy/commerce. Dedicated auth tests turn
    # this back on and exercise the real verification gate.
    monkeypatch.setattr(settings, "require_email_verification", False)
