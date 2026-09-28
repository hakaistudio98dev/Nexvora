"""Job worker.

Setiap job dikunci dengan advisory lock PostgreSQL, jadi beberapa replika worker boleh berjalan
bersamaan tanpa mengerjakan pekerjaan yang sama dua kali. Pilih job lewat WORKER_JOBS.
"""
import json
import time
import zlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.config import get_settings
from app.core.db import SessionLocal, engine, set_tenant_context
from app.models import OutboxEvent
from app.modules.billing.service import run_lifecycle
from app.modules.notifications import service as notif
from app.modules.orders.service import expire_reservations
from app.modules.shipping.service import poll_all

BACKOFF = [30, 120, 600, 3600]
_PUBLISHER = None  # dipakai tes / adapter lain; None = pakai konfigurasi EVENT_BUS


def now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ job: maintenance
async def run_maintenance(s: AsyncSession) -> dict:
    expired = await expire_reservations(s)
    purged = (await s.execute(text("DELETE FROM idempotency_keys WHERE expires_at < now()"))).rowcount
    billing = await run_lifecycle(s)
    tracked = await poll_all(s)
    notified = await notif.scan_all(s)
    delivered = await notif.deliver_due(s)
    metrics.JOB_ITEMS.labels("maintenance", "notifications").inc(notified)
    metrics.JOB_ITEMS.labels("maintenance", "deliveries").inc(delivered["sent"])
    reallocated = await _retry_holds(s)
    return {"expired_orders": expired, "purged_keys": purged, "billing": billing, "tracking_events": tracked,
            "notifications": notified, "deliveries": delivered, "orders_reallocated": reallocated}


# ------------------------------------------------------------------ job: dispatcher (outbox)
async def publish(event: OutboxEvent, tenant_slug: str) -> None:
    """Kirim event ke antrean luar. Default 'none' (hanya webhook internal); 'redis' memakai
    Redis Stream sehingga sistem lain bisa ikut mendengarkan tanpa menyentuh database."""
    st = get_settings()
    if _PUBLISHER is not None:
        await _PUBLISHER(event, tenant_slug)
        return
    if st.event_bus == "redis" and st.redis_url:
        import redis.asyncio as redis  # noqa: PLC0415
        r = redis.from_url(st.redis_url)
        await r.xadd(st.event_stream, {"id": str(event.id), "tenant": tenant_slug, "event": event.event_type,
                                       "aggregate": f"{event.aggregate_type}:{event.aggregate_id}",
                                       "payload": json.dumps(event.payload, default=str)},
                     maxlen=st.event_stream_maxlen, approximate=True)


async def run_dispatcher(s: AsyncSession, limit: int = 200) -> dict:
    rows = (await s.scalars(select(OutboxEvent).where(OutboxEvent.status == "PENDING", OutboxEvent.available_at <= now())
                            .order_by(OutboxEvent.id).limit(limit).with_for_update(skip_locked=True))).all()
    slugs = dict((await s.execute(text("SELECT id, slug FROM tenants"))).all())
    sent = failed = 0
    for e in rows:
        e.attempts += 1
        try:
            await publish(e, slugs.get(e.tenant_id, ""))
            # Webhook pelanggan untuk perubahan status order dikirim dari event ini, bukan dari
            # pemindaian berkala, supaya urutannya sama persis dengan yang terjadi di sistem.
            if e.event_type == "order.status_changed":
                await set_tenant_context(s, e.tenant_id)
                await notif.notify(s, e.tenant_id, "ORDER_STATUS",
                                   f"{e.payload.get('order_number')}: {e.payload.get('to_status')}",
                                   e.payload.get("reason") or "", data=e.payload, dedup_key=f"outbox:{e.id}")
                await set_tenant_context(s, None, superadmin=True)
            e.status, e.published_at, e.last_error = "PUBLISHED", now(), None
            sent += 1
        except Exception as ex:  # noqa: BLE001
            e.last_error = str(ex)[:500] or ex.__class__.__name__
            if e.attempts > len(BACKOFF):
                e.status = "FAILED"
                failed += 1
            else:
                e.available_at = now() + timedelta(seconds=BACKOFF[e.attempts - 1])
    await s.flush()
    pending = await s.scalar(text("SELECT count(*) FROM outbox_events WHERE status = 'PENDING'")) or 0
    oldest = await s.scalar(text("SELECT min(created_at) FROM outbox_events WHERE status = 'PENDING'"))
    metrics.OUTBOX_PENDING.set(pending)
    metrics.OUTBOX_LAG.set((now() - oldest).total_seconds() if oldest else 0)
    metrics.NOTIF_PENDING.set(await s.scalar(text("SELECT count(*) FROM notification_deliveries WHERE status = 'PENDING'")) or 0)
    metrics.JOB_ITEMS.labels("dispatcher", "events").inc(sent)
    return {"published": sent, "failed": failed, "pending": int(pending)}


