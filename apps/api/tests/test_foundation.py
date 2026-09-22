import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, set_tenant_context
from tests.conftest import PW, auth, login, make_tenant

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_login_and_me(client, sa_token):
    slug, email, tok = await make_tenant(client, sa_token)
    r = await client.get("/api/v1/auth/me", headers=auth(tok["access_token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["tenant"]["slug"] == slug and body["roles"] == ["TENANT_ADMIN"]
    assert "product:write" in body["permissions"] and "tenant:manage" not in body["permissions"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-correlation-id"]


async def test_generic_error_and_lockout(client, sa_token):
    slug, email, _ = await make_tenant(client, sa_token)
    r1 = await login(client, slug, "nobody@x.nexvora.id", "wrong")
    r2 = await login(client, slug, email, "wrong")
    r3 = await login(client, "no-such-tenant", email, "wrong")
    assert r1.status_code == r2.status_code == r3.status_code == 401
    assert r1.json()["error"]["message"] == r2.json()["error"]["message"] == r3.json()["error"]["message"]
    for _ in range(4):
        await login(client, slug, email, "wrong")
    # sudah 5x gagal → terkunci, password benar pun ditolak
    assert (await login(client, slug, email)).status_code == 401


async def test_tenant_isolation_api_and_db(client, sa_token):
    _, _, a = await make_tenant(client, sa_token)
    _, _, b = await make_tenant(client, sa_token)
    r = await client.post("/api/v1/products", headers=auth(a["access_token"]),
                          json={"code": "TSH-01", "name": "Kaos"})
    assert r.status_code == 201
    pid = r.json()["id"]
    # tenant B tidak bisa melihat / membuat SKU di produk tenant A
    assert (await client.get(f"/api/v1/products/{pid}", headers=auth(b["access_token"]))).status_code == 404
    assert (await client.post(f"/api/v1/products/{pid}/skus", headers=auth(b["access_token"]),
                              json={"sku_code": "X-1"})).status_code == 404
    assert (await client.get("/api/v1/products", headers=auth(b["access_token"]))).json()["total"] == 0
    # kode sama boleh dipakai di tenant lain
    assert (await client.post("/api/v1/products", headers=auth(b["access_token"]),
                              json={"code": "TSH-01", "name": "Kaos B"})).status_code == 201

    # Lapisan DB: tanpa konteks tenant, role app melihat 0 baris (RLS)
    async with SessionLocal() as s, s.begin():
        assert await s.scalar(text("SELECT count(*) FROM products")) == 0
        assert await s.scalar(text("SELECT count(*) FROM users")) == 0


async def test_rls_blocks_cross_tenant_write(client, sa_token):
    _, _, a = await make_tenant(client, sa_token)
    _, _, b = await make_tenant(client, sa_token)
    me_a = (await client.get("/api/v1/auth/me", headers=auth(a["access_token"]))).json()
    me_b = (await client.get("/api/v1/auth/me", headers=auth(b["access_token"]))).json()
    with pytest.raises(Exception, match="row-level security"):
        async with SessionLocal() as s, s.begin():
            await set_tenant_context(s, me_a["tenant"]["id"])
            await s.execute(text("INSERT INTO products(tenant_id, code, name) VALUES (:t, 'HACK', 'x')"),
                            {"t": me_b["tenant"]["id"]})


async def test_rbac_viewer_cannot_write(client, sa_token):
    slug, _, a = await make_tenant(client, sa_token)
    r = await client.post("/api/v1/users", headers=auth(a["access_token"]), json={
        "email": "viewer@x.nexvora.id", "full_name": "Viewer", "password": PW, "roles": ["VIEWER"]})
    assert r.status_code == 201
    v = (await login(client, slug, "viewer@x.nexvora.id")).json()
    assert (await client.get("/api/v1/products", headers=auth(v["access_token"]))).status_code == 200
    r = await client.post("/api/v1/products", headers=auth(v["access_token"]), json={"code": "A", "name": "A"})
    assert r.status_code == 403
    # tenant admin tidak boleh memberi SUPER_ADMIN
    r = await client.post("/api/v1/users", headers=auth(a["access_token"]), json={
        "email": "evil@x.nexvora.id", "full_name": "Evil", "password": PW, "roles": ["SUPER_ADMIN"]})
    assert r.status_code == 400


async def test_deactivate_revokes_sessions(client, sa_token):
    slug, _, a = await make_tenant(client, sa_token)
    u = (await client.post("/api/v1/users", headers=auth(a["access_token"]), json={
        "email": "op@x.nexvora.id", "full_name": "Op", "password": PW, "roles": ["WAREHOUSE_OPERATOR"]})).json()
    op = (await login(client, slug, "op@x.nexvora.id")).json()
    assert (await client.get("/api/v1/auth/me", headers=auth(op["access_token"]))).status_code == 200
    r = await client.patch(f"/api/v1/users/{u['id']}", headers=auth(a["access_token"]), json={"is_active": False})
    assert r.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=auth(op["access_token"]))).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": op["refresh_token"]})).status_code == 401


async def test_refresh_rotation_and_reuse_detection(client, sa_token):
    _, _, a = await make_tenant(client, sa_token)
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": a["refresh_token"]})
    assert r1.status_code == 200
    new = r1.json()
    # token lama dipakai lagi → ditolak dan seluruh keluarga sesi dicabut
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": a["refresh_token"]})).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]})).status_code == 401


async def test_warehouse_hierarchy(client, sa_token):
    _, _, a = await make_tenant(client, sa_token)
    h = auth(a["access_token"])
    w = (await client.post("/api/v1/warehouses", headers=h, json={"code": "JKT-01", "name": "Jakarta 1"})).json()
    base = f"/api/v1/warehouses/{w['id']}/locations"
    zone = (await client.post(base, headers=h, json={"type": "ZONE", "code": "A"})).json()
    # BIN langsung di ZONE → ditolak
    assert (await client.post(base, headers=h, json={"type": "BIN", "code": "B1",
                                                     "parent_id": zone["id"]})).status_code == 400
    rack = (await client.post(base, headers=h, json={"type": "RACK", "code": "03", "parent_id": zone["id"]})).json()
    shelf = (await client.post(base, headers=h, json={"type": "SHELF", "code": "2", "parent_id": rack["id"]})).json()
    b = (await client.post(base, headers=h, json={"type": "BIN", "code": "B14", "parent_id": shelf["id"]})).json()
    assert b["full_code"] == "A-03-2-B14"
    assert (await client.post(base, headers=h, json={"type": "ZONE", "code": "A"})).status_code == 409
    assert len((await client.get(base, headers=h)).json()) == 4


async def test_audit_log_append_only(client, sa_token):
    _, _, a = await make_tenant(client, sa_token)
    h = auth(a["access_token"])
    await client.post("/api/v1/products", headers=h, json={"code": "AUD-1", "name": "Audit"})
    logs = (await client.get("/api/v1/audit-logs", headers=h)).json()
    assert any(x["action"] == "product.created" for x in logs)
    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    with pytest.raises(Exception):
        async with SessionLocal() as s, s.begin():
            await set_tenant_context(s, me["tenant"]["id"])
            await s.execute(text("UPDATE audit_logs SET action='x'"))
    with pytest.raises(Exception):
        async with SessionLocal() as s, s.begin():
            await set_tenant_context(s, me["tenant"]["id"])
            await s.execute(text("DELETE FROM audit_logs"))


async def test_validation_and_weak_password(client, sa_token):
    _, _, a = await make_tenant(client, sa_token)
    h = auth(a["access_token"])
    r = await client.post("/api/v1/users", headers=h, json={
        "email": "weak@x.nexvora.id", "full_name": "W", "password": "password", "roles": ["VIEWER"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (await client.get("/api/v1/products")).status_code == 401
