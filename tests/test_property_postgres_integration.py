"""PostgreSQL property workflow integration after Alembic upgrade."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.core.config import settings
from app.models import PropertyTransaction, User, UserPhoneVerification, utc_now

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith(("postgresql://", "postgresql+")),
    reason="Dedicated PostgreSQL integration database is required",
)


def test_postgres_property_listing_and_bilateral_sale_workflow(
    monkeypatch, verified_user_factory
):
    suffix = uuid4().hex[:10]
    seller_email = f"property-seller-{suffix}@example.com"
    buyer_email = f"property-buyer-{suffix}@example.com"
    admin_email = f"property-admin-{suffix}@example.com"
    monkeypatch.setattr(settings, "platform_admin_emails", admin_email)

    with TestClient(app) as client:
        seller_token = verified_user_factory(client, seller_email)
        buyer_token = verified_user_factory(client, buyer_email)
        admin_token = verified_user_factory(client, admin_email)
        seller_headers = {"Authorization": f"Bearer {seller_token}"}
        buyer_headers = {"Authorization": f"Bearer {buyer_token}"}
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        with SessionLocal() as db:
            seller = db.scalar(select(User).where(User.email == seller_email))
            assert seller is not None
            db.add(UserPhoneVerification(
                user_id=seller.id, phone_e164="+923001234567", verified_at=utc_now()
            ))
            db.commit()

        created = client.post(
            "/api/v1/me/property-listings",
            headers=seller_headers,
            json={
                "property_type": "plot", "title": "PostgreSQL Test Plot",
                "city": "Lahore", "country": "Pakistan", "area_value": "5.00",
                "area_unit": "marla", "asking_price_minor": 5000000,
                "dispute_status": "unknown",
            },
        )
        assert created.status_code == 201, created.text
        listing_id = created.json()["listing"]["id"]
        listing_payment_id = created.json()["listing_fee_payment"]["id"]
        submitted = client.post(
            f"/api/v1/me/property-payments/{listing_payment_id}/submit",
            headers=seller_headers,
            json={"payment_method": "bank_transfer", "payment_reference": "PG-PROPERTY-LISTING-01"},
        )
        assert submitted.status_code == 200, submitted.text
        reviewed = client.post(
            f"/api/v1/platform/property-payments/{listing_payment_id}/review",
            headers=admin_headers,
            json={"decision": "verify", "evidence_checked": True, "notes": "PostgreSQL manual review"},
        )
        assert reviewed.status_code == 200, reviewed.text
        published = client.post(
            f"/api/v1/platform/property-listings/{listing_id}/review",
            headers=admin_headers,
            json={"decision": "publish", "evidence_checked": True, "notes": "PostgreSQL moderation"},
        )
        assert published.status_code == 200, published.text

        interest = client.post(f"/api/v1/properties/{listing_id}/interest", headers=buyer_headers)
        assert interest.status_code == 201, interest.text
        transaction_id = interest.json()["id"]
        buyer_confirmed = client.post(
            f"/api/v1/me/property-transactions/{transaction_id}/confirm-sale",
            headers=buyer_headers, json={"final_sale_price_minor": 5000000},
        )
        seller_confirmed = client.post(
            f"/api/v1/me/property-transactions/{transaction_id}/confirm-sale",
            headers=seller_headers, json={"final_sale_price_minor": 5000000},
        )
        assert buyer_confirmed.status_code == 200
        assert seller_confirmed.status_code == 200
        with SessionLocal() as db:
            transaction = db.get(PropertyTransaction, transaction_id)
            assert transaction is not None
            assert transaction.status == "sale_confirmed"
            assert transaction.buyer_commission_minor == 50000
            assert transaction.seller_commission_minor == 50000
