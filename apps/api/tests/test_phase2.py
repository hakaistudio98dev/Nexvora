import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, set_tenant_context
from app.workers.maintenance import run_once
from tests.conftest import PW, auth, login, make_tenant

pytestmark = pytest.mark.asyncio(loop_scope="session")

ADDR = {"address": "Jl. Sudirman No. 1", "city": "Jakarta"}
CUST = {"name": "Budi Santoso", "phone": "08123456789"}


async def setup_catalog(client, sa_token, stock=None):
    """Tenant baru + 1 produk 2 SKU + gudang JKT (Jakarta) & BDG (Bandung)."""
    slug, email, tok = await make_tenant(client, sa_token)
    h = auth(tok["access_token"])
    prod = (await client.post("/api/v1/products", headers=h, json={"code": "TSH", "name": "Kaos"})).json()
    a = (await client.post(f"/api/v1/products/{prod['id']}/skus", headers=h, json={"sku_code": "TSH-M"})).json()
    b = (await client.post(f"/api/v1/products/{prod['id']}/skus", headers=h, json={"sku_code": "TSH-L"})).json()
    jkt = (await client.post("/api/v1/warehouses", headers=h, json={"code": "JKT-01", "name": "Jakarta", "city": "Jakarta"})).json()
    bdg = (await client.post("/api/v1/warehouses", headers=h, json={"code": "BDG-01", "name": "Bandung", "city": "Bandung"})).json()
    for (wh, sku), qty in (stock or {}).items():
        wid = {"jkt": jkt, "bdg": bdg}[wh]["id"]
        sid = {"a": a, "b": b}[sku]["id"]
        r = await client.post("/api/v1/inventory/receipts", headers=h,
                              json={"warehouse_id": wid, "reference": "PO-1", "lines": [{"sku_id": sid, "quantity": qty}]})
        assert r.status_code == 201, r.text
    return {"slug": slug, "h": h, "a": a, "b": b, "jkt": jkt, "bdg": bdg, "tok": tok}


async def bal(client, h, wid, sid):
    r = (await client.get(f"/api/v1/inventory?warehouse_id={wid}&sku_id={sid}", headers=h)).json()
    return r["items"][0] if r["items"] else {"on_hand": 0, "reserved": 0, "available": 0, "damaged": 0}


def order_body(items, **kw):
    return {"customer": CUST, "shipping": ADDR, "items": items, **kw}


async def test_receipt_ledger_reconstructs_balance(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 100})
    h, a, jkt = c["h"], c["a"], c["jkt"]
    await client.post("/api/v1/inventory/receipts", headers=h,
                      json={"warehouse_id": jkt["id"], "lines": [{"sku_id": a["id"], "quantity": 200}]})
    r = await client.post("/api/v1/inventory/adjustments", headers=h, json={
        "warehouse_id": jkt["id"], "sku_id": a["id"], "reason_code": "COUNT_CORRECTION", "delta": -2, "note": "opname"})
    assert r.status_code == 201, r.text
    await client.post("/api/v1/inventory/adjustments", headers=h, json={
        "warehouse_id": jkt["id"], "sku_id": a["id"], "reason_code": "DAMAGED", "delta": 5, "note": "sobek"})
    b = await bal(client, h, jkt["id"], a["id"])
    assert (b["on_hand"], b["damaged"], b["available"]) == (293, 5, 293)
    rec = (await client.get("/api/v1/inventory/reconcile", headers=h)).json()
    assert rec["ok"] and rec["checked"] == 1
    led = (await client.get(f"/api/v1/inventory/ledger?sku_id={a['id']}", headers=h)).json()
    assert [x["entry_type"] for x in led] == ["DAMAGE", "ADJUSTMENT", "RECEIPT", "RECEIPT"]


