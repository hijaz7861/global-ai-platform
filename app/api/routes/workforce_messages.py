"""Privacy-preserving internal contact relay for assigned workforce requests."""
import re
from datetime import timedelta, timezone
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_active_role
from app.api.routes.workforce_requests import _require_platform_admin
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
from app.workforce_contact_models import (
    WorkforceMessageAppeal,
    WorkforceMessageReport,
    WorkforceServiceMessage,
)

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
        .where(
            WorkforceServiceMessage.service_request_id == item.id,
            WorkforceServiceMessage.moderation_status == "visible",
        )
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


class WorkforceMessageReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: Literal[
        "spam",
        "harassment",
        "contact_sharing",
        "scam_or_fraud",
        "unsafe_content",
        "other",
    ]


class WorkforceMessageReportReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["dismiss", "hide_message"]


def _message_report_payload(report: WorkforceMessageReport) -> dict:
    return {
        "id": report.id,
        "reason_code": report.reason_code,
        "status": report.status,
        "created_at": report.created_at,
    }


def _report_workforce_message(
    db: Session,
    user: User,
    item: WorkforceServiceRequest,
    message_id: str,
    *,
    reported_side: str,
    reason_code: str,
) -> dict:
    _require_message_encryption()
    message = db.scalar(
        select(WorkforceServiceMessage)
        .where(
            WorkforceServiceMessage.id == message_id,
            WorkforceServiceMessage.service_request_id == item.id,
        )
        .with_for_update()
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_user_id == user.id:
        raise HTTPException(status_code=409, detail="You cannot report your own message")
    if reported_side == "provider":
        assigned_profile = db.get(WorkforceProfile, item.provider_profile_id)
        expected_sender_user_id = (
            assigned_profile.user_id if assigned_profile is not None else None
        )
    else:
        expected_sender_user_id = item.client_user_id
    if (
        message.sender_side != reported_side
        or message.sender_user_id != expected_sender_user_id
    ):
        raise HTTPException(status_code=404, detail="Message not found")
    if message.moderation_status != "visible":
        raise HTTPException(status_code=409, detail="Message is no longer reportable")

    report = WorkforceMessageReport(
        message_id=message.id,
        reporter_user_id=user.id,
        reason_code=reason_code,
        status="open",
        created_at=utc_now(),
    )
    db.add(report)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="You have already reported this message",
        ) from None

    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="workforce.service_message.reported",
        entity_type="workforce_message_report",
        entity_id=report.id,
        details={"status": "open"},
    )
    db.commit()
    db.refresh(report)
    return {"report": _message_report_payload(report)}


def _report_client_message(
    request_id: str,
    message_id: str,
    payload: WorkforceMessageReportCreate,
    user: User,
    db: Session,
) -> dict:
    _require_verified_user(user)
    item = _client_request(db, user, request_id)
    return _report_workforce_message(
        db, user, item, message_id, reported_side="provider",
        reason_code=payload.reason_code,
    )


def _report_provider_message(
    role_key: str,
    request_id: str,
    message_id: str,
    payload: WorkforceMessageReportCreate,
    user: User,
    db: Session,
) -> dict:
    _require_verified_user(user)
    item = _provider_request(db, user, role_key, request_id)
    return _report_workforce_message(
        db, user, item, message_id, reported_side="client",
        reason_code=payload.reason_code,
    )


@router.post(
    "/me/workforce/service-requests/{request_id}/messages/{message_id}/reports",
    status_code=status.HTTP_201_CREATED,
)
def report_client_workforce_message(
    request_id: str,
    message_id: str,
    payload: WorkforceMessageReportCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    return _report_client_message(request_id, message_id, payload, user, db)


@router.post(
    "/me/workforce/contractor-service-requests/{request_id}/messages/{message_id}/reports",
    status_code=status.HTTP_201_CREATED,
)
def report_contractor_workforce_message(
    request_id: str,
    message_id: str,
    payload: WorkforceMessageReportCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("contractor"))],
) -> dict:
    return _report_provider_message("contractor", request_id, message_id, payload, user, db)


@router.post(
    "/me/workforce/mason-service-requests/{request_id}/messages/{message_id}/reports",
    status_code=status.HTTP_201_CREATED,
)
def report_mason_workforce_message(
    request_id: str,
    message_id: str,
    payload: WorkforceMessageReportCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("mason"))],
) -> dict:
    return _report_provider_message("mason", request_id, message_id, payload, user, db)


@router.post(
    "/me/workforce/labour-service-requests/{request_id}/messages/{message_id}/reports",
    status_code=status.HTTP_201_CREATED,
)
def report_labour_workforce_message(
    request_id: str,
    message_id: str,
    payload: WorkforceMessageReportCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("labour"))],
) -> dict:
    return _report_provider_message("labour", request_id, message_id, payload, user, db)


