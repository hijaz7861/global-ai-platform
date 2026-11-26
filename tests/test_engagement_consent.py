"""Customer engagement ownership, explicit consent and unsubscribe regressions."""

import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.api.routes.engagement import unsubscribe_engagement
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import (
    AuditEvent,
    CustomerEngagementConsentEvent,
    CustomerEngagementLead,
    CustomerEngagementUnsubscribeToken,
    User,
    UserPhoneVerification,
    utc_now,
)
from app.schemas import CustomerEngagementUnsubscribeRequest


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_phone(sessions, email: str, *, verified_at=None, phone="+923001234567") -> None:
    with sessions() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        verification = db.get(UserPhoneVerification, user.id)
        if verification is None:
            verification = UserPhoneVerification(
                user_id=user.id,
                phone_e164=phone,
                verified_at=verified_at or utc_now(),
            )
            db.add(verification)
        else:
            verification.phone_e164 = phone
            verification.verified_at = verified_at or utc_now()
        db.commit()


def _create_lead(client, token: str, **overrides):
    payload = {
        "interest_category": "construction",
        "interest_text": "Need a grey-structure estimate in Mianwali",
        "preferred_language": "en",
        "source": "account",
    }
    payload.update(overrides)
    return client.post("/api/v1/me/engagement/leads", headers=_headers(token), json=payload)



@pytest.fixture
def engagement_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "require_email_verification", True)
    monkeypatch.setattr(settings, "platform_admin_emails", "platform-admin@example.com")

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


def test_lead_without_consent_creates_no_token_or_followup(
    engagement_client, verified_user_factory
):
    client = engagement_client
    token = verified_user_factory(client, "engagement-no-consent@example.com")
    created = _create_lead(client, token)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["lead"]["status"] == "new"
    assert body["lead"]["next_followup_at"] is None
    assert body["unsubscribe_token"] is None
    assert body["lead"]["consent"] == {
        "marketing": False, "email": False, "sms": False, "whatsapp": False,
    }
    with client.testing_sessions() as db:
        assert db.scalar(select(func.count()).select_from(CustomerEngagementConsentEvent)) == 0
        assert db.scalar(select(func.count()).select_from(CustomerEngagementUnsubscribeToken)) == 0


def test_channel_consent_requires_marketing_and_verified_destination(
    engagement_client, verified_user_factory
):
    client = engagement_client
    email = "engagement-consent-gates@example.com"
    token = verified_user_factory(client, email)

    assert _create_lead(client, token, email_consent=True).status_code == 422
    with client.testing_sessions() as db:
        user = db.scalar(select(User).where(User.email == email))
        user.email_verified = False
        db.commit()
    assert _create_lead(client, token, marketing_consent=True, email_consent=True).status_code == 403

    with client.testing_sessions() as db:
        user = db.scalar(select(User).where(User.email == email))
        user.email_verified = True
        db.commit()
    assert _create_lead(client, token, marketing_consent=True, sms_consent=True).status_code == 403

    _seed_phone(client.testing_sessions, email, verified_at=utc_now() - timedelta(days=31))
    assert _create_lead(client, token, marketing_consent=True, whatsapp_consent=True).status_code == 403
    _seed_phone(client.testing_sessions, email)
    valid = _create_lead(client, token, marketing_consent=True, sms_consent=True)
    assert valid.status_code == 201, valid.text


def test_unsubscribe_token_is_hashed_idempotent_and_suppresses_all_channels(
    engagement_client, verified_user_factory
):
    client = engagement_client
    token = verified_user_factory(client, "engagement-unsubscribe@example.com")
    created = _create_lead(client, token, marketing_consent=True, email_consent=True)
    assert created.status_code == 201, created.text
    raw_token = created.json()["unsubscribe_token"]
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    with client.testing_sessions() as db:
        stored = db.get(CustomerEngagementUnsubscribeToken, digest)
        assert stored is not None
        assert raw_token not in stored.token_hash
        lead = db.get(CustomerEngagementLead, created.json()["lead"]["id"])
        assert lead is not None
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.entity_id == lead.id,
            AuditEvent.action == "customer_engagement.lead_created",
        )) == 1

    assert client.post("/api/v1/engagement/unsubscribe", json={"token": raw_token}).status_code == 204
    assert client.post("/api/v1/engagement/unsubscribe", json={"token": raw_token}).status_code == 204
    mine = client.get("/api/v1/me/engagement/leads", headers=_headers(token))
    lead = next(item for item in mine.json() if item["id"] == created.json()["lead"]["id"])
    assert lead["status"] == "opted_out"
    assert lead["next_followup_at"] is None
    assert all(value is False for value in lead["consent"].values())
    with client.testing_sessions() as db:
        assert db.scalar(select(func.count()).select_from(CustomerEngagementConsentEvent).where(
            CustomerEngagementConsentEvent.consent.is_(False)
        )) == 4
        audit = db.scalar(select(AuditEvent).where(
            AuditEvent.entity_id == lead["id"],
            AuditEvent.action == "customer_engagement.unsubscribed",
        ))
        assert audit is not None
        assert "email" not in str(audit.details).lower()
        assert "phone" not in str(audit.details).lower()


