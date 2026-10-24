"""Organization membership invitation lifecycle tests."""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app


def test_membership_invitation_is_email_bound_single_use_and_audited(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "require_email_verification", False)
    delivered = {}

    def fake_send(email, token, organization_name):
        delivered.update(email=email, token=token, organization_name=organization_name)

    monkeypatch.setattr("app.api.routes.memberships.send_membership_invitation", fake_send)

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            password = "correct-horse-battery-12"
            owner_email = "invite-owner@example.com"
            assert client.post(
                "/api/v1/auth/register",
                json={"email": owner_email, "password": password},
            ).status_code == 201
            owner_login = client.post(
                "/api/v1/auth/token",
                json={"email": owner_email, "password": password},
            )
            assert owner_login.status_code == 200, owner_login.text
            owner_headers = {"Authorization": f"Bearer {owner_login.json()['access_token']}"}
            organization = client.post(
                "/api/v1/organizations",
                headers=owner_headers,
                json={"name": "Invitation Organization"},
            )
            assert organization.status_code == 201, organization.text
            org_id = organization.json()["id"]

            created = client.post(
                f"/api/v1/organizations/{org_id}/invitations",
                headers=owner_headers,
                json={"email": "invited-member@example.com", "role": "member"},
            )
            assert created.status_code == 201, created.text
            assert delivered["email"] == "invited-member@example.com"
            assert delivered["organization_name"] == "Invitation Organization"
            assert "token" not in created.json()
            assert created.json()["role"] == "member"

            duplicate = client.post(
                f"/api/v1/organizations/{org_id}/invitations",
                headers=owner_headers,
                json={"email": "invited-member@example.com", "role": "member"},
            )
            assert duplicate.status_code == 409

            assert client.post(
                "/api/v1/auth/register",
                json={"email": "invited-member@example.com", "password": password},
            ).status_code == 201
            invitee_login = client.post(
                "/api/v1/auth/token",
                json={"email": "invited-member@example.com", "password": password},
            )
            assert invitee_login.status_code == 200, invitee_login.text
            invitee_headers = {"Authorization": f"Bearer {invitee_login.json()['access_token']}"}

            wrong_email = client.post(
                "/api/v1/invitations/accept",
                headers=owner_headers,
                json={"token": delivered["token"]},
            )
            assert wrong_email.status_code == 403

            accepted = client.post(
                "/api/v1/invitations/accept",
                headers=invitee_headers,
                json={"token": delivered["token"]},
            )
            assert accepted.status_code == 201, accepted.text
            assert accepted.json()["email"] == "invited-member@example.com"
            assert accepted.json()["role"] == "member"

            replay = client.post(
                "/api/v1/invitations/accept",
                headers=invitee_headers,
                json={"token": delivered["token"]},
            )
            assert replay.status_code == 400

            members = client.get(
                f"/api/v1/organizations/{org_id}/memberships",
                headers=owner_headers,
            )
            assert members.status_code == 200, members.text
            assert {row["email"] for row in members.json()} == {
                "invite-owner@example.com",
                "invited-member@example.com",
            }
            audit = client.get(
                f"/api/v1/organizations/{org_id}/audit-events",
                headers=owner_headers,
            )
            assert audit.status_code == 200, audit.text
            actions = {row["action"] for row in audit.json()}
            assert "membership.invitation_created" in actions
            assert "membership.invitation_accepted" in actions
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
