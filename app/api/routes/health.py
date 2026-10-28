"""Health and readiness endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from typing import Annotated

from app.db import get_db

router = APIRouter()


@router.get("/health", summary="Service health")
def health() -> dict[str, str]:
    """Report process-level health only; does not imply dependencies are healthy."""
    return {"status": "ok"}


@router.get("/ready", summary="Dependency readiness")
def readiness(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """Return ready only when the configured database accepts a simple query."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # Avoid leaking database URLs, driver messages, or internal connection details.
        raise HTTPException(status_code=503, detail="Database is not ready") from None
    return {"status": "ready"}
