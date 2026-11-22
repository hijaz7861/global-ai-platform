"""Workforce entitlement, moderation, lifecycle and privacy regressions."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import RoleDefinition, UserRoleEntitlement, utc_now


@pytest.fixture
def workforce_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "workforce-test-signing-key-not-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform-admin@example.com")
    monkeypatch.setattr(settings, "require_email_verification", True)
    with sessions() as db:
        db.add_all(
            [
                RoleDefinition(
                    role_key="contractor",
                    display_name="Contractor",
                    description="Professional contractor profile",
                    min_age=18,
                    fee_amount_minor=50_000,
                    fee_currency="PKR",
                    requires_review=True,
                    is_active=True,
                ),
                RoleDefinition(
                    role_key="mason",
                    display_name="Mason",
                    description="Skilled mason profile",
                    min_age=18,
                    fee_amount_minor=20_000,
                    fee_currency="PKR",
                    requires_review=True,
                    is_active=True,
                ),
                RoleDefinition(
                    role_key="labour",
                    display_name="Labour",
                    description="Construction labour profile",
                    min_age=18,
                    fee_amount_minor=5_000,
                    fee_currency="PKR",
                    requires_review=True,
                    is_active=True,
                ),
            ]
        )
        db.commit()

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        client.testing_sessions = sessions
        yield client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def activate_role(client, token, admin_headers, role_key):
    application = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {token}"},
        json={"role_key": role_key},
    )
    assert application.status_code == 201, application.text
    reviewed = client.post(
        f"/api/v1/platform/role-entitlements/{application.json()['id']}/review",
        headers=admin_headers,
        json={
            "decision": "approve",
            "payment_verified": True,
            "age_verified": True,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    return application.json()["id"]


def profile_payload(**overrides):
    payload = {
        "full_name": "Contractor Example",
        "specialty": "Residential finishing",
        "skills": ["tiling", "estimation"],
        "experience_years": "8.5",
        "city": "Lahore",
        "region": "Punjab",
        "country_code": "PK",
        "service_radius_km": 30,
        "travels_for_work": True,
        "available_cities": ["Lahore", "Sheikhupura"],
        "bio": "Experienced construction professional.",
        "availability": "available",
    }
    payload.update(overrides)
    return payload


def publish_profile(client, admin_headers, profile_id):
    return client.post(
        f"/api/v1/platform/workforce/profiles/{profile_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "content_reviewed": True},
    )


def test_workforce_profile_requires_active_role_and_admin_moderation(
    workforce_client, verified_user_factory
):
    client = workforce_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    user_token = verified_user_factory(client, "contractor-one@example.com")
    user_headers = {"Authorization": f"Bearer {user_token}"}

    pending = client.put(
        "/api/v1/me/workforce/contractor-profile",
        headers=user_headers,
        json=profile_payload(),
    )
    assert pending.status_code == 403

    entitlement_id = activate_role(client, user_token, admin_headers, "contractor")
    created = client.put(
        "/api/v1/me/workforce/contractor-profile",
        headers=user_headers,
        json=profile_payload(),
    )
    assert created.status_code == 200, created.text
    profile_id = created.json()["profile"]["id"]
    assert created.json()["profile"]["moderation_status"] == "pending"
    assert client.get("/api/v1/workforce/profiles").json() == []

    queue = client.get(
        "/api/v1/platform/workforce/review-queue",
        headers=admin_headers,
    )
    assert queue.status_code == 200
    assert queue.json()[0]["id"] == profile_id
    assert "user_id" not in queue.json()[0]

    self_review = client.post(
        f"/api/v1/platform/workforce/profiles/{profile_id}/review",
        headers=user_headers,
        json={"decision": "publish", "content_reviewed": True},
    )
    assert self_review.status_code == 403

    incomplete_review = client.post(
        f"/api/v1/platform/workforce/profiles/{profile_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "content_reviewed": False},
    )
    assert incomplete_review.status_code == 409

    published = publish_profile(client, admin_headers, profile_id)
    assert published.status_code == 200, published.text
    assert published.json()["profile"]["moderation_status"] == "published"

    public = client.get("/api/v1/workforce/profiles")
    assert public.status_code == 200
    assert len(public.json()) == 1
    item = public.json()[0]
    assert item["role_key"] == "contractor"
    assert item["full_name"] == "Contractor Example"
    assert item["experience_years"] == "8.5"
    assert "moderation_status" not in item
    assert not {
        "user_id",
        "email",
        "phone",
        "latitude",
        "longitude",
        "age_verification_reference",
        "payment_reference",
    }.intersection(item)

    other_token = verified_user_factory(client, "contractor-two@example.com")
    other_headers = {"Authorization": f"Bearer {other_token}"}
    assert client.get(
        "/api/v1/me/workforce/profiles",
        headers=other_headers,
    ).json() == []

    suspended = client.post(
        f"/api/v1/platform/role-entitlements/{entitlement_id}/lifecycle",
        headers=admin_headers,
        json={"action": "suspend"},
    )
    assert suspended.status_code == 200, suspended.text
    assert client.get("/api/v1/workforce/profiles").json() == []
    blocked_update = client.put(
        "/api/v1/me/workforce/contractor-profile",
        headers=user_headers,
        json=profile_payload(city="Islamabad"),
    )
    assert blocked_update.status_code == 403


def test_workforce_roles_are_independent_and_profile_edits_require_remoderation(
    workforce_client, verified_user_factory
):
    client = workforce_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    user_token = verified_user_factory(client, "multi-role-worker@example.com")
    user_headers = {"Authorization": f"Bearer {user_token}"}

    activate_role(client, user_token, admin_headers, "contractor")
    wrong_role = client.put(
        "/api/v1/me/workforce/mason-profile",
        headers=user_headers,
        json=profile_payload(),
    )
    assert wrong_role.status_code == 403

    activate_role(client, user_token, admin_headers, "mason")
    contractor = client.put(
        "/api/v1/me/workforce/contractor-profile",
        headers=user_headers,
        json=profile_payload(specialty="Project supervision"),
    )
    mason = client.put(
        "/api/v1/me/workforce/mason-profile",
        headers=user_headers,
        json=profile_payload(specialty="Stone masonry"),
    )
    assert contractor.status_code == 200, contractor.text
    assert mason.status_code == 200, mason.text
    profiles = client.get("/api/v1/me/workforce/profiles", headers=user_headers)
    assert profiles.status_code == 200
    assert {item["role_key"] for item in profiles.json()} == {"contractor", "mason"}

    contractor_id = contractor.json()["profile"]["id"]
    assert publish_profile(client, admin_headers, contractor_id).status_code == 200
    edited = client.put(
        "/api/v1/me/workforce/contractor-profile",
        headers=user_headers,
        json=profile_payload(
            specialty="Construction management",
            city="Islamabad",
        ),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["profile"]["moderation_status"] == "pending"
    assert client.get("/api/v1/workforce/profiles").json() == []


def test_expired_role_entitlement_hides_workforce_profile_and_blocks_republication(
    workforce_client, verified_user_factory
):
    client = workforce_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    user_token = verified_user_factory(client, "expired-worker@example.com")
    user_headers = {"Authorization": f"Bearer {user_token}"}
    entitlement_id = activate_role(client, user_token, admin_headers, "labour")

    created = client.put(
        "/api/v1/me/workforce/labour-profile",
        headers=user_headers,
        json=profile_payload(),
    )
    assert created.status_code == 200, created.text
    profile_id = created.json()["profile"]["id"]
    assert publish_profile(client, admin_headers, profile_id).status_code == 200
    assert len(client.get("/api/v1/workforce/profiles").json()) == 1

    with client.testing_sessions() as db:
        entitlement = db.get(UserRoleEntitlement, entitlement_id)
        assert entitlement is not None
        entitlement.expires_at = utc_now() - timedelta(days=1)
        db.commit()

    assert client.get("/api/v1/workforce/profiles").json() == []
    blocked_update = client.put(
        "/api/v1/me/workforce/labour-profile",
        headers=user_headers,
        json=profile_payload(city="Karachi"),
    )
    assert blocked_update.status_code == 403
    blocked_publish = publish_profile(client, admin_headers, profile_id)
    assert blocked_publish.status_code == 409


def test_workforce_profile_rejects_self_asserted_verification_and_private_fields(
    workforce_client, verified_user_factory
):
    client = workforce_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    user_token = verified_user_factory(client, "profile-validation@example.com")
    user_headers = {"Authorization": f"Bearer {user_token}"}
    activate_role(client, user_token, admin_headers, "contractor")

    forged = profile_payload(
        phone="+923001234567",
        latitude=31.5204,
        longitude=74.3587,
        adult_declaration=True,
        age_verified=True,
        moderation_status="published",
    )
    rejected = client.put(
        "/api/v1/me/workforce/contractor-profile",
        headers=user_headers,
        json=forged,
    )
    assert rejected.status_code == 422

    excessive_experience = client.put(
        "/api/v1/me/workforce/contractor-profile",
        headers=user_headers,
        json=profile_payload(experience_years="71.0"),
    )
    assert excessive_experience.status_code == 422

    invalid_country = client.put(
        "/api/v1/me/workforce/contractor-profile",
        headers=user_headers,
        json=profile_payload(country_code="P1"),
    )
    assert invalid_country.status_code == 422
