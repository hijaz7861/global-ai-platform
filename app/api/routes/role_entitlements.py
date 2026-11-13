"""Independent user role applications, fees and platform-admin review."""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.audit import record_audit_event
from app.core.config import settings
from app.db import get_db
from app.models import (
    Membership, Organization, RoleDefinition, RoleEntitlementEvent,
    User, UserRoleEntitlement, utc_now,
)

router = APIRouter(tags=["role-entitlements"])


class RoleApplicationCreate(BaseModel):
    role_key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    organization_id: str | None = Field(default=None, min_length=36, max_length=36)
    payment_reference: str | None = Field(default=None, max_length=160)

    @field_validator("payment_reference")
    @classmethod
    def normalize_payment_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class RoleReviewRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    payment_verified: bool = False
    age_verified: bool = False
    notes: str | None = Field(default=None, max_length=1000)
    validity_days: int | None = Field(default=None, ge=1, le=3650)


class RoleDefinitionUpdate(BaseModel):
    fee_amount_minor: int | None = Field(default=None, ge=0, le=9_000_000_000_000_000)
    fee_currency: str | None = Field(default=None, min_length=3, max_length=3)
    min_age: int | None = Field(default=None, ge=0, le=120)
    is_active: bool | None = None

    @field_validator("fee_currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        if not value.isascii() or not value.isalpha():
            raise ValueError("Currency must be a three-letter ASCII code")
        return value


def _is_platform_admin(email: str) -> bool:
    allowed = {part.strip().lower() for part in settings.platform_admin_emails.split(",") if part.strip()}
    return email.strip().lower() in allowed


def _role_payload(role: RoleDefinition) -> dict:
    return {
        "role_key": role.role_key,
        "display_name": role.display_name,
        "description": role.description,
        "min_age": role.min_age,
        "fee_amount_minor": role.fee_amount_minor,
        "fee_currency": role.fee_currency,
        "fee_configured": role.fee_amount_minor is not None,
        "requires_review": role.requires_review,
        "is_active": role.is_active,
    }


def _entitlement_payload(item: UserRoleEntitlement, role: RoleDefinition) -> dict:
    return {
        "id": item.id,
        "role_key": item.role_key,
        "display_name": role.display_name,
        "organization_id": item.organization_id,
        "status": item.status,
        "payment_status": item.payment_status,
        "fee_amount_minor": item.fee_amount_minor,
        "fee_currency": item.fee_currency,
        "payment_reference": item.payment_reference,
        "age_verified": item.age_verified,
        "reviewer_notes": item.reviewer_notes,
        "requested_at": item.requested_at,
        "reviewed_at": item.reviewed_at,
        "activated_at": item.activated_at,
        "expires_at": item.expires_at,
    }


@router.get("/role-definitions")
def list_role_definitions(db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    roles = db.scalars(select(RoleDefinition).where(RoleDefinition.is_active.is_(True)).order_by(RoleDefinition.display_name)).all()
    return [_role_payload(role) for role in roles]


@router.get("/me/role-entitlements")
def list_my_role_entitlements(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    rows = db.execute(
        select(UserRoleEntitlement, RoleDefinition)
        .join(RoleDefinition, RoleDefinition.role_key == UserRoleEntitlement.role_key)
        .where(UserRoleEntitlement.user_id == user.id)
        .order_by(UserRoleEntitlement.requested_at.desc())
    ).all()
    return [_entitlement_payload(item, role) for item, role in rows]


@router.post("/me/role-entitlements", status_code=status.HTTP_201_CREATED)
def apply_for_role(
    payload: RoleApplicationCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    role = db.get(RoleDefinition, payload.role_key)
    if role is None or not role.is_active:
        raise HTTPException(status_code=404, detail="Role is unavailable")
    if role.fee_amount_minor is None:
        raise HTTPException(status_code=409, detail="Role fee is not configured; contact platform support")
    if payload.organization_id is not None:
        membership = db.scalar(
            select(Membership).where(
                Membership.organization_id == payload.organization_id,
                Membership.user_id == user.id,
            )
        )
        if membership is None:
            raise HTTPException(status_code=404, detail="Organization not found")
    existing = db.scalar(
        select(UserRoleEntitlement).where(
            UserRoleEntitlement.user_id == user.id,
            UserRoleEntitlement.role_key == role.role_key,
            UserRoleEntitlement.organization_id == payload.organization_id,
            UserRoleEntitlement.status.in_(["pending_payment", "pending_review", "active", "suspended"]),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="An active or pending application already exists for this role")
    fee = int(role.fee_amount_minor)
    payment_status = "not_required" if fee == 0 else "pending"
    status_value = "pending_review" if fee == 0 else "pending_payment"
    item = UserRoleEntitlement(
        user_id=user.id,
        role_key=role.role_key,
        organization_id=payload.organization_id,
        status=status_value,
        payment_status=payment_status,
        fee_amount_minor=fee,
        fee_currency=role.fee_currency,
        payment_reference=payload.payment_reference,
    )
    db.add(item)
    db.flush()
    db.add(RoleEntitlementEvent(
        entitlement_id=item.id,
        actor_user_id=user.id,
        event_type="application_submitted",
        details={"role_key": role.role_key, "fee_amount_minor": fee, "fee_currency": role.fee_currency},
    ))
    record_audit_event(
        db, actor_user_id=user.id, organization_id=payload.organization_id,
        action="role_entitlement.applied", entity_type="user_role_entitlement",
        entity_id=item.id, details={"role_key": role.role_key, "status": status_value},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Role application already exists") from None
    db.refresh(item)
    return _entitlement_payload(item, role)


@router.patch("/platform/role-definitions/{role_key}")
def update_role_definition(
    role_key: str,
    payload: RoleDefinitionUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not _is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    role = db.get(RoleDefinition, role_key)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is not None:
            setattr(role, key, value)
    role.updated_at = utc_now()
    record_audit_event(
        db, actor_user_id=user.id, organization_id=None,
        action="role_definition.updated", entity_type="role_definition",
        entity_id=role.role_key, details={key: value for key, value in changes.items()},
    )
    db.commit()
    db.refresh(role)
    return _role_payload(role)


@router.post("/platform/role-entitlements/{entitlement_id}/review")
def review_role_entitlement(
    entitlement_id: str,
    payload: RoleReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not _is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    item = db.get(UserRoleEntitlement, entitlement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Role application not found")
    if item.status not in {"pending_payment", "pending_review"}:
        raise HTTPException(status_code=409, detail="Only pending role applications can be reviewed")
    role = db.get(RoleDefinition, item.role_key)
    if role is None:
        raise HTTPException(status_code=409, detail="Role definition is unavailable")
    if payload.decision == "reject":
        item.status = "rejected"
        if item.payment_status == "pending":
            item.payment_status = "rejected"
    else:
        if item.fee_amount_minor > 0 and not payload.payment_verified:
            raise HTTPException(status_code=409, detail="Verified payment is required before role activation")
        if item.fee_amount_minor > 0:
            item.payment_status = "verified"
        if role.min_age >= 18 and not payload.age_verified:
            raise HTTPException(status_code=409, detail="Verified age and identity evidence is required for this role")
        item.age_verified = payload.age_verified
        item.status = "active"
        item.activated_at = utc_now()
        item.expires_at = utc_now() + timedelta(days=payload.validity_days) if payload.validity_days else None
    item.reviewer_user_id = user.id
    item.reviewer_notes = payload.notes.strip() if payload.notes else None
    item.reviewed_at = utc_now()
    item.updated_at = utc_now()
    db.add(RoleEntitlementEvent(
        entitlement_id=item.id,
        actor_user_id=user.id,
        event_type="approved" if payload.decision == "approve" else "rejected",
        details={
            "payment_verified": payload.payment_verified,
            "age_verified": payload.age_verified,
            "notes": item.reviewer_notes,
        },
    ))
    record_audit_event(
        db, actor_user_id=user.id, organization_id=item.organization_id,
        action="role_entitlement.reviewed", entity_type="user_role_entitlement",
        entity_id=item.id, details={"role_key": item.role_key, "status": item.status},
    )
    db.commit()
    db.refresh(item)
    return _entitlement_payload(item, role)
