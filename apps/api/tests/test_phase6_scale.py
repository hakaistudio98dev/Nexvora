import asyncio

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import ReadSessionLocal, SessionLocal, set_tenant_context
from app.workers import jobs
from app.workers.maintenance import run_once
from tests.conftest import auth
from tests.test_phase3_wms import new_order, setup_wh
from tests.test_phase4_shipping import stocked

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def sql(q, **kw):
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        r = await s.execute(text(q), kw)
        return r.all() if q.strip().upper().startswith("SELECT") else None


async def test_order_events_land_in_outbox_and_dispatch_in_order(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    tenant = (await client.get("/api/v1/auth/me", headers=h)).json()["tenant"]["id"]
    o = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1000"}])
    rows = await sql("""SELECT event_type, payload->>'to_status' st, status FROM outbox_events
                        WHERE tenant_id = :t AND aggregate_type = 'order' ORDER BY id""", t=tenant)
    assert [r.st for r in rows] == ["CREATED", "PAID", "ALLOCATED"] and all(r.status == "PENDING" for r in rows)

    seen = []
    async def publisher(event, slug):  # noqa: ANN001, ANN202
        seen.append((event.id, event.event_type, event.payload.get("to_status"), slug))
    jobs._PUBLISHER = publisher
    try:
        r = await jobs.run_job("dispatcher")
        assert r["published"] >= 3 and r["failed"] == 0
        mine = [x for x in seen if x[3] == c["slug"]]
        assert [x[2] for x in mine] == ["CREATED", "PAID", "ALLOCATED"]     # urutan terjaga
        assert mine == sorted(mine)                                          # id menaik
        left = await sql("SELECT count(*) n FROM outbox_events WHERE status = 'PENDING'")
        assert left[0].n == 0
        assert (await jobs.run_job("dispatcher"))["published"] == 0          # tidak dikirim dua kali
    finally:
        jobs._PUBLISHER = None
    assert o["status"] == "ALLOCATED"


async def test_dispatcher_retries_then_fails(client, sa_token):
    c = await setup_wh(client, sa_token)
    await new_order(client, c["h"], [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1"}])
    async def broken(event, slug):  # noqa: ANN001, ANN202
        raise RuntimeError("bus mati")
    jobs._PUBLISHER = broken
    try:
        r = await jobs.run_job("dispatcher")
        assert r["published"] == 0 and r["failed"] == 0        # dijadwalkan ulang, belum menyerah
        rows = await sql("""SELECT attempts, last_error, available_at > now() future FROM outbox_events
                            WHERE status = 'PENDING' ORDER BY id LIMIT 1""")
        assert rows[0].attempts == 1 and rows[0].last_error == "bus mati" and rows[0].future
        await sql("UPDATE outbox_events SET attempts = 9, available_at = now() WHERE status = 'PENDING'")
        r = await jobs.run_job("dispatcher")
        assert r["failed"] >= 1
        assert (await sql("SELECT count(*) n FROM outbox_events WHERE status = 'FAILED'"))[0].n >= 1
    finally:
        jobs._PUBLISHER = None
        await sql("UPDATE outbox_events SET status = 'PUBLISHED' WHERE status = 'FAILED'")


async def test_order_status_webhook_comes_from_outbox(client, sa_token, monkeypatch):
    import httpx

    from app.core import netguard
    from app.modules.notifications import service as notif
    c = await stocked(client, sa_token)
    h = c["h"]
    monkeypatch.setattr(netguard, "resolve", lambda host: ["93.184.216.34"])
    got = []
    notif._WEBHOOK_TRANSPORT = httpx.MockTransport(lambda req: (got.append(req), httpx.Response(200))[1])
    try:
        await client.post("/api/v1/notifications/channels", headers=h, json={
            "kind": "WEBHOOK", "name": "Integrasi", "target": "https://hooks.example.com/x", "events": ["ORDER_STATUS"]})
        o = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1"}])
        await run_once()
        await run_once()   # pengiriman webhook pada putaran berikutnya
        bodies = [r.content.decode() for r in got]
        assert any(o["order_number"] in b and '"to_status":"ALLOCATED"' in b for b in bodies)
        assert len([b for b in bodies if o["order_number"] in b and '"to_status":"PAID"' in b]) == 1  # tidak dobel
    finally:
        notif._WEBHOOK_TRANSPORT = None


async def test_jobs_are_locked_so_replicas_do_not_overlap():
    import zlib
    key = zlib.crc32(b"maintenance") & 0x7FFFFFFF
    async with SessionLocal() as s, s.begin():
        await s.execute(text("SELECT pg_advisory_xact_lock(4242, :k)"), {"k": key})
        # worker lain sedang memegang job ini
        assert (await jobs.run_job("maintenance")) == {"job": "maintenance", "skipped": True}
    assert (await jobs.run_job("maintenance")).get("skipped") is None   # kunci lepas setelah transaksi selesai
    with pytest.raises(RuntimeError, match="WORKER_JOBS"):
        st = get_settings()
        old = st.worker_jobs
        st.worker_jobs = "maintenance,tidak_ada"
        try:
            jobs.configured_jobs()
        finally:
            st.worker_jobs = old


async def test_metrics_endpoint_and_token(client, sa_token):
    await client.get("/api/v1/auth/me", headers=auth(sa_token))
    r = await client.get("/metrics")
    assert r.status_code == 200 and "nexvora_http_requests_total" in r.text
    assert 'route="/api/v1/auth/me"' in r.text and "nexvora_worker_job_total" in r.text
    st = get_settings()
    st.metrics_token = "rahasia-metrics"
    try:
        assert (await client.get("/metrics")).status_code == 401
        assert (await client.get("/metrics", headers={"Authorization": "Bearer rahasia-metrics"})).status_code == 200
    finally:
        st.metrics_token = None


async def test_read_session_is_read_only_and_rls_applies(client, sa_token):
    c = await setup_wh(client, sa_token)
    tenant = (await client.get("/api/v1/auth/me", headers=c["h"])).json()["tenant"]["id"]
    async with ReadSessionLocal() as s, s.begin():
        await s.execute(text("SET TRANSACTION READ ONLY"))
        await set_tenant_context(s, tenant)
        assert (await s.execute(text("SELECT count(*) FROM products"))).scalar() >= 0
        with pytest.raises(Exception, match="read-only"):
            await s.execute(text("INSERT INTO products(tenant_id, code, name) VALUES (:t, 'X', 'X')"), {"t": tenant})
    # endpoint analitik & laporan memakai sesi ini
    assert (await client.get("/api/v1/analytics/overview", headers=c["h"])).status_code == 200
    assert (await client.get("/api/v1/reports/orders.csv", headers=c["h"])).status_code == 200


async def test_analytics_indexes_exist():
    rows = await sql("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    names = {r.indexname for r in rows}
    assert {"ix_outbox_due", "ix_orders_placed_range", "ix_order_items_order", "ix_shipments_handover",
            "ix_tasks_completed", "ix_ledger_time"} <= names


async def test_large_response_is_compressed(client, sa_token):
    c = await stocked(client, sa_token)
    await asyncio.gather(*[new_order(client, c["h"], [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1000"}]) for _ in range(12)])
    r = await client.get("/api/v1/orders?limit=200", headers=c["h"] | {"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"


async def test_outbox_respects_tenant_isolation(client, sa_token):
    a = await setup_wh(client, sa_token)
    b = await setup_wh(client, sa_token)
    await new_order(client, a["h"], [{"sku_id": a["a"]["id"], "quantity": 1, "unit_price": "1"}])
    tb = (await client.get("/api/v1/auth/me", headers=b["h"])).json()["tenant"]["id"]
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, tb)
        assert (await s.execute(text("SELECT count(*) FROM outbox_events"))).scalar() == 0


async def test_platform_noc_reports_event_queue(client, sa_token):
    await run_once()
    p = (await client.get("/api/v1/platform/noc", headers=auth(sa_token))).json()
    assert "outbox_pending" in p["queues"] and "outbox_lag_seconds" in p["queues"]
    c = await stocked(client, sa_token)
    noc = (await client.get("/api/v1/analytics/noc", headers=c["h"])).json()
    assert any(x["key"] == "events" for x in noc["checks"])


