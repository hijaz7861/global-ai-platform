"""Registration and token issuance endpoints."""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import bearer_scheme, get_current_user
from app.audit import record_audit_event
from app.core.config import settings
from app.core.mfa import decrypt_totp_secret, encrypt_totp_secret, generate_totp_secret, make_otpauth_uri, verify_totp
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    signing_key_is_configured,
    verify_password,
)
from app.db import get_db
from app.models import AuthAttempt, AuthRateLimitLock, EmailVerificationToken, MFARecoveryCode, PasswordResetToken, RefreshToken, TokenRevocation, User, utc_now
from app.schemas import EmailVerificationConfirm, LoginRequest, MFAConfirmRequest, MFADisableRequest, MFAEnrollRequest, MFAEnrollmentResponse, MFARecoveryCodesRequest, MFARecoveryCodesResponse, PasswordResetConfirm, PasswordResetRequest, RefreshTokenRequest, RegisterRequest, TokenResponse
from app.services.email import send_email_verification, send_password_reset_email

router = APIRouter(prefix="/auth", tags=["identity"])
logger = logging.getLogger(__name__)
_RESET_ACCEPTED = {"message": "If the account exists, reset instructions will be sent."}
_VERIFICATION_ACCEPTED = {"message": "If the account needs verification, instructions will be sent."}


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _consume_mfa_step(db: Session, user_id: str, step: int) -> bool:
    consumed = db.execute(
        update(User)
        .where(
            User.id == user_id,
            User.mfa_enabled.is_(True),
            or_(User.mfa_last_verified_step.is_(None), User.mfa_last_verified_step < step),
        )
        .values(mfa_last_verified_step=step)
        .execution_options(synchronize_session=False)
    )
    return consumed.rowcount == 1



def _rate_limit_subject(identifier: str, request: Request) -> str:
    """Hash the account identifier and direct peer address; do not persist raw PII."""
    client_host = request.client.host if request.client else "unknown"
    material = f"{identifier.strip().lower()}\\0{client_host}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record_auth_attempt(
    db: Session, *, scope: str, subject_hash: str, limit: int, window_minutes: int
) -> None:
    """Serialize each subject's sliding-window check and insert across workers."""
    now = utc_now()
    cutoff = now - timedelta(minutes=window_minutes)
    lock_values = {"scope": scope, "subject_hash": subject_hash, "touched_at": now}
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        lock_insert = sqlite_insert(AuthRateLimitLock)
    elif dialect == "postgresql":
        lock_insert = postgresql_insert(AuthRateLimitLock)
    else:
        raise RuntimeError("Auth rate limiting requires SQLite or PostgreSQL")

    # Acquire a per-subject write/row lock before counting so concurrent requests
    # cannot both pass the same limit based on the same prior count.
    db.execute(
        lock_insert.values(**lock_values).on_conflict_do_update(
            index_elements=[AuthRateLimitLock.scope, AuthRateLimitLock.subject_hash],
            set_={"touched_at": now},
        )
    )
    db.execute(
        delete(AuthRateLimitLock).where(AuthRateLimitLock.touched_at < now - timedelta(hours=24))
    )
    db.execute(delete(AuthAttempt).where(AuthAttempt.created_at < now - timedelta(hours=24)))
    db.flush()
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
    if user.mfa_enabled:
        if not user.mfa_secret_encrypted or (payload.totp_code is None) == (payload.recovery_code is None):
            raise HTTPException(status_code=401, detail="Invalid email, password, or MFA code",
                                headers={"WWW-Authenticate": "Bearer"})
        if payload.recovery_code is not None:
            normalized_code = payload.recovery_code.strip().lower()
            recovery_digest = hashlib.sha256(normalized_code.encode("utf-8")).hexdigest()
            consumed = db.execute(
                update(MFARecoveryCode)
                .where(
                    MFARecoveryCode.user_id == user.id,
                    MFARecoveryCode.code_hash == recovery_digest,
                    MFARecoveryCode.used_at.is_(None),
                )
                .values(used_at=utc_now())
                .execution_options(synchronize_session=False)
            )
            if consumed.rowcount != 1:
                db.rollback()
                raise HTTPException(status_code=401, detail="Invalid email, password, or MFA code",
                                    headers={"WWW-Authenticate": "Bearer"})
            record_audit_event(
                db,
                actor_user_id=user.id,
                organization_id=None,
                action="auth.mfa_recovery_code_used",
                entity_type="user",
                entity_id=user.id,
                details={"status": "consumed"},
            )
            db.commit()
        else:
            try:
                secret = decrypt_totp_secret(user.mfa_secret_encrypted)
            except (RuntimeError, ValueError):
                raise HTTPException(status_code=503, detail="MFA encryption configuration is unavailable") from None
            step = verify_totp(secret, payload.totp_code, last_step=user.mfa_last_verified_step)
            if step is None or not _consume_mfa_step(db, user.id, step):
                db.rollback()
                raise HTTPException(status_code=401, detail="Invalid email, password, or MFA code",
                                    headers={"WWW-Authenticate": "Bearer"})
            db.commit()

    family_id = str(uuid4())
    raw_refresh_token = secrets.token_urlsafe(48)
    refresh_expires_at = utc_now() + timedelta(days=settings.refresh_token_expire_days)
    db.add(
        RefreshToken(
            token_hash=hashlib.sha256(raw_refresh_token.encode("utf-8")).hexdigest(),
            family_id=family_id,
            user_id=user.id,
            auth_version=user.auth_version,
            expires_at=refresh_expires_at,
        )
    )
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="auth.session_created",
        entity_type="auth_session",
        entity_id=family_id,
        details={"status": "created"},
    )
    db.commit()
    token = create_access_token(user.id, auth_version=user.auth_version, session_id=family_id)
    return TokenResponse(
        access_token=token,
        refresh_token=raw_refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
        refresh_expires_in=settings.refresh_token_expire_days * 24 * 60 * 60,
    )


