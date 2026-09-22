from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, ratelimit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, get_human
from app.core.errors import AppError
from app.core.middleware import client_ip
from app.core.security import hash_password, verify_password
from app.models import Tenant, User
from app.modules.auth import service
from app.modules.auth.schemas import ChangePasswordIn, LoginIn, MeOut, RefreshIn, TenantBrief, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _unauthorized(request: Request, message: str = "Kode workspace, email, atau password salah") -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": {
        "code": "INVALID_CREDENTIALS", "message": message,
        "correlation_id": getattr(request.state, "correlation_id", None)}})


@router.post("/login", response_model=TokenOut, responses={401: {}, 429: {}})
async def login(body: LoginIn, request: Request, session: AsyncSession = Depends(get_session)):
    limit = get_settings().login_rate_limit_per_minute
    await ratelimit.hit(f"login-ip:{client_ip(request)}", limit)
    await ratelimit.hit(f"login-acct:{body.tenant.lower()}:{body.email.lower()}", limit)
    try:
        return await service.login(session, request, tenant_slug=body.tenant.lower(),
                                   email=body.email, password=body.password)
    except service.AuthFailed:
        return _unauthorized(request)


@router.post("/refresh", response_model=TokenOut, responses={401: {}})
async def refresh(body: RefreshIn, request: Request, session: AsyncSession = Depends(get_session)):
    await ratelimit.hit(f"refresh-ip:{client_ip(request)}", 60)
    try:
        return await service.refresh(session, request, body.refresh_token)
    except service.AuthFailed:
        return _unauthorized(request, "Sesi berakhir, silakan login kembali")


@router.post("/logout", status_code=204)
async def logout(body: RefreshIn, session: AsyncSession = Depends(get_session)):
    await service.logout(session, body.refresh_token)
    return Response(status_code=204)


@router.post("/logout-all", status_code=204)
async def logout_all(p: Principal = Depends(get_human), session: AsyncSession = Depends(get_session)):
    user = await session.get(User, p.user_id)
    await service.revoke_all_sessions(session, user)
    await audit.record(session, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="auth.logout_all",
                       entity_type="user", entity_id=p.user_id, correlation_id=p.correlation_id, ip=p.ip)
    return Response(status_code=204)


@router.get("/me", response_model=MeOut)
async def me(p: Principal = Depends(get_human), session: AsyncSession = Depends(get_session)):
    user = await session.get(User, p.user_id)
    tenant = await session.get(Tenant, p.tenant_id)
    return MeOut(id=user.id, email=user.email, full_name=user.full_name, roles=p.roles,
                 permissions=sorted(p.permissions),
                 tenant=TenantBrief(id=tenant.id, slug=tenant.slug, name=tenant.name),
                 subscription=p.entitlement.as_dict())


@router.post("/change-password", status_code=204)
async def change_password(body: ChangePasswordIn, p: Principal = Depends(get_human),
                          session: AsyncSession = Depends(get_session)):
    user = await session.get(User, p.user_id)
    if not verify_password(body.current_password, user.password_hash):
        raise AppError(400, "WRONG_PASSWORD", "Password saat ini salah")
    user.password_hash = hash_password(body.new_password)
    await service.revoke_all_sessions(session, user)
    await audit.record(session, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="auth.password_changed",
                       entity_type="user", entity_id=p.user_id, correlation_id=p.correlation_id, ip=p.ip)
    return Response(status_code=204)


