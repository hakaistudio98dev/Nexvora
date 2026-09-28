"""Worker latar Nexvora. Jalankan: python -m app.workers.maintenance

Job yang dijalankan diatur lewat WORKER_JOBS (default: semua). Beberapa replika boleh berjalan
bersamaan — setiap job dikunci di database.
"""
import asyncio
import logging
import signal

from app.core.config import get_settings
from app.workers.jobs import configured_jobs, run_job

log = logging.getLogger("nexvora.worker")


async def run_once() -> dict:
    out: dict = {}
    for name in configured_jobs():
        out |= await run_job(name)
    return out


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
    jobs = configured_jobs()
    log.info("worker mulai: job=%s interval=%ss", ",".join(jobs), interval)
    while not stop.is_set():
        for name in jobs:
            if stop.is_set():
                break
            try:
                r = await run_job(name)
                if any(v for k, v in r.items() if k not in ("job", "skipped") and v):
                    log.info("%s", r)
            except Exception:  # noqa: BLE001
                log.exception("job %s gagal", name)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
