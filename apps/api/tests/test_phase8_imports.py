import pytest

from tests.conftest import PW, auth, login
from tests.test_phase3_wms import new_order, setup_wh
from tests.test_phase4_shipping import stocked

pytestmark = pytest.mark.asyncio(loop_scope="session")


def csv_file(text: str, name: str = "data.csv"):
    return {"file": (name, text.encode("utf-8"), "text/csv")}


async def preview(client, h, kind, text, name="data.csv"):
    return await client.post(f"/api/v1/imports/{kind}/preview", headers=h, files=csv_file(text, name))


async def test_template_and_column_validation(client, sa_token):
    c = await setup_wh(client, sa_token)
    h = c["h"]
    r = await client.get("/api/v1/imports/products/template.csv", headers=h)
    assert r.status_code == 200 and r.text.startswith("\ufeff")
    assert "kode_sku" in r.text and "attachment" in r.headers["content-disposition"]
    r = await preview(client, h, "products", "nama_produk,harga\nKaos,1000\n")
    assert r.status_code == 422 and "kode_sku" in r.json()["error"]["message"]
    assert (await preview(client, h, "products", "")).status_code == 422
    assert (await client.get("/api/v1/imports/nope/template.csv", headers=h)).status_code == 404


async def test_import_products_preview_then_commit(client, sa_token):
    c = await setup_wh(client, sa_token)
    h = c["h"]
    # kolom dengan nama alternatif (sku, product_name) dan pemisah titik koma juga diterima
    body = ("product_code;product_name;sku;barcode;varian;berat;batas_stok_menipis\n"
            "MUG;Mug Keramik;MUG-WHT;8991111111111;Putih;350;12\n"
            "MUG;Mug Keramik;MUG-BLK;;Hitam;350;\n"
            "MUG;Mug Keramik;MUG-WHT;;Duplikat;350;\n"          # duplikat dalam file
            "GLS;Gelas;GLS-01;;;bukan angka;\n"                  # berat salah
            ";;;;;;\n")                                          # baris kosong: dilewati diam-diam
    r = (await preview(client, h, "products", body, "produk.csv")).json()
    assert r["valid_rows"] == 3 and r["error_rows"] == 2
    msgs = " ".join(e["message"] for e in r["errors"])
    assert "dua kali" in msgs and "angka" in msgs
    assert {e["row"] for e in r["errors"]} == {4, 5}              # nomor baris sesuai file asli
    # belum ada yang berubah sebelum dikonfirmasi
    assert (await client.get("/api/v1/products?limit=50", headers=h)).json()["total"] == 1
    assert (await client.post(f"/api/v1/imports/{r['id']}/commit", headers=h, json={})).status_code == 409
    done = (await client.post(f"/api/v1/imports/{r['id']}/commit", headers=h, json={"skip_errors": True})).json()
    assert done["status"] == "COMMITTED" and done["result"]["sku_baru"] == 3 and done["result"]["produk_baru"] == 2
    assert done["skipped"] == 2
    skus = (await client.get("/api/v1/skus?q=MUG", headers=h)).json()
    mug = next(x for x in skus["items"] if x["sku_code"] == "MUG-WHT")
    assert mug["barcode"] == "8991111111111" and mug["weight_g"] == 350 and mug["reorder_point"] == 12
    # dijalankan dua kali tidak menggandakan data
    again = (await client.post(f"/api/v1/imports/{r['id']}/commit", headers=h, json={"skip_errors": True})).json()
    assert again["already"] is True
    assert (await client.get("/api/v1/skus?q=MUG", headers=h)).json()["total"] == 2
    # impor ulang SKU yang sama = memperbarui, bukan menggandakan
    r2 = (await preview(client, h, "products", "kode_produk,nama_produk,kode_sku,berat_gram\nMUG,Mug Keramik,MUG-WHT,400\n")).json()
    assert r2["error_rows"] == 0
    await client.post(f"/api/v1/imports/{r2['id']}/commit", headers=h, json={})
    assert (await client.get("/api/v1/skus?q=MUG-WHT", headers=h)).json()["items"][0]["weight_g"] == 400
    hist = (await client.get("/api/v1/imports/history", headers=h)).json()
    assert hist[0]["status"] == "COMMITTED" and hist[0]["label"] == "Produk & SKU"


