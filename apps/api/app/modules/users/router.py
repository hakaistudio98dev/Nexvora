from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, entitlements
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.core.schemas import ORM, Page
from app.core.security import hash_password, validate_password_strength
from app.core.utils import escape_like, paginate
from app.models import Role, RolePermission, User, UserRole
from app.modules.auth.service import revoke_all_sessions

router = APIRouter(tags=["users"])
ASSIGNABLE = {"TENANT_ADMIN", "WAREHOUSE_MANAGER", "WAREHOUSE_OPERATOR", "FINANCE", "CUSTOMER_SERVICE", "VIEWER"}


class UserOut(ORM):
    id: UUID
    email: str
    full_name: str
    is_active: bool
    role_codes: list[str]
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(max_length=256)
    roles: list[str] = Field(min_length=1)

    @field_validator("password")
    @classmethod
    def _strong(cls, v: str) -> str:
        return validate_password_strength(v)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    is_active: bool | None = None
    roles: list[str] | None = Field(default=None, min_length=1)


class RoleOut(BaseModel):
    code: str
    name: str
    description: str
    permissions: list[str]


def _check_roles(p: Principal, roles: list[str]) -> list[str]:
    allowed = ASSIGNABLE | ({"SUPER_ADMIN"} if p.superadmin else set())
    bad = set(roles) - allowed
    if bad:
        raise AppError(400, "INVALID_ROLE", f"Peran tidak diizinkan: {', '.join(sorted(bad))}")
    return sorted(set(roles))


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(p: Principal = Depends(require("role:read")), s: AsyncSession = Depends(get_session)):
    roles = (await s.scalars(select(Role).order_by(Role.code))).all()
    rp = (await s.execute(select(RolePermission.role_code, RolePermission.permission_code))).all()
    perms: dict[str, list[str]] = {}
    for r, perm in rp:
        perms.setdefault(r, []).append(perm)
    return [RoleOut(code=r.code, name=r.name, description=r.description, permissions=sorted(perms.get(r.code, [])))
            for r in roles]


@router.get("/users", response_model=Page[UserOut])
async def list_users(q: str | None = Query(default=None, max_length=100), limit: int = Query(50, ge=1, le=200),
                     offset: int = Query(0, ge=0), p: Principal = Depends(require("user:read")),
                     s: AsyncSession = Depends(get_session)):
    stmt = select(User).where(User.tenant_id == p.tenant_id).order_by(User.created_at)
    if q:
        like = f"%{escape_like(q.lower())}%"
        stmt = stmt.where(or_(User.email.ilike(like, escape="\\"), User.full_name.ilike(like, escape="\\")))
    items, total = await paginate(s, stmt, limit, offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, p: Principal = Depends(require("user:write")),
                      s: AsyncSession = Depends(get_session)):
    roles = _check_roles(p, body.roles)
    await entitlements.enforce_limit(s, p, "users")
    u = User(tenant_id=p.tenant_id, email=body.email.lower(), full_name=body.full_name,
             password_hash=hash_password(body.password))
    u.roles = [UserRole(tenant_id=p.tenant_id, role_code=r) for r in roles]
    s.add(u)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="user.created", entity_type="user",
                       entity_id=u.id, after={"email": u.email, "full_name": u.full_name, "roles": roles},
                       correlation_id=p.correlation_id, ip=p.ip)
    return u


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: UUID, body: UserUpdate, p: Principal = Depends(require("user:write")),
                      s: AsyncSession = Depends(get_session)):
    u = await s.scalar(select(User).where(User.id == user_id, User.tenant_id == p.tenant_id))
    if u is None:
        raise AppError(404, "NOT_FOUND", "Pengguna tidak ditemukan")
    if u.id == p.user_id and (body.is_active is False or body.roles is not None):
        raise AppError(400, "SELF_LOCKOUT", "Anda tidak bisa menonaktifkan atau mengubah peran akun sendiri")
    if "SUPER_ADMIN" in u.role_codes and not p.superadmin:
        raise AppError(403, "FORBIDDEN", "Akun Super Admin hanya bisa diubah oleh Super Admin")

    before = {"full_name": u.full_name, "is_active": u.is_active, "roles": u.role_codes}
    if body.full_name is not None:
        u.full_name = body.full_name
    if body.roles is not None:
        wanted = set(_check_roles(p, body.roles))
        current = {r.role_code: r for r in u.roles}
        for code, link in current.items():
            if code not in wanted:
                u.roles.remove(link)
        for code in sorted(wanted - current.keys()):
            u.roles.append(UserRole(tenant_id=p.tenant_id, role_code=code))
    if body.is_active is not None and body.is_active != u.is_active:
        if body.is_active:
            await entitlements.enforce_limit(s, p, "users")
        u.is_active = body.is_active
        if not u.is_active:
            await revoke_all_sessions(s, u)
    await s.flush()
    await s.refresh(u, ["roles"])
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="user.updated", entity_type="user",
                       entity_id=u.id, before=before,
                       after={"full_name": u.full_name, "is_active": u.is_active, "roles": u.role_codes},
                       correlation_id=p.correlation_id, ip=p.ip)
    return u