@router.get("/platform/workforce/message-reports")
def list_workforce_message_reports(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    report_status: Literal["open", "dismissed", "actioned"] = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
) -> list[dict]:
    _require_platform_admin(user)
    _require_message_encryption()
    rows = db.execute(
        select(WorkforceMessageReport, WorkforceServiceMessage, WorkforceServiceRequest)
        .join(
            WorkforceServiceMessage,
            WorkforceServiceMessage.id == WorkforceMessageReport.message_id,
        )
        .join(
            WorkforceServiceRequest,
            WorkforceServiceRequest.id == WorkforceServiceMessage.service_request_id,
        )
        .where(WorkforceMessageReport.status == report_status)
        .order_by(
            WorkforceMessageReport.created_at.asc(),
            WorkforceMessageReport.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {
            "report": _message_report_payload(report),
            "message": {
                "id": message.id,
                "sender_side": message.sender_side,
                "body": message.body,
                "moderation_status": message.moderation_status,
                "created_at": message.created_at,
            },
            "request": {
                "request_code": request.request_code,
                "title": request.title,
                "status": request.status,
            },
        }
        for report, message, request in rows
    ]


@router.post("/platform/workforce/message-reports/{report_id}/review")
def review_workforce_message_report(
    report_id: str,
    payload: WorkforceMessageReportReview,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    report = db.scalar(
        select(WorkforceMessageReport)
        .where(WorkforceMessageReport.id == report_id)
        .with_for_update()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Message report not found")
    if report.status != "open":
        raise HTTPException(status_code=409, detail="Message report has already been reviewed")

    message = db.scalar(
        select(WorkforceServiceMessage)
        .where(WorkforceServiceMessage.id == report.message_id)
        .with_for_update()
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Reported message not found")

    now = utc_now()
    if payload.decision == "hide_message":
        message.moderation_status = "hidden"
        affected_reports = db.scalars(
            select(WorkforceMessageReport)
            .where(
                WorkforceMessageReport.message_id == message.id,
                WorkforceMessageReport.status == "open",
            )
            .with_for_update()
        ).all()
        for affected in affected_reports:
            affected.status = "actioned"
            affected.reviewed_by_user_id = user.id
            affected.reviewed_at = now
            record_audit_event(
                db,
                actor_user_id=user.id,
                organization_id=None,
                action="workforce.message_report.reviewed",
                entity_type="workforce_message_report",
                entity_id=affected.id,
                details={"status": "actioned"},
            )
        report_status = "actioned"
        record_audit_event(
            db,
            actor_user_id=user.id,
            organization_id=None,
            action="workforce.service_message.hidden",
            entity_type="workforce_service_message",
            entity_id=message.id,
            details={"status": "hidden"},
        )
    else:
        report.status = "dismissed"
        report.reviewed_by_user_id = user.id
        report.reviewed_at = now
        report_status = "dismissed"
        record_audit_event(
            db,
            actor_user_id=user.id,
            organization_id=None,
            action="workforce.message_report.reviewed",
            entity_type="workforce_message_report",
            entity_id=report.id,
            details={"status": "dismissed"},
        )

    db.commit()
    db.refresh(report)
    return {
        "report": {"id": report.id, "status": report_status},
        "message": {"id": message.id, "moderation_status": message.moderation_status},
    }


# A sender may appeal a moderator's hide decision once, within this fixed window.
WORKFORCE_MESSAGE_APPEAL_WINDOW_DAYS = 14


class WorkforceMessageAppealCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: Literal[
        "decision_incorrect",
        "context_missing",
        "identity_misuse",
        "other",
    ]


class WorkforceMessageAppealReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["deny", "restore_message"]


def _message_appeal_payload(appeal: WorkforceMessageAppeal) -> dict:
    return {
        "id": appeal.id,
        "status": appeal.status,
        "reason_code": appeal.reason_code,
        "created_at": appeal.created_at,
        "reviewed_at": appeal.reviewed_at,
    }


@router.post(
    "/me/workforce/messages/{message_id}/appeals",
    status_code=status.HTTP_201_CREATED,
)
def create_workforce_message_appeal(
    message_id: str,
    payload: WorkforceMessageAppealCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Allow only the author of a hidden message to appeal the hide decision."""
    _require_verified_user(user)
    message = db.scalar(
        select(WorkforceServiceMessage)
        .where(WorkforceServiceMessage.id == message_id)
        .with_for_update()
    )
    # Do not reveal whether another participant's message exists.
    if message is None or message.sender_user_id != user.id:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.moderation_status != "hidden":
        raise HTTPException(status_code=409, detail="Only hidden messages can be appealed")

    moderated_at = db.scalar(
        select(func.max(WorkforceMessageReport.reviewed_at)).where(
            WorkforceMessageReport.message_id == message.id,
            WorkforceMessageReport.status == "actioned",
        )
    )
    if moderated_at is None:
        raise HTTPException(status_code=409, detail="No appealable moderation decision exists")
    if moderated_at.tzinfo is None:
        moderated_at = moderated_at.replace(tzinfo=timezone.utc)
    if utc_now() > moderated_at + timedelta(days=WORKFORCE_MESSAGE_APPEAL_WINDOW_DAYS):
        raise HTTPException(status_code=409, detail="The message appeal window has expired")

    appeal = WorkforceMessageAppeal(
        message_id=message.id,
        appellant_user_id=user.id,
        reason_code=payload.reason_code,
        status="open",
        created_at=utc_now(),
    )
    db.add(appeal)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An appeal has already been submitted for this message",
        ) from None

    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="workforce.service_message.appealed",
        entity_type="workforce_message_appeal",
        entity_id=appeal.id,
        details={"status": "open"},
    )
    db.commit()
    db.refresh(appeal)
    return {"appeal": _message_appeal_payload(appeal)}


@router.get("/me/workforce/message-appeals/{appeal_id}")
def get_workforce_message_appeal(
    appeal_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_verified_user(user)
    appeal = db.scalar(
        select(WorkforceMessageAppeal).where(
            WorkforceMessageAppeal.id == appeal_id,
            WorkforceMessageAppeal.appellant_user_id == user.id,
        )
    )
    if appeal is None:
        raise HTTPException(status_code=404, detail="Message appeal not found")
    return {"appeal": _message_appeal_payload(appeal)}


@router.get("/platform/workforce/message-appeals")
def list_workforce_message_appeals(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    appeal_status: Literal["open", "upheld", "denied"] = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
) -> list[dict]:
    _require_platform_admin(user)
    _require_message_encryption()
    rows = db.execute(
        select(WorkforceMessageAppeal, WorkforceServiceMessage, WorkforceServiceRequest)
        .join(
            WorkforceServiceMessage,
            WorkforceServiceMessage.id == WorkforceMessageAppeal.message_id,
        )
        .join(
            WorkforceServiceRequest,
            WorkforceServiceRequest.id == WorkforceServiceMessage.service_request_id,
        )
        .where(WorkforceMessageAppeal.status == appeal_status)
        .order_by(
            WorkforceMessageAppeal.created_at.asc(),
            WorkforceMessageAppeal.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {
            "appeal": _message_appeal_payload(appeal),
            "message": {
                "id": message.id,
                "body": message.body,
                "moderation_status": message.moderation_status,
                "created_at": message.created_at,
            },
            "request": {
                "request_code": request.request_code,
                "title": request.title,
                "status": request.status,
            },
        }
        for appeal, message, request in rows
    ]


@router.post("/platform/workforce/message-appeals/{appeal_id}/review")
def review_workforce_message_appeal(
    appeal_id: str,
    payload: WorkforceMessageAppealReview,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    appeal = db.scalar(
        select(WorkforceMessageAppeal)
        .where(WorkforceMessageAppeal.id == appeal_id)
        .with_for_update()
    )
    if appeal is None:
        raise HTTPException(status_code=404, detail="Message appeal not found")
    if appeal.status != "open":
        raise HTTPException(status_code=409, detail="Message appeal has already been reviewed")

    message = db.scalar(
        select(WorkforceServiceMessage)
        .where(WorkforceServiceMessage.id == appeal.message_id)
        .with_for_update()
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Appealed message not found")
    if message.moderation_status != "hidden":
        raise HTTPException(status_code=409, detail="Appealed message is no longer hidden")

    now = utc_now()
    appeal.reviewed_by_user_id = user.id
    appeal.reviewed_at = now
    if payload.decision == "restore_message":
        message.moderation_status = "visible"
        appeal.status = "upheld"
        record_audit_event(
            db,
            actor_user_id=user.id,
            organization_id=None,
            action="workforce.service_message.restored",
            entity_type="workforce_service_message",
            entity_id=message.id,
            details={"status": "visible"},
        )
    else:
        appeal.status = "denied"

    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="workforce.message_appeal.reviewed",
        entity_type="workforce_message_appeal",
        entity_id=appeal.id,
        details={"status": appeal.status},
    )
    db.commit()
    db.refresh(appeal)
    return {
        "appeal": {"id": appeal.id, "status": appeal.status},
        "message": {"id": message.id, "moderation_status": message.moderation_status},
    }
