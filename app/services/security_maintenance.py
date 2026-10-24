"""Bounded cleanup for expired authentication and rate-limit records."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AuthAttempt,
    AuthRateLimitLock,
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
    TokenRevocation,
    utc_now,
)


def cleanup_expired_security_records(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    current_time = now or utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    retention_cutoff = current_time - timedelta(hours=24)

    email_result = db.execute(
        delete(EmailVerificationToken).where(
            or_(
                EmailVerificationToken.expires_at < retention_cutoff,
                and_(
                    EmailVerificationToken.used_at.is_not(None),
                    EmailVerificationToken.used_at < retention_cutoff,
                ),
            )
        )
    )
    reset_result = db.execute(
        delete(PasswordResetToken).where(
            or_(
                PasswordResetToken.expires_at < retention_cutoff,
                and_(PasswordResetToken.used_at.is_not(None), PasswordResetToken.used_at < retention_cutoff),
            )
        )
    )
    revocation_result = db.execute(
        delete(TokenRevocation).where(TokenRevocation.expires_at < current_time)
    )
    # Keep consumed refresh-token hashes until every token in the family has expired;
    # otherwise an old token could no longer identify a still-live family on replay.
    expired_families = (
        select(RefreshToken.family_id)
        .group_by(RefreshToken.family_id)
        .having(func.max(RefreshToken.expires_at) < current_time)
    )
    refresh_result = db.execute(
        delete(RefreshToken).where(RefreshToken.family_id.in_(expired_families))
    )
    attempts_result = db.execute(
        delete(AuthAttempt).where(AuthAttempt.created_at < retention_cutoff)
    )
    locks_result = db.execute(
        delete(AuthRateLimitLock).where(AuthRateLimitLock.touched_at < retention_cutoff)
    )
    return {
        "email_verification_tokens_deleted": int(email_result.rowcount or 0),
        "password_reset_tokens_deleted": int(reset_result.rowcount or 0),
        "expired_access_revocations_deleted": int(revocation_result.rowcount or 0),
        "expired_refresh_families_deleted": int(refresh_result.rowcount or 0),
        "auth_attempts_deleted": int(attempts_result.rowcount or 0),
        "rate_limit_locks_deleted": int(locks_result.rowcount or 0),
    }
