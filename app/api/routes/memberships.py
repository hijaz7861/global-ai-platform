"""Owner/admin-managed organization memberships, invitations, and roles."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.core.config import settings
from app.models import Membership, MembershipInvitation, Organization, User, utc_now
from app.schemas import MembershipCreate, MembershipInvitationAccept, MembershipInvitationCreate, MembershipInvitationResponse, MembershipResponse
from app.services.email import send_membership_invitation

router = APIRouter(tags=["organization-memberships"])




def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@router.post(
    "/organizations/{organization_id}/invitations",
    response_model=MembershipInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_membership_invitation(
    organization_id: str,
    payload: MembershipInvitationCreate,
    current: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> MembershipInvitationResponse:
    if current.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    if payload.role == "admin" and current.role != "owner":
        raise HTTPException(status_code=403, detail="Only the organization owner can invite an admin")
    organization = db.scalar(select(Organization).where(Organization.id == current.organization_id))
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    email = str(payload.email).strip().lower()
    invited_user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if invited_user is not None:
        existing_member = db.scalar(
            select(Membership.id).where(
                Membership.organization_id == current.organization_id,
                Membership.user_id == invited_user.id,
            )
        )
        if existing_member is not None:
            raise HTTPException(status_code=409, detail="User is already a member of this organization")
    now = utc_now()
    pending = db.scalar(
        select(MembershipInvitation.id).where(
            MembershipInvitation.organization_id == current.organization_id,
            MembershipInvitation.email == email,
            MembershipInvitation.accepted_at.is_(None),
            MembershipInvitation.revoked_at.is_(None),
            MembershipInvitation.expires_at > now,
        )
    )
    if pending is not None:
        raise HTTPException(status_code=409, detail="A pending invitation already exists for this email")
    raw_token = secrets.token_urlsafe(32)
    invitation = MembershipInvitation(
        organization_id=current.organization_id,
        email=email,
        role=payload.role,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=now + timedelta(days=7),
        created_by_user_id=current.user_id,
    )
    db.add(invitation)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=current.user_id,
        organization_id=current.organization_id,
        action="membership.invitation_created",
        entity_type="membership_invitation",
        entity_id=invitation.id,
        details={"role": invitation.role, "status": "pending"},
    )
    db.commit()
    try:
        send_membership_invitation(email, raw_token, organization.name)
    except Exception:
        db.execute(
            update(MembershipInvitation)
            .where(MembershipInvitation.id == invitation.id, MembershipInvitation.accepted_at.is_(None))
            .values(revoked_at=utc_now())
        )
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Invitation email could not be delivered; configure SMTP and retry",
        ) from None
    db.refresh(invitation)
    return MembershipInvitationResponse.model_validate(invitation)


@router.post("/invitations/accept", response_model=MembershipResponse, status_code=status.HTTP_201_CREATED)
def accept_membership_invitation(
    payload: MembershipInvitationAccept,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MembershipResponse:
    now = utc_now()
    digest = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    invitation = db.scalar(
        select(MembershipInvitation).where(MembershipInvitation.token_hash == digest)
    )
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or _as_utc(invitation.expires_at) <= now
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired membership invitation")
    if invitation.email != user.email:
        raise HTTPException(status_code=403, detail="Invitation belongs to a different email address")
    if settings.require_email_verification and not user.email_verified:
        raise HTTPException(status_code=403, detail="Email verification required before accepting invitations")
    existing = db.scalar(
        select(Membership).where(
            Membership.organization_id == invitation.organization_id,
            Membership.user_id == user.id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="User is already a member of this organization")

    consumed = db.execute(
        update(MembershipInvitation)
        .where(
            MembershipInvitation.id == invitation.id,
            MembershipInvitation.accepted_at.is_(None),
            MembershipInvitation.revoked_at.is_(None),
            MembershipInvitation.expires_at > now,
        )
        .values(accepted_at=now)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or expired membership invitation")
    membership = Membership(
        organization_id=invitation.organization_id,
        user_id=user.id,
        role=invitation.role,
    )
    db.add(membership)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=invitation.organization_id,
        action="membership.invitation_accepted",
        entity_type="membership",
        entity_id=membership.id,
        details={"role": membership.role},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Membership invitation could not be accepted") from None
    db.refresh(membership)
    return MembershipResponse(
        user_id=user.id,
        email=user.email,
        role=membership.role,
        created_at=membership.created_at,
    )


@router.get(
    "/organizations/{organization_id}/memberships",
    response_model=list[MembershipResponse],
)
def list_memberships(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[MembershipResponse]:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    rows = db.execute(
        select(Membership, User.email)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == membership.organization_id)
        .order_by(User.email)
    ).all()
    return [
        MembershipResponse(
            user_id=item.user_id,
            email=email,
            role=item.role,
            created_at=item.created_at,
        )
        for item, email in rows
    ]


@router.post(
    "/organizations/{organization_id}/memberships",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_membership(
    payload: MembershipCreate,
    current: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> MembershipResponse:
    if current.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    if payload.role == "admin" and current.role != "owner":
        raise HTTPException(status_code=403, detail="Only the organization owner can assign admin role")

    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is None:
        raise HTTPException(status_code=404, detail="Registered user not found")
    existing = db.scalar(
        select(Membership).where(
            Membership.organization_id == current.organization_id,
            Membership.user_id == user.id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="User is already a member of this organization")

    new_membership = Membership(
        organization_id=current.organization_id,
        user_id=user.id,
        role=payload.role,
    )
    db.add(new_membership)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=current.user_id,
        organization_id=current.organization_id,
        action="membership.created",
        entity_type="membership",
        entity_id=new_membership.id,
        details={"role": new_membership.role},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Membership could not be created") from None
    db.refresh(new_membership)
    return MembershipResponse(
        user_id=new_membership.user_id,
        email=user.email,
        role=new_membership.role,
        created_at=new_membership.created_at,
    )


@router.delete(
    "/organizations/{organization_id}/memberships/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_membership(
    user_id: str,
    current: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    if current.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    target = db.scalar(
        select(Membership).where(
            Membership.organization_id == current.organization_id,
            Membership.user_id == user_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Membership not found")
    if target.role == "owner":
        raise HTTPException(status_code=409, detail="Owner membership cannot be removed")
    if current.role == "admin" and target.role != "member":
        raise HTTPException(status_code=403, detail="Admins may remove members only")
    if current.user_id == user_id:
        raise HTTPException(status_code=409, detail="Self-removal is not supported")

    record_audit_event(
        db,
        actor_user_id=current.user_id,
        organization_id=current.organization_id,
        action="membership.removed",
        entity_type="membership",
        entity_id=target.id,
        details={"role": target.role},
    )
    db.delete(target)
    db.commit()
