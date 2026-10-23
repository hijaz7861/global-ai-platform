"""Registration and token issuance endpoints."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, signing_key_is_configured, verify_password
from app.db import get_db
from app.models import TokenRevocation, User
from app.schemas import LoginRequest, RegisterRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["identity"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=str(payload.email), password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists") from None
    db.refresh(user)
    return {"id": user.id, "email": user.email, "status": "created"}


@router.post("/token", response_model=TokenResponse)
def login(payload: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    if not signing_key_is_configured():
        raise HTTPException(status_code=503, detail="A SECRET_KEY of at least 32 characters is required")
    user = db.scalar(select(User).where(User.email == str(payload.email)))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password",
                            headers={"WWW-Authenticate": "Bearer"})
    token = create_access_token(user.id)
    from app.core.config import settings
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
