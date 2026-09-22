"""Endpoint tanpa login: katalog harga & pendaftaran mandiri (self-signup + trial)."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, ratelimit
from app.core.config import get_settings
from app.core.db import get_session, set_tenant_context
from app.core.errors import AppError
from app.core.middleware import client_ip
from app.core.security import hash_password, validate_password_strength
from app.models import Plan, Tenant, User, UserRole
from app.modules.auth.service import resolve_tenant
from app.modules.billing import service as billing
from app.modules.billing.router import PlanOut, public_plans

router = APIRouter(prefix="/public", tags=["public"])
RESERVED = {"platform", "admin", "api", "www", "app", "billing", "support", "help", "status", "demo", "ext"}


class SignupIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,40}$", description="Kode workspace untuk login")
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(max_length=256)
    plan_code: str | None = Field(default=None, max_length=30)

    @field_validator("password")
    @classmethod
    def _strong(cls, v: str) -> str:
        return validate_password_strength(v)


@router.get("/plans", response_model=list[PlanOut])
async def plans(s: AsyncSession = Depends(get_session)):
    return await public_plans(s)


@router.post("/signup", status_code=201)
async def signup(body: SignupIn, request: Request, s: AsyncSession = Depends(get_session)):
    st = get_settings()
    if not st.signup_enabled:
        raise AppError(404, "NOT_FOUND", "Pendaftaran mandiri tidak tersedia")
    await ratelimit.hit(f"signup:{client_ip(request)}", 5, window_seconds=3600)
    if body.slug in RESERVED:
        raise AppError(409, "SLUG_TAKEN", "Kode workspace ini tidak bisa dipakai, pilih yang lain")
    if await resolve_tenant(s, body.slug):
        raise AppError(409, "SLUG_TAKEN", "Kode workspace sudah dipakai, pilih yang lain")
    plan_code = body.plan_code or st.trial_plan
    plan = await s.get(Plan, plan_code)
    if plan is None or not plan.is_public or not plan.self_serve:
        raise AppError(422, "PLAN_NOT_AVAILABLE", "Paket ini tidak bisa dicoba mandiri. Hubungi tim sales.")

    # Pembuatan tenant butuh hak platform; dibatasi ke transaksi ini saja lalu segera dipersempit
    await set_tenant_context(s, None, superadmin=True)
    t = Tenant(slug=body.slug, name=body.company_name, status="ACTIVE")
    s.add(t)
    await s.flush()
    await set_tenant_context(s, t.id)
    admin = User(tenant_id=t.id, email=body.email.lower(), full_name=body.full_name,
                 password_hash=hash_password(body.password))
    admin.roles = [UserRole(tenant_id=t.id, role_code="TENANT_ADMIN")]
    s.add(admin)
    sub = await billing.start_trial(s, t.id, plan.code)
    await audit.record(s, tenant_id=t.id, actor_user_id=admin.id, action="tenant.signed_up", entity_type="tenant",
                       entity_id=t.id, after={"slug": t.slug, "plan": plan.code, "trial_ends_at": str(sub.trial_ends_at)},
                       correlation_id=getattr(request.state, "correlation_id", None), ip=client_ip(request))
    return {"tenant": t.slug, "email": admin.email, "plan": plan.code, "trial_ends_at": sub.trial_ends_at}
