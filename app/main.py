"""ASGI application entry point."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings, validate_runtime_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Validate production-critical settings before accepting traffic."""
    validate_runtime_settings(settings)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="A modular foundation for the Global AI-Powered Business Ecosystem.",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
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
