from zoneinfo import available_timezones

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.db import get_session
from app.core.deps import Principal, require
from app.modules.settings import service

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsOut(BaseModel):
    sla_ship_hours: int
    sla_risk_hours: int
    low_stock_threshold: int
    timezone: str
    lead_time_days: int
    service_level: float
    cover_days: int


class SettingsIn(BaseModel):
    sla_ship_hours: int | None = Field(default=None, ge=1, le=720, description="Batas jam dari dibayar sampai diserahkan ke kurir")
    sla_risk_hours: int | None = Field(default=None, ge=0, le=168, description="Peringatan dini sebelum batas SLA")
    low_stock_threshold: int | None = Field(default=None, ge=0, le=1_000_000)
    timezone: str | None = Field(default=None, max_length=64)
    lead_time_days: int | None = Field(default=None, ge=0, le=180, description="Lama barang datang setelah dipesan ke supplier")
    service_level: float | None = Field(default=None, ge=0.5, le=0.999, description="Target tidak kehabisan stok, mis. 0.95")
    cover_days: int | None = Field(default=None, ge=1, le=365, description="Stok ingin cukup untuk berapa hari")

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):  # noqa: ANN001, ANN206
        if v is not None and v not in available_timezones():
            raise ValueError("Zona waktu tidak dikenal, mis. Asia/Jakarta, Asia/Makassar, Asia/Jayapura")
        return v


def _out(r) -> SettingsOut:  # noqa: ANN001
    return SettingsOut(sla_ship_hours=r.sla_ship_hours, sla_risk_hours=r.sla_risk_hours,
                       low_stock_threshold=r.low_stock_threshold, timezone=r.timezone,
                       lead_time_days=r.lead_time_days, service_level=float(r.service_level),
                       cover_days=r.cover_days)


@router.get("", response_model=SettingsOut)
async def get_settings(p: Principal = Depends(require("tenant:read")), s: AsyncSession = Depends(get_session)):
    return _out(await service.get(s, p.tenant_id))


@router.patch("", response_model=SettingsOut)
async def update(body: SettingsIn, p: Principal = Depends(require("settings:manage")), s: AsyncSession = Depends(get_session)):
    row = await service.get(s, p.tenant_id)
    before = _out(row).model_dump()
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="settings.updated", entity_type="tenant",
                       entity_id=p.tenant_id, before=before, after=_out(row).model_dump(),
                       correlation_id=p.correlation_id, ip=p.ip)
    return _out(row)


