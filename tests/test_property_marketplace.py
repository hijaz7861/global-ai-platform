"""Property vertical API, ownership, payment and moderation regression tests."""

from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import PropertyTransaction, User, UserPhoneVerification, utc_now


@pytest.fixture
def property_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform-admin@example.com")
    monkeypatch.setattr(settings, "require_email_verification", True)

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, sessions
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def seed_verified_phone(sessions, email: str, *, verified_at=None, phone="+923001234567") -> None:
    with sessions() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        db.add(UserPhoneVerification(
            user_id=user.id,
            phone_e164=phone,
            verified_at=verified_at or utc_now(),
        ))
        db.commit()


def create_listing(client, sessions, verified_user_factory, email: str):
    token = verified_user_factory(client, email)
    seed_verified_phone(sessions, email)
    response = client.post(
        "/api/v1/me/property-listings",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "property_type": "house",
            "title": "Canal View House",
            "description": "A seller-described residential property",
            "city": "Lahore",
            "state_region": "Punjab",
            "country": "Pakistan",
            "locality": "Canal Road",
            "address_private": "Private address, not public",
            "latitude": "31.5204",
            "longitude": "74.3587",
            "area_value": "10.00",
            "area_unit": "marla",
            "asking_price_minor": 125000000,
            "dispute_status": "seller_declared_non_disputed",
        },
    )
    assert response.status_code == 201, response.text
    return token, response.json()


def make_admin(client, verified_user_factory, monkeypatch):
    email = "platform-admin@example.com"
    monkeypatch.setattr(settings, "platform_admin_emails", email)
    token = verified_user_factory(client, email)
    return {"Authorization": f"Bearer {token}"}


def submit_and_publish(client, seller_token, listing_payload, admin_headers):
    listing_id = listing_payload["listing"]["id"]
    payment_id = listing_payload["listing_fee_payment"]["id"]
    seller_headers = {"Authorization": f"Bearer {seller_token}"}
    submitted = client.post(
        f"/api/v1/me/property-payments/{payment_id}/submit",
        headers=seller_headers,
        json={"payment_method": "bank_transfer", "payment_reference": "BANK-REF-2026-001"},
    )
    assert submitted.status_code == 200, submitted.text
    reviewed = client.post(
        f"/api/v1/platform/property-payments/{payment_id}/review",
        headers=admin_headers,
        json={"decision": "verify", "evidence_checked": True, "notes": "Manual evidence checked"},
    )
    assert reviewed.status_code == 200, reviewed.text
    moderated = client.post(
        f"/api/v1/platform/property-listings/{listing_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "evidence_checked": True, "notes": "Listing content reviewed"},
    )
    assert moderated.status_code == 200, moderated.text
    return listing_id


def test_listing_requires_fresh_verified_phone_and_verified_email(
    property_client, verified_user_factory
):
    client, sessions = property_client
    email = "phone-gated-seller@example.com"
    token = verified_user_factory(client, email)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "property_type": "plot", "title": "Test Plot", "city": "Lahore",
        "area_value": "5.00", "area_unit": "marla", "asking_price_minor": 5000000,
        "dispute_status": "unknown",
    }
    assert client.post("/api/v1/me/property-listings", headers=headers, json=payload).status_code == 403

    seed_verified_phone(sessions, email, phone="not-e164")
    assert client.post("/api/v1/me/property-listings", headers=headers, json=payload).status_code == 403

    with sessions() as db:
        phone = db.get(UserPhoneVerification, db.scalar(select(User.id).where(User.email == email)))
        phone.phone_e164 = "+923001234567"
        phone.verified_at = utc_now() - timedelta(days=31)
        db.commit()
    assert client.post("/api/v1/me/property-listings", headers=headers, json=payload).status_code == 403

    with sessions() as db:
        phone = db.get(UserPhoneVerification, db.scalar(select(User.id).where(User.email == email)))
        phone.verified_at = utc_now() + timedelta(days=1)
        db.commit()
    assert client.post("/api/v1/me/property-listings", headers=headers, json=payload).status_code == 403

    with sessions() as db:
        phone = db.get(UserPhoneVerification, db.scalar(select(User.id).where(User.email == email)))
        phone.verified_at = utc_now()
        db.commit()
        user = db.scalar(select(User).where(User.email == email))
        user.email_verified = False
        db.commit()
    assert client.post("/api/v1/me/property-listings", headers=headers, json=payload).status_code == 403


