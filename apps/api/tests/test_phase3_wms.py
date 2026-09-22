import uuid

import pytest

from tests.conftest import PW, auth, login, make_tenant

pytestmark = pytest.mark.asyncio(loop_scope="session")
ADDR = {"address": "Jl. Sudirman No. 1", "city": "Jakarta"}


async def setup_wh(client, sa_token):
    slug, _, tok = await make_tenant(client, sa_token)
    h = auth(tok["access_token"])
    prod = (await client.post("/api/v1/products", headers=h, json={"code": "TSH", "name": "Kaos"})).json()
    a = (await client.post(f"/api/v1/products/{prod['id']}/skus", headers=h,
                           json={"sku_code": "TSH-M", "barcode": "899000000001", "weight_g": 200})).json()
    b = (await client.post(f"/api/v1/products/{prod['id']}/skus", headers=h,
                           json={"sku_code": "TSH-L", "barcode": "899000000002", "weight_g": 250})).json()
    wh = (await client.post("/api/v1/warehouses", headers=h, json={"code": "JKT-01", "name": "Jakarta", "city": "Jakarta"})).json()
    base = f"/api/v1/warehouses/{wh['id']}/locations"
    bins = {}
    for z in ("A", "B"):
        zone = (await client.post(base, headers=h, json={"type": "ZONE", "code": z})).json()
        rack = (await client.post(base, headers=h, json={"type": "RACK", "code": "01", "parent_id": zone["id"]})).json()
        shelf = (await client.post(base, headers=h, json={"type": "SHELF", "code": "1", "parent_id": rack["id"]})).json()
        for bn in ("B1", "B2"):
            loc = (await client.post(base, headers=h, json={"type": "BIN", "code": bn, "parent_id": shelf["id"]})).json()
            bins[loc["full_code"]] = loc
    return {"slug": slug, "h": h, "a": a, "b": b, "wh": wh, "bins": bins}


async def receive_and_putaway(client, c, placements):
    """placements: {(sku_key, bin_code): qty} lewat inbound → putaway."""
    h, wh = c["h"], c["wh"]["id"]
    totals = {}
    for (k, _), q in placements.items():
        totals[k] = totals.get(k, 0) + q
    inb = (await client.post("/api/v1/wms/inbound", headers=h, json={
        "warehouse_id": wh, "supplier": "PT Supplier",
        "lines": [{"sku_id": c[k]["id"], "expected_qty": q} for k, q in totals.items()]})).json()
    for k, q in totals.items():
        r = await client.post(f"/api/v1/wms/inbound/{inb['id']}/receive", headers=h,
                              json={"barcode": c[k]["barcode"], "quantity": q})
        assert r.status_code == 200, r.text
    assert (await client.post(f"/api/v1/wms/inbound/{inb['id']}/complete", headers=h)).status_code == 200
    tasks = {t["sku_code"]: t for t in (await client.get(f"/api/v1/wms/tasks?warehouse_id={wh}&task_type=PUTAWAY", headers=h)).json()}
    for (k, bin_code), q in placements.items():
        t = tasks[c[k]["sku_code"]]
        r = await client.post(f"/api/v1/wms/tasks/{t['id']}/putaway", headers=h, json={"location_code": bin_code, "quantity": q})
        assert r.status_code == 200, r.text
    return inb


async def new_order(client, h, items, paid=True):
    r = await client.post("/api/v1/orders", headers=h, json={"customer": {"name": "Sari"}, "shipping": ADDR,
                                                             "items": items, "paid": paid})
    assert r.status_code == 201, r.text
    return r.json()


