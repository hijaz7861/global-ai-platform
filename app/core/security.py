"""Password hashing and short-lived signed access tokens."""
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

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


def create_access_token(subject: str, auth_version: int = 0, session_id: str | None = None) -> str:
    if not signing_key_is_configured():
        raise RuntimeError("SECRET_KEY must contain at least 32 characters before issuing tokens")
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    claims = {"sub": subject, "exp": expires, "jti": str(uuid4()), "ver": auth_version}
    if session_id is not None:
        claims["sid"] = session_id
    return jwt.encode(claims, settings.secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    if not signing_key_is_configured():
        raise RuntimeError("SECRET_KEY must contain at least 32 characters before validating tokens")
    previous_keys = [item.strip() for item in settings.previous_secret_keys.split(",") if item.strip()]
    if any(len(key) < _MIN_SIGNING_KEY_LENGTH for key in previous_keys):
        raise RuntimeError("Every PREVIOUS_SECRET_KEYS entry must contain at least 32 characters")
    last_error: jwt.InvalidTokenError | None = None
    for key in [settings.secret_key, *previous_keys]:
        try:
            return jwt.decode(token, key, algorithms=[_ALGORITHM])
        except jwt.InvalidTokenError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise jwt.InvalidTokenError("Invalid access token")
