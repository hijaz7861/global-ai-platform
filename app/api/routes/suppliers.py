"""Supplier onboarding and platform-admin review endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_membership
from app.audit import record_audit_event
from app.core.config import settings
from app.db import get_db
from app.models import Membership, SupplierProfile, User, utc_now
from app.schemas import SupplierProfileCreate, SupplierProfileResponse, SupplierReviewRequest

router = APIRouter(tags=["suppliers"])


def require_owner_or_admin(membership: Membership) -> None:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")


def is_platform_admin(email: str) -> bool:
    allowed = {item.strip().lower() for item in settings.platform_admin_emails.split(",") if item.strip()}
    return email.strip().lower() in allowed


@router.post(
    "/organizations/{organization_id}/supplier-profile",
    response_model=SupplierProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_supplier_profile(
    organization_id: str,
    payload: SupplierProfileCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> SupplierProfileResponse:
    require_owner_or_admin(membership)
    existing = db.scalar(select(SupplierProfile).where(SupplierProfile.organization_id == organization_id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="A supplier profile already exists for this organization")
    profile = SupplierProfile(
        organization_id=membership.organization_id,
        legal_name=payload.legal_name,
        business_type=payload.business_type,
        country_code=payload.country_code,
        contact_email=str(payload.contact_email),
        website_url=payload.website_url,
        description=payload.description.strip(),
        status="draft",
        created_by_user_id=membership.user_id,
    )
    db.add(profile)
    db.flush()
    record_audit_event(
        db, actor_user_id=membership.user_id, organization_id=organization_id,
        action="supplier_profile.created", entity_type="supplier_profile", entity_id=profile.id,
        details={"status": profile.status, "business_type": profile.business_type},
    )
    db.commit()
    db.refresh(profile)
    return SupplierProfileResponse.model_validate(profile)


@router.get(
    "/organizations/{organization_id}/supplier-profile",
    response_model=SupplierProfileResponse,
)
def get_supplier_profile(
    organization_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> SupplierProfileResponse:
    profile = db.scalar(select(SupplierProfile).where(SupplierProfile.organization_id == membership.organization_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Supplier profile not found")
    return SupplierProfileResponse.model_validate(profile)


@router.put(
    "/organizations/{organization_id}/supplier-profile",
    response_model=SupplierProfileResponse,
)
def update_supplier_profile(
    organization_id: str,
    payload: SupplierProfileCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> SupplierProfileResponse:
    require_owner_or_admin(membership)
    profile = db.scalar(select(SupplierProfile).where(SupplierProfile.organization_id == membership.organization_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Supplier profile not found")
    if profile.status not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail="Only draft or rejected profiles can be edited")
    profile.legal_name = payload.legal_name
    profile.business_type = payload.business_type
    profile.country_code = payload.country_code
    profile.contact_email = str(payload.contact_email)
    profile.website_url = payload.website_url
    profile.description = payload.description.strip()
    profile.status = "draft"
    profile.reviewer_notes = None
    profile.reviewed_by_user_id = None
    profile.updated_at = utc_now()
    record_audit_event(
        db, actor_user_id=membership.user_id, organization_id=organization_id,
        action="supplier_profile.updated", entity_type="supplier_profile", entity_id=profile.id,
        details={"status": profile.status},
    )
    db.commit()
    db.refresh(profile)
    return SupplierProfileResponse.model_validate(profile)


@router.post(
    "/organizations/{organization_id}/supplier-profile/submit",
    response_model=SupplierProfileResponse,
)
def submit_supplier_profile(
    organization_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> SupplierProfileResponse:
    require_owner_or_admin(membership)
    profile = db.scalar(select(SupplierProfile).where(SupplierProfile.organization_id == membership.organization_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Supplier profile not found")
    if profile.status not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail="Only draft or rejected profiles can be submitted")
    profile.status = "submitted"
    profile.reviewer_notes = None
    profile.reviewed_by_user_id = None
    profile.updated_at = utc_now()
    record_audit_event(
        db, actor_user_id=membership.user_id, organization_id=organization_id,
        action="supplier_profile.submitted", entity_type="supplier_profile", entity_id=profile.id,
        details={"status": profile.status},
    )
    db.commit()
    db.refresh(profile)
    return SupplierProfileResponse.model_validate(profile)


@router.get("/platform/supplier-submissions", response_model=list[SupplierProfileResponse])
def list_supplier_submissions(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    profile_status: Annotated[str, Query(alias="status", pattern="^(submitted|approved|rejected)$")] = "submitted",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SupplierProfileResponse]:
    if not is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    profiles = db.scalars(
        select(SupplierProfile)
        .where(SupplierProfile.status == profile_status)
        .order_by(SupplierProfile.created_at, SupplierProfile.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [SupplierProfileResponse.model_validate(profile) for profile in profiles]


@router.post(
    "/platform/supplier-submissions/{profile_id}/review",
    response_model=SupplierProfileResponse,
)
def review_supplier_submission(
    profile_id: str,
    payload: SupplierReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SupplierProfileResponse:
    if not is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    profile = db.scalar(select(SupplierProfile).where(SupplierProfile.id == profile_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Supplier submission not found")
    if profile.status != "submitted":
        raise HTTPException(status_code=409, detail="Only submitted profiles can be reviewed")
    profile.status = payload.decision
    profile.reviewer_notes = payload.notes or None
    profile.reviewed_by_user_id = user.id
    profile.updated_at = utc_now()
    record_audit_event(
        db, actor_user_id=user.id, organization_id=profile.organization_id,
        action="supplier_profile.reviewed", entity_type="supplier_profile", entity_id=profile.id,
        details={"status": profile.status},
    )
    db.commit()
    db.refresh(profile)
    return SupplierProfileResponse.model_validate(profile)
