import json

import httpx
import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant_context
from app.modules.assistant import engine_llm, engine_rules
from tests.conftest import PW, auth, login
from tests.test_phase3_wms import new_order, setup_wh
from tests.test_phase4_shipping import delivered_order, ready_order, stocked

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def ask(client, h, msg):
    r = await client.post("/api/v1/assistant/chat", headers=h, json={"message": msg})
    assert r.status_code == 200, r.text
    return r.json()


def test_rules_engine_understands_indonesian():
    cases = {
        "berapa order hari ini?": ("ringkasan_hari_ini", {}),
        "apa yang perlu dikerjakan": ("ringkasan_hari_ini", {}),
        "stok TSH-BLK-M berapa": ("stok_sku", {"q": "TSH-BLK-M"}),
        "order mana yang terlambat": ("order_terlambat", {}),
        "ada retur baru?": ("retur_terbuka", {}),
        "stok apa yang mau habis": ("risiko_stok", {}),
        "kurir terbaik ke Bandung": ("kurir_terbaik", {"kota": "Bandung"}),
        "laporan penjualan 7 hari": ("kinerja", {"hari": 7}),
        "kinerja bulan ini": ("kinerja", {"hari": 30}),
        "lacak SO-2609-000001": ("lacak_paket", {"kode": "SO-2609-000001"}),
        "detail SO-2609-000001": ("detail_order", {"order_number": "SO-2609-000001"}),
        "mulai picking SO-2609-000001": ("mulai_picking", {"order_number": "SO-2609-000001"}),
        "batalkan SO-2609-000002 karena pelanggan berubah pikiran": ("batalkan_order", {"order_number": "SO-2609-000002"}),
        "tandai SO-2609-000003 sudah dibayar": ("tandai_dibayar", {"order_number": "SO-2609-000003"}),
        "ada yang aneh hari ini": ("temuan_anomali", {}),
    }
    for msg, (tool, args) in cases.items():
        got = engine_rules.parse(msg)
        assert got is not None, msg
        assert got[0] == tool, f"{msg} → {got[0]}, harusnya {tool}"
        for k, v in args.items():
            assert got[1].get(k) == v, f"{msg} → {got[1]}"
    assert engine_rules.parse("cuaca hari apa besok di mars") is None   # di luar cakupan: tidak menebak


async def test_assistant_answers_from_real_data(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    o = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 2, "unit_price": "75000"}])
    r = await ask(client, h, "berapa order hari ini?")
    assert r["engine"] == "rules" and r["cards"][0]["type"] == "metrics"
    r = await ask(client, h, "stok TSH-M")
    assert "TSH-M" in json.dumps(r["cards"][0]["rows"]) and "siap dijual" in r["reply"]
    r = await ask(client, h, f"detail {o['order_number']}")
    assert o["order_number"] in r["reply"] and r["cards"][0]["columns"][0] == "SKU"
    r = await ask(client, h, f"lacak {o['order_number']}")
    assert "Tidak ada pengiriman" in r["reply"]
    r = await ask(client, h, "cuaca besok bagaimana")
    assert "Saya bisa membantu" in r["reply"] and r["cards"] == []
    r = await ask(client, h, "stok SKU-YANG-TIDAK-ADA")
    assert "Tidak ada SKU" in r["reply"]


