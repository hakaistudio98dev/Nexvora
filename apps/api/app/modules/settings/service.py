from dataclasses import dataclass
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


@dataclass(frozen=True)
class Defaults:
    sla_ship_hours: int = 24
    sla_risk_hours: int = 4
    low_stock_threshold: int = 5
    timezone: str = "Asia/Jakarta"
    lead_time_days: int = 7
    service_level: float = 0.95
    cover_days: int = 30


async def read(s: AsyncSession, tenant_id: UUID) -> TenantSettings | Defaults:
    """Versi baca-saja: tidak membuat baris baru, supaya aman dipakai di sesi read-only/replika."""
    return await s.get(TenantSettings, tenant_id) or Defaults()