async def _retry_holds(s: AsyncSession) -> int:
    """Jaring pengaman: order yang tertahan dicoba ulang berkala, walau stok masuk lewat jalur lain."""
    from app.core.context import Ctx  # noqa: PLC0415
    from app.modules.orders.service import retry_holds  # noqa: PLC0415
    total = 0
    tenants = (await s.execute(text("""
        SELECT DISTINCT tenant_id FROM orders WHERE stock_status = 'OUT_OF_STOCK'
          AND status IN ('CREATED','PAID') AND updated_at > now() - interval '30 days'"""))).all()
    for t in tenants:
        total += await retry_holds(s, Ctx(tenant_id=t.tenant_id), t.tenant_id, limit=50)
    return total


async def run_ai(s: AsyncSession) -> dict:
    """Hitung ulang ramalan (maksimal tiap 6 jam), cari anomali, dan ingatkan stok yang akan habis."""
    from app.modules.ai import anomaly, forecast  # noqa: PLC0415
    from app.modules.notifications.service import notify  # noqa: PLC0415

    last = await s.scalar(text("SELECT beat_at FROM system_heartbeats WHERE name = 'ai_forecast'"))
    skus = 0
    if last is None or (now() - last).total_seconds() > 6 * 3600:
        skus = await forecast.rebuild(s)
        await s.execute(text("""
            INSERT INTO system_heartbeats(name, beat_at, info) VALUES ('ai_forecast', now(), CAST(:i AS jsonb))
            ON CONFLICT (name) DO UPDATE SET beat_at = now(), info = EXCLUDED.info
        """), {"i": json.dumps({"skus": skus})})
    anomalies = await anomaly.scan(s)

    # Peringatan stok akan habis — hanya untuk tenant yang paketnya memuat fitur AI
    warned = 0
    tenants = (await s.execute(text("""
        SELECT sub.tenant_id, ts.lead_time_days, ts.service_level, ts.cover_days
        FROM subscriptions sub JOIN plans pl ON pl.code = sub.plan_code
        LEFT JOIN tenant_settings ts ON ts.tenant_id = sub.tenant_id
        WHERE pl.features ? 'ai' AND sub.status IN ('TRIALING','ACTIVE','PAST_DUE')
    """))).all()
    for t in tenants:
        items = await forecast.stockout_risk(s, t.tenant_id, None, lead_time=t.lead_time_days or 7,
                                             service_level=float(t.service_level or 0.95),
                                             cover_days=t.cover_days or 30, limit=20)
        for it in items:
            if it["risk"] not in ("habis", "kritis"):
                break
            if await notify(s, t.tenant_id, "AI_STOCKOUT_RISK",
                            f"{it['sku_code']} diperkirakan habis dalam {it['days_until_out']} hari",
                            f"Stok tersedia {it['available']} di {it['warehouse']}, rata-rata terjual "
                            f"{it['daily_rate']}/hari. Saran pesan {it['suggested_order']} unit sekarang.",
                            data={k: v for k, v in it.items() if k != "generated_at"}, link="/ai", dedup_key=f"stockout:{it['warehouse_id']}:{it['sku_id']}:{now():%Y%m%d}"):
                warned += 1
    metrics.JOB_ITEMS.labels("ai", "anomalies").inc(anomalies)
    return {"forecast_skus": skus, "anomalies": anomalies, "stockout_warnings": warned}


JOBS = {"maintenance": run_maintenance, "dispatcher": run_dispatcher, "ai": run_ai}


async def run_job(name: str) -> dict:
    """Jalankan satu job bila belum ada worker lain yang memegangnya."""
    fn = JOBS[name]
    key = zlib.crc32(name.encode()) & 0x7FFFFFFF
    start = time.perf_counter()
    async with SessionLocal() as s, s.begin():
        got = await s.scalar(text("SELECT pg_try_advisory_xact_lock(4242, :k)"), {"k": key})
        if not got:
            metrics.JOB_RUNS.labels(name, "skipped").inc()
            return {"job": name, "skipped": True}
        await set_tenant_context(s, None, superadmin=True)
        try:
            result = await fn(s)
        except Exception:
            metrics.JOB_RUNS.labels(name, "error").inc()
            raise
        await s.execute(text("""
            INSERT INTO system_heartbeats(name, beat_at, info) VALUES (:n, now(), CAST(:i AS jsonb))
            ON CONFLICT (name) DO UPDATE SET beat_at = now(), info = EXCLUDED.info
        """), {"n": "worker", "i": json.dumps({"job": name, **{k: v for k, v in result.items() if k != "billing"}},
                                              default=str)})
    metrics.JOB_RUNS.labels(name, "ok").inc()
    metrics.JOB_SECONDS.labels(name).observe(time.perf_counter() - start)
    metrics.DB_POOL.labels("primary").set(engine.pool.checkedout())
    return {"job": name, **result}


def configured_jobs() -> list[str]:
    names = [x.strip() for x in get_settings().worker_jobs.split(",") if x.strip()]
    unknown = [x for x in names if x not in JOBS]
    if unknown:
        raise RuntimeError(f"WORKER_JOBS tidak dikenal: {', '.join(unknown)}. Pilihan: {', '.join(JOBS)}")
    return names
