from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog

_REDACT = {"password", "password_hash", "token_hash"}


def _clean(d: dict | None) -> dict | None:
    if d is None:
        return None
    return {k: ("[REDACTED]" if k in _REDACT else v) for k, v in d.items()}


async def record(session: AsyncSession, *, tenant_id: UUID | None, actor_user_id: UUID | None,
                 action: str, entity_type: str, entity_id: Any = None,
                 before: dict | None = None, after: dict | None = None,
                 correlation_id: str | None = None, ip: str | None = None) -> None:
    session.add(AuditLog(
        tenant_id=tenant_id, actor_user_id=actor_user_id, action=action,
        entity_type=entity_type, entity_id=str(entity_id) if entity_id is not None else None,
        before=_clean(before), after=_clean(after), correlation_id=correlation_id, ip=ip,
    ))
