"""Versioned API router."""
from fastapi import APIRouter

from app.api.routes.accounting import router as accounting_router
from app.api.routes.ai import router as ai_router
from app.api.routes.audit import router as audit_router
from app.api.routes.health import router as health_router
from app.api.routes.identity import router as identity_router
from app.api.routes.listings import router as listings_router
from app.api.routes.organizations import router as organizations_router
from app.api.routes.orders import router as orders_router
from app.api.routes.seller import router as seller_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(identity_router)
api_router.include_router(organizations_router)
api_router.include_router(listings_router)
api_router.include_router(orders_router)
api_router.include_router(audit_router)
api_router.include_router(accounting_router)
api_router.include_router(seller_router)
api_router.include_router(ai_router)
