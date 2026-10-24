"""Registration and token issuance endpoints."""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import bearer_scheme, get_current_user
from app.audit import record_audit_event
from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    signing_key_is_configured,
    verify_password,
)
from app.db import get_db
from app.models import AuthAttempt, EmailVerificationToken, PasswordResetToken, TokenRevocation, User, utc_now
from app.schemas import EmailVerificationConfirm, LoginRequest, PasswordResetConfirm, PasswordResetRequest, RegisterRequest, TokenResponse
from app.services.email import send_email_verification, send_password_reset_email

router = APIRouter(prefix="/auth", tags=["identity"])
logger = logging.getLogger(__name__)
_RESET_ACCEPTED = {"message": "If the account exists, reset instructions will be sent."}\n_VERIFICATION_ACCEPTED = {"message": "If the account needs verification, instructions will be sent."}


def _rate_limit_subject(identifier: str, request: Request) -> str:
    """Hash the account identifier and direct peer address; do not persist raw PII."""
    client_host = request.client.host if request.client else "unknown"
    material = f"{identifier.strip().lower()}\\0{client_host}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record_auth_attempt(
    db: Session, *, scope: str, subject_hash: str, limit: int, window_minutes: int
) -> None:
    """Enforce a database-backed sliding window shared by API workers."""
    now = utc_now()
    cutoff = now - timedelta(minutes=window_minutes)
    db.execute(delete(AuthAttempt).where(AuthAttempt.created_at < now - timedelta(hours=24)))
    count = db.scalar(
        select(func.count()).select_from(AuthAttempt).where(
            AuthAttempt.scope == scope,
            AuthAttempt.subject_hash == subject_hash,
            AuthAttempt.created_at >= cutoff,
        )
    ) or 0
    if count >= limit:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please wait before trying again.",
            headers={"Retry-After": str(window_minutes * 60)},
        )
    db.add(AuthAttempt(scope=scope, subject_hash=subject_hash, created_at=now))
    db.commit()


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    _record_auth_attempt(
        db,
        scope="register",
        subject_hash=_rate_limit_subject(str(payload.email), request),
        limit=settings.auth_register_attempt_limit,
        window_minutes=settings.auth_register_window_minutes,
    )
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        email_verified=False,
    )
    db.add(user)
    try:
        db.flush()
        raw_token = secrets.token_urlsafe(32)
        token_record = EmailVerificationToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            expires_at=utc_now() + timedelta(minutes=settings.email_verification_token_expire_minutes),
        )
        db.add(token_record)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists") from None
    try:
        send_email_verification(user.email, raw_token)
    except Exception:
        logger.warning("Email verification delivery failed; check SMTP configuration and service health")
        db.execute(
            update(EmailVerificationToken)
            .where(EmailVerificationToken.id == token_record.id, EmailVerificationToken.used_at.is_(None))
            .values(used_at=utc_now())
        )
        db.commit()
        return {"id": user.id, "email": user.email, "status": "verification_email_pending"}
    return {"id": user.id, "email": user.email, "status": "pending_verification"}


