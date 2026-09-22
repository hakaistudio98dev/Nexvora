import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant_context
from app.modules.billing.service import run_lifecycle
from tests.conftest import PW, auth, login, make_tenant

pytestmark = pytest.mark.asyncio(loop_scope="session")
ADDR = {"address": "Jl. Sudirman No. 1", "city": "Jakarta"}


async def signup(client, plan=None):
    slug = "s" + uuid.uuid4().hex[:8]
    body = {"company_name": "Toko " + slug, "slug": slug, "full_name": "Pemilik", "email": f"owner@{slug}.nexvora.id",
            "password": PW}
    if plan:
        body["plan_code"] = plan
    r = await client.post("/api/v1/public/signup", json=body)
    assert r.status_code == 201, r.text
    tok = (await login(client, slug, body["email"])).json()
    return slug, auth(tok["access_token"])


async def set_sub(tenant_slug, **fields):
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        await s.execute(text(f"UPDATE subscriptions SET {sets} WHERE tenant_id = "
                             "(SELECT id FROM tenants WHERE slug = :slug)"), {**fields, "slug": tenant_slug})


async def test_public_plans_and_signup_trial(client):
    ratelimit.reset_memory()
    plans = (await client.get("/api/v1/public/plans")).json()
    assert [p["code"] for p in plans] == ["starter", "growth", "enterprise"]
    slug, h = await signup(client)
    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    sub = me["subscription"]
    assert sub["plan_code"] == "growth" and sub["status"] == "TRIALING" and "wms" in sub["features"]
    assert "billing:manage" in me["permissions"]
    r = await client.post("/api/v1/public/signup", json={"company_name": "Toko X", "slug": slug, "full_name": "Yudi",
                                                         "email": "a@b.nexvora.id", "password": PW})
    assert r.status_code == 409
    r = await client.post("/api/v1/public/signup", json={"company_name": "Toko X", "slug": "platform", "full_name": "Yudi",
                                                         "email": "a@b.nexvora.id", "password": PW})
    assert r.status_code == 409, r.text
    r = await client.post("/api/v1/public/signup", json={"company_name": "Toko X", "slug": "zzz-" + uuid.uuid4().hex[:6],
                                                         "full_name": "Yudi", "email": "a@b.nexvora.id", "password": PW,
                                                         "plan_code": "enterprise"})
    assert r.status_code == 422  # enterprise lewat sales


