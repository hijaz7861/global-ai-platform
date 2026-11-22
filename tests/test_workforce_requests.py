"""Workforce hiring, verified-phone, fee-review, assignment and state-machine regressions."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import (
    RoleDefinition,
    User,
    UserPhoneVerification,
    utc_now,
)


@pytest.fixture
def workforce_request_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "workforce-request-test-key-not-production")
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


def add_verified_phone(client, email, *, age_days=0):
    with client.testing_sessions() as db:
        user = db.query(User).filter(User.email == email).one()
        db.add(
            UserPhoneVerification(
                user_id=user.id,
                phone_e164="+923001234567",
                verified_at=utc_now() - timedelta(days=age_days),
            )
        )
        db.commit()


def make_published_provider(client, admin_headers, verified_user_factory, role_key="contractor"):
    email = f"provider-{role_key}@example.com"
    token = verified_user_factory(client, email)
    headers = {"Authorization": f"Bearer {token}"}
    activate_role(client, token, admin_headers, role_key)
    path = f"/api/v1/me/workforce/{role_key}-profile"
    profile = client.put(
        path,
        headers=headers,
        json={
            "full_name": f"{role_key.title()} Example",
            "specialty": "Residential works",
            "skills": ["masonry", "finishing"],
            "experience_years": "6.0",
            "city": "Lahore",
            "country_code": "PK",
        },
    )
    assert profile.status_code == 200, profile.text
    profile_id = profile.json()["profile"]["id"]
    published = client.post(
        f"/api/v1/platform/workforce/profiles/{profile_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "content_reviewed": True},
    )
    assert published.status_code == 200, published.text
    return token, profile_id


def create_request(client, headers, **overrides):
    payload = {
        "service_type": "hire_worker",
        "title": "Residential repair",
        "details": "Need a skilled worker for a small residential repair.",
        "city": "Lahore",
        "country_code": "PK",
        "budget_minor": 250_000,
        "client_terms_accepted": True,
    }
    payload.update(overrides)
    return client.post(
        "/api/v1/me/workforce/service-requests",
        headers=headers,
        json=payload,
    )


def submit_fee(client, headers, request_id, reference="BANK-REF-10001"):
    return client.post(
        f"/api/v1/me/workforce/service-requests/{request_id}/payment",
        headers=headers,
        json={"payment_method": "bank_transfer", "payment_reference": reference},
    )


def verify_fee(client, admin_headers, request_id, *, decision="verify", evidence_checked=True):
    return client.post(
        f"/api/v1/platform/workforce/service-requests/{request_id}/payment-review",
        headers=admin_headers,
        json={"decision": decision, "evidence_checked": evidence_checked},
    )


def test_workforce_service_request_requires_verified_phone_and_client_acceptance(
    workforce_request_client, verified_user_factory
):
    client = workforce_request_client
    token = verified_user_factory(client, "service-client@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    no_phone = create_request(client, headers)
    assert no_phone.status_code == 403

    add_verified_phone(client, "service-client@example.com")
    no_terms = create_request(client, headers, client_terms_accepted=False)
    assert no_terms.status_code == 409

    created = create_request(client, headers)
    assert created.status_code == 201, created.text
    item = created.json()["request"]
    assert item["status"] == "new"
    assert item["platform_fee_minor"] == 5000
    assert item["platform_fee_currency"] == "PKR"
    assert item["platform_fee_status"] == "pending"
    assert item["client_terms_version"] == "workforce-client-service-v1"
    assert "client_phone" not in item
    assert "phone_e164" not in item
    assert "client_user_id" not in item


def test_manual_service_fee_submission_never_self_verifies_and_provider_only_sees_assigned_paid_work(
    workforce_request_client, verified_user_factory
):
    client = workforce_request_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    client_token = verified_user_factory(client, "service-client-two@example.com")
    client_headers = {"Authorization": f"Bearer {client_token}"}
    add_verified_phone(client, "service-client-two@example.com")

    provider_token, profile_id = make_published_provider(
        client, admin_headers, verified_user_factory, "contractor"
    )
    provider_headers = {"Authorization": f"Bearer {provider_token}"}
    wrong_mason_scope = client.get(
        "/api/v1/me/workforce/mason-service-requests",
        headers=provider_headers,
    )
    wrong_labour_scope = client.get(
        "/api/v1/me/workforce/labour-service-requests",
        headers=provider_headers,
    )
    assert wrong_mason_scope.status_code == 403
    assert wrong_labour_scope.status_code == 403

    created = create_request(client, client_headers)
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["id"]

    unsupported = client.post(
        f"/api/v1/me/workforce/service-requests/{request_id}/payment",
        headers=client_headers,
        json={"payment_method": "jazzcash", "payment_reference": "JZ-10001"},
    )
    assert unsupported.status_code == 422

    submitted = submit_fee(client, client_headers, request_id)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["request"]["platform_fee_status"] == "submitted"
    assert submitted.json()["payment"]["status"] == "submitted"
    assert client.get(
        "/api/v1/me/workforce/contractor-service-requests",
        headers=provider_headers,
    ).json() == []

    denied = verify_fee(
        client, admin_headers, request_id, evidence_checked=False
    )
    assert denied.status_code == 409
    non_admin = verify_fee(
        client,
        client_headers,
        request_id,
        evidence_checked=True,
    )
    assert non_admin.status_code == 403

    verified = verify_fee(client, admin_headers, request_id)
    assert verified.status_code == 200, verified.text
    assert verified.json()["request"]["platform_fee_status"] == "verified"

    assigned = client.post(
        f"/api/v1/platform/workforce/service-requests/{request_id}/assign",
        headers=admin_headers,
        json={"provider_profile_id": profile_id},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["request"]["status"] == "matched"

    provider_jobs = client.get(
        "/api/v1/me/workforce/contractor-service-requests",
        headers=provider_headers,
    )
    assert provider_jobs.status_code == 200
    assert len(provider_jobs.json()) == 1
    job = provider_jobs.json()[0]
    assert job["id"] == request_id
    assert "client_phone" not in job
    assert "phone_e164" not in job
    assert "client_user_id" not in job
    assert "payment_reference" not in job

    missing_terms = client.post(
        f"/api/v1/me/workforce/contractor-service-requests/{request_id}/status",
        headers=provider_headers,
        json={"status": "accepted"},
    )
    assert missing_terms.status_code == 409
    accepted = client.post(
        f"/api/v1/me/workforce/contractor-service-requests/{request_id}/status",
        headers=provider_headers,
        json={"status": "accepted", "provider_terms_accepted": True},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["request"]["provider_terms_version"] == "workforce-provider-service-v1"

    started = client.post(
        f"/api/v1/me/workforce/contractor-service-requests/{request_id}/status",
        headers=provider_headers,
        json={"status": "in_progress"},
    )
    assert started.status_code == 200, started.text
    completed = client.post(
        f"/api/v1/me/workforce/contractor-service-requests/{request_id}/status",
        headers=provider_headers,
        json={"status": "completed"},
    )
    assert completed.status_code == 200, completed.text
    repeated = client.post(
        f"/api/v1/me/workforce/contractor-service-requests/{request_id}/status",
        headers=provider_headers,
        json={"status": "completed"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["request"]["status"] == "completed"
    invalid_after_completion = client.post(
        f"/api/v1/me/workforce/contractor-service-requests/{request_id}/status",
        headers=provider_headers,
        json={"status": "in_progress"},
    )
    assert invalid_after_completion.status_code == 409

    client_view = client.get(
        "/api/v1/me/workforce/service-requests",
        headers=client_headers,
    )
    assert client_view.status_code == 200
    assert client_view.json()[0]["status"] == "completed"
    assert "client_phone" not in client_view.json()[0]


def test_workforce_service_payment_rejection_allows_retry_but_not_duplicate_active_payment(
    workforce_request_client, verified_user_factory
):
    client = workforce_request_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    token = verified_user_factory(client, "service-client-retry@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    add_verified_phone(client, "service-client-retry@example.com")

    created = create_request(client, headers)
    request_id = created.json()["request"]["id"]
    first = submit_fee(client, headers, request_id, "BANK-REF-RETRY-1")
    assert first.status_code == 200
    duplicate = submit_fee(client, headers, request_id, "BANK-REF-RETRY-2")
    assert duplicate.status_code == 409

    rejected = verify_fee(
        client, admin_headers, request_id, decision="reject"
    )
    assert rejected.status_code == 200
    assert rejected.json()["request"]["platform_fee_status"] == "rejected"

    retry = submit_fee(client, headers, request_id, "BANK-REF-RETRY-3")
    assert retry.status_code == 200
    assert retry.json()["payment"]["status"] == "submitted"


def test_workforce_service_requests_enforce_client_ownership_and_state_transitions(
    workforce_request_client, verified_user_factory
):
    client = workforce_request_client
    client_token = verified_user_factory(client, "service-owner@example.com")
    client_headers = {"Authorization": f"Bearer {client_token}"}
    other_token = verified_user_factory(client, "service-other@example.com")
    other_headers = {"Authorization": f"Bearer {other_token}"}
    add_verified_phone(client, "service-owner@example.com")

    created = create_request(client, client_headers)
    request_id = created.json()["request"]["id"]
    assert client.get(
        "/api/v1/me/workforce/service-requests",
        headers=other_headers,
    ).json() == []

    wrong_owner_payment = submit_fee(client, other_headers, request_id)
    assert wrong_owner_payment.status_code == 404
    cancelled = client.post(
        f"/api/v1/me/workforce/service-requests/{request_id}/cancel",
        headers=client_headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["request"]["status"] == "cancelled"
    repeated = client.post(
        f"/api/v1/me/workforce/service-requests/{request_id}/cancel",
        headers=client_headers,
    )
    assert repeated.status_code == 200

    invalid_transition = client.post(
        f"/api/v1/me/workforce/service-requests/{request_id}/dispute",
        headers=client_headers,
    )
    assert invalid_transition.status_code == 409


def test_workforce_service_request_rejects_untrusted_contact_and_provider_fields(
    workforce_request_client, verified_user_factory
):
    client = workforce_request_client
    token = verified_user_factory(client, "service-client-forgery@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    add_verified_phone(client, "service-client-forgery@example.com")

    forged = create_request(
        client,
        headers,
        client_phone="+923001234567",
        phone_e164="+923001234567",
        provider_terms_accepted=True,
        platform_fee_status="verified",
        platform_fee_minor=0,
    )
    assert forged.status_code == 422

    old_phone_token = verified_user_factory(client, "service-client-old-phone@example.com")
    old_phone_headers = {"Authorization": f"Bearer {old_phone_token}"}
    add_verified_phone(client, "service-client-old-phone@example.com", age_days=31)
    stale = create_request(client, old_phone_headers)
    assert stale.status_code == 403


def test_cancelled_request_cannot_be_fee_verified_and_cannot_accept_late_payment(
    workforce_request_client, verified_user_factory
):
    client = workforce_request_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    token = verified_user_factory(client, "service-client-cancelled@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    add_verified_phone(client, "service-client-cancelled@example.com")

    created = create_request(client, headers)
    request_id = created.json()["request"]["id"]
    submit = submit_fee(client, headers, request_id, "BANK-REF-CANCEL-1")
    assert submit.status_code == 200

    cancelled = client.post(
        f"/api/v1/me/workforce/service-requests/{request_id}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200
    blocked_verify = verify_fee(client, admin_headers, request_id)
    assert blocked_verify.status_code == 409
    rejected = verify_fee(
        client,
        admin_headers,
        request_id,
        decision="reject",
    )
    assert rejected.status_code == 200

    later_payment = submit_fee(client, headers, request_id, "BANK-REF-CANCEL-2")
    assert later_payment.status_code == 409


def test_client_cannot_assign_themselves_as_their_own_provider(
    workforce_request_client, verified_user_factory
):
    client = workforce_request_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    client_token = verified_user_factory(client, "self-hiring-client@example.com")
    client_headers = {"Authorization": f"Bearer {client_token}"}
    add_verified_phone(client, "self-hiring-client@example.com")
    activate_role(client, client_token, admin_headers, "contractor")

    profile = client.put(
        "/api/v1/me/workforce/contractor-profile",
        headers=client_headers,
        json={
            "full_name": "Self Hiring Example",
            "specialty": "General works",
            "skills": ["repair"],
            "experience_years": "3.0",
            "city": "Lahore",
            "country_code": "PK",
        },
    )
    assert profile.status_code == 200, profile.text
    profile_id = profile.json()["profile"]["id"]
    published = client.post(
        f"/api/v1/platform/workforce/profiles/{profile_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "content_reviewed": True},
    )
    assert published.status_code == 200

    created = create_request(client, client_headers)
    request_id = created.json()["request"]["id"]
    submitted = submit_fee(client, client_headers, request_id)
    assert submitted.status_code == 200
    assert verify_fee(client, admin_headers, request_id).status_code == 200

    self_assignment = client.post(
        f"/api/v1/platform/workforce/service-requests/{request_id}/assign",
        headers=admin_headers,
        json={"provider_profile_id": profile_id},
    )
    assert self_assignment.status_code == 409


def _prepare_assigned_workforce_request(
    client, verified_user_factory, role_key="contractor"
):
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    provider_token, profile_id = make_published_provider(
        client, admin_headers, verified_user_factory, role_key=role_key
    )
    provider_headers = {"Authorization": f"Bearer {provider_token}"}
    client_token = verified_user_factory(client, "relay-client@example.com")
    client_headers = {"Authorization": f"Bearer {client_token}"}
    add_verified_phone(client, "relay-client@example.com")
    created = create_request(client, client_headers)
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["id"]
    submitted = submit_fee(client, client_headers, request_id)
    assert submitted.status_code == 200, submitted.text
    reviewed = verify_fee(client, admin_headers, request_id)
    assert reviewed.status_code == 200, reviewed.text
    assigned = client.post(
        f"/api/v1/platform/workforce/service-requests/{request_id}/assign",
        headers=admin_headers,
        json={"provider_profile_id": profile_id},
    )
    assert assigned.status_code == 200, assigned.text
    return request_id, client_headers, provider_headers, admin_headers


@pytest.mark.parametrize("role_key", ["contractor", "mason", "labour"])
def test_workforce_contact_relay_is_scoped_to_the_client_and_exact_active_provider_role(
    workforce_request_client, verified_user_factory, role_key
):
    client = workforce_request_client
    request_id, client_headers, provider_headers, admin_headers = _prepare_assigned_workforce_request(
        client, verified_user_factory, role_key
    )
    client_message = client.post(
        f"/api/v1/me/workforce/service-requests/{request_id}/messages",
        headers=client_headers,
        json={"body": "Please confirm the materials and site access plan."},
    )
    assert client_message.status_code == 201, client_message.text
    assert "sender_user_id" not in client_message.json()["message"]

    provider_path = (
        f"/api/v1/me/workforce/{role_key}-service-requests/{request_id}/messages"
    )
    provider_read = client.get(provider_path, headers=provider_headers)
    assert provider_read.status_code == 200, provider_read.text
    assert len(provider_read.json()["messages"]) == 1
    assert provider_read.json()["messages"][0]["sender_side"] == "client"

    premature_provider_message = client.post(
        provider_path,
        headers=provider_headers,
        json={"body": "I can confirm the work scope after reviewing the site details."},
    )
    assert premature_provider_message.status_code == 409

    accepted = client.post(
        f"/api/v1/me/workforce/{role_key}-service-requests/{request_id}/status",
        headers=provider_headers,
        json={"status": "accepted", "provider_terms_accepted": True},
    )
    assert accepted.status_code == 200, accepted.text
    provider_message = client.post(
        provider_path,
        headers=provider_headers,
        json={"body": "I have accepted the request and can begin after the scope is confirmed."},
    )
    assert provider_message.status_code == 201, provider_message.text

    client_messages = client.get(
        f"/api/v1/me/workforce/service-requests/{request_id}/messages",
        headers=client_headers,
    )
    assert client_messages.status_code == 200
    assert [item["sender_side"] for item in client_messages.json()["messages"]] == [
        "client",
        "provider",
    ]

    unrelated_token = verified_user_factory(client, "relay-unrelated@example.com")
    unrelated_headers = {"Authorization": f"Bearer {unrelated_token}"}
    denied_client = client.get(
        f"/api/v1/me/workforce/service-requests/{request_id}/messages",
        headers=unrelated_headers,
    )
    assert denied_client.status_code == 404
    denied_provider = client.get(provider_path, headers=unrelated_headers)
    assert denied_provider.status_code == 403

    wrong_role_key = "mason" if role_key != "mason" else "contractor"
    wrong_role_token, _ = make_published_provider(
        client, admin_headers, verified_user_factory, role_key=wrong_role_key
    )
    wrong_role_headers = {"Authorization": f"Bearer {wrong_role_token}"}
    wrong_role_read = client.get(provider_path, headers=wrong_role_headers)
    assert wrong_role_read.status_code == 403


def test_workforce_contact_relay_waits_for_fee_and_provider_assignment(
    workforce_request_client, verified_user_factory
):
    client = workforce_request_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    provider_token, profile_id = make_published_provider(
        client, admin_headers, verified_user_factory
    )
    client_token = verified_user_factory(client, "relay-prerequisite-client@example.com")
    client_headers = {"Authorization": f"Bearer {client_token}"}
    add_verified_phone(client, "relay-prerequisite-client@example.com")
    created = create_request(client, client_headers)
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["id"]
    path = f"/api/v1/me/workforce/service-requests/{request_id}/messages"

    before_fee = client.post(path, headers=client_headers, json={"body": "Please confirm the scope."})
    assert before_fee.status_code == 409
    assert submit_fee(client, client_headers, request_id).status_code == 200
    assert verify_fee(client, admin_headers, request_id).status_code == 200
    before_assignment = client.post(
        path, headers=client_headers, json={"body": "Please confirm the scope."}
    )
    assert before_assignment.status_code == 409

    assigned = client.post(
        f"/api/v1/platform/workforce/service-requests/{request_id}/assign",
        headers=admin_headers,
        json={"provider_profile_id": profile_id},
    )
    assert assigned.status_code == 200
    ready = client.post(path, headers=client_headers, json={"body": "Please confirm the scope."})
    assert ready.status_code == 201
    assert set(ready.json()["message"]) == {"id", "sender_side", "body", "created_at"}


def test_workforce_contact_relay_rejects_contact_details_and_closes_on_terminal_state(
    workforce_request_client, verified_user_factory
):
    client = workforce_request_client
    request_id, client_headers, provider_headers, _ = _prepare_assigned_workforce_request(
        client, verified_user_factory, "contractor"
    )
    path = f"/api/v1/me/workforce/service-requests/{request_id}/messages"
    for body in (
        "Call me at +923001234567",
        "Use this number 92 300 1234567",
        "Email me at client@example.com",
        "Use https://example.com for the details",
        "Message my account @client_handle",
    ):
        response = client.post(path, headers=client_headers, json={"body": body})
        assert response.status_code == 422, body

    valid_body = "Please confirm the site measurements before starting."
    sent = client.post(path, headers=client_headers, json={"body": valid_body})
    assert sent.status_code == 201, sent.text

    provider_status = (
        f"/api/v1/me/workforce/contractor-service-requests/{request_id}/status"
    )
    accepted = client.post(
        provider_status,
        headers=provider_headers,
        json={"status": "accepted", "provider_terms_accepted": True},
    )
    assert accepted.status_code == 200
    started = client.post(
        provider_status,
        headers=provider_headers,
        json={"status": "in_progress"},
    )
    assert started.status_code == 200
    completed = client.post(
        provider_status,
        headers=provider_headers,
        json={"status": "completed"},
    )
    assert completed.status_code == 200
    closed = client.post(
        path,
        headers=client_headers,
        json={"body": "Thank you for completing the agreed work."},
    )
    assert closed.status_code == 409

    import json

    from sqlalchemy import text

    with client.testing_sessions() as db:
        audit_rows = db.execute(
            text(
                "SELECT details FROM audit_events "
                "WHERE action = 'workforce.service_message.sent'"
            )
        ).all()
    assert audit_rows
    audit_details = [
        json.loads(row[0]) if isinstance(row[0], str) else row[0]
        for row in audit_rows
    ]
    assert all(set(item) == {"sender_side", "body_length"} for item in audit_details)
    assert all(item["sender_side"] == "client" for item in audit_details)
    assert all(item["body_length"] == len(valid_body) for item in audit_details)
    assert all(valid_body not in json.dumps(item) for item in audit_details)