async def test_command_requires_confirmation_and_is_audited(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    o = (await client.post("/api/v1/orders", headers=h, json={
        "customer": {"name": "Budi Santoso", "phone": "08123456789"},
        "shipping": {"address": "Jl. Melati 2", "city": "Jakarta"},
        "items": [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "50000"}]})).json()
    assert o["status"] == "CREATED"
    r = await ask(client, h, f"tandai {o['order_number']} sudah dibayar")
    pa = r["pending_action"]
    assert pa["name"] == "tandai_dibayar" and o["order_number"] in pa["confirm"]
    # belum dijalankan sebelum dikonfirmasi
    assert (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()["status"] == "CREATED"
    r2 = await client.post("/api/v1/assistant/act", headers=h, json={"name": pa["name"], "args": pa["args"]})
    assert r2.status_code == 200 and "berstatus" in r2.json()["reply"]
    assert (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()["status"] in ("PAID", "ALLOCATED")
    logs = (await client.get("/api/v1/audit-logs?limit=30", headers=h)).json()
    assert any(x["action"] == "assistant.action" for x in logs)
    # perkakas baca tidak boleh lewat /act
    assert (await client.post("/api/v1/assistant/act", headers=h,
                              json={"name": "stok_sku", "args": {"q": "TSH"}})).status_code == 422
    hist = (await client.get("/api/v1/assistant/history", headers=h)).json()
    assert hist[0]["role"] == "user" and any(m["role"] == "assistant" for m in hist)
    await client.delete("/api/v1/assistant/history", headers=h)
    assert (await client.get("/api/v1/assistant/history", headers=h)).json() == []


async def test_assistant_respects_roles_plan_and_tenant(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    # fitur AI belum termasuk paket bawaan
    r = await ask(client, h, "stok apa yang mau habis")
    assert "paket" in r["reply"].lower()
    await client.post("/api/v1/users", headers=h, json={"email": "cs@x.nexvora.id", "full_name": "Staf CS",
                                                        "password": PW, "roles": ["CUSTOMER_SERVICE"]})
    cs = auth((await login(client, c["slug"], "cs@x.nexvora.id")).json()["access_token"])
    caps = (await client.get("/api/v1/assistant/capabilities", headers=cs)).json()
    names = {t["name"] for t in caps["tools"]}
    assert "cari_order" in names and "mulai_picking" not in names   # CS tidak boleh menjalankan picking
    o = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1000"}])
    r = await client.post("/api/v1/assistant/act", headers=cs,
                          json={"name": "mulai_picking", "args": {"order_number": o["order_number"]}})
    assert r.status_code == 403
    other = await setup_wh(client, sa_token)
    r = await ask(client, other["h"], f"detail {o['order_number']}")
    assert "tidak ditemukan" in r["reply"].lower()


def llm_mock(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        last = body["messages"][-1]
        if len(calls) == 1:
            return httpx.Response(200, json={"content": [
                {"type": "text", "text": "Saya cek dulu."},
                {"type": "tool_use", "id": "t1", "name": "ringkasan_hari_ini", "input": {}}]})
        if isinstance(last.get("content"), list) and last["content"][0].get("type") == "tool_result":
            return httpx.Response(200, json={"content": [{"type": "text", "text": "Hari ini ada beberapa order yang perlu diproses."}]})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})
    return httpx.MockTransport(handler)


async def test_llm_engine_uses_tools_and_never_runs_commands(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    o = await new_order(client, h, [{"sku_id": c["a"]["id"], "quantity": 1, "unit_price": "1000"}])
    st = get_settings()
    old_engine, old_key = st.assistant_engine, st.anthropic_api_key
    st.assistant_engine, st.anthropic_api_key = "llm", "sk-test"
    calls = []
    engine_llm._TRANSPORT = llm_mock(calls)
    try:
        r = await ask(client, h, "tolong ringkas kondisi hari ini")
        assert r["engine"] == "llm" and "order" in r["reply"].lower()
        assert r["tools_used"] == ["ringkasan_hari_ini"] and r["cards"][0]["type"] == "metrics"
        sent = calls[0]
        names = {t["name"] for t in sent["tools"]}
        assert "ringkasan_hari_ini" in names and "risiko_stok" not in names   # fitur AI tidak ditawarkan ke paket ini

        # model memanggil perkakas pengubah data → hanya diusulkan, tidak dijalankan
        def handler2(req):
            return httpx.Response(200, json={"content": [
                {"type": "tool_use", "id": "t2", "name": "batalkan_order",
                 "input": {"order_number": o["order_number"], "alasan": "tes"}}]}) if len(calls) < 3 else \
                httpx.Response(200, json={"content": [{"type": "text", "text": "Perintah menunggu konfirmasi Anda."}]})
        calls.clear()
        engine_llm._TRANSPORT = httpx.MockTransport(lambda req: (calls.append(json.loads(req.content)), handler2(req))[1])
        r = await ask(client, h, f"batalkan {o['order_number']}")
        assert r["pending_action"]["name"] == "batalkan_order"
        assert (await client.get(f"/api/v1/orders/{o['id']}", headers=h)).json()["status"] != "CANCELLED"

        # layanan AI mati → otomatis jatuh ke mesin aturan, bukan error
        engine_llm._TRANSPORT = httpx.MockTransport(lambda req: httpx.Response(500, json={"error": "down"}))
        r = await ask(client, h, "berapa order hari ini?")
        assert r["engine"] == "rules" and r["cards"] and "mode dasar" in r.get("notice", "")
    finally:
        engine_llm._TRANSPORT = None
        st.assistant_engine, st.anthropic_api_key = old_engine, old_key


async def test_assistant_reads_shipping_and_performance(client, sa_token):
    c = await stocked(client, sa_token)
    h = c["h"]
    o, sh = await delivered_order(client, c, 1, 0)
    r = await ask(client, h, f"lacak {sh['tracking_number']}")
    assert sh["tracking_number"] in r["reply"] and r["cards"][0]["title"] == "Riwayat pelacakan"
    r = await ask(client, h, "laporan penjualan 7 hari terakhir")
    assert "order" in r["reply"] and r["cards"][0]["type"] == "metrics"
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        n = await s.scalar(text("SELECT count(*) FROM assistant_messages WHERE tenant_id = "
                                "(SELECT id FROM tenants WHERE slug = :s)"), {"s": c["slug"]})
    assert n >= 4   # setiap tanya-jawab tersimpan sebagai jejak


