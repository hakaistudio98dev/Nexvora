from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.config import get_settings
from app.core.db import set_tenant_context
from app.core.middleware import client_ip
from app.core.security import (create_access_token, hash_password, hash_token, needs_rehash,
                               new_refresh_token, verify_password)
from app.models import RefreshToken, User


class AuthFailed(Exception):
    """Login/refresh gagal. Ditangani router dengan respons 401 generik (transaksi tetap di-commit
    agar penghitung gagal-login & pencabutan token tersimpan)."""


def _now() -> datetime:
    return datetime.now(UTC)


async def resolve_tenant(session: AsyncSession, slug: str) -> tuple[UUID, str] | None:
    row = (await session.execute(text("SELECT id, status FROM resolve_tenant_by_slug(:s)"), {"s": slug})).first()
    return (row.id, row.status) if row else None


async def issue_tokens(session: AsyncSession, user: User, request: Request,
                       family_id: UUID | None = None) -> tuple[dict, RefreshToken]:
    s = get_settings()
    roles = user.role_codes
    access, ttl = create_access_token(user_id=user.id, tenant_id=user.tenant_id, roles=roles,
                                      token_version=user.token_version, superadmin="SUPER_ADMIN" in roles)
    raw, digest = new_refresh_token(user.tenant_id)
    rt = RefreshToken(
        id=uuid4(), tenant_id=user.tenant_id, user_id=user.id, family_id=family_id or uuid4(),
        token_hash=digest, expires_at=_now() + timedelta(days=s.refresh_token_ttl_days),
        user_agent=(request.headers.get("user-agent") or "")[:300], ip=client_ip(request),
    )
    session.add(rt)
    await session.flush()
    return {
        "access_token": access, "expires_in": ttl,
        "refresh_token": raw, "refresh_expires_in": s.refresh_token_ttl_days * 86400,
    }, rt


async def login(session: AsyncSession, request: Request, *, tenant_slug: str, email: str, password: str) -> dict:
    s = get_settings()
    cid, ip = getattr(request.state, "correlation_id", None), client_ip(request)
    resolved = await resolve_tenant(session, tenant_slug)
    if resolved is None or resolved[1] != "ACTIVE":
        verify_password(password, None)  # samakan waktu respons
        raise AuthFailed

    tenant_id = resolved[0]
    await set_tenant_context(session, tenant_id)
    user = await session.scalar(select(User).where(User.email == email.lower()).with_for_update())

    if user is None:
        verify_password(password, None)
        await audit.record(session, tenant_id=tenant_id, actor_user_id=None, action="auth.login_failed",
                           entity_type="user", after={"email": email.lower(), "reason": "unknown_user"},
                           correlation_id=cid, ip=ip)
        raise AuthFailed

    locked = user.locked_until is not None and user.locked_until > _now()
    ok = verify_password(password, user.password_hash)
    if locked or not ok or not user.is_active:
        if not locked and not ok:
            user.failed_login_count += 1
            if user.failed_login_count >= s.max_failed_logins:
                user.locked_until = _now() + timedelta(minutes=s.lockout_minutes)
                user.failed_login_count = 0
        reason = "locked" if locked else ("inactive" if ok else "bad_password")
        await audit.record(session, tenant_id=tenant_id, actor_user_id=user.id, action="auth.login_failed",
                           entity_type="user", entity_id=user.id, after={"reason": reason},
                           correlation_id=cid, ip=ip)
        raise AuthFailed

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _now()
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    tokens, _ = await issue_tokens(session, user, request)
    await audit.record(session, tenant_id=tenant_id, actor_user_id=user.id, action="auth.login",
                       entity_type="user", entity_id=user.id, correlation_id=cid, ip=ip)
    return tokens


async def refresh(session: AsyncSession, request: Request, raw: str) -> dict:
    cid, ip = getattr(request.state, "correlation_id", None), client_ip(request)
    try:
        tenant_id = UUID(raw.split(".", 1)[0])
    except ValueError:
        raise AuthFailed from None
    await set_tenant_context(session, tenant_id)

    rt = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
                              .with_for_update())
    if rt is None:
        raise AuthFailed
    if rt.revoked_at is not None:
        # Token lama dipakai ulang → kemungkinan dicuri. Cabut seluruh keluarga sesi.
        await session.execute(update(RefreshToken).where(RefreshToken.family_id == rt.family_id,
                                                         RefreshToken.revoked_at.is_(None))
                              .values(revoked_at=_now()))
        await audit.record(session, tenant_id=tenant_id, actor_user_id=rt.user_id,
                           action="auth.refresh_reuse_detected", entity_type="session",
                           entity_id=rt.family_id, correlation_id=cid, ip=ip)
        raise AuthFailed
    if rt.expires_at <= _now():
        raise AuthFailed

    user = await session.get(User, rt.user_id)
    if user is None or not user.is_active:
        raise AuthFailed
    from app.models import Tenant  # noqa: PLC0415
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "ACTIVE":
        raise AuthFailed

    tokens, new_rt = await issue_tokens(session, user, request, family_id=rt.family_id)
    rt.revoked_at = _now()
    rt.replaced_by = new_rt.id
    return tokens


async def logout(session: AsyncSession, raw: str) -> None:
    try:
        tenant_id = UUID(raw.split(".", 1)[0])
    except ValueError:
        return
    await set_tenant_context(session, tenant_id)
    rt = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if rt is not None:
        await session.execute(update(RefreshToken).where(RefreshToken.family_id == rt.family_id,
                                                         RefreshToken.revoked_at.is_(None))
                              .values(revoked_at=_now()))


async def revoke_all_sessions(session: AsyncSession, user: User) -> None:
    user.token_version += 1  # access token lama langsung tidak berlaku
    await session.execute(update(RefreshToken).where(RefreshToken.user_id == user.id,
                                                     RefreshToken.revoked_at.is_(None))
                          .values(revoked_at=_now()))
