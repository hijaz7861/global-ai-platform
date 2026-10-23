"""Owner/admin-managed organization memberships and roles."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.models import Membership, User
from app.schemas import MembershipCreate, MembershipResponse

router = APIRouter(tags=["organization-memberships"])


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
