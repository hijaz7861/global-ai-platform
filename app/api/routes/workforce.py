"""Role-gated workforce directory with moderation and privacy-safe discovery."""
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_active_role
from app.audit import record_audit_event
from app.core.config import settings
from app.db import get_db
from app.models import (
    RoleDefinition,
    User,
    UserRoleEntitlement,
    WorkforceProfile,
    utc_now,
)

router = APIRouter(tags=["workforce"])
class WorkforceProfileUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=2, max_length=160)
    specialty: str = Field(min_length=2, max_length=120)
    skills: list[str] = Field(default_factory=list, max_length=30)
    experience_years: Decimal = Field(
        default=Decimal("0.0"), ge=0, le=70, max_digits=4, decimal_places=1
    )
    city: str = Field(min_length=2, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    country_code: str = Field(default="PK", min_length=2, max_length=2)
    service_radius_km: int = Field(default=25, ge=1, le=1000)
    travels_for_work: bool = False
    available_cities: list[str] = Field(default_factory=list, max_length=25)
    bio: str | None = Field(default=None, max_length=1500)
    availability: Literal["available", "unavailable"] = "available"

    @field_validator("full_name", "specialty", "city")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Value must contain at least two non-space characters")
        return normalized

    @field_validator("region", "bio")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("Country code must be two ASCII letters")
        return normalized

    @field_validator("skills", "available_cities")
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in normalized:
                normalized.append(item[:100])
        return normalized


class WorkforceModerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["publish", "reject", "suspend"]
    content_reviewed: bool = False
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


def _require_platform_admin(user: User) -> None:
    allowed = {
        item.strip().lower()
        for item in settings.platform_admin_emails.split(",")
        if item.strip()
    }
    if not user.email_verified or user.email.strip().lower() not in allowed:
        raise HTTPException(
            status_code=403,
            detail="Verified platform administrator required",
        )


def _active_role_conditions(now):
    return (
        UserRoleEntitlement.status == "active",
        UserRoleEntitlement.organization_id.is_(None),
        UserRoleEntitlement.age_verified.is_(True),
        User.is_active.is_(True),
        User.email_verified.is_(True),
        RoleDefinition.is_active.is_(True),
        or_(
            and_(
                UserRoleEntitlement.fee_amount_minor == 0,
                UserRoleEntitlement.payment_status.in_(["not_required", "verified"]),
            ),
            and_(
                UserRoleEntitlement.fee_amount_minor > 0,
                UserRoleEntitlement.payment_status == "verified",
            ),
        ),
        or_(
            UserRoleEntitlement.expires_at.is_(None),
            UserRoleEntitlement.expires_at > now,
        ),
    )


def _has_current_role_entitlement(db: Session, profile: WorkforceProfile) -> bool:
    now = utc_now()
    statement = (
        select(UserRoleEntitlement.id)
        .join(RoleDefinition, RoleDefinition.role_key == UserRoleEntitlement.role_key)
        .join(User, User.id == UserRoleEntitlement.user_id)
        .where(
            UserRoleEntitlement.user_id == profile.user_id,
            UserRoleEntitlement.role_key == profile.role_key,
            *_active_role_conditions(now),
        )
        .limit(1)
    )
    return db.scalar(statement) is not None


def _visible_profiles_statement():
    now = utc_now()
    return (
        select(WorkforceProfile)
        .join(User, User.id == WorkforceProfile.user_id)
        .join(RoleDefinition, RoleDefinition.role_key == WorkforceProfile.role_key)
        .join(
            UserRoleEntitlement,
            and_(
                UserRoleEntitlement.user_id == WorkforceProfile.user_id,
                UserRoleEntitlement.role_key == WorkforceProfile.role_key,
            ),
        )
        .where(
            WorkforceProfile.moderation_status == "published",
            WorkforceProfile.availability == "available",
            *_active_role_conditions(now),
        )
    )


def _profile_payload(
    item: WorkforceProfile,
    *,
    include_moderation: bool = False,
) -> dict:
    payload = {
        "id": item.id,
        "role_key": item.role_key,
        "full_name": item.full_name,
        "specialty": item.specialty,
        "skills": item.skills or [],
        "experience_years": format(item.experience_years or Decimal("0.0"), "f"),
        "city": item.city,
        "region": item.region,
        "country_code": item.country_code,
        "service_radius_km": item.service_radius_km,
        "travels_for_work": item.travels_for_work,
        "available_cities": item.available_cities or [],
        "bio": item.bio,
        "availability": item.availability,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_moderation:
        payload["moderation_status"] = item.moderation_status
    return payload


def _save_profile(
    role_key: str,
    payload: WorkforceProfileUpsert,
    user: User,
    db: Session,
) -> dict:
    item = db.scalar(
        select(WorkforceProfile)
        .where(
            WorkforceProfile.user_id == user.id,
            WorkforceProfile.role_key == role_key,
        )
        .with_for_update()
    )
    action = "workforce.profile.created"
    if item is None:
        item = WorkforceProfile(
            user_id=user.id,
            role_key=role_key,
            full_name=payload.full_name,
            specialty=payload.specialty,
            skills=payload.skills,
            experience_years=payload.experience_years,
            city=payload.city,
            region=payload.region,
            country_code=payload.country_code,
            service_radius_km=payload.service_radius_km,
            travels_for_work=payload.travels_for_work,
            available_cities=payload.available_cities,
            bio=payload.bio,
            availability=payload.availability,
        )
        db.add(item)
    else:
        action = "workforce.profile.updated"
        for key, value in payload.model_dump().items():
            setattr(item, key, value)
    item.moderation_status = "pending"
    item.reviewer_user_id = None
    item.reviewer_notes = None
    item.reviewed_at = None
    item.updated_at = utc_now()
    try:
        db.flush()
        record_audit_event(
            db,
            actor_user_id=user.id,
            organization_id=None,
            action=action,
            entity_type="workforce_profile",
            entity_id=item.id,
            details={
                "role_key": role_key,
                "status": "pending",
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A workforce profile already exists for this role",
        ) from None
    db.refresh(item)
    return {"profile": _profile_payload(item, include_moderation=True)}


@router.get("/me/workforce/profiles")
def list_my_workforce_profiles(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    items = db.scalars(
        select(WorkforceProfile)
        .where(WorkforceProfile.user_id == user.id)
        .order_by(WorkforceProfile.created_at.desc())
    ).all()
    return [_profile_payload(item, include_moderation=True) for item in items]


@router.put("/me/workforce/contractor-profile")
def save_contractor_profile(
    payload: WorkforceProfileUpsert,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[
        UserRoleEntitlement, Depends(require_active_role("contractor"))
    ],
) -> dict:
    return _save_profile("contractor", payload, user, db)


@router.put("/me/workforce/mason-profile")
def save_mason_profile(
    payload: WorkforceProfileUpsert,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[
        UserRoleEntitlement, Depends(require_active_role("mason"))
    ],
) -> dict:
    return _save_profile("mason", payload, user, db)


@router.put("/me/workforce/labour-profile")
def save_labour_profile(
    payload: WorkforceProfileUpsert,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[
        UserRoleEntitlement, Depends(require_active_role("labour"))
    ],
) -> dict:
    return _save_profile("labour", payload, user, db)


@router.get("/workforce/profiles")
def discover_workforce_profiles(
    db: Annotated[Session, Depends(get_db)],
    role_key: Literal["contractor", "mason", "labour"] | None = None,
    city: str | None = Query(default=None, min_length=2, max_length=100),
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    specialty: str | None = Query(default=None, min_length=2, max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    statement = _visible_profiles_statement()
    if role_key is not None:
        statement = statement.where(WorkforceProfile.role_key == role_key)
    if city is not None:
        statement = statement.where(WorkforceProfile.city.ilike(f"%{city.strip()}%"))
    if country_code is not None:
        normalized = country_code.strip().upper()
        if not normalized.isascii() or not normalized.isalpha():
            raise HTTPException(status_code=422, detail="Invalid country code")
        statement = statement.where(WorkforceProfile.country_code == normalized)
    if specialty is not None:
        statement = statement.where(
            WorkforceProfile.specialty.ilike(f"%{specialty.strip()}%")
        )
    items = db.scalars(
        statement.order_by(WorkforceProfile.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [_profile_payload(item) for item in items]


@router.get("/workforce/profiles/{profile_id}")
def get_public_workforce_profile(
    profile_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.scalar(
        _visible_profiles_statement().where(WorkforceProfile.id == profile_id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Workforce profile not found")
    return _profile_payload(item)


@router.get("/platform/workforce/review-queue")
def list_workforce_review_queue(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: Literal["pending", "published", "rejected", "suspended"] = Query(
        default="pending", alias="status"
    ),
    role_key: Literal["contractor", "mason", "labour"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    _require_platform_admin(user)
    statement = select(WorkforceProfile).where(
        WorkforceProfile.moderation_status == status_filter
    )
    if role_key is not None:
        statement = statement.where(WorkforceProfile.role_key == role_key)
    items = db.scalars(
        statement.order_by(WorkforceProfile.created_at.asc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [_profile_payload(item, include_moderation=True) for item in items]


@router.post(
    "/platform/workforce/profiles/{profile_id}/review",
    status_code=status.HTTP_200_OK,
)
def review_workforce_profile(
    profile_id: str,
    payload: WorkforceModerationRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = db.scalar(
        select(WorkforceProfile)
        .where(WorkforceProfile.id == profile_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Workforce profile not found")
    if payload.decision == "publish":
        if not payload.content_reviewed:
            raise HTTPException(
                status_code=409,
                detail="Content review must be confirmed before publication",
            )
        if not _has_current_role_entitlement(db, item):
            raise HTTPException(
                status_code=409,
                detail="Current verified role entitlement is required for publication",
            )
        item.moderation_status = "published"
    elif payload.decision == "reject":
        item.moderation_status = "rejected"
    else:
        item.moderation_status = "suspended"
    item.reviewer_user_id = user.id
    item.reviewer_notes = payload.notes
    item.reviewed_at = utc_now()
    item.updated_at = utc_now()
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="workforce.profile.reviewed",
        entity_type="workforce_profile",
        entity_id=item.id,
        details={
            "role_key": item.role_key,
            "status": item.moderation_status,
        },
    )
    db.commit()
    db.refresh(item)
    return {"profile": _profile_payload(item, include_moderation=True)}
