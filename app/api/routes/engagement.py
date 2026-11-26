"""Bilingual customer engagement leads and explicit, auditable consent."""

import hashlib
import re
import secrets
from datetime import timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.audit import record_audit_event
from app.core.config import settings
from app.db import get_db
from app.models import (
    CustomerEngagementConsentEvent,
    CustomerEngagementLead,
    CustomerEngagementUnsubscribeToken,
    User,
    UserPhoneVerification,
    utc_now,
)
from app.schemas import (
    CustomerEngagementConsentUpdate,
    CustomerEngagementLeadCreate,
    CustomerEngagementUnsubscribeRequest,
)

router = APIRouter(tags=["customer-engagement"])
CONSENT_POLICY_VERSION = "2026-11-26-v1"
CONSENT_CHANNELS = ("marketing", "email", "sms", "whatsapp")
CONTACT_CHANNELS = ("email", "sms", "whatsapp")
PHONE_VERIFICATION_MAX_AGE = timedelta(days=30)
_PHONE_PATTERN = re.compile(r"\+[1-9][0-9]{7,14}\Z")


def _require_platform_admin(user: User) -> None:
    allowed = {
        item.strip().lower()
        for item in settings.platform_admin_emails.split(",")
        if item.strip()
    }
    if not user.email_verified or user.email.strip().lower() not in allowed:
        raise HTTPException(status_code=403, detail="Verified platform administrator required")


def _require_fresh_verified_phone(db: Session, user: User) -> None:
    verification = db.get(UserPhoneVerification, user.id)
    if verification is None or not _PHONE_PATTERN.fullmatch(verification.phone_e164):
        raise HTTPException(status_code=403, detail="A provider-verified phone number is required")
    verified_at = verification.verified_at
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    now = utc_now()
    if verified_at > now or now - verified_at > PHONE_VERIFICATION_MAX_AGE:
        raise HTTPException(status_code=403, detail="Phone verification must be no older than 30 days")


def _record_consent(
    db: Session,
    lead: CustomerEngagementLead,
    channel: str,
    consent: bool,
    source: str,
) -> None:
    db.add(
        CustomerEngagementConsentEvent(
            lead_id=lead.id,
            user_id=lead.user_id,
            channel=channel,
            consent=consent,
            policy_version=CONSENT_POLICY_VERSION,
            source=source,
        )
    )


def _consent_state(db: Session, lead_id: str) -> dict[str, bool]:
    events = db.scalars(
        select(CustomerEngagementConsentEvent)
        .where(CustomerEngagementConsentEvent.lead_id == lead_id)
        .order_by(
            CustomerEngagementConsentEvent.created_at.desc(),
            CustomerEngagementConsentEvent.id.desc(),
        )
    ).all()
    state = {channel: False for channel in CONSENT_CHANNELS}
    seen: set[str] = set()
    for event in events:
        if event.channel not in seen:
            state[event.channel] = event.consent
            seen.add(event.channel)
    return state


def _issue_unsubscribe_token(db: Session, lead: CustomerEngagementLead) -> str:
    raw_token = secrets.token_urlsafe(32)
    db.add(
        CustomerEngagementUnsubscribeToken(
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            lead_id=lead.id,
            expires_at=utc_now() + timedelta(days=365),
        )
    )
    return raw_token


def _lead_payload(db: Session, lead: CustomerEngagementLead) -> dict:
    return {
        "id": lead.id,
        "interest_category": lead.interest_category,
        "interest_text": lead.interest_text,
        "preferred_language": lead.preferred_language,
        "source": lead.source,
        "status": lead.status,
        "followup_count": lead.followup_count,
        "next_followup_at": lead.next_followup_at,
        "consent": _consent_state(db, lead.id),
        "created_at": lead.created_at,
    }


@router.post("/me/engagement/leads", status_code=status.HTTP_201_CREATED)
def create_engagement_lead(
    payload: CustomerEngagementLeadCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if payload.email_consent and not user.email_verified:
        raise HTTPException(status_code=403, detail="Verified email is required for email consent")
    if payload.sms_consent or payload.whatsapp_consent:
        _require_fresh_verified_phone(db, user)

    lead = CustomerEngagementLead(
        id=str(uuid4()),
        user_id=user.id,
        source=payload.source,
        interest_category=payload.interest_category,
        interest_text=payload.interest_text,
        preferred_language=payload.preferred_language,
        status="new",
        followup_count=0,
        next_followup_at=None,
    )
    db.add(lead)
    db.flush()

    if payload.marketing_consent:
        _record_consent(db, lead, "marketing", True, "lead_creation")
    for channel in CONTACT_CHANNELS:
        if getattr(payload, f"{channel}_consent"):
            _record_consent(db, lead, channel, True, "lead_creation")

    unsubscribe_token = (
        _issue_unsubscribe_token(db, lead) if payload.marketing_consent else None
    )
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="customer_engagement.lead_created",
        entity_type="customer_engagement_lead",
        entity_id=lead.id,
        details={"status": "new"},
    )
    db.commit()
    db.refresh(lead)
    return {
        "lead": _lead_payload(db, lead),
        "consent_policy_version": CONSENT_POLICY_VERSION,
        "unsubscribe_token": unsubscribe_token,
    }


