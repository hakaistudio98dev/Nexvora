import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core import ratelimit
from app.core.db import SessionLocal, set_tenant_context
from app.modules.ai import forecast
from app.workers.jobs import run_job
from tests.conftest import PW, auth, login
from tests.test_phase3_wms import new_order, receive_and_putaway, setup_wh
from tests.test_phase4_shipping import delivered_order, ready_order, stocked

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def sql(q, **kw):
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        r = await s.execute(text(q), kw)
        return r.all() if q.strip().upper().startswith(("SELECT", "WITH")) else None


async def make_enterprise(client, sa_token, slug_prefix="ai"):
    """Tenant dengan paket Enterprise (fitur AI aktif)."""
    ratelimit.reset_memory()
    slug = slug_prefix + uuid.uuid4().hex[:8]
    await client.post("/api/v1/public/signup", json={"company_name": "Toko AI", "slug": slug, "full_name": "Rina Pratama",
                                                     "email": f"o@{slug}.nexvora.id", "password": PW, "plan_code": "enterprise"})
    h = auth((await login(client, slug, f"o@{slug}.nexvora.id")).json()["access_token"])
    return slug, h


def test_forecast_math_weekly_pattern():
    today = datetime(2026, 9, 25, tzinfo=UTC).date()
    rows = [(today - timedelta(days=i), 10 if (today - timedelta(days=i)).weekday() >= 5 else 4) for i in range(60)]
    rate, sigma, factors, method, span = forecast.fit(rows, today, today - timedelta(days=59))
    assert method == "pola_mingguan" and span == 60
    assert 5.0 < rate < 6.5                       # rata-rata tertimbang (5×4 + 2×10) / 7 ≈ 5.7
    assert factors[5] > factors[0] * 1.5          # akhir pekan jelas lebih ramai
    f = forecast.Forecast(None, None, method, rate, sigma, factors, span, 0)
    assert forecast.days_until_out(f, 0, today) == 0
    assert forecast.days_until_out(f, 57, today) in range(8, 13)   # ±10 hari
    assert forecast.days_until_out(f, 100000, today) is None       # tidak habis dalam horizon
    assert round(forecast.z_for(0.95), 2) == 1.64 and forecast.z_for(0.99) > forecast.z_for(0.95)


def test_forecast_without_history_is_honest():
    today = datetime(2026, 9, 25, tzinfo=UTC).date()
    rate, sigma, factors, method, span = forecast.fit([], today, None)
    assert method == "belum_ada_penjualan" and rate == 0 and span == 0
    rows = [(today - timedelta(days=i), 2) for i in range(10)]
    _, _, _, method2, _ = forecast.fit(rows, today, today - timedelta(days=9))
    assert method2 == "rata_rata"   # data < 4 minggu: pola mingguan belum dipakai


async def test_stockout_risk_and_reorder_suggestion(client, sa_token):
    c = await stocked(client, sa_token)
    # pindahkan tenant demo-test ke paket enterprise lewat super admin agar fitur AI aktif
    await sql("""UPDATE subscriptions SET plan_code = 'enterprise'
                 WHERE tenant_id = (SELECT id FROM tenants WHERE slug = :s)""", s=c["slug"])
    h = auth((await login(client, c["slug"], c["email"])).json()["access_token"])
    for _ in range(6):
        await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 2, "unit_price": "1000"}])
    await sql("""UPDATE orders SET placed_at = now() - (random() * interval '20 days')
                 WHERE tenant_id = (SELECT id FROM tenants WHERE slug = :s)""", s=c["slug"])
    assert (await client.post("/api/v1/ai/rebuild", headers=h)).json()["skus"] >= 1
    r = (await client.get("/api/v1/ai/stockout-risk", headers=h)).json()
    item = next(x for x in r["items"] if x["sku_code"] == "TSH-M")
    assert item["daily_rate"] > 0 and item["days_until_out"] is not None
    assert item["risk"] in ("habis", "kritis", "waspada", "aman")
    assert item["reorder_point"] >= 0 and item["suggested_order"] >= 0
    assert r["settings"]["lead_time"] == 7
    d = (await client.get(f"/api/v1/ai/demand?warehouse_id={c['wh']['id']}&sku_id={c['a']['id']}&horizon=7", headers=h)).json()
    assert len(d["forecast"]) == 7 and len(d["history"]) == 30 and d["sku_code"] == "TSH-M"
    assert all(x["low"] <= x["expected"] <= x["high"] for x in d["forecast"])
    # lead time lebih panjang → titik pesan ulang naik
    await client.patch("/api/v1/settings", headers=h, json={"lead_time_days": 30})
    r2 = (await client.get("/api/v1/ai/stockout-risk", headers=h)).json()
    item2 = next(x for x in r2["items"] if x["sku_code"] == "TSH-M")
    assert item2["reorder_point"] > item["reorder_point"]


