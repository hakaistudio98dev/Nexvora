import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.db import get_session
from app.core.deps import API_KEY_SCOPES, Principal, get_human, require
from app.core.errors import AppError
from app.core.security import hash_token
from app.models import ApiKey

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class KeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100, description="Mis. 'Integrasi Shopee' atau 'Website toko'")
    scopes: list[str] = Field(min_length=1)
    expires_in_days: int | None = Field(default=365, ge=1, le=3650)


def _out(k: ApiKey) -> dict:
    return {"id": str(k.id), "name": k.name, "hint": f"nxk_…{k.prefix}", "scopes": k.scopes,
            "created_at": k.created_at, "last_used_at": k.last_used_at, "expires_at": k.expires_at,
            "revoked_at": k.revoked_at, "active": k.revoked_at is None and
            (k.expires_at is None or k.expires_at > datetime.now(UTC))}


@router.get("/scopes")
async def scopes(p: Principal = Depends(require("apikey:manage"))):
    return [x for x in API_KEY_SCOPES if p.can(x)]


@router.get("")
async def list_keys(p: Principal = Depends(require("apikey:manage")), s: AsyncSession = Depends(get_session)):
    return [_out(k) for k in (await s.scalars(select(ApiKey).where(ApiKey.tenant_id == p.tenant_id)
                                              .order_by(ApiKey.created_at.desc()))).all()]


@router.post("", status_code=201)
async def create_key(body: KeyCreate, p: Principal = Depends(require("apikey:manage")),
                     _h: Principal = Depends(get_human), s: AsyncSession = Depends(get_session)):
    bad = [x for x in body.scopes if x not in API_KEY_SCOPES or not p.can(x)]
    if bad:
        raise AppError(422, "INVALID_SCOPE", f"Izin tidak boleh diberikan ke API key: {', '.join(bad)}")
    secret = secrets.token_urlsafe(32)
    raw = f"nxk_{p.tenant_id.hex}_{secret}"
    k = ApiKey(tenant_id=p.tenant_id, name=body.name, prefix=secret[-6:], key_hash=hash_token(raw),
               scopes=sorted(set(body.scopes)), created_by=p.user_id,
               expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    s.add(k)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="api_key.created",
                       entity_type="api_key", entity_id=k.id, after={"name": k.name, "scopes": k.scopes},
                       correlation_id=p.correlation_id, ip=p.ip)
    # Rahasia hanya ditampilkan SEKALI; server hanya menyimpan hash-nya
    return {**_out(k), "key": raw}


@router.post("/{key_id}/revoke")
async def revoke(key_id: UUID, p: Principal = Depends(require("apikey:manage")), s: AsyncSession = Depends(get_session)):
    k = await s.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == p.tenant_id).with_for_update())
    if k is None:
        raise AppError(404, "NOT_FOUND", "API key tidak ditemukan")
    if k.revoked_at is None:
        k.revoked_at = datetime.now(UTC)
        await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="api_key.revoked",
                           entity_type="api_key", entity_id=k.id, after={"name": k.name},
                           correlation_id=p.correlation_id, ip=p.ip)
    return _out(k)
