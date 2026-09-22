"""Worker latar: lepas reservasi kedaluwarsa & bersihkan idempotency key lama.
Jalankan: python -m app.workers.maintenance"""
import asyncio
import logging
import signal

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant_context
from app.modules.billing.service import run_lifecycle
from app.modules.orders.service import expire_reservations
from app.modules.notifications.service import deliver_due, scan_all
from app.modules.shipping.service import poll_all

log = logging.getLogger("nexvora.worker")


async def run_once() -> dict:
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        expired = await expire_reservations(s)
        purged = (await s.execute(text("DELETE FROM idempotency_keys WHERE expires_at < now()"))).rowcount
        billing = await run_lifecycle(s)
        tracked = await poll_all(s)
        notified = await scan_all(s)
        delivered = await deliver_due(s)
        await s.execute(text("""
            INSERT INTO system_heartbeats(name, beat_at, info) VALUES ('worker', now(), CAST(:i AS jsonb))
            ON CONFLICT (name) DO UPDATE SET beat_at = now(), info = EXCLUDED.info
        """), {"i": __import__("json").dumps({"notified": notified, **delivered})})
    return {"expired_orders": expired, "purged_keys": purged, "billing": billing, "tracking_events": tracked,
            "notifications": notified, "deliveries": delivered}


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    interval = get_settings().worker_interval_seconds
    log.info("worker mulai, interval %ss", interval)
    while not stop.is_set():
        try:
            r = await run_once()
            if r["expired_orders"] or r["purged_keys"] or any(r["billing"].values()) or r["tracking_events"] \
                    or r["notifications"] or any(r["deliveries"].values()):
                log.info("maintenance: %s", r)
        except Exception:  # noqa: BLE001
            log.exception("maintenance gagal")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
