from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import entitlements
from app.core.db import get_session
from app.core.deps import Principal, require
from app.modules.analytics import service

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _filters(date_from: date | None = None, date_to: date | None = None, warehouse_id: UUID | None = None,
             channel: str | None = Query(None, max_length=20)) -> dict:
    return {"date_from": date_from, "date_to": date_to, "warehouse_id": warehouse_id, "channel": channel}


async def _p(s: AsyncSession, p: Principal, f: dict) -> dict:
    return await service.params(s, p.tenant_id, f["date_from"], f["date_to"], f["warehouse_id"], f["channel"])


@router.get("/overview")
async def overview(f: dict = Depends(_filters), p: Principal = Depends(require("analytics:read")),
                   s: AsyncSession = Depends(get_session)):
    return await service.overview(s, await _p(s, p, f))


@router.get("/timeseries")
async def timeseries(f: dict = Depends(_filters), p: Principal = Depends(require("analytics:read")),
                     s: AsyncSession = Depends(get_session)):
    return await service.timeseries(s, await _p(s, p, f))


@router.get("/sla")
async def sla(f: dict = Depends(_filters), p: Principal = Depends(require("analytics:read")),
              s: AsyncSession = Depends(get_session)):
    entitlements.require_feature(p, "analytics")
    return await service.sla_board(s, await _p(s, p, f))


@router.get("/productivity")
async def productivity(f: dict = Depends(_filters), p: Principal = Depends(require("analytics:read")),
                       s: AsyncSession = Depends(get_session)):
    entitlements.require_feature(p, "analytics")
    return await service.productivity(s, await _p(s, p, f))


@router.get("/top-skus")
async def top_skus(f: dict = Depends(_filters), limit: int = Query(20, ge=1, le=100),
                   p: Principal = Depends(require("analytics:read")), s: AsyncSession = Depends(get_session)):
    entitlements.require_feature(p, "analytics")
    return await service.top_skus(s, await _p(s, p, f), limit)


@router.get("/inventory-health")
async def inventory_health(f: dict = Depends(_filters), p: Principal = Depends(require("analytics:read")),
                           s: AsyncSession = Depends(get_session)):
    entitlements.require_feature(p, "analytics")
    return await service.inventory_health(s, await _p(s, p, f))


@router.get("/noc")
async def noc(p: Principal = Depends(require("analytics:read")), s: AsyncSession = Depends(get_session)):
    """Kesehatan operasional & integrasi workspace (PRD §13.3)."""
    entitlements.require_feature(p, "analytics")
    q = lambda sql, **kw: s.execute(text(sql), {"t": p.tenant_id, **kw})  # noqa: E731
    now = datetime.now(UTC)
    hb = (await q("SELECT beat_at, info FROM system_heartbeats WHERE name = 'worker'")).first()
    worker_age = (now - hb.beat_at).total_seconds() if hb else None
    couriers = (await q("""
        SELECT a.name, a.provider, a.is_active,
               count(sh.id) FILTER (WHERE sh.status IN ('HANDED_OVER','IN_TRANSIT','OUT_FOR_DELIVERY')) active,
               count(sh.id) FILTER (WHERE sh.status IN ('HANDED_OVER','IN_TRANSIT','OUT_FOR_DELIVERY')
                 AND COALESCE(sh.last_tracked_at, sh.handed_over_at) < now() - interval '48 hours') stale,
               max(sh.last_tracked_at) last_tracked
        FROM courier_accounts a LEFT JOIN shipments sh ON sh.courier_account_id = a.id
        WHERE a.tenant_id = :t GROUP BY a.id, a.name, a.provider, a.is_active ORDER BY a.created_at""")).all()
    problems = (await q("""SELECT count(*) FROM shipments WHERE tenant_id = :t AND status IN ('FAILED_DELIVERY','RETURNED_TO_SENDER')
                           AND updated_at > now() - interval '7 days'""")).scalar()
    st = await service.params(s, p.tenant_id, None, None, None, None)
    board = await service.sla_board(s, st)
    exc = (await q("SELECT count(*) FROM wms_exceptions WHERE tenant_id = :t AND status = 'OPEN'")).scalar()
    hold = (await q("""SELECT count(*) FROM orders WHERE tenant_id = :t AND stock_status = 'OUT_OF_STOCK'
                       AND status IN ('CREATED','PAID')""")).scalar()
    deliv = (await q("""
        SELECT count(*) FILTER (WHERE status = 'PENDING') pending,
               count(*) FILTER (WHERE status = 'FAILED' AND created_at > now() - interval '24 hours') failed_24h
        FROM notification_deliveries WHERE tenant_id = :t""")).one()
    keys = (await q("""SELECT count(*) FILTER (WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())) active,
                              max(last_used_at) last_used FROM api_keys WHERE tenant_id = :t""")).one()
    channels = (await q("""SELECT channel, max(placed_at) last_order, count(*) FILTER (WHERE placed_at > now() - interval '24 hours') n24
                           FROM orders WHERE tenant_id = :t GROUP BY channel ORDER BY last_order DESC""")).all()
    checks = [
        {"key": "worker", "label": "Worker latar (reservasi, tracking, notifikasi)",
         "status": "ok" if worker_age is not None and worker_age < 120 else "critical",
         "detail": f"detak terakhir {int(worker_age)} detik lalu" if worker_age is not None else "belum pernah berjalan"},
        {"key": "sla", "label": "SLA pengiriman", "status": "critical" if board["counts"]["overdue"] else
         "warning" if board["counts"]["at_risk"] else "ok",
         "detail": f"{board['counts']['overdue']} terlambat · {board['counts']['at_risk']} berisiko"},
        {"key": "holds", "label": "Order tertahan stok", "status": "warning" if hold else "ok", "detail": f"{hold} order"},
        {"key": "exceptions", "label": "Exception gudang terbuka", "status": "warning" if exc else "ok", "detail": f"{exc} exception"},
        {"key": "shipments", "label": "Masalah pengiriman 7 hari", "status": "warning" if problems else "ok",
         "detail": f"{problems} gagal antar / dikembalikan"},
        {"key": "tracking", "label": "Tracking kurir macet (>48 jam)",
         "status": "warning" if sum(c.stale for c in couriers) else "ok",
         "detail": f"{sum(c.stale for c in couriers)} paket tanpa update"},
        {"key": "notifications", "label": "Pengiriman notifikasi", "status": "warning" if deliv.failed_24h else "ok",
         "detail": f"{deliv.pending} antre · {deliv.failed_24h} gagal (24 jam)"},
    ]
    order = {"ok": 0, "warning": 1, "critical": 2}
    return {
        "overall": max((c["status"] for c in checks), key=lambda x: order[x]),
        "checks": checks,
        "couriers": [{"name": c.name, "provider": c.provider, "is_active": c.is_active, "active": c.active,
                      "stale": c.stale, "last_tracked": c.last_tracked} for c in couriers],
        "channels": [{"channel": c.channel, "last_order": c.last_order, "orders_24h": c.n24} for c in channels],
        "api_keys": {"active": keys.active, "last_used": keys.last_used},
    }