async def test_order_reserves_and_allocates_best_warehouse(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 5, ("bdg", "a"): 50})
    h = c["h"]
    # Tujuan Jakarta, stok JKT cukup → pilih JKT walau BDG lebih banyak
    r = await client.post("/api/v1/orders", headers=h, json=order_body([{"sku_id": c["a"]["id"], "quantity": 3, "unit_price": "75000"}]))
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["status"] == "CREATED" and o["stock_status"] == "RESERVED" and o["warehouse_code"] == "JKT-01"
    assert o["total"] == "225000.00" and o["reservations"][0]["expires_at"] is not None
    assert (await bal(client, h, c["jkt"]["id"], c["a"]["id"]))["available"] == 2
    # Butuh 4, JKT tinggal 2 → BDG
    o2 = (await client.post("/api/v1/orders", headers=h, json=order_body([{"sku_id": c["a"]["id"], "quantity": 4, "unit_price": "1"}]))).json()
    assert o2["warehouse_code"] == "BDG-01" and "stok tersedia terbanyak" in o2["allocation_note"]


async def test_full_lifecycle_consumes_stock(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 10})
    h = c["h"]
    o = (await client.post("/api/v1/orders", headers=h, json=order_body(
        [{"sku_code": "TSH-M", "quantity": 2, "unit_price": "50000"}], channel="SHOPEE", external_ref="SHP-1"))).json()
    oid = o["id"]
    o = (await client.post(f"/api/v1/orders/{oid}/actions/mark_paid", headers=h)).json()
    assert o["status"] == "ALLOCATED" and o["payment_status"] == "PAID"
    assert o["reservations"][0]["expires_at"] is None  # sudah bayar → tidak kedaluwarsa
    o = (await client.post(f"/api/v1/orders/{oid}/actions/start_picking", headers=h)).json()
    assert o["status"] == "PICKING"
    wh = c["jkt"]["id"]
    tasks = (await client.get(f"/api/v1/wms/tasks?warehouse_id={wh}&task_type=PICK", headers=h)).json()
    assert len(tasks) == 1 and tasks[0]["from_location"] is None  # stok belum di-putaway → ambil di area terima
    r = await client.post(f"/api/v1/wms/tasks/{tasks[0]['id']}/pick", headers=h, json={"barcode": "TSH-M", "quantity": 2})
    assert r.status_code == 200 and r.json()["status"] == "DONE", r.text
    assert (await client.post(f"/api/v1/wms/pack/{o['order_number']}/scan", headers=h, json={"barcode": "tsh-m", "quantity": 2})).status_code == 200
    r = await client.post(f"/api/v1/wms/pack/{o['order_number']}/complete", headers=h, json={"weight_g": 400})
    assert r.status_code == 200 and r.json()["status"] == "READY_TO_SHIP", r.text
    r = await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": oid, "courier_code": "jne",
                                                                          "service_code": "reg", "tracking_number": "JNE0012345678"})
    assert r.status_code == 201, r.text
    for act, st in [("ship", "SHIPPED"), ("deliver", "DELIVERED")]:
        r = await client.post(f"/api/v1/orders/{oid}/actions/{act}", headers=h)
        assert r.status_code == 200 and r.json()["status"] == st, r.text
    b = await bal(client, h, c["jkt"]["id"], c["a"]["id"])
    assert (b["on_hand"], b["reserved"], b["available"]) == (8, 0, 8)
    final = (await client.get(f"/api/v1/orders/{oid}", headers=h)).json()
    assert [x["to_status"] for x in final["history"]] == [
        "CREATED", "PAID", "ALLOCATED", "PICKING", "PACKING", "READY_TO_SHIP", "SHIPPED", "DELIVERED"]
    assert final["actions"] == [] and final["stock_status"] == "CONSUMED"
    assert (await client.get("/api/v1/inventory/reconcile", headers=h)).json()["ok"]


