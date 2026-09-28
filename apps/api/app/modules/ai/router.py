from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import entitlements
from app.core.db import get_session
from app.core.deps import Principal, get_read_db, require
from app.core.errors import AppError
from app.models import Anomaly
from app.modules.ai import anomaly, courier, forecast
from app.modules.settings import service as settings_svc

router = APIRouter(prefix="/ai", tags=["ai"])


def _gate(p: Principal) -> None:
    entitlements.require_feature(p, "ai")


async def _params(s: AsyncSession, tenant_id: UUID) -> dict:
    st = await settings_svc.read(s, tenant_id)   # read-only: aman di sesi replika
    return {"lead_time": st.lead_time_days, "service_level": float(st.service_level), "cover_days": st.cover_days}


def _anom_out(a: Anomaly) -> dict:
    return {"id": str(a.id), "kind": a.kind, "kind_label": anomaly.KINDS.get(a.kind, a.kind), "severity": a.severity,
            "title": a.title, "detail": a.detail, "score": float(a.score), "entity_type": a.entity_type,
            "entity_id": str(a.entity_id) if a.entity_id else None, "entity_label": a.entity_label, "data": a.data,
            "status": a.status, "created_at": a.created_at, "decided_at": a.decided_at}


@router.get("/stockout-risk")
async def stockout_risk(warehouse_id: UUID | None = None, p: Principal = Depends(require("ai:read")),
                        s: AsyncSession = Depends(get_read_db)):
    """Perkiraan kapan setiap SKU habis, dan berapa yang sebaiknya dipesan."""
    _gate(p)
    prm = await _params(s, p.tenant_id)
    items = await forecast.stockout_risk(s, p.tenant_id, warehouse_id, **prm)
    counts = {k: sum(1 for x in items if x["risk"] == k) for k in ("habis", "kritis", "waspada", "aman")}
    return {"settings": prm, "counts": counts, "items": items}


@router.get("/demand")
async def demand(warehouse_id: UUID, sku_id: UUID, horizon: int = Query(14, ge=1, le=90),
                 p: Principal = Depends(require("ai:read")), s: AsyncSession = Depends(get_read_db)):
    """Penjualan 30 hari terakhir + ramalan ke depan untuk satu SKU."""
    _gate(p)
    out = await forecast.daily_forecast(s, p.tenant_id, warehouse_id, sku_id, horizon)
    if out is None:
        raise AppError(404, "NOT_FOUND", "Ramalan untuk SKU ini belum tersedia; tunggu perhitungan berikutnya")
    return out


@router.get("/courier")
async def courier_recommendation(city: str | None = Query(None, max_length=100), order_id: UUID | None = None,
                                 p: Principal = Depends(require("ai:read")), s: AsyncSession = Depends(get_read_db)):
    """Kurir mana yang paling bisa diandalkan untuk kota tujuan ini."""
    _gate(p)
    if order_id:
        return await courier.recommend_for_order(s, p.tenant_id, order_id)
    return await courier.recommend(s, p.tenant_id, city)


@router.get("/anomalies")
async def list_anomalies(status: str = Query("OPEN", pattern="^(OPEN|ACK|DISMISSED|ALL)$"),
                         limit: int = Query(100, ge=1, le=300), p: Principal = Depends(require("ai:read")),
                         s: AsyncSession = Depends(get_session)):
    _gate(p)
    stmt = select(Anomaly).where(Anomaly.tenant_id == p.tenant_id).order_by(Anomaly.created_at.desc()).limit(limit)
    if status != "ALL":
        stmt = stmt.where(Anomaly.status == status)
    return [_anom_out(a) for a in (await s.scalars(stmt)).all()]


class DecideIn(BaseModel):
    status: str = Field(pattern="^(ACK|DISMISSED)$")


@router.post("/anomalies/{anomaly_id}/decide")
async def decide(anomaly_id: UUID, body: DecideIn, p: Principal = Depends(require("ai:manage")),
                 s: AsyncSession = Depends(get_session)):
    _gate(p)
    a = await s.scalar(select(Anomaly).where(Anomaly.id == anomaly_id, Anomaly.tenant_id == p.tenant_id)
                       .with_for_update())
    if a is None:
        raise AppError(404, "NOT_FOUND", "Temuan tidak ditemukan")
    a.status, a.decided_by, a.decided_at = body.status, p.user_id, datetime.now(UTC)
    await s.flush()
    return _anom_out(a)


@router.post("/rebuild")
async def rebuild(p: Principal = Depends(require("ai:manage")), s: AsyncSession = Depends(get_session)):
    """Hitung ulang ramalan sekarang, tanpa menunggu jadwal worker."""
    _gate(p)
    return {"skus": await forecast.rebuild(s, p.tenant_id)}


@router.get("/summary")
async def summary(p: Principal = Depends(require("ai:read")), s: AsyncSession = Depends(get_read_db)):
    """Ringkasan untuk Beranda: berapa yang berisiko habis dan berapa temuan yang belum ditindak."""
    _gate(p)
    prm = await _params(s, p.tenant_id)
    items = await forecast.stockout_risk(s, p.tenant_id, None, **prm)
    urgent = [x for x in items if x["risk"] in ("habis", "kritis")]
    open_anom = (await s.scalars(select(Anomaly).where(Anomaly.tenant_id == p.tenant_id, Anomaly.status == "OPEN")
                                 .order_by(Anomaly.created_at.desc()).limit(5))).all()
    return {"stockout_urgent": len(urgent), "stockout_watch": sum(1 for x in items if x["risk"] == "waspada"),
            "top_reorder": [{"sku_code": x["sku_code"], "warehouse": x["warehouse"], "days_until_out": x["days_until_out"],
                             "suggested_order": x["suggested_order"]} for x in urgent[:5]],
            "anomalies_open": len(open_anom), "anomalies": [_anom_out(a) for a in open_anom]}