async def test_inbound_receiving_putaway_and_exceptions(client, sa_token):
    c = await setup_wh(client, sa_token)
    h, wh = c["h"], c["wh"]["id"]
    inb = (await client.post("/api/v1/wms/inbound", headers=h, json={
        "warehouse_id": wh, "supplier": "PT Kain", "reference": "PO-77",
        "lines": [{"sku_id": c["a"]["id"], "expected_qty": 10}]})).json()
    assert inb["number"].startswith("IN-") and inb["status"] == "RECEIVING"
    ev = "dev1-" + uuid.uuid4().hex
    for _ in range(2):  # dikirim ulang dari antrean offline → tetap dihitung sekali
        r = await client.post(f"/api/v1/wms/inbound/{inb['id']}/receive", headers=h,
                              json={"barcode": "899000000001", "quantity": 8, "client_event_id": ev})
        assert r.status_code == 200 and r.json()["received_qty"] == 8
    await client.post(f"/api/v1/wms/inbound/{inb['id']}/receive", headers=h, json={"barcode": "TSH-M", "quantity": 1, "damaged": True})
    await client.post(f"/api/v1/wms/inbound/{inb['id']}/receive", headers=h, json={"barcode": "TSH-L", "quantity": 3})
    assert (await client.post(f"/api/v1/wms/inbound/{inb['id']}/receive", headers=h, json={"barcode": "NOPE"})).status_code == 404
    done = (await client.post(f"/api/v1/wms/inbound/{inb['id']}/complete", headers=h)).json()
    assert done["result"] == {"putaway_tasks": 2, "units_received": 11, "units_damaged": 1}
    inv = {x["sku_code"]: x for x in (await client.get(f"/api/v1/inventory?warehouse_id={wh}", headers=h)).json()["items"]}
    assert (inv["TSH-M"]["on_hand"], inv["TSH-M"]["damaged"], inv["TSH-L"]["on_hand"]) == (8, 1, 3)
    exc = {e["exc_type"] for e in (await client.get(f"/api/v1/wms/exceptions?warehouse_id={wh}", headers=h)).json()}
    assert exc == {"SHORT_RECEIPT", "OVER_RECEIPT", "DAMAGED"}  # M kurang 1 dari 10, L tidak di dokumen, M rusak 1
    assert (await client.post(f"/api/v1/wms/inbound/{inb['id']}/receive", headers=h, json={"barcode": "TSH-M"})).status_code == 409

    tasks = {t["sku_code"]: t for t in (await client.get(f"/api/v1/wms/tasks?warehouse_id={wh}&task_type=PUTAWAY", headers=h)).json()}
    t = tasks["TSH-M"]
    assert (await client.post(f"/api/v1/wms/tasks/{t['id']}/putaway", headers=h, json={"location_code": "A-01", "quantity": 1})).status_code == 422  # RACK bukan BIN
    assert (await client.post(f"/api/v1/wms/tasks/{t['id']}/putaway", headers=h, json={"location_code": "A-01-1-B1", "quantity": 9})).status_code == 422
    r = await client.post(f"/api/v1/wms/tasks/{t['id']}/putaway", headers=h, json={"location_code": "a-01-1-b1", "quantity": 5})
    assert r.json()["status"] == "IN_PROGRESS" and r.json()["placed_in"] == "A-01-1-B1"
    r = await client.post(f"/api/v1/wms/tasks/{t['id']}/putaway", headers=h, json={"location_code": "A-01-1-B2", "quantity": 3})
    assert r.json()["status"] == "DONE"
    look = (await client.get(f"/api/v1/wms/scan?warehouse_id={wh}&code=899000000001", headers=h)).json()
    assert look["type"] == "SKU" and look["unplaced"] == 0 and [b["quantity"] for b in look["bins"]] == [5, 3]
    assert (await client.get(f"/api/v1/wms/scan?warehouse_id={wh}&code=A-01-1-B1", headers=h)).json()["type"] == "LOCATION"
    unpl = (await client.get(f"/api/v1/wms/unplaced?warehouse_id={wh}", headers=h)).json()
    assert [(x["sku_code"], x["unplaced"]) for x in unpl] == [("TSH-L", 3)]
    assert (await client.get("/api/v1/inventory/reconcile", headers=h)).json()["ok"]