@router.get("/me/engagement/leads")
def list_my_engagement_leads(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    leads = db.scalars(
        select(CustomerEngagementLead)
        .where(CustomerEngagementLead.user_id == user.id)
        .order_by(CustomerEngagementLead.created_at.desc(), CustomerEngagementLead.id)
        .limit(50)
    ).all()
    return [_lead_payload(db, lead) for lead in leads]


@router.post("/me/engagement/leads/{lead_id}/consent")
def update_engagement_consent(
    lead_id: str,
    payload: CustomerEngagementConsentUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    lead = db.scalar(
        select(CustomerEngagementLead)
        .where(
            CustomerEngagementLead.id == lead_id,
            CustomerEngagementLead.user_id == user.id,
        )
        .with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Engagement lead not found")

    current = _consent_state(db, lead.id)
    issued_unsubscribe_token = None
    if payload.channel == "marketing":
        if payload.consent and not current["marketing"]:
            _record_consent(db, lead, "marketing", True, "user_settings")
            if lead.status == "opted_out":
                lead.status = "new"
            issued_unsubscribe_token = _issue_unsubscribe_token(db, lead)
        elif not payload.consent:
            for channel in CONSENT_CHANNELS:
                if current[channel]:
                    _record_consent(db, lead, channel, False, "user_settings")
            lead.status = "opted_out"
            lead.next_followup_at = None
            tokens = db.scalars(
                select(CustomerEngagementUnsubscribeToken)
                .where(
                    CustomerEngagementUnsubscribeToken.lead_id == lead.id,
                    CustomerEngagementUnsubscribeToken.suppressed_at.is_(None),
                )
                .with_for_update()
            ).all()
            for token in tokens:
                token.suppressed_at = utc_now()
    else:
        if payload.consent and not current["marketing"]:
            raise HTTPException(
                status_code=409,
                detail="Marketing consent must be granted before channel consent",
            )
        if payload.consent and payload.channel == "email" and not user.email_verified:
            raise HTTPException(status_code=403, detail="Verified email is required for email consent")
        if payload.consent and payload.channel in {"sms", "whatsapp"}:
            _require_fresh_verified_phone(db, user)
        if current[payload.channel] != payload.consent:
            _record_consent(db, lead, payload.channel, payload.consent, "user_settings")

    lead.updated_at = utc_now()
    db.flush()
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="customer_engagement.consent_updated",
        entity_type="customer_engagement_lead",
        entity_id=lead.id,
        details={
            "status": "opted_out" if lead.status == "opted_out" else "new",
            "reason": "user_requested",
        },
    )
    db.commit()
    db.refresh(lead)
    return {
        "lead": _lead_payload(db, lead),
        "consent_policy_version": CONSENT_POLICY_VERSION,
        "unsubscribe_token": issued_unsubscribe_token,
    }


@router.post("/engagement/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe_engagement(
    payload: CustomerEngagementUnsubscribeRequest,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    digest = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    token = db.scalar(
        select(CustomerEngagementUnsubscribeToken)
        .where(CustomerEngagementUnsubscribeToken.token_hash == digest)
        .with_for_update()
    )
    now = utc_now()
    if token is None or token.suppressed_at is not None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    lead = db.scalar(
        select(CustomerEngagementLead)
        .where(CustomerEngagementLead.id == token.lead_id)
        .with_for_update()
    )
    if lead is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    for channel in CONSENT_CHANNELS:
        _record_consent(db, lead, channel, False, "unsubscribe")
    lead.status = "opted_out"
    lead.next_followup_at = None
    lead.updated_at = now
    token.suppressed_at = now
    record_audit_event(
        db,
        actor_user_id=None,
        organization_id=None,
        action="customer_engagement.unsubscribed",
        entity_type="customer_engagement_lead",
        entity_id=lead.id,
        details={"status": "opted_out", "reason": "user_requested"},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/platform/engagement/leads")
def list_platform_engagement_leads(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    _require_platform_admin(user)
    leads = db.scalars(
        select(CustomerEngagementLead)
        .order_by(CustomerEngagementLead.created_at.desc(), CustomerEngagementLead.id)
        .limit(500)
    ).all()
    return [
        {
            "id": lead.id,
            "user_id": lead.user_id,
            "interest_category": lead.interest_category,
            "interest_text": lead.interest_text,
            "preferred_language": lead.preferred_language,
            "status": lead.status,
            "consent": _consent_state(db, lead.id),
            "created_at": lead.created_at,
        }
        for lead in leads
    ]
