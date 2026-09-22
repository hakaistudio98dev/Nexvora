import csv
import hashlib
import hmac
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.core import netguard, ratelimit
from app.core.db import SessionLocal, set_tenant_context
from app.modules.notifications import service as notif
from app.workers.maintenance import run_once
from tests.conftest import PW, auth, login
from tests.test_phase3_wms import new_order, setup_wh
from tests.test_phase4_shipping import delivered_order, ready_order, stocked

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def sql(q, **kw):
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        return (await s.execute(text(q), kw)).all() if q.strip().upper().startswith("SELECT") else await s.execute(text(q), kw)


async def tenant_id(client, h):
    return (await client.get("/api/v1/auth/me", headers=h)).json()["tenant"]["id"]


async def test_overview_kpis(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    o1, _ = await delivered_order(client, c, 1, 0)          # dikirim tepat waktu
    o2, _ = await delivered_order(client, c, 1, 0)          # akan dibuat terlambat
    await sql("UPDATE orders SET paid_at = shipped_at - interval '30 hours' WHERE id = :i", i=o2["id"])
    await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1000"}])  # masih diproses
    o4 = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1000"}])
    await client.post(f"/api/v1/orders/{o4['id']}/actions/cancel", headers=h, json={"reason": "batal"})
    # 1 pick SHORT dari order lain, 1 paket dengan berat di luar toleransi
    o5 = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1"}])
    await client.post(f"/api/v1/orders/{o5['id']}/actions/start_picking", headers=h)
    t = (await client.get(f"/api/v1/wms/tasks?warehouse_id={c['wh']['id']}&order_id={o5['id']}", headers=h)).json()[0]
    await client.post(f"/api/v1/wms/tasks/{t['id']}/short", headers=h, json={"note": "hilang"})
    # retur untuk o1
    await client.post("/api/v1/returns", headers=h, json={"order_id": o1["id"], "reason_code": "DAMAGED",
                                                          "lines": [{"order_item_id": o1["items"][0]["id"], "quantity": 1}]})
    r = (await client.get("/api/v1/analytics/overview", headers=h)).json()
    k = r["kpi"]
    assert r["orders"]["total"] == 5 and r["orders"]["cancelled"] == 1 and r["orders"]["shipped"] == 2
    assert k["fulfillment_rate"] == 50.0          # 2 dikirim dari 4 order aktif
    assert k["sla_compliance"] == 50.0 and r["samples"]["sla_orders"] == 2
    assert k["pick_accuracy"] == round(2 * 100 / 3, 1)   # 2 selesai, 1 kurang
    assert k["return_rate"] == 50.0 and k["exception_rate"] == 20.0
    assert {x["channel"] for x in r["by_channel"]} == {"MANUAL"}
    ts = (await client.get("/api/v1/analytics/timeseries", headers=h)).json()
    assert len(ts) == 30 and sum(x["placed"] for x in ts) == 5 and sum(x["on_time"] for x in ts) == 1
    assert (await client.get("/api/v1/analytics/overview?date_from=2026-01-10&date_to=2026-01-01", headers=h)).status_code == 422


async def test_sla_board_productivity_top_skus_health(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    await client.patch("/api/v1/settings", headers=h, json={"sla_ship_hours": 12, "sla_risk_hours": 2})
    late = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 2, "unit_price": "1"}])
    risky = await new_order(client, h, [{"sku_id": c["b"]["id"], "quantity": 1, "unit_price": "1"}])
    await sql("UPDATE orders SET paid_at = now() - interval '13 hours' WHERE id = :i", i=late["id"])
    await sql("UPDATE orders SET paid_at = now() - interval '11 hours' WHERE id = :i", i=risky["id"])
    b = (await client.get("/api/v1/analytics/sla", headers=h)).json()
    assert b["sla_ship_hours"] == 12 and b["counts"]["overdue"] == 1 and b["counts"]["at_risk"] == 1
    assert b["orders"][0]["order_number"] == late["order_number"] and b["orders"][0]["state"] == "overdue"
    await ready_order(client, c, 1)
    prod = (await client.get("/api/v1/analytics/productivity", headers=h)).json()
    assert prod[0]["picks"] >= 1 and prod[0]["packages"] == 1 and prod[0]["putaways"] == 2
    top = (await client.get("/api/v1/analytics/top-skus", headers=h)).json()
    assert top[0]["sku_code"] == "TSH-M" and top[0]["units"] == 3
    await client.patch(f"/api/v1/skus/{c['b']['id']}", headers=h, json={"reorder_point": 100})
    health = (await client.get("/api/v1/analytics/inventory-health", headers=h)).json()
    tsh_l = next(x for x in health["items"] if x["sku_code"] == "TSH-L")
    assert tsh_l["state"] == "low" and tsh_l["threshold"] == 100 and tsh_l["suggested_reorder"] >= 0


