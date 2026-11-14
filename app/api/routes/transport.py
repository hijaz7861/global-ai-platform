"""Organization-scoped transporter profiles and moderated vehicle discovery."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_active_role, require_membership
from app.audit import record_audit_event
from app.core.config import settings
from app.db import get_db
from app.models import (
    Membership, RoleDefinition, TransportProviderProfile, TransportVehicle,
    User, UserRoleEntitlement, utc_now,
)
from app.schemas import (
    TransportProfileCreate, TransportProfileReviewRequest, TransportVehicleCreate,
    TransportVehicleReviewRequest, TransportVehicleUpdate,
)

router = APIRouter(tags=["transport"])


def _is_platform_admin(email: str) -> bool:
    allowed = {part.strip().lower() for part in settings.platform_admin_emails.split(",") if part.strip()}
    return email.strip().lower() in allowed


def _owner_or_admin(membership: Membership) -> None:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")


def _current_entitlement(db: Session, profile: TransportProviderProfile):
    now = utc_now()
    fee_clear = or_(
        and_(UserRoleEntitlement.fee_amount_minor > 0,
             UserRoleEntitlement.payment_status == "verified"),
        and_(UserRoleEntitlement.fee_amount_minor == 0,
             UserRoleEntitlement.payment_status.in_(["not_required", "verified"])),
    )
    return db.scalar(
        select(UserRoleEntitlement)
        .join(RoleDefinition, RoleDefinition.role_key == UserRoleEntitlement.role_key)
        .join(User, User.id == UserRoleEntitlement.user_id)
        .join(Membership, and_(
            Membership.user_id == UserRoleEntitlement.user_id,
            Membership.organization_id == UserRoleEntitlement.organization_id))
        .where(
            UserRoleEntitlement.user_id == profile.owner_user_id,
            UserRoleEntitlement.organization_id == profile.organization_id,
            UserRoleEntitlement.role_key == "transporter",
            UserRoleEntitlement.status == "active",
            UserRoleEntitlement.age_verified.is_(True),
            or_(UserRoleEntitlement.expires_at.is_(None),
                UserRoleEntitlement.expires_at > now),
            fee_clear, RoleDefinition.is_active.is_(True),
            User.is_active.is_(True), User.email_verified.is_(True))
        .limit(1)
    )


def _profile_out(item: TransportProviderProfile) -> dict:
    return {
        "id": item.id, "organization_id": item.organization_id,
        "business_name": item.business_name, "city": item.city,
        "state_region": item.state_region, "country_code": item.country_code,
        "service_description": item.service_description, "status": item.status,
        "review_notes": item.review_notes, "reviewed_at": item.reviewed_at,
        "created_at": item.created_at, "updated_at": item.updated_at,
    }


def _vehicle_out(item: TransportVehicle) -> dict:
    return {
        "id": item.id, "organization_id": item.organization_id,
        "provider_profile_id": item.provider_profile_id, "vehicle_type": item.vehicle_type,
        "title": item.title, "make_model": item.make_model,
        "capacity_description": item.capacity_description,
        "operator_included": item.operator_included, "city": item.city,
        "state_region": item.state_region, "country_code": item.country_code,
        "pricing_basis": item.pricing_basis, "rate_minor": item.rate_minor,
        "currency": item.currency, "minimum_charge_minor": item.minimum_charge_minor,
        "service_radius_km": item.service_radius_km, "description": item.description,
        "availability": item.availability, "verification_status": item.verification_status,
        "review_notes": item.review_notes, "reviewed_at": item.reviewed_at,
        "created_at": item.created_at, "updated_at": item.updated_at,
    }


@router.post("/organizations/{organization_id}/transport-profile",
             status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: TransportProfileCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("transporter", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _owner_or_admin(membership)
    if db.scalar(select(TransportProviderProfile).where(
        TransportProviderProfile.organization_id == membership.organization_id)):
        raise HTTPException(status_code=409, detail="A transport profile already exists")
    item = TransportProviderProfile(
        organization_id=membership.organization_id, owner_user_id=membership.user_id,
        created_by_user_id=membership.user_id, business_name=payload.business_name,
        city=payload.city, state_region=payload.state_region,
        country_code=payload.country_code, service_description=payload.service_description)
    db.add(item)
    db.flush()
    record_audit_event(db, actor_user_id=membership.user_id,
        organization_id=membership.organization_id, action="transport_profile.created",
        entity_type="transport_provider_profile", entity_id=item.id,
        details={"status": item.status})
    db.commit()
    db.refresh(item)
    return _profile_out(item)


@router.get("/organizations/{organization_id}/transport-profile")
def get_profile(
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("transporter", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.scalar(select(TransportProviderProfile).where(
        TransportProviderProfile.organization_id == membership.organization_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Transport profile not found")
    return _profile_out(item)


@router.put("/organizations/{organization_id}/transport-profile")
def update_profile(
    payload: TransportProfileCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("transporter", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _owner_or_admin(membership)
    item = db.scalar(select(TransportProviderProfile).where(
        TransportProviderProfile.organization_id == membership.organization_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Transport profile not found")
    if item.status in {"submitted", "suspended"}:
        raise HTTPException(status_code=409, detail="Submitted or suspended profiles cannot be edited")
    item.business_name = payload.business_name
    item.city = payload.city
    item.state_region = payload.state_region
    item.country_code = payload.country_code
    item.service_description = payload.service_description
    item.status = "draft"
    item.review_notes = None
    item.reviewed_by_user_id = None
    item.reviewed_at = None
    item.updated_at = utc_now()
    record_audit_event(db, actor_user_id=membership.user_id,
        organization_id=membership.organization_id, action="transport_profile.updated",
        entity_type="transport_provider_profile", entity_id=item.id,
        details={"status": item.status})
    db.commit()
    db.refresh(item)
    return _profile_out(item)


@router.post("/organizations/{organization_id}/transport-profile/submit")
def submit_profile(
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("transporter", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _owner_or_admin(membership)
    item = db.scalar(select(TransportProviderProfile).where(
        TransportProviderProfile.organization_id == membership.organization_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Transport profile not found")
    if item.status not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail="Only draft or rejected profiles can be submitted")
    item.status = "submitted"
    item.review_notes = None
    item.reviewed_by_user_id = None
    item.reviewed_at = None
    item.updated_at = utc_now()
    record_audit_event(db, actor_user_id=membership.user_id,
        organization_id=membership.organization_id, action="transport_profile.submitted",
        entity_type="transport_provider_profile", entity_id=item.id,
        details={"status": item.status})
    db.commit()
    db.refresh(item)
    return _profile_out(item)


@router.post("/organizations/{organization_id}/transport-vehicles",
             status_code=status.HTTP_201_CREATED)
def create_vehicle(
    payload: TransportVehicleCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("transporter", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _owner_or_admin(membership)
    profile = db.scalar(select(TransportProviderProfile).where(
        TransportProviderProfile.organization_id == membership.organization_id))
    if profile is None or profile.status not in {"submitted", "approved"}:
        raise HTTPException(status_code=409, detail="Submit a transport profile before adding vehicles")
    item = TransportVehicle(
        organization_id=membership.organization_id, provider_profile_id=profile.id,
        created_by_user_id=membership.user_id, vehicle_type=payload.vehicle_type,
        title=payload.title, make_model=payload.make_model,
        capacity_description=payload.capacity_description,
        operator_included=payload.operator_included, city=payload.city,
        state_region=payload.state_region, country_code=payload.country_code,
        pricing_basis=payload.pricing_basis, rate_minor=payload.rate_minor,
        currency=payload.currency, minimum_charge_minor=payload.minimum_charge_minor,
        service_radius_km=payload.service_radius_km, description=payload.description)
    db.add(item)
    db.flush()
    record_audit_event(db, actor_user_id=membership.user_id,
        organization_id=membership.organization_id, action="transport_vehicle.created",
        entity_type="transport_vehicle", entity_id=item.id,
        details={"status": item.verification_status, "price_minor": item.rate_minor,
                 "currency": item.currency})
    db.commit()
    db.refresh(item)
    return _vehicle_out(item)


@router.get("/organizations/{organization_id}/transport-vehicles")
def list_vehicles(
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("transporter", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    rows = db.scalars(select(TransportVehicle).where(
        TransportVehicle.organization_id == membership.organization_id
    ).order_by(TransportVehicle.created_at.desc(), TransportVehicle.id).limit(100)).all()
    return [_vehicle_out(item) for item in rows]


@router.patch("/organizations/{organization_id}/transport-vehicles/{vehicle_id}")
def update_vehicle(
    vehicle_id: str,
    payload: TransportVehicleUpdate,
    membership: Annotated[Membership, Depends(require_membership)],
    entitlement: Annotated[UserRoleEntitlement, Depends(
        require_active_role("transporter", organization_scoped=True))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _owner_or_admin(membership)
    item = db.scalar(select(TransportVehicle).where(
        TransportVehicle.id == vehicle_id,
        TransportVehicle.organization_id == membership.organization_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Transport vehicle not found")
    if item.verification_status == "suspended":
        raise HTTPException(status_code=409, detail="Suspended vehicles require platform-admin review")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is None:
            raise HTTPException(status_code=422, detail=f"{key} cannot be null")
        setattr(item, key, value)
    if {"title", "city", "rate_minor", "currency", "description"}.intersection(changes):
        item.verification_status = "pending"
        item.review_notes = None
        item.reviewed_by_user_id = None
        item.reviewed_at = None
    item.updated_at = utc_now()
    record_audit_event(db, actor_user_id=membership.user_id,
        organization_id=membership.organization_id, action="transport_vehicle.updated",
        entity_type="transport_vehicle", entity_id=item.id,
        details={"status": item.verification_status, "price_minor": item.rate_minor,
                 "currency": item.currency})
    db.commit()
    db.refresh(item)
    return _vehicle_out(item)


@router.get("/transport/vehicles")
def browse_vehicles(
    db: Annotated[Session, Depends(get_db)],
    city: str | None = Query(default=None, min_length=1, max_length=100),
    country_code: str | None = Query(default=None, min_length=2, max_length=2,
                                     pattern=r"^[A-Z]{2}$"),
    vehicle_type: str | None = Query(default=None,
        pattern=r"^(loader_rickshaw|qingqi|tractor_trolley|tractor_bucket|dumper|truck|mazda|pickup|crane|jcb|excavator|backhoe_loader|forklift|water_tanker|concrete_mixer|other)$"),
    currency: str | None = Query(default=None, min_length=3, max_length=3,
                                 pattern=r"^[A-Z]{3}$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    now = utc_now()
    paid_ok = or_(
        and_(UserRoleEntitlement.fee_amount_minor > 0,
             UserRoleEntitlement.payment_status == "verified"),
        and_(UserRoleEntitlement.fee_amount_minor == 0,
             UserRoleEntitlement.payment_status.in_(["not_required", "verified"])))
    statement = (
        select(TransportVehicle, TransportProviderProfile)
        .join(TransportProviderProfile,
              TransportProviderProfile.id == TransportVehicle.provider_profile_id)
        .join(UserRoleEntitlement, and_(
            UserRoleEntitlement.user_id == TransportProviderProfile.owner_user_id,
            UserRoleEntitlement.organization_id == TransportProviderProfile.organization_id,
            UserRoleEntitlement.role_key == "transporter",
            UserRoleEntitlement.status == "active"))
        .join(RoleDefinition, RoleDefinition.role_key == UserRoleEntitlement.role_key)
        .join(User, User.id == UserRoleEntitlement.user_id)
        .join(Membership, and_(
            Membership.user_id == UserRoleEntitlement.user_id,
            Membership.organization_id == UserRoleEntitlement.organization_id))
        .where(
            TransportProviderProfile.status == "approved",
            TransportVehicle.verification_status == "verified",
            TransportVehicle.availability == "available",
            User.is_active.is_(True), User.email_verified.is_(True),
            RoleDefinition.is_active.is_(True),
            UserRoleEntitlement.age_verified.is_(True),
            or_(UserRoleEntitlement.expires_at.is_(None),
                UserRoleEntitlement.expires_at > now),
            paid_ok))
    if city is not None:
        statement = statement.where(func.lower(TransportVehicle.city) == city.strip().lower())
    if country_code is not None:
        statement = statement.where(TransportVehicle.country_code == country_code)
    if vehicle_type is not None:
        statement = statement.where(TransportVehicle.vehicle_type == vehicle_type)
    if currency is not None:
        statement = statement.where(TransportVehicle.currency == currency)
    rows = db.execute(statement.order_by(
        TransportVehicle.rate_minor.asc(), TransportVehicle.created_at.desc(),
        TransportVehicle.id).limit(limit).offset(offset)).all()
    return [{
        "id": vehicle.id, "business_name": profile.business_name,
        "vehicle_type": vehicle.vehicle_type, "title": vehicle.title,
        "make_model": vehicle.make_model, "capacity_description": vehicle.capacity_description,
        "operator_included": vehicle.operator_included, "city": vehicle.city,
        "state_region": vehicle.state_region, "country_code": vehicle.country_code,
        "pricing_basis": vehicle.pricing_basis, "rate_minor": vehicle.rate_minor,
        "currency": vehicle.currency, "minimum_charge_minor": vehicle.minimum_charge_minor,
        "service_radius_km": vehicle.service_radius_km, "description": vehicle.description,
    } for vehicle, profile in rows]


@router.get("/platform/transport-profiles")
def profile_queue(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    profile_status: str = Query(default="submitted",
        pattern=r"^(submitted|approved|rejected|suspended)$"),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict]:
    if not user.email_verified or not _is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Verified platform administrator required")
    rows = db.scalars(select(TransportProviderProfile).where(
        TransportProviderProfile.status == profile_status
    ).order_by(TransportProviderProfile.created_at, TransportProviderProfile.id).limit(limit)).all()
    return [{"id": item.id, "organization_id": item.organization_id,
        "business_name": item.business_name, "city": item.city,
        "country_code": item.country_code, "status": item.status,
        "created_at": item.created_at} for item in rows]


@router.post("/platform/transport-profiles/{profile_id}/review")
def review_profile(
    profile_id: str,
    payload: TransportProfileReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not user.email_verified or not _is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Verified platform administrator required")
    item = db.get(TransportProviderProfile, profile_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Transport profile not found")
    if payload.decision == "approve":
        if item.status != "submitted" or _current_entitlement(db, item) is None:
            raise HTTPException(status_code=409, detail="Submitted profile and active transporter role required")
        item.status = "approved"
    elif payload.decision == "reject":
        if item.status != "submitted":
            raise HTTPException(status_code=409, detail="Only submitted profiles can be rejected")
        item.status = "rejected"
    else:
        if item.status != "approved":
            raise HTTPException(status_code=409, detail="Only approved profiles can be suspended")
        item.status = "suspended"
    item.review_notes = payload.notes
    item.reviewed_by_user_id = user.id
    item.reviewed_at = utc_now()
    item.updated_at = utc_now()
    record_audit_event(db, actor_user_id=user.id, organization_id=item.organization_id,
        action="transport_profile.reviewed", entity_type="transport_provider_profile",
        entity_id=item.id, details={"status": item.status})
    db.commit()
    db.refresh(item)
    return _profile_out(item)


@router.get("/platform/transport-vehicles")
def vehicle_queue(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    verification_status: str = Query(default="pending",
        pattern=r"^(pending|verified|rejected|suspended)$"),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict]:
    if not user.email_verified or not _is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Verified platform administrator required")
    rows = db.execute(select(TransportVehicle, TransportProviderProfile)
        .join(TransportProviderProfile,
              TransportProviderProfile.id == TransportVehicle.provider_profile_id)
        .where(TransportVehicle.verification_status == verification_status)
        .order_by(TransportVehicle.created_at, TransportVehicle.id).limit(limit)).all()
    return [{"id": v.id, "organization_id": v.organization_id,
        "business_name": p.business_name, "vehicle_type": v.vehicle_type,
        "title": v.title, "city": v.city, "country_code": v.country_code,
        "rate_minor": v.rate_minor, "currency": v.currency,
        "verification_status": v.verification_status, "created_at": v.created_at}
        for v, p in rows]


@router.post("/platform/transport-vehicles/{vehicle_id}/review")
def review_vehicle(
    vehicle_id: str,
    payload: TransportVehicleReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not user.email_verified or not _is_platform_admin(user.email):
        raise HTTPException(status_code=403, detail="Verified platform administrator required")
    item = db.get(TransportVehicle, vehicle_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Transport vehicle not found")
    profile = db.get(TransportProviderProfile, item.provider_profile_id)
    if profile is None:
        raise HTTPException(status_code=409, detail="Transport profile unavailable")
    if payload.decision == "verify":
        if profile.status != "approved" or _current_entitlement(db, profile) is None:
            raise HTTPException(status_code=409, detail="Approved profile and active transporter role required")
        if item.verification_status not in {"pending", "rejected"}:
            raise HTTPException(status_code=409, detail="Vehicle is not awaiting verification")
        item.verification_status = "verified"
    elif payload.decision == "reject":
        if item.verification_status != "pending":
            raise HTTPException(status_code=409, detail="Only pending vehicles can be rejected")
        item.verification_status = "rejected"
    else:
        if item.verification_status != "verified":
            raise HTTPException(status_code=409, detail="Only verified vehicles can be suspended")
        item.verification_status = "suspended"
    item.review_notes = payload.notes
    item.reviewed_by_user_id = user.id
    item.reviewed_at = utc_now()
    item.updated_at = utc_now()
    record_audit_event(db, actor_user_id=user.id, organization_id=item.organization_id,
        action="transport_vehicle.reviewed", entity_type="transport_vehicle",
        entity_id=item.id, details={"status": item.verification_status})
    db.commit()
    db.refresh(item)
    return _vehicle_out(item)
