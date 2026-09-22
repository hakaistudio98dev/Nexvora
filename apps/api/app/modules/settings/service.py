from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TenantSettings


async def get(s: AsyncSession, tenant_id: UUID) -> TenantSettings:
    row = await s.get(TenantSettings, tenant_id)
    if row is None:
        await s.execute(pg_insert(TenantSettings).values(tenant_id=tenant_id).on_conflict_do_nothing())
        row = await s.get(TenantSettings, tenant_id)
    return row