@router.post("/mfa/enroll", response_model=MFAEnrollmentResponse)
def enroll_mfa(
    payload: MFAEnrollRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MFAEnrollmentResponse:
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is required")
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="MFA is already enabled")
    if not signing_key_is_configured():
        raise HTTPException(status_code=503, detail="MFA encryption configuration is unavailable")
    secret = generate_totp_secret()
    user.mfa_pending_secret_encrypted = encrypt_totp_secret(secret)
    user.mfa_pending_expires_at = utc_now() + timedelta(minutes=settings.mfa_enrollment_expire_minutes)
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="auth.mfa_enrollment_started",
        entity_type="user",
        entity_id=user.id,
        details={"status": "pending"},
    )
    db.commit()
    return MFAEnrollmentResponse(
        secret=secret,
        otpauth_uri=make_otpauth_uri(secret, issuer=settings.app_name, account_name=user.email),
        expires_in_seconds=settings.mfa_enrollment_expire_minutes * 60,
    )


@router.post("/mfa/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_mfa(
    payload: MFAConfirmRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    if user.mfa_enabled or not user.mfa_pending_secret_encrypted or not user.mfa_pending_expires_at:
        raise HTTPException(status_code=409, detail="No pending MFA enrollment")
    if _as_utc(user.mfa_pending_expires_at) <= utc_now():
        user.mfa_pending_secret_encrypted = None
        user.mfa_pending_expires_at = None
        db.commit()
        raise HTTPException(status_code=400, detail="MFA enrollment expired; start again")
    try:
        secret = decrypt_totp_secret(user.mfa_pending_secret_encrypted)
    except (RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="MFA encryption configuration is unavailable") from None
    step = verify_totp(secret, payload.code)
    if step is None:
        raise HTTPException(status_code=400, detail="Invalid MFA code")
    user.mfa_secret_encrypted = user.mfa_pending_secret_encrypted
    user.mfa_pending_secret_encrypted = None
    user.mfa_pending_expires_at = None
    user.mfa_enabled = True
    user.mfa_last_verified_step = step
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="auth.mfa_enabled",
        entity_type="user",
        entity_id=user.id,
        details={"status": "enabled"},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mfa/recovery-codes", response_model=MFARecoveryCodesResponse)
def regenerate_mfa_recovery_codes(
    payload: MFARecoveryCodesRequest,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MFARecoveryCodesResponse:
    """Replace one-time MFA recovery codes; show raw codes only in this response."""
    if not user.mfa_enabled or not user.mfa_secret_encrypted:
        raise HTTPException(status_code=409, detail="MFA is not enabled")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is required")
    try:
        secret = decrypt_totp_secret(user.mfa_secret_encrypted)
    except (RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="MFA encryption configuration is unavailable") from None
    step = verify_totp(secret, payload.code, last_step=user.mfa_last_verified_step)
    if step is None or not _consume_mfa_step(db, user.id, step):
        db.rollback()
        raise HTTPException(status_code=401, detail="Invalid current password or MFA code")

    raw_codes = [secrets.token_hex(10) for _ in range(10)]
    db.execute(delete(MFARecoveryCode).where(MFARecoveryCode.user_id == user.id))
    db.add_all([
        MFARecoveryCode(
            code_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
            user_id=user.id,
        )
        for code in raw_codes
    ])
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="auth.mfa_recovery_codes_regenerated",
        entity_type="user",
        entity_id=user.id,
        details={"count": len(raw_codes)},
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return MFARecoveryCodesResponse(codes=raw_codes)


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
def disable_mfa(
    payload: MFADisableRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    if not user.mfa_enabled or not user.mfa_secret_encrypted:
        raise HTTPException(status_code=409, detail="MFA is not enabled")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is required")
    step: int | None = None
    if payload.recovery_code is not None:
        recovery_digest = hashlib.sha256(payload.recovery_code.strip().lower().encode("utf-8")).hexdigest()
        consumed = db.execute(
            update(MFARecoveryCode)
            .where(
                MFARecoveryCode.user_id == user.id,
                MFARecoveryCode.code_hash == recovery_digest,
                MFARecoveryCode.used_at.is_(None),
            )
            .values(used_at=utc_now())
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=401, detail="Invalid current password or MFA code")
    else:
        try:
            secret = decrypt_totp_secret(user.mfa_secret_encrypted)
        except (RuntimeError, ValueError):
            raise HTTPException(status_code=503, detail="MFA encryption configuration is unavailable") from None
        step = verify_totp(secret, payload.code, last_step=user.mfa_last_verified_step)
        if step is None:
            raise HTTPException(status_code=401, detail="Invalid current password or MFA code")
    now = utc_now()
    predicates = [User.id == user.id, User.mfa_enabled.is_(True)]
    if step is not None:
        predicates.append(or_(User.mfa_last_verified_step.is_(None), User.mfa_last_verified_step < step))
    values = {
        "mfa_enabled": False,
        "mfa_secret_encrypted": None,
        "mfa_pending_secret_encrypted": None,
        "mfa_pending_expires_at": None,
        "auth_version": User.auth_version + 1,
    }
    if step is not None:
        values["mfa_last_verified_step"] = step
    disabled = db.execute(
        update(User)
        .where(*predicates)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if disabled.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=401, detail="Invalid current password or MFA code")
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.execute(delete(MFARecoveryCode).where(MFARecoveryCode.user_id == user.id))
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="auth.mfa_disabled",
        entity_type="user",
        entity_id=user.id,
        details={"status": "disabled"},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/refresh", response_model=TokenResponse)
def refresh_access_token(
    payload: RefreshTokenRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Rotate refresh credentials; replay of a consumed token revokes its whole family."""
    now = utc_now()
    digest = hashlib.sha256(payload.refresh_token.encode("utf-8")).hexdigest()
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest))
    if record is None or record.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    if record.used_at is not None:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == record.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        user = db.scalar(select(User).where(User.id == record.user_id))
        if user is not None:
            user.auth_version += 1
        record_audit_event(
            db,
            actor_user_id=record.user_id,
            organization_id=None,
            action="auth.refresh_replay_detected",
            entity_type="auth_session",
            entity_id=record.family_id,
            details={"status": "family_revoked"},
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Refresh token reuse detected; sign in again")
    record_expiry = record.expires_at
    if record_expiry.tzinfo is None:
        record_expiry = record_expiry.replace(tzinfo=timezone.utc)
    if record_expiry <= now:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user = db.scalar(select(User).where(User.id == record.user_id, User.is_active.is_(True)))
    if user is None or user.auth_version != record.auth_version:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == record.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    consumed = db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == digest,
            RefreshToken.used_at.is_(None),
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        db.rollback()
        current = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest))
        if current is not None and current.used_at is not None and current.revoked_at is None:
            replay_time = utc_now()
            db.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == current.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=replay_time)
            )
            replay_user = db.scalar(select(User).where(User.id == current.user_id))
            if replay_user is not None:
                replay_user.auth_version += 1
            record_audit_event(
                db,
                actor_user_id=current.user_id,
                organization_id=None,
                action="auth.refresh_replay_detected",
                entity_type="auth_session",
                entity_id=current.family_id,
                details={"status": "family_revoked"},
            )
            db.commit()
            raise HTTPException(status_code=401, detail="Refresh token reuse detected; sign in again")
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    new_raw_token = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            token_hash=hashlib.sha256(new_raw_token.encode("utf-8")).hexdigest(),
            family_id=record.family_id,
            user_id=user.id,
            auth_version=user.auth_version,
            expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="auth.session_rotated",
        entity_type="auth_session",
        entity_id=record.family_id,
        details={"status": "rotated"},
    )
    db.commit()
    access = create_access_token(user.id, auth_version=user.auth_version, session_id=record.family_id)
    return TokenResponse(
        access_token=access,
        refresh_token=new_raw_token,
        expires_in=settings.access_token_expire_minutes * 60,
        refresh_expires_in=settings.refresh_token_expire_days * 24 * 60 * 60,
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
    session_id = payload.get("sid")
    if isinstance(session_id, str) and session_id:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == session_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action="auth.logout",
        entity_type="auth_session" if isinstance(session_id, str) else "access_token",
        entity_id=session_id if isinstance(session_id, str) else token_id,
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
