"""ASGI application entry point."""
from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="A modular foundation for the Global AI-Powered Business Ecosystem.",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/", tags=["system"])
def root() -> dict[str, str]:
    """Expose a minimal service identity endpoint."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "available",
        "api_docs": "/docs",
    }