async def test_starter_gets_basic_analytics_only(client, sa_token):
    ratelimit.reset_memory()
    slug = "an" + uuid.uuid4().hex[:8]
    await client.post("/api/v1/public/signup", json={"company_name": "Toko Analitik", "slug": slug, "full_name": "Rudi Santoso",
                                                     "email": f"o@{slug}.nexvora.id", "password": PW, "plan_code": "starter"})
    h = auth((await login(client, slug, f"o@{slug}.nexvora.id")).json()["access_token"])
    assert (await client.get("/api/v1/analytics/overview", headers=h)).status_code == 200
    assert (await client.get("/api/v1/reports/orders.csv", headers=h)).status_code == 200
    for path in ("sla", "productivity", "top-skus", "inventory-health", "noc"):
        assert (await client.get(f"/api/v1/analytics/{path}", headers=h)).status_code == 402
    assert (await client.post("/api/v1/notifications/channels", headers=h, json={
        "kind": "WEBHOOK", "name": "Hook", "target": "https://hooks.example.com/x", "events": ["LOW_STOCK"]})).status_code == 402
    assert (await client.post("/api/v1/notifications/channels", headers=h, json={
        "kind": "EMAIL", "name": "Gudang", "target": "Gudang@Toko.co.id", "events": ["LOW_STOCK"]})).json()["target"] == "gudang@toko.co.id"


async def test_csv_export_safety_and_isolation(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    await client.post("/api/v1/orders", headers=h, json={"customer": {"name": "=HYPERLINK(\"http://x\")"},
                                                         "shipping": {"address": "Jl. A 1", "city": "Jakarta"},
                                                         "items": [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1500"}]})
    r = await client.get("/api/v1/reports/orders.csv", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and r.text.startswith("\ufeff")
    rows = list(csv.reader(io.StringIO(r.text.lstrip("\ufeff"))))
    assert rows[0][0] == "order_number" and len(rows) == 2
    assert rows[1][rows[0].index("customer_name")].startswith("'=")  # formula injection dinetralkan
    semi = await client.get("/api/v1/reports/inventory.csv?sep=semicolon", headers=h)
    assert semi.text.lstrip("\ufeff").split("\r\n")[0].count(";") >= 5
    other = await setup_wh(client, sa_token)
    assert len(list(csv.reader(io.StringIO((await client.get("/api/v1/reports/orders.csv", headers=other["h"])).text.lstrip("\ufeff"))))) == 1
    assert (await client.get("/api/v1/reports/nope.csv", headers=h)).status_code == 404
    await client.post("/api/v1/users", headers=h, json={"email": "op@x.nexvora.id", "full_name": "Operator Gudang", "password": PW, "roles": ["WAREHOUSE_OPERATOR"]})
    op = auth((await login(client, c["slug"], "op@x.nexvora.id")).json()["access_token"])
    assert (await client.get("/api/v1/reports/orders.csv", headers=op)).status_code == 403


async def test_notifications_scan_dedup_and_inbox(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    await client.patch("/api/v1/settings", headers=h, json={"low_stock_threshold": 25})  # stok 20 → menipis
    held = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 500, "unit_price": "1"}])
    assert held["stock_status"] == "OUT_OF_STOCK"
    await run_once()
    await run_once()  # dedup: tidak dobel
    inbox = (await client.get("/api/v1/notifications", headers=h)).json()
    types = [n["event_type"] for n in inbox["items"]]
    assert types.count("LOW_STOCK") == 2 and types.count("ORDER_ON_HOLD") == 1 and "SUBSCRIPTION" not in types
    assert inbox["unread"] == len(inbox["items"])
    await client.post(f"/api/v1/notifications/{inbox['items'][0]['id']}/read", headers=h)
    assert (await client.get("/api/v1/notifications?unread=true", headers=h)).json()["unread"] == inbox["unread"] - 1
    await client.post("/api/v1/notifications/read-all", headers=h)
    assert (await client.get("/api/v1/notifications", headers=h)).json()["unread"] == 0
    other = await setup_wh(client, sa_token)
    assert all(n["event_type"] != "ORDER_ON_HOLD" for n in (await client.get("/api/v1/notifications", headers=other["h"])).json()["items"])


async def test_webhook_delivery_signature_retry_and_ssrf(client, sa_token, monkeypatch):
    c = await stocked(client, sa_token)
    h = c["h"]
    for bad in ("http://hooks.example.com/x", "https://127.0.0.1/x", "https://10.0.0.5/x", "https://user:pw@hooks.example.com/"):
        r = await client.post("/api/v1/notifications/channels", headers=h, json={"kind": "WEBHOOK", "name": "Hook", "target": bad, "events": ["LOW_STOCK"]})
        assert r.status_code == 422, bad
    monkeypatch.setattr(netguard, "resolve", lambda host: ["93.184.216.34"])  # host publik
    got, fail = [], {"n": 1}

    def handler(req: httpx.Request) -> httpx.Response:
        if fail["n"] > 0:
            fail["n"] -= 1
            return httpx.Response(500)
        got.append(req)
        return httpx.Response(204)
    notif._WEBHOOK_TRANSPORT = httpx.MockTransport(handler)
    try:
        ch = (await client.post("/api/v1/notifications/channels", headers=h, json={
            "kind": "WEBHOOK", "name": "n8n", "target": "https://hooks.example.com/nexvora", "events": ["ORDER_STATUS", "ORDER_ON_HOLD"]})).json()
        secret = ch["secret"]
        assert secret.startswith("whsec_") and "secret" not in (await client.get("/api/v1/notifications/channels", headers=h)).json()[0]
        await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 999, "unit_price": "1"}])
        await run_once()  # percobaan pertama gagal (500) → dijadwalkan ulang
        log = (await client.get(f"/api/v1/notifications/channels/{ch['id']}/deliveries", headers=h)).json()
        assert any(d["status"] == "PENDING" and d["attempts"] == 1 and d["last_error"] == "HTTP 500" for d in log)
        await sql("UPDATE notification_deliveries SET next_attempt_at = now() WHERE status = 'PENDING'")
        await run_once()
        events = {r.headers["x-nexvora-event"] for r in got}
        assert {"ORDER_STATUS", "ORDER_ON_HOLD"} <= events
        req = got[0]
        ts, sig = [x.split("=", 1)[1] for x in req.headers["x-nexvora-signature"].split(",")]
        expected = hmac.new(secret.encode(), f"{ts}.".encode() + req.content, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(sig, expected)
        body = json.loads(req.content)
        assert body["tenant"] == c["slug"] and body["data"]["order_number"].startswith("SO-")
        assert (await client.post(f"/api/v1/notifications/channels/{ch['id']}/test", headers=h)).json()["ok"]
    finally:
        notif._WEBHOOK_TRANSPORT = None


async def test_email_without_smtp_fails_cleanly(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    ch = (await client.post("/api/v1/notifications/channels", headers=h, json={
        "kind": "EMAIL", "name": "Admin", "target": "admin@toko.co.id", "events": ["ORDER_ON_HOLD"]})).json()
    await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 999, "unit_price": "1"}])
    await run_once()
    log = (await client.get(f"/api/v1/notifications/channels/{ch['id']}/deliveries", headers=h)).json()
    assert log[0]["status"] == "FAILED" and "SMTP" in log[0]["last_error"]
    assert (await client.post(f"/api/v1/notifications/channels/{ch['id']}/test", headers=h)).status_code in (403, 502)


