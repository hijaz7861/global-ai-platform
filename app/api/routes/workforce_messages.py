"""Privacy-preserving internal contact relay for assigned workforce requests."""
import re
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_active_role
from app.audit import record_audit_event
from app.db import get_db
from app.security.encrypted_message import message_encryption_ready
from app.services.workforce_message_limits import record_workforce_message_attempt
from app.models import (
    User,
    UserRoleEntitlement,
    WorkforceProfile,
    WorkforceServiceRequest,
    utc_now,
)
from app.workforce_contact_models import WorkforceServiceMessage

router = APIRouter(tags=["workforce contact relay"])

ACTIVE_MESSAGE_STATUSES = {"matched", "accepted", "in_progress"}
PROVIDER_MESSAGE_STATUSES = {"accepted", "in_progress"}
EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
PHONE_PATTERN = re.compile(
    r"(?<!\w)(?:\+\d(?:[\s().-]*\d){7,14}|0\d(?:[\s().-]*\d){8,13}|[1-9]\d(?:[\s().-]*\d){7,13})(?!\w)"
)
HANDLE_PATTERN = re.compile(r"(?<![\w.])@[A-Za-z0-9_]{2,}")
CONTACT_PATTERNS = (EMAIL_PATTERN, URL_PATTERN, PHONE_PATTERN, HANDLE_PATTERN)


class WorkforceMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def normalize_and_screen_contact_data(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message body cannot be blank")
        if any(pattern.search(normalized) for pattern in CONTACT_PATTERNS):
            raise ValueError(
                "Direct contact details and external links are not allowed in the relay"
            )
        return normalized


def _require_message_encryption() -> None:
    if not message_encryption_ready():
        raise HTTPException(
            status_code=503,
            detail="Workforce message encryption is not configured",
        )


def _require_verified_user(user: User) -> None:
    if not user.is_active or not user.email_verified:
        raise HTTPException(status_code=403, detail="Active verified email required")


def _require_thread_ready(item: WorkforceServiceRequest) -> None:
    if item.provider_profile_id is None or item.platform_fee_status != "verified":
        raise HTTPException(
            status_code=409,
            detail="A verified service fee and assigned provider are required",
        )


def _client_request(
    db: Session, user: User, request_id: str, *, lock: bool = False
) -> WorkforceServiceRequest:
    query = select(WorkforceServiceRequest).where(
        WorkforceServiceRequest.id == request_id,
        WorkforceServiceRequest.client_user_id == user.id,
    )
    if lock:
        query = query.with_for_update()
    item = db.scalar(query)
    if item is None:
        raise HTTPException(status_code=404, detail="Service request not found")
    _require_thread_ready(item)
    return item


def _provider_request(
    db: Session,
    user: User,
    role_key: str,
    request_id: str,
    *,
    lock: bool = False,
) -> WorkforceServiceRequest:
    profile = db.scalar(
        select(WorkforceProfile).where(
            WorkforceProfile.user_id == user.id,
            WorkforceProfile.role_key == role_key,
            WorkforceProfile.moderation_status == "published",
        )
    )
    if profile is None:
        raise HTTPException(status_code=403, detail="Published provider profile required")
    query = select(WorkforceServiceRequest).where(
        WorkforceServiceRequest.id == request_id,
        WorkforceServiceRequest.provider_profile_id == profile.id,
        WorkforceServiceRequest.platform_fee_status == "verified",
    )
    if lock:
        query = query.with_for_update()
    item = db.scalar(query)
    if item is None:
        raise HTTPException(status_code=404, detail="Assigned service request not found")
    return item


def _message_payload(item: WorkforceServiceMessage) -> dict:
    return {
        "id": item.id,
        "sender_side": item.sender_side,
        "body": item.body,
        "created_at": item.created_at,
    }


def _list_messages(
    db: Session, item: WorkforceServiceRequest, *, limit: int, offset: int
) -> dict:
    _require_message_encryption()
    messages = db.scalars(
        select(WorkforceServiceMessage)
        .where(WorkforceServiceMessage.service_request_id == item.id)
        .order_by(
            WorkforceServiceMessage.created_at.asc(),
            WorkforceServiceMessage.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return {"messages": [_message_payload(message) for message in messages]}


def _send_message(
    db: Session,
    user: User,
    item: WorkforceServiceRequest,
    *,
    sender_side: str,
    body: str,
) -> dict:
    _require_message_encryption()
    allowed_statuses = (
        ACTIVE_MESSAGE_STATUSES
        if sender_side == "client"
        else PROVIDER_MESSAGE_STATUSES
    )
    if item.status not in allowed_statuses:
        raise HTTPException(
            status_code=409,
            detail="Messages are available only while the service is active",
        )
    record_workforce_message_attempt(db, user.id)
    message = WorkforceServiceMessage(
        id=str(uuid4()),
        service_request_id=item.id,
        sender_user_id=user.id,
        sender_side=sender_side,
        body=body,
        created_at=utc_now(),
    )
    db.add(message)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="workforce.service_message.sent",
        entity_type="workforce_service_message",
        entity_id=message.id,
        details={
            "sender_side": sender_side,
            "body_length": len(body),
        },
    )
    db.commit()
    db.refresh(message)
    return {"message": _message_payload(message)}


@router.get("/me/workforce/service-requests/{request_id}/messages")
def list_client_workforce_messages(
    request_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
) -> dict:
    _require_verified_user(user)
    item = _client_request(db, user, request_id)
    return _list_messages(db, item, limit=limit, offset=offset)


@router.post(
    "/me/workforce/service-requests/{request_id}/messages",
    status_code=status.HTTP_201_CREATED,
)
def send_client_workforce_message(
    request_id: str,
    payload: WorkforceMessageCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_verified_user(user)
    item = _client_request(db, user, request_id, lock=True)
    return _send_message(db, user, item, sender_side="client", body=payload.body)


def _list_provider_messages(
    role_key: str,
    request_id: str,
    user: User,
    db: Session,
    *,
    limit: int,
    offset: int,
) -> dict:
    item = _provider_request(db, user, role_key, request_id)
    return _list_messages(db, item, limit=limit, offset=offset)


def _send_provider_message(
    role_key: str,
    request_id: str,
    payload: WorkforceMessageCreate,
    user: User,
    db: Session,
) -> dict:
    item = _provider_request(db, user, role_key, request_id, lock=True)
    return _send_message(db, user, item, sender_side="provider", body=payload.body)


@router.get("/me/workforce/contractor-service-requests/{request_id}/messages")
def list_contractor_workforce_messages(
    request_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("contractor"))],
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
) -> dict:
    _require_verified_user(user)
    return _list_provider_messages(
        "contractor", request_id, user, db, limit=limit, offset=offset
    )


@router.post(
    "/me/workforce/contractor-service-requests/{request_id}/messages",
    status_code=status.HTTP_201_CREATED,
)
def send_contractor_workforce_message(
    request_id: str,
    payload: WorkforceMessageCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("contractor"))],
) -> dict:
    _require_verified_user(user)
    return _send_provider_message("contractor", request_id, payload, user, db)


@router.get("/me/workforce/mason-service-requests/{request_id}/messages")
def list_mason_workforce_messages(
    request_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("mason"))],
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
) -> dict:
    _require_verified_user(user)
    return _list_provider_messages(
        "mason", request_id, user, db, limit=limit, offset=offset
    )


@router.post(
    "/me/workforce/mason-service-requests/{request_id}/messages",
    status_code=status.HTTP_201_CREATED,
)
def send_mason_workforce_message(
    request_id: str,
    payload: WorkforceMessageCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("mason"))],
) -> dict:
    _require_verified_user(user)
    return _send_provider_message("mason", request_id, payload, user, db)


@router.get("/me/workforce/labour-service-requests/{request_id}/messages")
def list_labour_workforce_messages(
    request_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("labour"))],
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
) -> dict:
    _require_verified_user(user)
    return _list_provider_messages(
        "labour", request_id, user, db, limit=limit, offset=offset
    )


@router.post(
    "/me/workforce/labour-service-requests/{request_id}/messages",
    status_code=status.HTTP_201_CREATED,
)
def send_labour_workforce_message(
    request_id: str,
    payload: WorkforceMessageCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("labour"))],
) -> dict:
    _require_verified_user(user)
    return _send_provider_message("labour", request_id, payload, user, db)
