import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import register_error_handlers
from app.core.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.inventory.router import router as inventory_router
from app.modules.orders.router import router as orders_router
from app.modules.tenants.router import router as tenants_router
from app.modules.users.router import router as users_router
from app.modules.warehouses.router import router as warehouses_router
from app.modules.wms.router import router as wms_router
from app.modules.apikeys.router import router as apikeys_router
from app.modules.billing.router import router as billing_router
from app.modules.platform.router import router as platform_router
from app.modules.public.router import router as public_router
from app.modules.returns.router import router as returns_router
from app.modules.shipping.router import router as shipping_router
from app.modules.analytics.router import router as analytics_router
from app.modules.notifications.router import router as notifications_router
from app.modules.reports.router import router as reports_router
from app.modules.settings.router import router as settings_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.6.0",
    docs_url=None if settings.env == "production" else "/docs",
    redoc_url=None,
    openapi_url=None if settings.env == "production" else "/openapi.json",
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID", "Idempotency-Key", "X-API-Key"],
)
register_error_handlers(app)

v1 = APIRouter(prefix="/api/v1")
for r in (auth_router, tenants_router, users_router, catalog_router, warehouses_router, inventory_router,
          orders_router, wms_router, audit_router, billing_router, apikeys_router, platform_router,
          public_router, shipping_router, returns_router, analytics_router, notifications_router, reports_router,
          settings_router):
    v1.include_router(r)
app.include_router(v1)


@app.get("/healthz", tags=["ops"])
async def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
async def readyz():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ready"}
