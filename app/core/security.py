"""Password hashing and short-lived signed access tokens."""
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

_password_hash = PasswordHash.recommended()
_ALGORITHM = "HS256"
_MIN_SIGNING_KEY_LENGTH = 32


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def signing_key_is_configured() -> bool:
    return bool(settings.secret_key and len(settings.secret_key) >= _MIN_SIGNING_KEY_LENGTH)


def create_access_token(subject: str) -> str:
    if not signing_key_is_configured():
        raise RuntimeError("SECRET_KEY must contain at least 32 characters before issuing tokens")
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": subject, "exp": expires}, settings.secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    if not signing_key_is_configured():
        raise RuntimeError("SECRET_KEY must contain at least 32 characters before validating tokens")
    return jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