async def test_invalid_transition_and_cancel_releases(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 10})
    h = c["h"]
    oid = (await client.post("/api/v1/orders", headers=h, json=order_body([{"sku_id": c["a"]["id"], "quantity": 4, "unit_price": "1"}]))).json()["id"]
    r = await client.post(f"/api/v1/orders/{oid}/actions/ship", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_TRANSITION"
    assert (await client.post(f"/api/v1/orders/{oid}/actions/cancel", headers=h)).status_code == 400  # alasan wajib
    o = (await client.post(f"/api/v1/orders/{oid}/actions/cancel", headers=h, json={"reason": "Pembeli batal"})).json()
    assert o["status"] == "CANCELLED" and o["stock_status"] == "RELEASED"
    assert (await bal(client, h, c["jkt"]["id"], c["a"]["id"]))["available"] == 10


async def test_out_of_stock_goes_to_hold_then_retry(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 1})
    h = c["h"]
    o = (await client.post("/api/v1/orders", headers=h, json=order_body(
        [{"sku_id": c["a"]["id"], "quantity": 3, "unit_price": "1"}], paid=True))).json()
    assert o["status"] == "PAID" and o["stock_status"] == "OUT_OF_STOCK"
    assert any(a["name"] == "reserve" for a in o["actions"])
    assert (await client.post(f"/api/v1/inventory/reserve", headers=h, json={"order_id": o["id"]})).status_code == 409
    await client.post("/api/v1/inventory/receipts", headers=h,
                      json={"warehouse_id": c["jkt"]["id"], "lines": [{"sku_id": c["a"]["id"], "quantity": 5}]})
    r = await client.post("/api/v1/inventory/reserve", headers=h, json={"order_id": o["id"]})
    assert r.status_code == 200 and r.json()["status"] == "ALLOCATED"
    stats = (await client.get("/api/v1/orders/stats", headers=h)).json()
    assert stats["out_of_stock"] == 0 and stats["by_status"]["ALLOCATED"] == 1