@router.post("/email-verification/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_email_verification(
    payload: EmailVerificationConfirm,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Atomically consume a valid one-time token and verify the owning account."""
    now = utc_now()
    digest = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    record = db.scalar(
        select(EmailVerificationToken).where(EmailVerificationToken.token_hash == digest)
    )
    if record is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    consumed = db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.id == record.id,
            EmailVerificationToken.used_at.is_(None),
            EmailVerificationToken.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    user = db.scalar(select(User).where(User.id == record.user_id, User.is_active.is_(True)))
    if user is None:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    user.email_verified = True
    db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.id != record.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="auth.email_verified",
        entity_type="user",
        entity_id=user.id,
        details={"status": "verified"},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/email-verification/request", status_code=status.HTTP_202_ACCEPTED)
def request_email_verification(
    payload: PasswordResetRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    """Resend verification without disclosing whether an account exists."""
    _record_auth_attempt(
        db,
        scope="email_verification",
        subject_hash=_rate_limit_subject(str(payload.email), request),
        limit=settings.password_reset_request_limit,
        window_minutes=settings.password_reset_window_minutes,
    )
    user = db.scalar(
        select(User).where(User.email == str(payload.email), User.is_active.is_(True))
    )
    if user is None or user.email_verified:
        return _VERIFICATION_ACCEPTED
    now = utc_now()
    db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    raw_token = secrets.token_urlsafe(32)
    record = EmailVerificationToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=now + timedelta(minutes=settings.email_verification_token_expire_minutes),
    )
    db.add(record)
    db.commit()
    try:
        send_email_verification(user.email, raw_token)
    except Exception:
        logger.warning("Email verification delivery failed; check SMTP configuration and service health")
        db.execute(
            update(EmailVerificationToken)
            .where(EmailVerificationToken.id == record.id, EmailVerificationToken.used_at.is_(None))
            .values(used_at=utc_now())
        )
        db.commit()
    return _VERIFICATION_ACCEPTED


@router.post("/token", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    _record_auth_attempt(
        db,
        scope="login",
        subject_hash=_rate_limit_subject(str(payload.email), request),
        limit=settings.auth_login_attempt_limit,
        window_minutes=settings.auth_login_window_minutes,
    )
    if not signing_key_is_configured():
        raise HTTPException(status_code=503, detail="A SECRET_KEY of at least 32 characters is required")
    user = db.scalar(select(User).where(User.email == str(payload.email)))
    if settings.require_email_verification and user is not None and user.is_active and verify_password(payload.password, user.password_hash) and not user.email_verified:
        raise HTTPException(status_code=403, detail="Email verification required")
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password",
                            headers={"WWW-Authenticate": "Bearer"})
    token = create_access_token(user.id, auth_version=user.auth_version)
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
    )



@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = decode_access_token(credentials.credentials)
    token_id = payload.get("jti")
    expires = payload.get("exp")
    if not isinstance(token_id, str) or not isinstance(expires, (int, float)):
        raise HTTPException(status_code=401, detail="Invalid access token")
    db.add(
        TokenRevocation(
            jti=token_id,
            user_id=user.id,
            expires_at=datetime.fromtimestamp(expires, timezone.utc),
        )
    )
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="auth.logout",
        entity_type="access_token",
        entity_id=token_id,
        details={"status": "revoked"},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(payload: PasswordResetRequest, request: Request, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """Return an identical response for known and unknown accounts."""
    _record_auth_attempt(
        db,
        scope="password_reset",
        subject_hash=_rate_limit_subject(str(payload.email), request),
        limit=settings.password_reset_request_limit,
        window_minutes=settings.password_reset_window_minutes,
    )
    user = db.scalar(select(User).where(User.email == str(payload.email), User.is_active.is_(True)))
    if user is None:
        return _RESET_ACCEPTED
    now = utc_now()
    db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)).values(used_at=now))
    raw_token = secrets.token_urlsafe(32)
    record = PasswordResetToken(user_id=user.id, token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(), expires_at=now + timedelta(minutes=settings.password_reset_token_expire_minutes))
    db.add(record)
    db.commit()
    try:
        send_password_reset_email(user.email, raw_token)
    except Exception:
        logger.warning("Password reset email delivery failed; check SMTP configuration and service health")
        db.execute(update(PasswordResetToken).where(PasswordResetToken.id == record.id, PasswordResetToken.used_at.is_(None)).values(used_at=utc_now()))
        db.commit()
    return _RESET_ACCEPTED


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(payload: PasswordResetConfirm, db: Annotated[Session, Depends(get_db)]) -> Response:
    """Consume a reset token atomically and invalidate previously issued access tokens."""
    now = utc_now()
    digest = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    record = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == digest))
    if record is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    consumed = db.execute(update(PasswordResetToken).where(PasswordResetToken.id == record.id, PasswordResetToken.used_at.is_(None), PasswordResetToken.expires_at > now).values(used_at=now).execution_options(synchronize_session=False))
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user = db.scalar(select(User).where(User.id == record.user_id, User.is_active.is_(True)))
    if user is None:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user.password_hash = hash_password(payload.new_password)
    user.auth_version += 1
    db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.id != record.id, PasswordResetToken.used_at.is_(None)).values(used_at=now))
    record_audit_event(db, actor_user_id=user.id, organization_id=None, action="auth.password_reset", entity_type="user", entity_id=user.id, details={"status": "completed"})
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
