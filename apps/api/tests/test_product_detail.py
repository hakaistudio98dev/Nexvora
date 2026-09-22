import pytest
from tests.conftest import auth, make_tenant
pytestmark = pytest.mark.asyncio(loop_scope="session")
async def test_product_detail(client, sa_token):
    _, _, t = await make_tenant(client, sa_token); h = auth(t["access_token"])
    p = (await client.post("/api/v1/products", headers=h, json={"code": "X1", "name": "X"})).json()
    await client.post(f"/api/v1/products/{p['id']}/skus", headers=h, json={"sku_code": "X1-M"})
    r = await client.get(f"/api/v1/products/{p['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert [s["sku_code"] for s in r.json()["skus"]] == ["X1-M"]
