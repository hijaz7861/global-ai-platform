"""Authentication and tenant membership dependencies."""
from datetime import datetime, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db import get_db
from app.models import Membership, TokenRevocation, User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = payload.get("sub")
        token_id = payload.get("jti")
        auth_version = payload.get("ver", 0)
        if (not isinstance(user_id, str) or not user_id or not isinstance(token_id, str) or not token_id
                or not isinstance(auth_version, int) or isinstance(auth_version, bool):
            raise ValueError("Invalid token claims")
        revoked = db.scalar(
            select(TokenRevocation.jti).where(
                TokenRevocation.jti == token_id,
                TokenRevocation.expires_at > datetime.now(timezone.utc),
            )
        )
        if revoked is not None:
            raise jwt.InvalidTokenError("Revoked token")
    except (jwt.InvalidTokenError, RuntimeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired access token",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    user = db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if user is None or user.auth_version != auth_version:
        raise HTTPException(status_code=401, detail="Invalid or inactive account",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def require_membership(
    organization_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Membership:
    membership = db.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == user.id,
        )
    )
    if membership is None:
        # Do not reveal whether an organization exists to a non-member.
        raise HTTPException(status_code=404, detail="Organization not found")
    return membership