def test_consent_updates_are_owner_scoped_and_channel_specific(
    engagement_client, verified_user_factory
):
    client = engagement_client
    owner_email = "engagement-owner@example.com"
    owner_token = verified_user_factory(client, owner_email)
    created = _create_lead(client, owner_token, marketing_consent=True, email_consent=True)
    assert created.status_code == 201, created.text
    lead_id = created.json()["lead"]["id"]

    other_token = verified_user_factory(client, "engagement-other@example.com")
    assert client.get("/api/v1/me/engagement/leads", headers=_headers(other_token)).json() == []
    forbidden = client.post(
        f"/api/v1/me/engagement/leads/{lead_id}/consent",
        headers=_headers(other_token),
        json={"channel": "email", "consent": False},
    )
    assert forbidden.status_code == 404

    revoked_email = client.post(
        f"/api/v1/me/engagement/leads/{lead_id}/consent",
        headers=_headers(owner_token),
        json={"channel": "email", "consent": False},
    )
    assert revoked_email.status_code == 200, revoked_email.text
    assert revoked_email.json()["lead"]["consent"]["marketing"] is True
    assert revoked_email.json()["lead"]["consent"]["email"] is False

    revoked_all = client.post(
        f"/api/v1/me/engagement/leads/{lead_id}/consent",
        headers=_headers(owner_token),
        json={"channel": "marketing", "consent": False},
    )
    assert revoked_all.status_code == 200, revoked_all.text
    assert revoked_all.json()["lead"]["status"] == "opted_out"
    assert all(value is False for value in revoked_all.json()["lead"]["consent"].values())

    non_admin = client.get("/api/v1/platform/engagement/leads", headers=_headers(owner_token))
    assert non_admin.status_code == 403
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin = client.get("/api/v1/platform/engagement/leads", headers=_headers(admin_token))
    assert admin.status_code == 200, admin.text
    row = next(item for item in admin.json() if item["id"] == lead_id)
    assert "email" not in row and "phone" not in row


def test_unknown_unsubscribe_token_does_not_disclose_token_existence(engagement_client):
    response = engagement_client.post("/api/v1/engagement/unsubscribe", json={"token": "x" * 48})
    assert response.status_code == 204


def test_unsubscribe_locks_lead_before_token_to_match_consent_revocation_order(monkeypatch):
    raw_token = "l" * 48
    now = utc_now()
    token_preview = SimpleNamespace(
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        lead_id="lead-lock-order",
        suppressed_at=None,
        expires_at=now + timedelta(days=1),
    )
    lead = SimpleNamespace(
        id="lead-lock-order",
        user_id="user-lock-order",
        status="new",
        next_followup_at=now,
        updated_at=now,
    )
    token_locked = SimpleNamespace(
        token_hash=token_preview.token_hash,
        lead_id=lead.id,
        suppressed_at=None,
        expires_at=token_preview.expires_at,
    )

    class RecordingSession:
        def __init__(self):
            self.statements = []
            self.added = []
            self.committed = False

        def scalar(self, statement):
            self.statements.append(statement)
            return (token_preview, lead, token_locked)[len(self.statements) - 1]

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.committed = True

    db = RecordingSession()
    monkeypatch.setattr(
        "app.api.routes.engagement.record_audit_event",
        lambda *_args, **_kwargs: None,
    )

    response = unsubscribe_engagement(
        CustomerEngagementUnsubscribeRequest(token=raw_token),
        db,
    )

    assert response.status_code == 204
    assert len(db.statements) == 3
    assert [
        getattr(statement, "_for_update_arg", None) is not None
        for statement in db.statements
    ] == [False, True, True]
    assert lead.status == "opted_out"
    assert lead.next_followup_at is None
    assert token_locked.suppressed_at is not None
    assert db.committed
    assert len(db.added) == 4
