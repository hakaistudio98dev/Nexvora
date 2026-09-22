
import uuid

import httpx
import pytest
from sqlalchemy import text

from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant_context
from app.modules.shipping import providers
from app.workers.maintenance import run_once
from tests.conftest import PW, auth, login
from tests.test_phase3_wms import new_order, receive_and_putaway, setup_wh

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def ready_order(client, c, qty_a=2, qty_b=0):
    """Order sampai READY_TO_SHIP lewat picking + packing station."""
    h, wh = c["h"], c["wh"]["id"]
    items = [{"sku_id": c["a"]["id"], "quantity": qty_a, "unit_price": "50000"}]
    if qty_b:
        items.append({"sku_id": c["b"]["id"], "quantity": qty_b, "unit_price": "70000"})
    o = await new_order(client, h, items)
    await client.post(f"/api/v1/orders/{o['id']}/actions/start_picking", headers=h)
    for t in (await client.get(f"/api/v1/wms/tasks?warehouse_id={wh}&order_id={o['id']}", headers=h)).json():
        await client.post(f"/api/v1/wms/tasks/{t['id']}/pick", headers=h,
                          json={"location_code": t["from_location"], "barcode": t["sku_code"], "quantity": t["quantity"]})
    for it in [("TSH-M", qty_a), ("TSH-L", qty_b)]:
        if it[1]:
            await client.post(f"/api/v1/wms/pack/{o['order_number']}/scan", headers=h, json={"barcode": it[0], "quantity": it[1]})
    r = await client.post(f"/api/v1/wms/pack/{o['order_number']}/complete", headers=h,
                          json={"weight_g": 200 * qty_a + 250 * qty_b})
    assert r.json()["status"] == "READY_TO_SHIP", r.text
    return o


async def stocked(client, sa_token):
    c = await setup_wh(client, sa_token)
    await receive_and_putaway(client, c, {("a", "A-01-1-B1"): 20, ("b", "A-01-1-B2"): 20})
    return c


async def inv(client, h, wh):
    return {x["sku_code"]: x for x in (await client.get(f"/api/v1/inventory?warehouse_id={wh}", headers=h)).json()["items"]}