async def test_starter_feature_gate_and_limits(client):
    ratelimit.reset_memory()
    slug, h = await signup(client, "starter")
    w = await client.post("/api/v1/warehouses", headers=h, json={"code": "JKT-01", "name": "Jakarta"})
    assert w.status_code == 201
    r = await client.post("/api/v1/warehouses", headers=h, json={"code": "BDG-01", "name": "Bandung"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "PLAN_LIMIT"
    r = await client.get(f"/api/v1/wms/stats?warehouse_id={w.json()['id']}", headers=h)
    assert r.status_code == 402 and r.json()["error"]["code"] == "FEATURE_NOT_IN_PLAN" and "Growth" in r.json()["error"]["message"]
    for i in range(2):
        assert (await client.post("/api/v1/users", headers=h, json={"email": f"u{i}@x.nexvora.id", "full_name": "Ujang",
                                                                     "password": PW, "roles": ["VIEWER"]})).status_code == 201
    r = await client.post("/api/v1/users", headers=h, json={"email": "u9@x.nexvora.id", "full_name": "Ujang", "password": PW, "roles": ["VIEWER"]})
    assert r.status_code == 402  # starter: 3 pengguna
    assert (await client.get("/api/v1/orders", headers=h)).status_code == 200  # OMS tetap ada
    assert (await client.post("/api/v1/api-keys", headers=h, json={"name": "x", "scopes": ["order:read"]})).status_code == 402


async def test_read_only_when_suspended_but_billing_works(client):
    ratelimit.reset_memory()
    slug, h = await signup(client)
    prod = (await client.post("/api/v1/products", headers=h, json={"code": "P1", "name": "P"})).json()
    await set_sub(slug, status="SUSPENDED")
    assert (await client.get("/api/v1/products", headers=h)).status_code == 200
    r = await client.post("/api/v1/products", headers=h, json={"code": "P2", "name": "P"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "SUBSCRIPTION_INACTIVE"
    assert (await client.post(f"/api/v1/products/{prod['id']}/skus", headers=h, json={"sku_code": "P1-A"})).status_code == 402
    assert (await client.get("/api/v1/billing/subscription", headers=h)).status_code == 200
    r = await client.post("/api/v1/billing/checkout", headers=h, json={"plan_code": "starter"})
    assert r.status_code == 201 and r.json()["status"] == "OPEN"


async def test_checkout_mark_paid_activates_and_renews(client, sa_token):
    ratelimit.reset_memory()
    slug, h = await signup(client)
    r = await client.post("/api/v1/billing/checkout", headers=h, json={"plan_code": "growth", "billing_cycle": "YEARLY"})
    inv = r.json()
    assert inv["number"].startswith("INV-") and inv["amount"] == "8990000.00" and inv["kind"] == "NEW"
    # tenant tidak boleh menandai lunas sendiri
    assert (await client.post(f"/api/v1/platform/invoices/{inv['id']}/mark-paid", headers=h,
                              json={"payment_ref": "TRF-1"})).status_code == 403
    sa = auth(sa_token)
    r = await client.post(f"/api/v1/platform/invoices/{inv['id']}/mark-paid", headers=sa, json={"payment_ref": "TRF-001"})
    assert r.status_code == 200 and r.json()["status"] == "PAID"
    again = await client.post(f"/api/v1/platform/invoices/{inv['id']}/mark-paid", headers=sa, json={"payment_ref": "TRF-001"})
    assert again.status_code == 200  # idempoten
    sub = (await client.get("/api/v1/billing/subscription", headers=h)).json()["subscription"]
    assert sub["status"] == "ACTIVE" and sub["plan_code"] == "growth"
    end = datetime.fromisoformat(sub["current_period_end"])
    assert 360 <= (end - datetime.now(UTC)).days <= 366
    # perpanjangan: periode baru disambung dari akhir periode lama
    inv2 = (await client.post("/api/v1/billing/checkout", headers=h, json={"plan_code": "growth", "billing_cycle": "YEARLY"})).json()
    assert inv2["kind"] == "RENEWAL"
    await client.post(f"/api/v1/platform/invoices/{inv2['id']}/mark-paid", headers=sa, json={"payment_ref": "TRF-002"})
    sub2 = (await client.get("/api/v1/billing/subscription", headers=h)).json()["subscription"]
    assert (datetime.fromisoformat(sub2["current_period_end"]) - end).days >= 364
    rows = (await client.get("/api/v1/platform/subscriptions", headers=sa)).json()
    assert any(x["slug"] == slug and x["status"] == "ACTIVE" for x in rows)


async def test_downgrade_blocked_when_usage_exceeds(client):
    ratelimit.reset_memory()
    slug, h = await signup(client)
    for c in ("A-01", "B-01"):
        await client.post("/api/v1/warehouses", headers=h, json={"code": c, "name": c})
    r = await client.post("/api/v1/billing/checkout", headers=h, json={"plan_code": "starter"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "USAGE_EXCEEDS_PLAN"


async def test_lifecycle_trial_to_suspended_and_renewal_invoice(client):
    ratelimit.reset_memory()
    slug, h = await signup(client)
    past = datetime.now(UTC) - timedelta(minutes=1)
    await set_sub(slug, trial_ends_at=past)
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        await run_lifecycle(s)
    sub = (await client.get("/api/v1/billing/subscription", headers=h)).json()["subscription"]
    assert sub["status"] == "PAST_DUE" and sub["grace_until"]
    assert (await client.post("/api/v1/products", headers=h, json={"code": "OK1", "name": "masih bisa"})).status_code == 201
    await set_sub(slug, grace_until=past)
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        await run_lifecycle(s)
    assert (await client.get("/api/v1/billing/subscription", headers=h)).json()["subscription"]["status"] == "SUSPENDED"
    # langganan aktif yang mendekati akhir periode → invoice perpanjangan otomatis
    await set_sub(slug, status="ACTIVE", grace_until=None, current_period_end=datetime.now(UTC) + timedelta(days=3))
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        await run_lifecycle(s)
    invs = (await client.get("/api/v1/billing/invoices", headers=h)).json()
    assert invs and invs[0]["kind"] == "RENEWAL" and invs[0]["status"] == "OPEN"


async def test_orders_soft_quota_never_blocks(client):
    ratelimit.reset_memory()
    slug, h = await signup(client, "starter")
    prod = (await client.post("/api/v1/products", headers=h, json={"code": "Q", "name": "Q"})).json()
    sku = (await client.post(f"/api/v1/products/{prod['id']}/skus", headers=h, json={"sku_code": "Q-1"})).json()
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        await s.execute(text("""UPDATE plans SET limits = jsonb_set(limits, '{orders_per_month}', '1') WHERE code='starter'"""))
    try:
        for _ in range(2):
            r = await client.post("/api/v1/orders", headers=h, json={"customer": {"name": "Andi"}, "shipping": ADDR,
                                                                     "items": [{"sku_id": sku["id"], "quantity": 1, "unit_price": "1"}]})
            assert r.status_code == 201
        info = (await client.get("/api/v1/billing/subscription", headers=h)).json()
        assert info["usage"]["orders_per_month"] == 2 and any("melebihi" in w for w in info["warnings"])
    finally:
        async with SessionLocal() as s, s.begin():
            await set_tenant_context(s, None, superadmin=True)
            await s.execute(text("""UPDATE plans SET limits = jsonb_set(limits, '{orders_per_month}', '1000') WHERE code='starter'"""))


async def test_api_key_lifecycle_and_scopes(client):
    ratelimit.reset_memory()
    slug, h = await signup(client)
    prod = (await client.post("/api/v1/products", headers=h, json={"code": "K", "name": "K"})).json()
    await client.post(f"/api/v1/products/{prod['id']}/skus", headers=h, json={"sku_code": "K-1"})
    r = await client.post("/api/v1/api-keys", headers=h, json={"name": "Shopee", "scopes": ["order:read", "order:write", "product:read"]})
    assert r.status_code == 201
    k = r.json()
    assert k["key"].startswith("nxk_") and "key" not in (await client.get("/api/v1/api-keys", headers=h)).json()[0]
    kh = {"X-API-Key": k["key"]}
    r = await client.post("/api/v1/orders", headers=kh, json={"channel": "SHOPEE", "external_ref": "SHP-1", "customer": {"name": "Budi"},
                                                              "shipping": ADDR, "items": [{"sku_code": "K-1", "quantity": 1, "unit_price": "5"}]})
    assert r.status_code == 201, r.text
    assert (await client.get("/api/v1/orders", headers=kh)).json()["total"] == 1
    assert (await client.post("/api/v1/products", headers=kh, json={"code": "Z", "name": "Z"})).status_code == 403  # scope
    assert (await client.get("/api/v1/users", headers=kh)).status_code == 403
    assert (await client.get("/api/v1/auth/me", headers=kh)).status_code == 403  # bukan manusia
    assert (await client.post("/api/v1/api-keys", headers=h, json={"name": "x", "scopes": ["user:write"]})).status_code == 422
    audit = (await client.get("/api/v1/audit-logs?entity_type=order", headers=h)).json()
    assert audit[0]["actor_user_id"] is None
    await client.post(f"/api/v1/api-keys/{k['id']}/revoke", headers=h)
    assert (await client.get("/api/v1/orders", headers=kh)).status_code == 401
    assert (await client.get("/api/v1/orders", headers={"X-API-Key": "nxk_bad_key"})).status_code == 401


async def test_midtrans_webhook_signature(client, sa_token):
    ratelimit.reset_memory()
    slug, h = await signup(client)
    inv = (await client.post("/api/v1/billing/checkout", headers=h, json={"plan_code": "starter"})).json()
    st = get_settings()
    old = st.midtrans_server_key
    st.midtrans_server_key = "SB-test-server-key"
    try:
        def signed(amount, status="settlement"):
            p = {"order_id": inv["id"], "status_code": "200", "gross_amount": amount, "transaction_status": status,
                 "transaction_id": "mt-123"}
            p["signature_key"] = hashlib.sha512(f"{p['order_id']}200{amount}{st.midtrans_server_key}".encode()).hexdigest()
            return p
        bad = signed("299000.00") | {"signature_key": "x"}
        assert (await client.post("/api/v1/billing/webhooks/midtrans", json=bad)).status_code == 403
        assert (await client.post("/api/v1/billing/webhooks/midtrans", json=signed("1.00"))).status_code == 422
        assert (await client.post("/api/v1/billing/webhooks/midtrans", json=signed("299000.00"))).status_code == 200
        sub = (await client.get("/api/v1/billing/subscription", headers=h)).json()["subscription"]
        assert sub["status"] == "ACTIVE" and sub["plan_code"] == "starter"
    finally:
        st.midtrans_server_key = old


async def test_platform_plan_edit_is_admin_only(client, sa_token):
    ratelimit.reset_memory()
    slug, h = await signup(client)
    assert (await client.patch("/api/v1/platform/plans/starter", headers=h, json={"price_monthly": "1"})).status_code == 403
    sa = auth(sa_token)
    r = await client.patch("/api/v1/platform/plans/starter", headers=sa, json={"description": "Paket awal"})
    assert r.status_code == 200 and r.json()["description"] == "Paket awal"
    assert (await client.patch("/api/v1/platform/plans/starter", headers=sa, json={"features": ["teleport"]})).status_code == 422
