"""One-shot MFA secret re-encryption for signing-key rotation."""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.mfa import decrypt_totp_secret, encrypt_totp_secret
from app.db import SessionLocal
from app.models import User


def rotate_mfa_secrets(db: Session) -> int:
    """Re-encrypt stored active/pending TOTP secrets with the current SECRET_KEY.

    PREVIOUS_SECRET_KEYS must include any key that encrypted existing MFA secrets.
    The caller owns the transaction so a single failed decryption rolls back all updates.
    """
    if not settings.secret_key or len(settings.secret_key) < 32:
        raise RuntimeError("A SECRET_KEY of at least 32 characters is required for MFA rotation")
    users = db.scalars(
        select(User).where(
            (User.mfa_secret_encrypted.is_not(None))
            | (User.mfa_pending_secret_encrypted.is_not(None))
        )
    ).all()
    rotated = 0
    for user in users:
        if user.mfa_secret_encrypted is not None:
            plaintext = decrypt_totp_secret(user.mfa_secret_encrypted)
            user.mfa_secret_encrypted = encrypt_totp_secret(plaintext)
        if user.mfa_pending_secret_encrypted is not None:
            plaintext = decrypt_totp_secret(user.mfa_pending_secret_encrypted)
            user.mfa_pending_secret_encrypted = encrypt_totp_secret(plaintext)
        rotated += 1
    return rotated


def main() -> None:
    with SessionLocal() as db:
        with db.begin():
            count = rotate_mfa_secrets(db)
    print(json.dumps({"mfa_users_reencrypted": count}, sort_keys=True))


if __name__ == "__main__":
    main()
