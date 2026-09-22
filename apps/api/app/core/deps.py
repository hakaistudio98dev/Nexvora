from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import entitlements, ratelimit
from app.core.db import get_session, set_tenant_context
from app.core.errors import AppError
from app.core.middleware import client_ip
from app.core.security import decode_access_token, hash_token
from app.models import ApiKey, RolePermission, Tenant, User

_bearer = HTTPBearer(auto_error=False)

# Izin maksimum yang boleh diberikan ke API key (integrasi mesin, bukan manusia)
API_KEY_SCOPES = ["order:read", "order:write", "inventory:read", "inventory:write", "product:read", "product:write",
                  "warehouse:read", "wms:read", "shipping:read", "returns:read"]


@dataclass
class Principal:
    user_id: UUID | None
    tenant_id: UUID
    email: str
    roles: list[str]
    permissions: set[str] = field(default_factory=set)
    superadmin: bool = False
    correlation_id: str | None = None
    ip: str | None = None
    api_key_id: UUID | None = None
    entitlement: entitlements.Entitlement | None = None

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    @property
    def is_human(self) -> bool:
        return self.api_key_id is None


async def _from_api_key(request: Request, raw: str, session: AsyncSession) -> Principal:
    bad = AppError(401, "INVALID_API_KEY", "API key tidak valid, kedaluwarsa, atau sudah dicabut")
    # Format: nxk_<tenant-uuid-hex>_<rahasia>  — prefix tenant untuk memasang konteks RLS sebelum lookup
    parts = raw.split("_", 2)
    if len(parts) != 3 or parts[0] != "nxk":
        raise bad
    try:
        tenant_id = UUID(hex=parts[1])
    except ValueError:
        raise bad from None
    await set_tenant_context(session, tenant_id)
    key = await session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_token(raw)))
    now = datetime.now(UTC)
    if key is None or key.revoked_at or (key.expires_at and key.expires_at <= now) or key.tenant_id != tenant_id:
        raise bad
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "ACTIVE":
        raise bad
    await ratelimit.hit(f"apikey:{key.id}", 1200)
    if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=5):
        key.last_used_at = now
    ent = await entitlements.load(session, tenant_id, superadmin=False)
    if "api_keys" not in ent.features:
        raise AppError(402, "FEATURE_NOT_IN_PLAN", "Paket saat ini tidak mencakup API key integrasi")
    return Principal(user_id=None, tenant_id=tenant_id, email=f"api-key:{key.name}", roles=["API_KEY"],
                     permissions=set(key.scopes) & set(API_KEY_SCOPES), api_key_id=key.id, entitlement=ent,
                     correlation_id=getattr(request.state, "correlation_id", None), ip=client_ip(request))


async def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    api_key = request.headers.get("x-api-key")
    if api_key:
        return await _from_api_key(request, api_key.strip(), session)

    unauthorized = AppError(401, "UNAUTHORIZED", "Sesi tidak valid atau sudah berakhir")
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized
    try:
        claims = decode_access_token(creds.credentials)
        user_id, tenant_id = UUID(claims["sub"]), UUID(claims["tid"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise unauthorized from None

    # Konteks RLS dipasang SEBELUM query apa pun
    await set_tenant_context(session, tenant_id, superadmin=bool(claims.get("sa")))

    user = await session.get(User, user_id)
    tenant = await session.get(Tenant, tenant_id)
    if (user is None or not user.is_active or user.tenant_id != tenant_id
            or user.token_version != claims.get("tv") or tenant is None or tenant.status != "ACTIVE"):
        raise unauthorized

    # Peran & izin diambil dari DB (bukan dari klaim token) → perubahan role langsung berlaku
    roles = user.role_codes
    superadmin = "SUPER_ADMIN" in roles
    if bool(claims.get("sa")) != superadmin:
        raise unauthorized
    perms = set((await session.scalars(
        select(RolePermission.permission_code).where(RolePermission.role_code.in_(roles))
    )).all())

    return Principal(user_id=user.id, tenant_id=tenant_id, email=user.email, roles=roles,
                     permissions=perms, superadmin=superadmin,
                     entitlement=await entitlements.load(session, tenant_id, superadmin=superadmin),
                     correlation_id=getattr(request.state, "correlation_id", None), ip=client_ip(request))


def require(*perms: str):
    async def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if not all(p.can(x) for x in perms):
            raise AppError(403, "FORBIDDEN", "Anda tidak memiliki izin untuk aksi ini")
        for x in perms:
            entitlements.check_permission(p.entitlement, x)
        return p
    return _dep


async def get_human(p: Principal = Depends(get_principal)) -> Principal:
    if not p.is_human:
        raise AppError(403, "HUMAN_ONLY", "Endpoint ini hanya untuk pengguna yang login, bukan API key")
    return p
