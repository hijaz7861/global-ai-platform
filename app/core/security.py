"""Password hashing and short-lived signed access tokens."""
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

_password_hash = PasswordHash.recommended()
_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def create_access_token(subject: str) -> str:
    if not settings.secret_key:
        raise RuntimeError("SECRET_KEY must be configured before issuing access tokens")
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": subject, "exp": expires}, settings.secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    if not settings.secret_key:
        raise RuntimeError("SECRET_KEY must be configured before validating access tokens")
    return jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