def test_payment_reference_never_activates_listing_and_public_payload_is_redacted(
    property_client, verified_user_factory, monkeypatch
):
    client, sessions = property_client
    seller, listing = create_listing(client, sessions, verified_user_factory, "seller-redaction@example.com")
    listing_id = listing["listing"]["id"]
    payment_id = listing["listing_fee_payment"]["id"]
    seller_headers = {"Authorization": f"Bearer {seller}"}

    assert listing["listing"]["listing_status"] == "pending_fee"
    assert listing["listing"]["listing_fee_minor"] == 50000
    assert client.get("/api/v1/properties").json() == []
    submitted = client.post(
        f"/api/v1/me/property-payments/{payment_id}/submit",
        headers=seller_headers,
        json={"payment_method": "bank_transfer", "payment_reference": "BANK-REF-2026-002"},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "submitted"
    assert "payment_reference" not in submitted.json()
    assert client.get("/api/v1/properties").json() == []

    denied = client.post(
        f"/api/v1/platform/property-payments/{payment_id}/review",
        headers=seller_headers,
        json={"decision": "verify", "evidence_checked": True, "notes": "Not an administrator"},
    )
    assert denied.status_code == 403

    admin_headers = make_admin(client, verified_user_factory, monkeypatch)
    before_fee = client.post(
        f"/api/v1/platform/property-listings/{listing_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "evidence_checked": True, "notes": "Fee not verified"},
    )
    assert before_fee.status_code == 409
    no_evidence = client.post(
        f"/api/v1/platform/property-payments/{payment_id}/review",
        headers=admin_headers,
        json={"decision": "verify", "notes": "No evidence flag"},
    )
    assert no_evidence.status_code == 422
    reviewed = client.post(
        f"/api/v1/platform/property-payments/{payment_id}/review",
        headers=admin_headers,
        json={"decision": "verify", "evidence_checked": True, "notes": "Manual evidence checked"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "verified"

    no_moderation_evidence = client.post(
        f"/api/v1/platform/property-listings/{listing_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "notes": "No moderation evidence"},
    )
    assert no_moderation_evidence.status_code == 422
    published = client.post(
        f"/api/v1/platform/property-listings/{listing_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "evidence_checked": True, "notes": "Content reviewed"},
    )
    assert published.status_code == 200
    assert published.json()["title_verification_status"] == "not_verified"
    assert published.json()["listing_status"] == "published"

    public = client.get("/api/v1/properties")
    assert public.status_code == 200 and len(public.json()) == 1
    item = public.json()[0]
    assert item["listing_code"] == listing["listing"]["listing_code"]
    for private_key in ("address_private", "seller_phone", "seller_user_id", "document_keys", "payment_reference"):
        assert private_key not in item
    owner = client.get("/api/v1/me/property-listings", headers=seller_headers)
    assert owner.status_code == 200
    assert owner.json()[0]["address_private"] == "Private address, not public"


def test_bilateral_sale_creates_separate_commissions_and_requires_both_manual_reviews(
    property_client, verified_user_factory, monkeypatch
):
    client, sessions = property_client
    seller, listing = create_listing(client, sessions, verified_user_factory, "property-seller@example.com")
    admin_headers = make_admin(client, verified_user_factory, monkeypatch)
    listing_id = submit_and_publish(client, seller, listing, admin_headers)

    buyer = verified_user_factory(client, "property-buyer@example.com")
    buyer_headers = {"Authorization": f"Bearer {buyer}"}
    seller_headers = {"Authorization": f"Bearer {seller}"}
    interest = client.post(f"/api/v1/properties/{listing_id}/interest", headers=buyer_headers)
    assert interest.status_code == 201, interest.text
    transaction_id = interest.json()["id"]
    duplicate_interest = client.post(f"/api/v1/properties/{listing_id}/interest", headers=buyer_headers)
    assert duplicate_interest.status_code == 200
    assert duplicate_interest.json()["id"] == transaction_id
    self_interest = client.post(f"/api/v1/properties/{listing_id}/interest", headers=seller_headers)
    assert self_interest.status_code == 400
    first = client.post(
        f"/api/v1/me/property-transactions/{transaction_id}/confirm-sale",
        headers=buyer_headers,
        json={"final_sale_price_minor": 1000000},
    )
    assert first.status_code == 200 and first.json()["status"] == "awaiting_confirmation"
    second = client.post(
        f"/api/v1/me/property-transactions/{transaction_id}/confirm-sale",
        headers=seller_headers,
        json={"final_sale_price_minor": 1000000},
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "sale_confirmed"
    assert second.json()["buyer_commission_minor"] == 10000
    assert second.json()["seller_commission_minor"] == 10000
    assert client.get("/api/v1/properties").json() == []

    buyer_payments = client.get("/api/v1/me/property-payments", headers=buyer_headers).json()
    seller_payments = client.get("/api/v1/me/property-payments", headers=seller_headers).json()
    buyer_commission = next(item for item in buyer_payments if item["payment_type"] == "buyer_commission")
    seller_commission = next(item for item in seller_payments if item["payment_type"] == "seller_commission")
    for payment, headers, reference in (
        (buyer_commission, buyer_headers, "BUYER-COMMISSION-01"),
        (seller_commission, seller_headers, "SELLER-COMMISSION-01"),
    ):
        submitted = client.post(
            f"/api/v1/me/property-payments/{payment['id']}/submit",
            headers=headers,
            json={"payment_method": "cash", "payment_reference": reference},
        )
        assert submitted.status_code == 200

    verified_buyer = client.post(
        f"/api/v1/platform/property-payments/{buyer_commission['id']}/review",
        headers=admin_headers,
        json={"decision": "verify", "evidence_checked": True, "notes": "Buyer payment checked"},
    )
    assert verified_buyer.status_code == 200
    with sessions() as db:
        transaction = db.get(PropertyTransaction, transaction_id)
        assert transaction.status == "sale_confirmed"

    verified_seller = client.post(
        f"/api/v1/platform/property-payments/{seller_commission['id']}/review",
        headers=admin_headers,
        json={"decision": "verify", "evidence_checked": True, "notes": "Seller payment checked"},
    )
    assert verified_seller.status_code == 200
    with sessions() as db:
        transaction = db.get(PropertyTransaction, transaction_id)
        assert transaction.status == "completed"


def test_foreign_user_cannot_confirm_and_sale_prices_must_match(
    property_client, verified_user_factory, monkeypatch
):
    client, sessions = property_client
    seller, listing = create_listing(client, sessions, verified_user_factory, "property-owner-scope@example.com")
    admin_headers = make_admin(client, verified_user_factory, monkeypatch)
    listing_id = submit_and_publish(client, seller, listing, admin_headers)
    buyer = verified_user_factory(client, "property-buyer-scope@example.com")
    outsider = verified_user_factory(client, "property-outsider@example.com")
    buyer_headers = {"Authorization": f"Bearer {buyer}"}
    seller_headers = {"Authorization": f"Bearer {seller}"}
    outsider_headers = {"Authorization": f"Bearer {outsider}"}
    interest = client.post(f"/api/v1/properties/{listing_id}/interest", headers=buyer_headers)
    transaction_id = interest.json()["id"]
    denied = client.post(
        f"/api/v1/me/property-transactions/{transaction_id}/confirm-sale",
        headers=outsider_headers,
        json={"final_sale_price_minor": 1000000},
    )
    assert denied.status_code == 404
    first = client.post(
        f"/api/v1/me/property-transactions/{transaction_id}/confirm-sale",
        headers=buyer_headers,
        json={"final_sale_price_minor": 1000000},
    )
    assert first.status_code == 200
    mismatch = client.post(
        f"/api/v1/me/property-transactions/{transaction_id}/confirm-sale",
        headers=seller_headers,
        json={"final_sale_price_minor": 1000001},
    )
    assert mismatch.status_code == 409
    with sessions() as db:
        transaction = db.get(PropertyTransaction, transaction_id)
        assert transaction.status == "awaiting_confirmation"
        assert transaction.final_sale_price_minor == 1000000
