import pytest

from tests.conftest import PW, auth, login
from tests.test_phase3_wms import setup_wh
from tests.test_phase4_shipping import ready_order, stocked

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_builtin_catalog_has_local_couriers(client, sa_token):
    c = await setup_wh(client, sa_token)
    codes = {x["code"]: x for x in (await client.get("/api/v1/shipping/couriers", headers=c["h"])).json()}
    for k in ("paxel", "wahana", "tiki", "lalamove", "sap"):
        assert k in codes and not codes[k]["custom"]


async def test_custom_courier_end_to_end(client, sa_token):
    c = await stocked(client, sa_token)
    h, wh = c["h"], c["wh"]["id"]
    body = {"code": "kurir_bdg", "name": "Kurir Kilat Bandung", "services": ["Reguler", "Same day"],
            "tracking_url_template": "https://kilatbdg.id/lacak?no={resi}", "phone": "0812-000"}
    r = await client.post("/api/v1/shipping/couriers", headers=h, json=body)
    assert r.status_code == 201, r.text
    k = r.json()
    assert k["custom"] and [x["code"] for x in k["services"]] == ["reguler", "same_day"] and not k["auto_booking"]
    assert (await client.post("/api/v1/shipping/couriers", headers=h, json=body)).status_code == 409   # duplikat
    assert (await client.post("/api/v1/shipping/couriers", headers=h, json=body | {"code": "jne"})).status_code == 409
    assert (await client.post("/api/v1/shipping/couriers", headers=h,
                              json=body | {"code": "kurir_x", "tracking_url_template": "https://x.id/lacak"})).status_code == 422

    o = await ready_order(client, c)
    r = await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o["id"], "courier_code": "kurir_bdg",
                                                                         "service_code": "tidak_ada", "tracking_number": "KKB-000123"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_SERVICE"
    sh = (await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o["id"], "courier_code": "kurir_bdg",
                                                                           "service_code": "same_day", "tracking_number": "KKB-000123"})).json()
    assert sh["courier_name"] == "Kurir Kilat Bandung" and sh["tracking_url"] == "https://kilatbdg.id/lacak?no=KKB-000123"
    assert sh["events"][0]["description"] == "Resi dibuat (Kurir Kilat Bandung)"
    m = (await client.post("/api/v1/shipping/manifests", headers=h, json={"warehouse_id": wh, "courier_code": "kurir_bdg"})).json()
    assert m["courier_name"] == "Kurir Kilat Bandung"
    assert (await client.post(f"/api/v1/shipping/manifests/{m['id']}/scan", headers=h, json={"code": "KKB-000123"})).status_code == 200
    r = await client.post(f"/api/v1/shipping/manifests/{m['id']}/handover", headers=h, json={"driver_name": "Asep"})
    assert r.status_code == 200 and r.json()["handed_over"] == 1
    await client.post(f"/api/v1/shipping/shipments/{sh['id']}/tracking", headers=h, json={"status": "DELIVERED", "description": "Diterima satpam"})
    assert (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()["status"] == "DELIVERED"

    # kurir kustom tidak bisa dipesan lewat Biteship; nonaktif = tidak bisa dipakai lagi tapi riwayat tetap bernama
    assert (await client.post("/api/v1/shipping/accounts", headers=h, json={"name": "BS", "provider": "biteship", "api_key": "k",
                                                                            "couriers": ["kurir_bdg"]})).status_code == 422
    assert (await client.patch("/api/v1/shipping/couriers/kurir_bdg", headers=h, json={"is_active": False})).json()["is_active"] is False
    o2 = await ready_order(client, c, 1)
    r = await client.post("/api/v1/shipping/shipments", headers=h, json={"order_id": o2["id"], "courier_code": "kurir_bdg", "tracking_number": "KKB-000999"})
    assert r.status_code == 422
    assert (await client.get(f"/api/v1/shipping/shipments/{sh['id']}", headers=h)).json()["courier_name"] == "Kurir Kilat Bandung"
    assert (await client.patch("/api/v1/shipping/couriers/jne", headers=h, json={"name": "Kurir X"})).status_code == 404


async def test_custom_courier_isolation_and_permission(client, sa_token):
    a = await setup_wh(client, sa_token)
    b = await stocked(client, sa_token)
    await client.post("/api/v1/shipping/couriers", headers=a["h"], json={"code": "kurir_a", "name": "Kurir Tenant A"})
    assert "kurir_a" not in {x["code"] for x in (await client.get("/api/v1/shipping/couriers", headers=b["h"])).json()}
    o = await ready_order(client, b, 1)
    r = await client.post("/api/v1/shipping/shipments", headers=b["h"], json={"order_id": o["id"], "courier_code": "kurir_a", "tracking_number": "AAA-111222"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_COURIER"
    await client.post("/api/v1/users", headers=b["h"], json={"email": "op@x.nexvora.id", "full_name": "Operator", "password": PW, "roles": ["WAREHOUSE_OPERATOR"]})
    op = auth((await login(client, b["slug"], "op@x.nexvora.id")).json()["access_token"])
    assert (await client.post("/api/v1/shipping/couriers", headers=op, json={"code": "kurir_z", "name": "Kurir Z"})).status_code == 403