async def test_no_oversell_under_concurrency(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 5})
    h = c["h"]
    body = order_body([{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1"}])
    rs = await asyncio.gather(*[client.post("/api/v1/orders", headers=h, json=body) for _ in range(12)])
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs if r.status_code != 201]
    reserved = [r.json() for r in rs if r.json()["stock_status"] == "RESERVED"]
    assert len(reserved) == 5
    b = await bal(client, h, c["jkt"]["id"], c["a"]["id"])
    assert (b["reserved"], b["available"]) == (5, 0)
    assert (await client.get("/api/v1/inventory/reconcile", headers=h)).json()["ok"]


async def test_idempotency_and_duplicate_external_ref(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 10})
    h = c["h"]
    key = "ord-" + uuid.uuid4().hex
    body = order_body([{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "10"}])
    r1, r2 = await asyncio.gather(
        client.post("/api/v1/orders", headers=h | {"Idempotency-Key": key}, json=body),
        client.post("/api/v1/orders", headers=h | {"Idempotency-Key": key}, json=body))
    assert {r1.status_code, r2.status_code} == {201}
    assert r1.json()["id"] == r2.json()["id"]
    assert "true" in (r1.headers.get("idempotent-replayed", ""), r2.headers.get("idempotent-replayed", ""))
    assert (await client.get("/api/v1/orders", headers=h)).json()["total"] == 1
    other = order_body([{"sku_id": c["a"]["id"], "quantity": 2, "unit_price": "10"}])
    r3 = await client.post("/api/v1/orders", headers=h | {"Idempotency-Key": key}, json=other)
    assert r3.status_code == 422 and r3.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    mk = order_body([{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "10"}], channel="TOKOPEDIA", external_ref="TKP-9")
    assert (await client.post("/api/v1/orders", headers=h, json=mk)).status_code == 201
    r4 = await client.post("/api/v1/orders", headers=h, json=mk)
    assert r4.status_code == 409 and r4.json()["error"]["code"] == "DUPLICATE_ORDER"


async def test_adjustment_rules_and_permissions(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 5})
    h = c["h"]
    await client.post("/api/v1/orders", headers=h, json=order_body([{"sku_id": c["a"]["id"], "quantity": 4, "unit_price": "1"}]))
    r = await client.post("/api/v1/inventory/adjustments", headers=h, json={
        "warehouse_id": c["jkt"]["id"], "sku_id": c["a"]["id"], "reason_code": "LOST", "delta": -3, "note": "hilang"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "STOCK_RESERVED"
    await client.post("/api/v1/users", headers=h, json={"email": "op@x.nexvora.id", "full_name": "Op", "password": PW,
                                                         "roles": ["WAREHOUSE_OPERATOR"]})
    op = auth((await login(client, c["slug"], "op@x.nexvora.id")).json()["access_token"])
    assert (await client.get("/api/v1/inventory", headers=op)).status_code == 200
    assert (await client.post("/api/v1/inventory/adjustments", headers=op, json={
        "warehouse_id": c["jkt"]["id"], "sku_id": c["a"]["id"], "reason_code": "FOUND", "delta": 1, "note": "x x"})).status_code == 403
    assert (await client.post("/api/v1/orders", headers=op, json=order_body(
        [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1"}]))).status_code == 403


async def test_reservation_expiry_cancels_unpaid(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 3})
    h = c["h"]
    o = (await client.post("/api/v1/orders", headers=h, json=order_body([{"sku_id": c["a"]["id"], "quantity": 3, "unit_price": "1"}]))).json()
    paid = (await client.post("/api/v1/orders", headers=h, json=order_body(
        [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1"}], paid=True))).json()
    assert paid["stock_status"] == "OUT_OF_STOCK"
    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, me["tenant"]["id"])
        await s.execute(text("UPDATE reservations SET expires_at = :x WHERE order_id = :o"),
                        {"x": datetime.now(UTC) - timedelta(minutes=1), "o": o["id"]})
    result = await run_once()
    assert result["expired_orders"] >= 1
    o = (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()
    assert o["status"] == "CANCELLED" and o["reservations"][0]["status"] == "EXPIRED"
    assert (await bal(client, h, c["jkt"]["id"], c["a"]["id"]))["available"] == 3


async def test_ledger_and_history_append_only(client, sa_token):
    c = await setup_catalog(client, sa_token, {("jkt", "a"): 3})
    me = (await client.get("/api/v1/auth/me", headers=c["h"])).json()
    for sql in ("UPDATE inventory_ledger SET d_on_hand = 999", "DELETE FROM inventory_ledger",
                "UPDATE inventory_balances SET on_hand = -1", "DELETE FROM inventory_balances"):
        with pytest.raises(Exception):
            async with SessionLocal() as s, s.begin():
                await set_tenant_context(s, me["tenant"]["id"])
                await s.execute(text(sql))


async def test_tenant_isolation_orders_inventory(client, sa_token):
    a = await setup_catalog(client, sa_token, {("jkt", "a"): 3})
    b = await setup_catalog(client, sa_token)
    oa = (await client.post("/api/v1/orders", headers=a["h"], json=order_body([{"sku_id": a["a"]["id"], "quantity": 1, "unit_price": "1"}]))).json()
    assert (await client.get(f"/api/v1/orders/{oa['id']}", headers=b["h"])).status_code == 404
    assert (await client.post(f"/api/v1/orders/{oa['id']}/actions/cancel", headers=b["h"], json={"reason": "x"})).status_code == 404
    assert (await client.get("/api/v1/inventory", headers=b["h"])).json()["total"] == 0
    r = await client.post("/api/v1/orders", headers=b["h"], json=order_body([{"sku_id": a["a"]["id"], "quantity": 1, "unit_price": "1"}]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_SKU"
    r = await client.post("/api/v1/inventory/receipts", headers=b["h"],
                          json={"warehouse_id": a["jkt"]["id"], "lines": [{"sku_id": a["a"]["id"], "quantity": 1}]})
    assert r.status_code == 404