async def test_import_stock_and_reallocates_held_orders(client, sa_token):
    c = await setup_wh(client, sa_token)          # gudang + SKU, tanpa stok
    h = c["h"]
    order = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 5, "unit_price": "10000"}])
    assert order["stock_status"] == "OUT_OF_STOCK"
    body = ("kode_gudang,kode_sku,jumlah,catatan\n"
            f"{c['wh']['code']},TSH-M,40,stok opname awal\n"
            "GUDANG-X,TSH-M,10,\n"                 # gudang tidak ada
            f"{c['wh']['code']},SKU-HANTU,5,\n")   # SKU tidak ada
    r = (await preview(client, h, "stock", body)).json()
    assert r["valid_rows"] == 1 and r["error_rows"] == 2
    assert any("tidak ada atau nonaktif" in e["message"] for e in r["errors"])
    assert any("belum terdaftar" in e["message"] for e in r["errors"])
    done = (await client.post(f"/api/v1/imports/{r['id']}/commit", headers=h, json={"skip_errors": True})).json()
    assert done["result"]["unit_masuk"] == 40
    assert done["result"]["order_dialokasikan"] == 1          # order tertahan langsung dapat stok
    o = (await client.get(f"/api/v1/orders/{order['id']}", headers=h)).json()
    assert o["status"] == "ALLOCATED" and o["stock_status"] == "RESERVED"
    inv = (await client.get(f"/api/v1/inventory?warehouse_id={c['wh']['id']}", headers=h)).json()["items"]
    tsh = next(x for x in inv if x["sku_code"] == "TSH-M")
    assert tsh["on_hand"] == 40 and tsh["reserved"] == 5
    led = (await client.get("/api/v1/inventory/ledger?limit=10", headers=h)).json()
    assert any(x["reason_code"] == "IMPORT" for x in led)
    assert (await client.get("/api/v1/inventory/reconcile", headers=h)).json()["ok"]


async def test_receiving_stock_releases_held_orders(client, sa_token):
    c = await setup_wh(client, sa_token)
    h = c["h"]
    o1 = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 3, "unit_price": "1000"}])
    o2 = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 2, "unit_price": "1000"}])
    assert o1["stock_status"] == o2["stock_status"] == "OUT_OF_STOCK"
    r = await client.post("/api/v1/inventory/receipts", headers=h, json={
        "warehouse_id": c["wh"]["id"], "reference": "PO-1",
        "lines": [{"sku_id": c["a"]["id"], "quantity": 4}]})
    assert r.status_code == 201 and r.json()["orders_reallocated"] == 1   # hanya yang pertama muat
    s1 = (await client.get(f"/api/v1/orders/{o1['id']}", headers=h)).json()
    s2 = (await client.get(f"/api/v1/orders/{o2['id']}", headers=h)).json()
    assert s1["stock_status"] == "RESERVED" and s2["stock_status"] == "OUT_OF_STOCK"   # antrean adil: yang lama dulu
    r2 = await client.post("/api/v1/inventory/receipts", headers=h, json={
        "warehouse_id": c["wh"]["id"], "lines": [{"sku_id": c["a"]["id"], "quantity": 10}]})
    assert r2.json()["orders_reallocated"] == 1
    assert (await client.get(f"/api/v1/orders/{o2['id']}", headers=h)).json()["stock_status"] == "RESERVED"


async def test_import_permissions_and_isolation(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    r = (await preview(client, h, "products", "kode_produk,nama_produk,kode_sku\nA,Produk A,A-1\n")).json()
    await client.post("/api/v1/users", headers=h, json={"email": "op@x.nexvora.id", "full_name": "Operator Gudang",
                                                        "password": PW, "roles": ["WAREHOUSE_OPERATOR"]})
    op = auth((await login(client, c["slug"], "op@x.nexvora.id")).json()["access_token"])
    assert (await preview(client, op, "products", "kode_produk,nama_produk,kode_sku\nB,Produk B,B-1\n")).status_code == 403
    other = await setup_wh(client, sa_token)
    assert (await client.post(f"/api/v1/imports/{r['id']}/commit", headers=other["h"], json={})).status_code == 404
    assert (await client.get("/api/v1/imports/history", headers=other["h"])).json() == []
