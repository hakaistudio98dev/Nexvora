"""Metrik Prometheus: satu tempat untuk angka yang dipakai alert & dashboard."""
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

REGISTRY = CollectorRegistry(auto_describe=True)

REQUESTS = Counter("nexvora_http_requests_total", "Jumlah request HTTP", ["method", "route", "status"], registry=REGISTRY)
LATENCY = Histogram("nexvora_http_request_seconds", "Durasi request HTTP", ["method", "route"], registry=REGISTRY,
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10))
JOB_RUNS = Counter("nexvora_worker_job_total", "Eksekusi job worker", ["job", "result"], registry=REGISTRY)
JOB_SECONDS = Histogram("nexvora_worker_job_seconds", "Durasi job worker", ["job"], registry=REGISTRY,
                        buckets=(0.1, 0.5, 1, 5, 15, 60))
JOB_ITEMS = Counter("nexvora_worker_items_total", "Item yang diproses worker", ["job", "kind"], registry=REGISTRY)
OUTBOX_PENDING = Gauge("nexvora_outbox_pending", "Event outbox menunggu dikirim", registry=REGISTRY)
OUTBOX_LAG = Gauge("nexvora_outbox_lag_seconds", "Umur event outbox tertua yang belum dikirim", registry=REGISTRY)
NOTIF_PENDING = Gauge("nexvora_notification_pending", "Notifikasi menunggu dikirim", registry=REGISTRY)
DB_POOL = Gauge("nexvora_db_pool_in_use", "Koneksi database yang sedang dipakai", ["pool"], registry=REGISTRY)


def render() -> bytes:
    return generate_latest(REGISTRY)


def route_of(request: Request) -> str:
    """Pakai pola rute (/orders/{order_id}) agar label metrik tidak meledak jumlahnya."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "other"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = route_of(request)
            if route != "/metrics":
                REQUESTS.labels(request.method, route, str(status)).inc()
                LATENCY.labels(request.method, route).observe(time.perf_counter() - start)
