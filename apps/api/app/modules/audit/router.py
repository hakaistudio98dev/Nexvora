from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.schemas import ORM
from app.models import AuditLog

router = APIRouter(prefix="/audit-logs", tags=["audit"])


class AuditOut(ORM):
    id: int
    actor_user_id: UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    before: dict | None
    after: dict | None
    correlation_id: str | None
    ip: str | None
    created_at: datetime


@router.get("", response_model=list[AuditOut])
async def list_audit(entity_type: str | None = Query(None, max_length=60), action: str | None = Query(None, max_length=80),
                     before_id: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=200),
                     p: Principal = Depends(require("audit:read")), s: AsyncSession = Depends(get_session)):
    stmt = select(AuditLog).where(AuditLog.tenant_id == p.tenant_id).order_by(AuditLog.id.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)  # keyset pagination
    return (await s.scalars(stmt)).all()