async def test_courier_recommendation_prefers_reliable(client, sa_token):
    c = await stocked(client, sa_token)
    await sql("""UPDATE subscriptions SET plan_code = 'enterprise'
                 WHERE tenant_id = (SELECT id FROM tenants WHERE slug = :s)""", s=c["slug"])
    h = auth((await login(client, c["slug"], c["email"])).json()["access_token"])
    empty = (await client.get("/api/v1/ai/courier?city=Jakarta", headers=h)).json()
    assert empty["candidates"] == [] and "Belum ada" in empty["note"]
    for i in range(4):
        o, sh = await delivered_order(client, c, 1, 0)
        await sql("""UPDATE shipments SET courier_code = 'jne', cost = 20000, handed_over_at = now() - interval '2 days',
                     delivered_at = now() - interval '1 day' WHERE id = :i""", i=sh["id"])
    for i in range(3):
        o, sh = await delivered_order(client, c, 1, 0)
        status = "RETURNED_TO_SENDER" if i < 2 else "DELIVERED"
        await sql("""UPDATE shipments SET courier_code = 'jnt', cost = 15000, status = CAST(:st AS varchar),
                     handed_over_at = now() - interval '5 days',
                     delivered_at = CASE WHEN CAST(:st2 AS varchar) = 'DELIVERED' THEN now() ELSE NULL END
                     WHERE id = :i""", i=sh["id"], st=status, st2=status)
    r = (await client.get("/api/v1/ai/courier?city=Jakarta", headers=h)).json()
    codes = [x["courier_code"] for x in r["candidates"]]
    assert codes[0] == "jne", r
    jne = r["candidates"][0]
    assert jne["reliability"] > next(x for x in r["candidates"] if x["courier_code"] == "jnt")["reliability"]
    assert "sampai" in jne["reason"] and jne["speed_days"] is not None
    assert r["basis"] in ("kota", "semua kota")
    by_order = (await client.get(f"/api/v1/ai/courier?order_id={o['id']}", headers=h)).json()
    assert by_order["candidates"][0]["courier_code"] == "jne"


async def test_anomaly_detection_and_workflow(client, sa_token):
    c = await stocked(client, sa_token)
    await sql("""UPDATE subscriptions SET plan_code = 'enterprise'
                 WHERE tenant_id = (SELECT id FROM tenants WHERE slug = :s)""", s=c["slug"])
    h = auth((await login(client, c["slug"], c["email"])).json()["access_token"])
    for _ in range(25):
        await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "100000"}])
    await sql("""UPDATE orders SET placed_at = now() - interval '10 days' WHERE tenant_id =
                 (SELECT id FROM tenants WHERE slug = :s)""", s=c["slug"])
    big = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "9000000"}])
    for _ in range(4):
        await client.post("/api/v1/orders", headers=h, json={
            "customer": {"name": "Pembeli Borongan", "phone": "08129998887"},
            "shipping": {"address": "Jl. Sama 1", "city": "Jakarta"},
            "items": [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "100000"}]})
    await run_job("ai")
    rows = (await client.get("/api/v1/ai/anomalies", headers=h)).json()
    kinds = {a["kind"] for a in rows}
    assert "ORDER_VALUE" in kinds and "REPEAT_CUSTOMER" in kinds, kinds
    val = next(a for a in rows if a["kind"] == "ORDER_VALUE")
    assert val["entity_label"] == big["order_number"] and val["score"] >= 6 and val["status"] == "OPEN"
    await run_job("ai")   # dedup: tidak dobel
    assert len((await client.get("/api/v1/ai/anomalies", headers=h)).json()) == len(rows)
    inbox = (await client.get("/api/v1/notifications", headers=h)).json()
    assert any(n["event_type"] == "AI_ANOMALY" for n in inbox["items"])
    r = await client.post(f"/api/v1/ai/anomalies/{val['id']}/decide", headers=h, json={"status": "DISMISSED"})
    assert r.json()["status"] == "DISMISSED"
    assert all(a["id"] != val["id"] for a in (await client.get("/api/v1/ai/anomalies", headers=h)).json())
    assert any(a["id"] == val["id"] for a in (await client.get("/api/v1/ai/anomalies?status=ALL", headers=h)).json())
    summary = (await client.get("/api/v1/ai/summary", headers=h)).json()
    assert summary["anomalies_open"] >= 1 and "top_reorder" in summary


async def test_ai_plan_gate_permissions_and_isolation(client, sa_token):
    c = await stocked(client, sa_token)   # paket bawaan (growth)
    h = c["h"]
    for path in ("/ai/stockout-risk", "/ai/anomalies", "/ai/courier?city=Jakarta", "/ai/summary"):
        r = await client.get("/api/v1" + path, headers=h)
        assert r.status_code == 402 and "Enterprise" in r.json()["error"]["message"], path
    await sql("""UPDATE subscriptions SET plan_code = 'enterprise'
                 WHERE tenant_id = (SELECT id FROM tenants WHERE slug = :s)""", s=c["slug"])
    h = auth((await login(client, c["slug"], c["email"])).json()["access_token"])
    assert (await client.get("/api/v1/ai/stockout-risk", headers=h)).status_code == 200
    await client.post("/api/v1/users", headers=h, json={"email": "op@x.nexvora.id", "full_name": "Operator Gudang",
                                                        "password": PW, "roles": ["WAREHOUSE_OPERATOR"]})
    op = auth((await login(client, c["slug"], "op@x.nexvora.id")).json()["access_token"])
    assert (await client.get("/api/v1/ai/stockout-risk", headers=op)).status_code == 403
    other = await stocked(client, sa_token)
    await sql("""UPDATE subscriptions SET plan_code = 'enterprise'
                 WHERE tenant_id = (SELECT id FROM tenants WHERE slug = :s)""", s=other["slug"])
    oh = auth((await login(client, other["slug"], other["email"])).json()["access_token"])
    mine = {x["sku_id"] for x in (await client.get("/api/v1/ai/stockout-risk", headers=h)).json()["items"]}
    theirs = {x["sku_id"] for x in (await client.get("/api/v1/ai/stockout-risk", headers=oh)).json()["items"]}
    assert not (mine & theirs)


