"""Tenant-scoped organization endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_membership
from app.db import get_db
from app.models import Membership, Organization, User
from app.schemas import OrganizationCreate, OrganizationResponse

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
def create_organization(
    payload: OrganizationCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> OrganizationResponse:
    organization = Organization(name=payload.name.strip())
    db.add(organization)
    db.flush()
    db.add(Membership(user_id=user.id, organization_id=organization.id, role="owner"))
    db.commit()
    db.refresh(organization)
    return OrganizationResponse(id=organization.id, name=organization.name, role="owner")


@router.get("", response_model=list[OrganizationResponse])
def list_my_organizations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[OrganizationResponse]:
    rows = db.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == user.id)
        .order_by(Organization.name)
    ).all()
    return [
        OrganizationResponse(id=organization.id, name=organization.name, role=role)
        for organization, role in rows
    ]


@router.get("/{organization_id}", response_model=OrganizationResponse)
def get_organization(
    organization_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
) -> OrganizationResponse:
    organization = membership.organization
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return OrganizationResponse(
        id=organization.id, name=organization.name, role=membership.role
    )
