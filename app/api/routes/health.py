"""Health and readiness endpoints."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health", summary="Service health")
def health() -> dict[str, str]:
    """Report process-level health only; does not imply dependencies are healthy."""
    return {"status": "ok"}