async def test_wave_picking_follows_bins_and_validates_scans(client, sa_token):
    c = await setup_wh(client, sa_token)
    h, wh = c["h"], c["wh"]["id"]
    await receive_and_putaway(client, c, {("a", "A-01-1-B1"): 3, ("a", "B-01-1-B1"): 5})
    o1 = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 4, "unit_price": "1"}])
    o2 = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 2, "unit_price": "1"}])
    assert o1["status"] == o2["status"] == "ALLOCATED"
    r = await client.post("/api/v1/wms/waves", headers=h, json={"warehouse_id": wh})
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["number"].startswith("WV-") and w["orders"] == 2 and w["units"] == 6
    tasks = (await client.get(f"/api/v1/wms/tasks?warehouse_id={wh}&task_type=PICK", headers=h)).json()
    assert [(t["from_location"], t["quantity"], t["order_number"]) for t in tasks] == [
        ("A-01-1-B1", 3, o1["order_number"]), ("B-01-1-B1", 1, o1["order_number"]), ("B-01-1-B1", 2, o2["order_number"])]
    t0 = tasks[0]
    r = await client.post(f"/api/v1/wms/tasks/{t0['id']}/pick", headers=h, json={"location_code": "B-01-1-B1", "barcode": "TSH-M"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "WRONG_LOCATION"
    r = await client.post(f"/api/v1/wms/tasks/{t0['id']}/pick", headers=h, json={"location_code": "A-01-1-B1", "barcode": "TSH-L"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "WRONG_SKU"
    for t in tasks:
        r = await client.post(f"/api/v1/wms/tasks/{t['id']}/pick", headers=h,
                              json={"location_code": t["from_location"], "barcode": "899000000001", "quantity": t["quantity"]})
        assert r.status_code == 200 and r.json()["status"] == "DONE", r.text
    assert (await client.get(f"/api/v1/wms/waves?warehouse_id={wh}", headers=h)).json()[0]["status"] == "DONE"
    bins = {x["location"]: x["quantity"] for x in (await client.get(f"/api/v1/wms/bins?warehouse_id={wh}", headers=h)).json()}
    assert bins == {"B-01-1-B1": 2}
    assert (await client.get("/api/v1/inventory/reconcile", headers=h)).json()["ok"]


async def test_packing_verification_and_ship(client, sa_token):
    c = await setup_wh(client, sa_token)
    h, wh = c["h"], c["wh"]["id"]
    await receive_and_putaway(client, c, {("a", "A-01-1-B1"): 5, ("b", "A-01-1-B2"): 5})
    o = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 2, "unit_price": "1"},
                                    {"sku_id": c["b"]["id"], "quantity": 1, "unit_price": "1"}])
    num = o["order_number"]
    await client.post(f"/api/v1/orders/{o['id']}/actions/start_picking", headers=h)
    r = await client.post(f"/api/v1/wms/pack/{num}/scan", headers=h, json={"barcode": "TSH-M"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PICK_INCOMPLETE"
    for t in (await client.get(f"/api/v1/wms/tasks?warehouse_id={wh}&order_id={o['id']}", headers=h)).json():
        await client.post(f"/api/v1/wms/tasks/{t['id']}/pick", headers=h,
                          json={"location_code": t["from_location"], "barcode": t["sku_code"], "quantity": t["quantity"]})
    view = (await client.get(f"/api/v1/wms/pack/{num}", headers=h)).json()
    assert view["picking_complete"] and view["expected_weight_g"] == 650
    assert (await client.post(f"/api/v1/wms/pack/{num}/scan", headers=h, json={"barcode": "899000000001"})).json()["view"]["status"] == "PACKING"
    assert (await client.post(f"/api/v1/wms/pack/{num}/complete", headers=h, json={"weight_g": 650})).status_code == 409
    await client.post(f"/api/v1/wms/pack/{num}/scan", headers=h, json={"barcode": "899000000001"})
    r = await client.post(f"/api/v1/wms/pack/{num}/scan", headers=h, json={"barcode": "899000000001"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "OVER_SCAN"
    await client.post(f"/api/v1/wms/pack/{num}/scan", headers=h, json={"barcode": "TSH-L"})
    r = await client.post(f"/api/v1/wms/pack/{num}/complete", headers=h, json={"weight_g": 1200})
    assert r.status_code == 422 and r.json()["error"]["code"] == "WEIGHT_MISMATCH"
    r = await client.post(f"/api/v1/wms/pack/{num}/complete", headers=h, json={"weight_g": 1200, "override_reason": "Kardus tebal"})
    assert r.status_code == 200 and r.json()["status"] == "READY_TO_SHIP"
    exc = (await client.get(f"/api/v1/wms/exceptions?warehouse_id={wh}", headers=h)).json()
    assert any(e["exc_type"] == "WEIGHT_MISMATCH" and e["order_number"] == num for e in exc)
    r = await client.post(f"/api/v1/orders/{o['id']}/actions/ship", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_SHIPMENT"  # wajib ada resi
    await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o["id"], "courier_code": "sicepat",
                                                                     "tracking_number": "SCP000111222"})
    assert (await client.post(f"/api/v1/orders/{o['id']}/actions/ship", headers=h)).json()["status"] == "SHIPPED"
    inv = {x["sku_code"]: x for x in (await client.get(f"/api/v1/inventory?warehouse_id={wh}", headers=h)).json()["items"]}
    assert (inv["TSH-M"]["on_hand"], inv["TSH-M"]["reserved"]) == (3, 0)
    assert (await client.get("/api/v1/inventory/reconcile", headers=h)).json()["ok"]


async def test_short_pick_exception_and_repick(client, sa_token):
    c = await setup_wh(client, sa_token)
    h, wh = c["h"], c["wh"]["id"]
    await receive_and_putaway(client, c, {("a", "A-01-1-B1"): 2, ("a", "B-01-1-B2"): 4})
    o = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 2, "unit_price": "1"}])
    await client.post(f"/api/v1/orders/{o['id']}/actions/start_picking", headers=h)
    t = (await client.get(f"/api/v1/wms/tasks?warehouse_id={wh}&order_id={o['id']}", headers=h)).json()[0]
    assert t["from_location"] == "A-01-1-B1"
    r = await client.post(f"/api/v1/wms/tasks/{t['id']}/short", headers=h, json={"note": "Bin kosong"})
    assert r.status_code == 200 and r.json()["task"]["status"] == "SHORT"
    exc = (await client.get(f"/api/v1/wms/exceptions?warehouse_id={wh}", headers=h)).json()[0]
    assert exc["exc_type"] == "MISSING" and exc["can_repick"] and exc["quantity"] == 2
    r = await client.post(f"/api/v1/wms/exceptions/{exc['id']}/resolve", headers=h, json={"action": "REPICK", "note": "Ambil dari zona B"})
    assert r.status_code == 200 and "task picking pengganti" in r.json()["resolution"]
    nt = (await client.get(f"/api/v1/wms/tasks?warehouse_id={wh}&order_id={o['id']}", headers=h)).json()
    assert [(x["from_location"], x["quantity"]) for x in nt] == [("B-01-1-B2", 2)]
    await client.post(f"/api/v1/wms/tasks/{nt[0]['id']}/pick", headers=h, json={"location_code": "B-01-1-B2", "barcode": "TSH-M", "quantity": 2})
    assert (await client.get(f"/api/v1/wms/pack/{o['order_number']}", headers=h)).json()["picking_complete"]


async def test_cycle_count_blind_and_approval(client, sa_token):
    c = await setup_wh(client, sa_token)
    h, wh = c["h"], c["wh"]["id"]
    await receive_and_putaway(client, c, {("a", "A-01-1-B1"): 10, ("b", "A-01-1-B2"): 4})
    cc = (await client.post("/api/v1/wms/counts", headers=h, json={"warehouse_id": wh, "prefix": "A-01"})).json()
    assert cc["number"].startswith("CC-") and len(cc["lines"]) == 2 and cc["lines"][0]["system_qty"] == 10
    await client.post("/api/v1/users", headers=h, json={"email": "op@x.nexvora.id", "full_name": "Op", "password": PW, "roles": ["WAREHOUSE_OPERATOR"]})
    op = auth((await login(client, c["slug"], "op@x.nexvora.id")).json()["access_token"])
    blind = (await client.get(f"/api/v1/wms/counts/{cc['id']}", headers=op)).json()
    assert blind["blind"] and all(x["system_qty"] is None for x in blind["lines"])
    for loc, code, n in [("A-01-1-B1", "TSH-M", 9), ("A-01-1-B2", "TSH-L", 4), ("A-01-1-B2", "TSH-M", 1)]:
        r = await client.post(f"/api/v1/wms/counts/{cc['id']}/lines", headers=op, json={"location_code": loc, "barcode": code, "counted_qty": n})
        assert r.status_code == 200, r.text
    assert (await client.post(f"/api/v1/wms/counts/{cc['id']}/lines", headers=op,
                              json={"location_code": "B-01-1-B1", "barcode": "TSH-M", "counted_qty": 1})).status_code == 422
    assert (await client.post(f"/api/v1/wms/counts/{cc['id']}/approve", headers=h)).status_code == 409  # belum submit
    assert (await client.post(f"/api/v1/wms/counts/{cc['id']}/submit", headers=op)).status_code == 200
    assert (await client.post(f"/api/v1/wms/counts/{cc['id']}/approve", headers=op)).status_code == 403
    r = (await client.post(f"/api/v1/wms/counts/{cc['id']}/approve", headers=h)).json()
    assert r["result"] == {"lines": 3, "matched": 1, "adjusted": 2, "accuracy_pct": 33.3}
    bins = {(x["location"], x["sku_code"]): x["quantity"] for x in (await client.get(f"/api/v1/wms/bins?warehouse_id={wh}", headers=h)).json()}
    assert bins == {("A-01-1-B1", "TSH-M"): 9, ("A-01-1-B2", "TSH-L"): 4, ("A-01-1-B2", "TSH-M"): 1}
    inv = {x["sku_code"]: x["on_hand"] for x in (await client.get(f"/api/v1/inventory?warehouse_id={wh}", headers=h)).json()["items"]}
    assert inv == {"TSH-M": 10, "TSH-L": 4}  # -1 di B1, +1 di B2
    led = (await client.get(f"/api/v1/inventory/ledger?reference_id={cc['number']}", headers=h)).json()
    assert sorted(x["d_on_hand"] for x in led) == [-1, 1]
    assert (await client.get("/api/v1/inventory/reconcile", headers=h)).json()["ok"]


async def test_adjustment_respects_bins_and_permissions(client, sa_token):
    c = await setup_wh(client, sa_token)
    h, wh = c["h"], c["wh"]["id"]
    await receive_and_putaway(client, c, {("a", "A-01-1-B1"): 5})
    body = {"warehouse_id": wh, "sku_id": c["a"]["id"], "reason_code": "LOST", "delta": -2, "note": "hilang"}
    r = await client.post("/api/v1/inventory/adjustments", headers=h, json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LOCATION_REQUIRED"
    assert (await client.post("/api/v1/inventory/adjustments", headers=h, json=body | {"location_code": "A-01-1-B1"})).status_code == 201
    assert (await client.get(f"/api/v1/wms/bins?warehouse_id={wh}", headers=h)).json()[0]["quantity"] == 3
    await client.post("/api/v1/users", headers=h, json={"email": "op@x.nexvora.id", "full_name": "Op", "password": PW, "roles": ["WAREHOUSE_OPERATOR"]})
    op = auth((await login(client, c["slug"], "op@x.nexvora.id")).json()["access_token"])
    assert (await client.post("/api/v1/wms/waves", headers=op, json={"warehouse_id": wh})).status_code == 403
    assert (await client.post("/api/v1/wms/inbound", headers=op, json={"warehouse_id": wh})).status_code == 403
    assert (await client.get(f"/api/v1/wms/stats?warehouse_id={wh}", headers=op)).status_code == 200
    other = await setup_wh(client, sa_token)
    task = (await client.get(f"/api/v1/wms/tasks?warehouse_id={wh}&status=ALL", headers=h)).json()[0]
    assert (await client.post(f"/api/v1/wms/tasks/{task['id']}/putaway", headers=other["h"],
                              json={"location_code": "A-01-1-B1", "quantity": 1})).status_code == 404
    assert (await client.get(f"/api/v1/wms/bins?warehouse_id={wh}", headers=other["h"])).json() == []
