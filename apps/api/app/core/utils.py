from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select


async def paginate(session: AsyncSession, stmt: Select, limit: int, offset: int) -> tuple[list[Any], int]:
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = (await session.scalars(stmt.limit(limit).offset(offset))).all()
    return list(rows), int(total or 0)


def snapshot(obj: Any, fields: list[str]) -> dict:
    out = {}
    for f in fields:
        v = getattr(obj, f)
        out[f] = str(v) if v is not None and not isinstance(v, (int, float, bool, str)) else v
    return out


def escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