async def test_manual_shipment_resi_label_and_tracking(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    o = await ready_order(client, c)
    body = {"order_id": o["id"], "courier_code": "jne", "service_code": "reg"}
    r = await client.post("/api/v1/shipping/shipments", headers=h, json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "TRACKING_REQUIRED"
    r = await client.post("/api/v1/shipping/shipments", headers=h, json=body | {"tracking_number": "jne0001112223"})
    assert r.status_code == 201 and r.json()["tracking_number"] == "JNE0001112223" and r.json()["status"] == "LABEL_READY"
    sid = r.json()["id"]
    assert (await client.post("/api/v1/shipping/shipments", headers=h, json=body | {"tracking_number": "JNE9"*3})).status_code == 409
    lab = (await client.get(f"/api/v1/shipping/shipments/{sid}/label", headers=h)).json()
    assert lab["recipient"]["name"] == "Sari" and lab["sender"]["city"] == "Jakarta" and lab["weight_g"] == 400
    # batal lalu buat ulang dengan resi lain
    assert (await client.post(f"/api/v1/shipping/shipments/{sid}/cancel", headers=h)).json()["status"] == "CANCELLED"
    r = await client.post("/api/v1/shipping/shipments", headers=h, json=body | {"tracking_number": "JNE0001112299"})
    sid = r.json()["id"]
    assert (await client.post(f"/api/v1/orders/{o['id']}/actions/ship", headers=h)).json()["status"] == "SHIPPED"
    assert (await client.post(f"/api/v1/shipping/shipments/{sid}/cancel", headers=h)).status_code == 409
    for st in ("IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED"):
        r = await client.post(f"/api/v1/shipping/shipments/{sid}/tracking", headers=h,
                              json={"status": st, "description": f"Update {st}", "location": "Jakarta"})
        assert r.status_code == 200, r.text
    assert (await client.post(f"/api/v1/shipping/shipments/{sid}/tracking", headers=h,
                              json={"status": "IN_TRANSIT", "description": "mundur"})).status_code == 409
    order = (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()
    assert order["status"] == "DELIVERED"
    sh = (await client.get(f"/api/v1/shipping/shipments/by-order/{o['id']}", headers=h)).json()
    assert [e["status"] for e in sh["events"]] == ["LABEL_READY", "HANDED_OVER", "IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED"]


async def test_manifest_handover_and_simulator_tracking(client, sa_token):
    c = await stocked(client, sa_token)
    h, wh = c["h"], c["wh"]["id"]
    acc = (await client.post("/api/v1/shipping/accounts", headers=h, json={"name": "Demo", "provider": "simulator",
                                                                           "couriers": ["jne", "jnt"]})).json()
    o1, o2, o3 = [await ready_order(client, c, 1) for _ in range(3)]
    s1 = (await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o1["id"], "account_id": acc["id"], "courier_code": "jne", "service_code": "reg"})).json()
    s2 = (await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o2["id"], "account_id": acc["id"], "courier_code": "jne", "service_code": "yes"})).json()
    s3 = (await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o3["id"], "account_id": acc["id"], "courier_code": "jnt"})).json()
    assert s1["tracking_number"].startswith("SIMJNE")
    assert (await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o1["id"], "account_id": acc["id"], "courier_code": "sicepat"})).status_code in (409, 422)
    m = (await client.post("/api/v1/shipping/manifests", headers=h, json={"warehouse_id": wh, "courier_code": "jne"})).json()
    assert m["number"].startswith("MF-")
    r = await client.post(f"/api/v1/shipping/manifests/{m['id']}/scan", headers=h, json={"code": s3["tracking_number"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "WRONG_COURIER"
    assert (await client.post(f"/api/v1/shipping/manifests/{m['id']}/scan", headers=h, json={"code": s1["tracking_number"]})).status_code == 200
    assert (await client.post(f"/api/v1/shipping/manifests/{m['id']}/scan", headers=h, json={"code": o2["order_number"]})).json()["count"] == 2
    assert (await client.post(f"/api/v1/shipping/manifests/{m['id']}/scan", headers=h, json={"code": s1["tracking_number"]})).status_code == 409
    before = (await inv(client, h, wh))["TSH-M"]["on_hand"]
    r = await client.post(f"/api/v1/shipping/manifests/{m['id']}/handover", headers=h, json={"driver_name": "Pak Budi", "vehicle_plate": "B 1234 XY"})
    assert r.status_code == 200 and r.json()["handed_over"] == 2 and r.json()["status"] == "HANDED_OVER"
    assert (await inv(client, h, wh))["TSH-M"]["on_hand"] == before - 2
    assert (await client.get(f"/api/v1/orders/{o1['id']}", headers=h)).json()["status"] == "SHIPPED"
    st = get_settings()
    old = st.simulator_step_seconds
    st.simulator_step_seconds = 0
    try:
        await run_once()
    finally:
        st.simulator_step_seconds = old
    assert (await client.get(f"/api/v1/orders/{o1['id']}", headers=h)).json()["status"] == "DELIVERED"
    assert (await client.get(f"/api/v1/orders/{o3['id']}", headers=h)).json()["status"] == "READY_TO_SHIP"
    assert (await client.get("/api/v1/inventory/reconcile", headers=h)).json()["ok"]


def biteship_mock(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.headers.get("authorization")))
        if req.method == "POST" and req.url.path == "/v1/orders":
            return httpx.Response(200, json={"success": True, "id": "bs-ord-1", "price": 12000,
                                             "courier": {"waybill_id": "WYB123456789", "tracking_id": "t1"}})
        if req.method == "GET" and req.url.path == "/v1/orders/bs-ord-1":
            return httpx.Response(200, json={"success": True, "id": "bs-ord-1", "status": "delivered",
                                             "courier": {"waybill_id": "WYB123456789", "history": [
                                                 {"status": "picked", "note": "Diambil kurir", "updated_at": "2026-09-24T01:00:00Z"},
                                                 {"status": "delivered", "note": "Diterima", "updated_at": "2026-09-24T05:00:00Z"}]}})
        if req.url.path == "/v1/rates/couriers":
            return httpx.Response(200, json={"success": True, "pricing": [{"courier_code": "jne", "courier_name": "JNE",
                "courier_service_code": "reg", "courier_service_name": "Reguler", "price": 11000, "duration": "1-2 hari"}]})
        return httpx.Response(404, json={"success": False, "error": "not found"})
    return httpx.MockTransport(handler)


async def test_biteship_integration_plan_gate_and_webhook_pull(client, sa_token):
    ratelimit.reset_memory()
    # paket Starter tidak boleh integrasi kurir otomatis
    slug = "st" + uuid.uuid4().hex[:8]
    await client.post("/api/v1/public/signup", json={"company_name": "Toko Starter", "slug": slug, "full_name": "Ani Wijaya",
                                                     "email": f"o@{slug}.nexvora.id", "password": PW, "plan_code": "starter"})
    hs = auth((await login(client, slug, f"o@{slug}.nexvora.id")).json()["access_token"])
    r = await client.post("/api/v1/shipping/accounts", headers=hs, json={"name": "BS", "provider": "biteship", "api_key": "biteship_test.x"})
    assert r.status_code == 402

    c = await stocked(client, sa_token)
    h = c["h"]
    await client.patch(f"/api/v1/warehouses/{c['wh']['id']}", headers=h, json={"postal_code": "12190", "phone": "0215550000"})
    calls = []
    providers._BITESHIP_TRANSPORT = biteship_mock(calls)
    try:
        acc = (await client.post("/api/v1/shipping/accounts", headers=h, json={"name": "Biteship", "provider": "biteship",
                                                                               "api_key": "biteship_test.SECRET", "couriers": ["jne"]})).json()
        assert acc["has_credentials"] and acc["webhook_path"].startswith("/ext/api/v1/shipping/webhooks/biteship/")
        async with SessionLocal() as s, s.begin():
            await set_tenant_context(s, None, superadmin=True)
            enc = await s.scalar(text("SELECT credentials_enc FROM courier_accounts WHERE id = :i"), {"i": acc["id"]})
        assert "SECRET" not in enc  # kredensial terenkripsi
        o = await ready_order(client, c)
        rates = (await client.get(f"/api/v1/shipping/rates?order_id={o['id']}&account_id={acc['id']}", headers=h)).json()
        assert rates["rates"][0]["price"] == "11000"
        sh = (await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o["id"], "account_id": acc["id"],
                                                                               "courier_code": "jne", "service_code": "reg"})).json()
        assert sh["tracking_number"] == "WYB123456789" and sh["cost"] == "12000.00"
        assert calls[0][2] == "biteship_test.SECRET"
        await client.post(f"/api/v1/orders/{o['id']}/actions/ship", headers=h)
        token = acc["webhook_path"].rsplit("/", 1)[1]
        assert (await client.post("/api/v1/shipping/webhooks/biteship/" + "x" * 40, json={"order_id": "bs-ord-1"})).status_code == 404
        # payload palsu "delivered" tidak dipercaya — status ditarik dari API kurir
        assert (await client.post(f"/api/v1/shipping/webhooks/biteship/{token}", json={"order_id": "bs-ord-1", "status": "whatever"})).status_code == 200
        assert (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()["status"] == "DELIVERED"
    finally:
        providers._BITESHIP_TRANSPORT = None


async def test_return_to_sender_creates_rma(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    o = await ready_order(client, c)
    sh = (await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o["id"], "courier_code": "jne", "tracking_number": "JNERTS000111"})).json()
    await client.post(f"/api/v1/orders/{o['id']}/actions/ship", headers=h)
    await client.post(f"/api/v1/shipping/shipments/{sh['id']}/tracking", headers=h, json={"status": "RETURNED_TO_SENDER", "description": "Alamat tidak ditemukan"})
    assert (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()["status"] == "RETURN_REQUESTED"
    rets = (await client.get("/api/v1/returns?status=OPEN", headers=h)).json()
    assert rets[0]["reason_code"] == "UNDELIVERED" and rets[0]["status"] == "APPROVED" and rets[0]["lines"][0]["quantity"] == 2


async def delivered_order(client, c, qa=2, qb=1):
    h = c["h"]
    o = await ready_order(client, c, qa, qb)
    sh = (await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o["id"], "courier_code": "own", "tracking_number": f"OWN{uuid.uuid4().hex[:10]}"})).json()
    await client.post(f"/api/v1/orders/{o['id']}/actions/ship", headers=h)
    await client.post(f"/api/v1/orders/{o['id']}/actions/deliver", headers=h)
    return (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json(), sh


async def test_return_refund_flow_with_inventory(client, sa_token):
    c = await stocked(client, sa_token)
    h, wh = c["h"], c["wh"]["id"]
    o, _ = await delivered_order(client, c)
    assert o["status"] == "DELIVERED"
    item_m = next(i for i in o["items"] if i["sku_code"] == "TSH-M")
    r = await client.post("/api/v1/returns", headers=h, json={"order_id": o["id"], "reason_code": "DAMAGED", "lines": [{"order_item_id": item_m["id"], "quantity": 3}]})
    assert r.status_code == 422  # melebihi yang dikirim (2)
    rma = (await client.post("/api/v1/returns", headers=h, json={"order_id": o["id"], "reason_code": "DAMAGED", "note": "Sobek",
                                                                  "lines": [{"order_item_id": item_m["id"], "quantity": 2}]})).json()
    assert rma["number"].startswith("RMA-") and rma["status"] == "REQUESTED"
    assert (await client.post("/api/v1/returns", headers=h, json={"order_id": o["id"], "reason_code": "OTHER", "lines": [{"order_item_id": item_m["id"], "quantity": 1}]})).status_code == 409
    lid = rma["lines"][0]["id"]
    assert (await client.post(f"/api/v1/returns/{rma['id']}/receive", headers=h, json=[{"line_id": lid, "received_qty": 2}])).status_code == 409  # belum disetujui
    await client.post(f"/api/v1/returns/{rma['id']}/decide", headers=h, json={"approve": True})
    before = (await inv(client, h, wh))["TSH-M"]
    r = (await client.post(f"/api/v1/returns/{rma['id']}/receive", headers=h, json=[{"line_id": lid, "received_qty": 2}])).json()
    assert r["status"] == "RECEIVED"
    mid = (await inv(client, h, wh))["TSH-M"]
    assert mid["returned"] == before["returned"] + 2 and mid["on_hand"] == before["on_hand"]
    assert (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()["status"] == "RETURNED"
    bad = await client.post(f"/api/v1/returns/{rma['id']}/inspect", headers=h, json=[{"line_id": lid, "restock_qty": 1, "damaged_qty": 0}])
    assert bad.status_code == 422
    r = (await client.post(f"/api/v1/returns/{rma['id']}/inspect", headers=h, json=[{"line_id": lid, "restock_qty": 1, "damaged_qty": 1}])).json()
    assert r["status"] == "INSPECTED" and r["putaway_tasks"] == 1
    after = (await inv(client, h, wh))["TSH-M"]
    assert (after["on_hand"], after["damaged"], after["returned"]) == (before["on_hand"] + 1, before["damaged"] + 1, before["returned"])
    assert (await client.post(f"/api/v1/returns/{rma['id']}/resolve", headers=h, json={"resolution": "REFUND", "refund_amount": "999999999"})).status_code == 422
    r = (await client.post(f"/api/v1/returns/{rma['id']}/resolve", headers=h, json={"resolution": "REFUND", "refund_amount": "100000", "refund_ref": "TRF-REF-1"})).json()
    assert r["status"] == "CLOSED" and r["resolution"] == "REFUND"
    final = (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()
    assert final["status"] == "REFUNDED" and final["payment_status"] == "REFUNDED"
    led = (await client.get(f"/api/v1/inventory/ledger?reference_id={rma['number']}", headers=h)).json()
    assert sorted((x["d_returned"], x["d_on_hand"], x["d_damaged"]) for x in led) == [(-1, 0, 1), (-1, 1, 0), (2, 0, 0)]
    assert (await client.get("/api/v1/inventory/reconcile", headers=h)).json()["ok"]


async def test_return_replacement_and_reject(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    o, _ = await delivered_order(client, c, 1, 1)
    items = {i["sku_code"]: i["id"] for i in o["items"]}
    rj = (await client.post("/api/v1/returns", headers=h, json={"order_id": o["id"], "reason_code": "CHANGED_MIND", "lines": [{"order_item_id": items["TSH-L"], "quantity": 1}]})).json()
    assert (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()["status"] == "RETURN_REQUESTED"
    await client.post(f"/api/v1/returns/{rj['id']}/decide", headers=h, json={"approve": False, "note": "Lewat 7 hari"})
    assert (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()["status"] == "DELIVERED"
    rma = (await client.post("/api/v1/returns", headers=h, json={"order_id": o["id"], "reason_code": "WRONG_ITEM", "lines": [{"order_item_id": items["TSH-M"], "quantity": 1}]})).json()
    lid = rma["lines"][0]["id"]
    await client.post(f"/api/v1/returns/{rma['id']}/decide", headers=h, json={"approve": True})
    await client.post(f"/api/v1/returns/{rma['id']}/receive", headers=h, json=[{"line_id": lid, "received_qty": 1}])
    await client.post(f"/api/v1/returns/{rma['id']}/inspect", headers=h, json=[{"line_id": lid, "restock_qty": 1, "damaged_qty": 0}])
    r = (await client.post(f"/api/v1/returns/{rma['id']}/resolve", headers=h, json={"resolution": "REPLACEMENT"})).json()
    assert r["replacement_order_number"]
    orders = (await client.get(f"/api/v1/orders?q={r['replacement_order_number']}", headers=h)).json()["items"]
    assert orders[0]["status"] == "ALLOCATED" and orders[0]["total"] == "0.00"


async def test_shipping_returns_permissions(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    o, _ = await delivered_order(client, c, 1, 0)
    for email, role in [("cs@x.nexvora.id", "CUSTOMER_SERVICE"), ("op@x.nexvora.id", "WAREHOUSE_OPERATOR")]:
        await client.post("/api/v1/users", headers=h, json={"email": email, "full_name": "Staf " + role, "password": PW, "roles": [role]})
    cs = auth((await login(client, c["slug"], "cs@x.nexvora.id")).json()["access_token"])
    op = auth((await login(client, c["slug"], "op@x.nexvora.id")).json()["access_token"])
    item = o["items"][0]["id"]
    assert (await client.post("/api/v1/returns", headers=op, json={"order_id": o["id"], "reason_code": "OTHER", "lines": [{"order_item_id": item, "quantity": 1}]})).status_code == 403
    rma = (await client.post("/api/v1/returns", headers=cs, json={"order_id": o["id"], "reason_code": "OTHER", "lines": [{"order_item_id": item, "quantity": 1}]})).json()
    await client.post(f"/api/v1/returns/{rma['id']}/decide", headers=cs, json={"approve": True})
    lid = rma["lines"][0]["id"]
    assert (await client.post(f"/api/v1/returns/{rma['id']}/receive", headers=cs, json=[{"line_id": lid, "received_qty": 1}])).status_code == 403
    assert (await client.post(f"/api/v1/returns/{rma['id']}/receive", headers=op, json=[{"line_id": lid, "received_qty": 1}])).status_code == 200
    assert (await client.post("/api/v1/shipping/accounts", headers=op, json={"name": "x", "provider": "simulator"})).status_code == 403
    other = await setup_wh(client, sa_token)
    assert (await client.get(f"/api/v1/returns/{rma['id']}", headers=other["h"])).status_code == 404
    assert (await client.get(f"/api/v1/shipping/shipments/by-order/{o['id']}", headers=other["h"])).status_code == 404


