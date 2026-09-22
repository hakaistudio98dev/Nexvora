from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.core.schemas import ORM
from app.core.security import hash_password, validate_password_strength
from app.core.utils import snapshot
from app.models import Tenant, User, UserRole
from app.modules.billing.service import start_trial

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantOut(ORM):
    id: UUID
    slug: str
    name: str
    status: str


class TenantCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=2, max_length=200)
    admin_email: EmailStr
    admin_full_name: str = Field(min_length=2, max_length=200)
    admin_password: str = Field(max_length=256)
    plan_code: str | None = Field(default=None, max_length=30, description="Default: paket trial")

    @field_validator("admin_password")
    @classmethod
    def _strong(cls, v: str) -> str:
        return validate_password_strength(v)


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    status: str | None = Field(default=None, pattern=r"^(ACTIVE|SUSPENDED)$")


@router.get("/current", response_model=TenantOut)
async def current(p: Principal = Depends(require("tenant:read")), s: AsyncSession = Depends(get_session)):
    return await s.get(Tenant, p.tenant_id)


@router.get("", response_model=list[TenantOut])
async def list_tenants(p: Principal = Depends(require("tenant:manage")), s: AsyncSession = Depends(get_session)):
    return (await s.scalars(select(Tenant).order_by(Tenant.created_at))).all()


@router.post("", response_model=TenantOut, status_code=201)
async def create_tenant(body: TenantCreate, p: Principal = Depends(require("tenant:manage")),
                        s: AsyncSession = Depends(get_session)):
    t = Tenant(slug=body.slug, name=body.name, status="ACTIVE")
    s.add(t)
    await s.flush()
    admin = User(tenant_id=t.id, email=body.admin_email.lower(), full_name=body.admin_full_name,
                 password_hash=hash_password(body.admin_password))
    admin.roles = [UserRole(tenant_id=t.id, role_code="TENANT_ADMIN")]
    s.add(admin)
    await s.flush()
    await start_trial(s, t.id, body.plan_code)
    await audit.record(s, tenant_id=t.id, actor_user_id=p.user_id, action="tenant.created", entity_type="tenant",
                       entity_id=t.id, after=snapshot(t, ["slug", "name", "status"]) | {"admin_email": admin.email},
                       correlation_id=p.correlation_id, ip=p.ip)
    return t


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(tenant_id: UUID, body: TenantUpdate, p: Principal = Depends(require("tenant:manage")),
                        s: AsyncSession = Depends(get_session)):
    t = await s.get(Tenant, tenant_id)
    if t is None:
        raise AppError(404, "NOT_FOUND", "Tenant tidak ditemukan")
    if tenant_id == p.tenant_id and body.status == "SUSPENDED":
        raise AppError(400, "INVALID", "Tenant platform tidak bisa disuspend")
    before = snapshot(t, ["name", "status"])
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(t, k, v)
    await audit.record(s, tenant_id=t.id, actor_user_id=p.user_id, action="tenant.updated", entity_type="tenant",
                       entity_id=t.id, before=before, after=snapshot(t, ["name", "status"]),
                       correlation_id=p.correlation_id, ip=p.ip)
    return t