async def test_noc_tenant_and_platform(client, sa_token):
    c = await stocked(client, sa_token)
    await run_once()
    noc = (await client.get("/api/v1/analytics/noc", headers=c["h"])).json()
    worker = next(x for x in noc["checks"] if x["key"] == "worker")
    assert worker["status"] == "ok" and noc["overall"] in ("ok", "warning", "critical")
    assert (await client.get("/api/v1/platform/noc", headers=c["h"])).status_code == 403
    p = (await client.get("/api/v1/platform/noc", headers=auth(sa_token))).json()
    assert p["database"]["status"] == "ok" and p["worker"]["status"] == "ok" and p["tenants"]["total"] >= 1


async def test_settings_permissions_and_validation(client, sa_token):
    c = await setup_wh(client, sa_token)
    h = c["h"]
    assert (await client.get("/api/v1/settings", headers=h)).json()["sla_ship_hours"] == 24
    assert (await client.patch("/api/v1/settings", headers=h, json={"timezone": "Mars/Base"})).status_code == 422
    assert (await client.patch("/api/v1/settings", headers=h, json={"timezone": "Asia/Makassar"})).json()["timezone"] == "Asia/Makassar"
    await client.post("/api/v1/users", headers=h, json={"email": "v@x.nexvora.id", "full_name": "Viewer Satu", "password": PW, "roles": ["VIEWER"]})
    v = auth((await login(client, c["slug"], "v@x.nexvora.id")).json()["access_token"])
    assert (await client.patch("/api/v1/settings", headers=v, json={"sla_ship_hours": 1})).status_code == 403
    assert (await client.get("/api/v1/analytics/overview", headers=v)).status_code == 200


_ = (datetime, UTC, timedelta, tenant_id)
