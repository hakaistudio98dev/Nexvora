"""Perkakas yang boleh dipakai asisten.

Aturan yang dipegang teguh:
- Asisten TIDAK punya hak istimewa. Setiap perkakas dijalankan memakai sesi dan izin pengguna yang sedang
  login, lewat service yang sama dengan tombol di layar. Isolasi tenant tetap dijaga RLS database.
- Perkakas yang MENGUBAH data ditandai `writes=True`; asisten tidak pernah menjalankannya sendiri —
  pengguna harus menekan tombol konfirmasi dulu.
- Setiap hasil berisi kalimat ringkas untuk dibacakan + data mentah untuk ditampilkan sebagai kartu.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Coroutine


from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Ctx
from app.core.entitlements import has_feature
from app.core.errors import AppError
from app.models import Order
from app.modules.ai import courier as ai_courier
from app.modules.ai import forecast as ai_forecast
from app.modules.analytics import service as analytics
from app.modules.orders import service as orders
from app.modules.settings import service as settings_svc

Handler = Callable[..., Coroutine[Any, Any, dict]]


@dataclass
class Tool:
    name: str
    description: str
    params: dict                      # JSON-schema sederhana untuk properti
    handler: Handler
    permission: str | None = None
    feature: str | None = None
    writes: bool = False
    confirm_template: str = ""
    examples: list[str] = field(default_factory=list)


REGISTRY: dict[str, Tool] = {}


def tool(**kw):  # noqa: ANN201
    def deco(fn: Handler) -> Handler:
        REGISTRY[kw["name"]] = Tool(handler=fn, **kw)
        return fn
    return deco


def rupiah(v) -> str:  # noqa: ANN001
    return "Rp" + f"{float(v):,.0f}".replace(",", ".")


async def _order_by_number(s: AsyncSession, p, number: str, lock: bool = False) -> Order:  # noqa: ANN001
    stmt = select(Order).where(Order.tenant_id == p.tenant_id, Order.order_number == number.strip().upper())
    o = await s.scalar(stmt.with_for_update() if lock else stmt)
    if o is None:
        raise AppError(404, "NOT_FOUND", f"Order {number} tidak ditemukan di workspace ini")
    return o


# ------------------------------------------------------------------ perkakas baca
@tool(name="ringkasan_hari_ini", description="Ringkasan pekerjaan hari ini: order per tahap, paket siap kirim, "
      "retur terbuka, stok menipis.", params={}, permission="order:read",
      examples=["apa yang perlu dikerjakan hari ini", "ringkasan hari ini"])
async def ringkasan(s: AsyncSession, p, **_) -> dict:  # noqa: ANN001
    r = (await s.execute(text("""
        SELECT count(*) FILTER (WHERE status = 'CREATED') baru,
               count(*) FILTER (WHERE status = 'PAID') dibayar,
               count(*) FILTER (WHERE status = 'ALLOCATED') siap_ambil,
               count(*) FILTER (WHERE status IN ('PICKING','PACKING')) diproses,
               count(*) FILTER (WHERE status = 'READY_TO_SHIP') siap_kirim,
               count(*) FILTER (WHERE stock_status = 'OUT_OF_STOCK' AND status IN ('CREATED','PAID')) tertahan,
               count(*) FILTER (WHERE placed_at >= now() - interval '24 hours') masuk_24jam,
               count(*) FILTER (WHERE shipped_at >= now() - interval '24 hours') dikirim_24jam
        FROM orders WHERE tenant_id = :t"""), {"t": p.tenant_id})).one()
    retur = (await s.execute(text("SELECT count(*) FROM returns WHERE tenant_id = :t AND status IN "
                                  "('REQUESTED','APPROVED','RECEIVED','INSPECTED')"), {"t": p.tenant_id})).scalar()
    menipis = (await s.execute(text("""
        SELECT count(*) FROM inventory_balances b JOIN skus k ON k.id = b.sku_id
        LEFT JOIN tenant_settings ts ON ts.tenant_id = b.tenant_id
        WHERE b.tenant_id = :t AND k.is_active AND b.available <= COALESCE(k.reorder_point, ts.low_stock_threshold, 5)
    """), {"t": p.tenant_id})).scalar()
    rows = [("Order masuk 24 jam", r.masuk_24jam), ("Dikirim 24 jam", r.dikirim_24jam),
            ("Menunggu pembayaran", r.baru), ("Siap diambil dari rak", r.siap_ambil),
            ("Sedang diambil/dikemas", r.diproses), ("Siap kirim", r.siap_kirim),
            ("Tertahan karena stok", r.tertahan), ("Retur diproses", retur), ("SKU stok menipis", menipis)]
    penting = [f"{v} {k.lower()}" for k, v in rows[2:] if v]
    return {"text": ("Hari ini: " + ", ".join(penting) + "." if penting else
                     "Tidak ada pekerjaan yang tertahan. Semua order sudah pada jalurnya."),
            "card": {"type": "metrics", "title": "Ringkasan hari ini",
                     "items": [{"label": k, "value": v} for k, v in rows]}}


@tool(name="cari_order", description="Cari order berdasarkan nomor order, nama/HP pelanggan, atau status.",
      params={"q": {"type": "string", "description": "nomor order, nama, atau nomor HP pelanggan"},
              "status": {"type": "string", "description": "mis. PAID, READY_TO_SHIP, SHIPPED"}},
      permission="order:read", examples=["cari order SO-2609-000001", "order milik Rina", "order yang siap kirim"])
async def cari_order(s: AsyncSession, p, q: str | None = None, status: str | None = None, **_) -> dict:  # noqa: ANN001
    rows = (await s.execute(text("""
        SELECT o.order_number, o.status, o.customer_name, o.ship_city, o.total, o.placed_at, w.code gudang
        FROM orders o LEFT JOIN warehouses w ON w.id = o.warehouse_id
        WHERE o.tenant_id = :t
          AND (CAST(:has_q AS boolean) IS FALSE OR o.order_number ILIKE CAST(:like AS varchar)
               OR o.customer_name ILIKE CAST(:like AS varchar) OR o.customer_phone ILIKE CAST(:like AS varchar)
               OR o.external_ref ILIKE CAST(:like AS varchar))
          AND (CAST(:has_st AS boolean) IS FALSE OR o.status = CAST(:st AS varchar))
        ORDER BY o.placed_at DESC LIMIT 15"""),
        {"t": p.tenant_id, "has_q": q is not None, "like": f"%{(q or '').strip()}%",
         "has_st": status is not None, "st": (status or "").upper()})).all()
    if not rows:
        return {"text": "Tidak ada order yang cocok dengan pencarian itu."}
    return {"text": f"Ketemu {len(rows)} order" + (" (15 teratas)" if len(rows) == 15 else "") + ".",
            "card": {"type": "table", "title": "Hasil pencarian order",
                     "columns": ["Order", "Status", "Pelanggan", "Kota", "Total"],
                     "rows": [[r.order_number, r.status, r.customer_name, r.ship_city, rupiah(r.total)] for r in rows]}}


@tool(name="detail_order", description="Detail satu order: status, isi, pengiriman, dan resinya.",
      params={"order_number": {"type": "string"}}, permission="order:read",
      examples=["detail SO-2609-000001", "status order SO-2609-000001"])
async def detail_order(s: AsyncSession, p, order_number: str, **_) -> dict:  # noqa: ANN001
    o = await _order_by_number(s, p, order_number)
    items = (await s.execute(text("""
        SELECT k.sku_code, i.quantity, i.line_total FROM order_items i JOIN skus k ON k.id = i.sku_id
        WHERE i.order_id = :o"""), {"o": o.id})).all()
    sh = (await s.execute(text("""
        SELECT courier_code, tracking_number, status FROM shipments WHERE order_id = :o AND status <> 'CANCELLED'
    """), {"o": o.id})).first()
    kirim = (f"Resi {sh.tracking_number} ({sh.courier_code.upper()}), status pengiriman {sh.status}."
             if sh else "Belum ada resi.")
    return {"text": f"{o.order_number} — {o.status}, pembayaran {o.payment_status}, total {rupiah(o.total)} "
                    f"untuk {o.customer_name} di {o.ship_city}. {kirim}",
            "card": {"type": "table", "title": f"Isi {o.order_number}", "columns": ["SKU", "Jumlah", "Subtotal"],
                     "rows": [[i.sku_code, i.quantity, rupiah(i.line_total)] for i in items]}}


@tool(name="stok_sku", description="Sisa stok satu SKU atau beberapa SKU di semua gudang.",
      params={"q": {"type": "string", "description": "kode SKU, barcode, atau nama produk"}},
      permission="inventory:read", examples=["stok TSH-BLK-M", "sisa stok kaos hitam"])
async def stok_sku(s: AsyncSession, p, q: str, **_) -> dict:  # noqa: ANN001
    rows = (await s.execute(text("""
        SELECT k.sku_code, pr.name, w.code gudang, b.on_hand, b.reserved, b.available, b.damaged
        FROM inventory_balances b JOIN skus k ON k.id = b.sku_id JOIN products pr ON pr.id = k.product_id
        JOIN warehouses w ON w.id = b.warehouse_id
        WHERE b.tenant_id = :t AND (k.sku_code ILIKE :like OR k.barcode = :exact OR pr.name ILIKE :like)
        ORDER BY k.sku_code, w.code LIMIT 30"""),
        {"t": p.tenant_id, "like": f"%{q.strip()}%", "exact": q.strip()})).all()
    if not rows:
        return {"text": f"Tidak ada SKU yang cocok dengan “{q}”."}
    total = sum(r.available for r in rows)
    return {"text": f"Total siap dijual {total} unit dari {len(rows)} baris stok.",
            "card": {"type": "table", "title": f"Stok “{q}”",
                     "columns": ["SKU", "Gudang", "Fisik", "Dipesan", "Bisa dijual"],
                     "rows": [[r.sku_code, r.gudang, r.on_hand, r.reserved, r.available] for r in rows]}}


@tool(name="kinerja", description="Angka kinerja periode tertentu: order, pendapatan, ketepatan SLA, akurasi.",
      params={"hari": {"type": "integer", "description": "jumlah hari ke belakang, bawaan 30"}},
      permission="analytics:read", examples=["kinerja 7 hari terakhir", "laporan bulan ini"])
async def kinerja(s: AsyncSession, p, hari: int = 30, **_) -> dict:  # noqa: ANN001
    hari = max(1, min(int(hari or 30), 366))
    today = datetime.now(UTC).date()
    prm = await analytics.params(s, p.tenant_id, today - timedelta(days=hari - 1), today, None, None)
    ov = await analytics.overview(s, prm)
    k = ov["kpi"]
    pct = lambda v: "—" if v is None else f"{v}%"  # noqa: E731
    return {"text": f"{hari} hari terakhir: {ov['orders']['total']} order, pendapatan {rupiah(ov['orders']['revenue'])}, "
                    f"terkirim {pct(k['fulfillment_rate'])}, tepat waktu {pct(k['sla_compliance'])}.",
            "card": {"type": "metrics", "title": f"Kinerja {hari} hari terakhir", "items": [
                {"label": "Order", "value": ov["orders"]["total"]},
                {"label": "Pendapatan", "value": rupiah(ov["orders"]["revenue"])},
                {"label": "Order terkirim", "value": pct(k["fulfillment_rate"])},
                {"label": "Dikirim tepat waktu", "value": pct(k["sla_compliance"])},
                {"label": "Rata-rata waktu kirim", "value": "—" if k["avg_hours_to_ship"] is None else f"{k['avg_hours_to_ship']} jam"},
                {"label": "Akurasi picking", "value": pct(k["pick_accuracy"])},
                {"label": "Akurasi packing", "value": pct(k["pack_accuracy"])},
                {"label": "Tingkat retur", "value": pct(k["return_rate"])}]}}


@tool(name="order_terlambat", description="Order yang melewati atau mendekati batas waktu kirim (SLA).",
      params={}, permission="analytics:read", feature="analytics",
      examples=["order mana yang terlambat", "ada yang lewat SLA?"])
async def order_terlambat(s: AsyncSession, p, **_) -> dict:  # noqa: ANN001
    prm = await analytics.params(s, p.tenant_id, None, None, None, None)
    board = await analytics.sla_board(s, prm)
    c = board["counts"]
    if not board["orders"]:
        return {"text": f"Tidak ada order yang terlambat. {c['on_track']} order masih dalam batas waktu."}
    return {"text": f"{c['overdue']} order terlambat dan {c['at_risk']} mendekati batas.",
            "card": {"type": "table", "title": "Order yang perlu didahulukan",
                     "columns": ["Order", "Tahap", "Sisa waktu"],
                     "rows": [[o["order_number"], o["status"],
                               f"terlambat {abs(o['hours_left'])} jam" if o["hours_left"] < 0 else f"{o['hours_left']} jam"]
                              for o in board["orders"][:15]]}}


@tool(name="lacak_paket", description="Status pengiriman berdasarkan nomor resi atau nomor order.",
      params={"kode": {"type": "string", "description": "nomor resi atau nomor order"}}, permission="shipping:read",
      examples=["lacak JNE0099887766", "paket SO-2609-000001 sudah sampai?"])
async def lacak_paket(s: AsyncSession, p, kode: str, **_) -> dict:  # noqa: ANN001
    code = kode.strip().upper()
    sh = (await s.execute(text("""
        SELECT sh.id, sh.tracking_number, sh.courier_code, sh.status, sh.handed_over_at, sh.delivered_at, o.order_number
        FROM shipments sh JOIN orders o ON o.id = sh.order_id
        WHERE sh.tenant_id = :t AND sh.status <> 'CANCELLED' AND (sh.tracking_number = :c OR o.order_number = :c)
    """), {"t": p.tenant_id, "c": code})).first()
    if sh is None:
        return {"text": f"Tidak ada pengiriman dengan kode {code}."}
    ev = (await s.execute(text("""
        SELECT status, description, location, occurred_at FROM tracking_events WHERE shipment_id = :i
        ORDER BY occurred_at DESC LIMIT 8"""), {"i": sh.id})).all()
    return {"text": f"{sh.order_number} · resi {sh.tracking_number} ({sh.courier_code.upper()}) berstatus {sh.status}.",
            "card": {"type": "table", "title": "Riwayat pelacakan", "columns": ["Waktu", "Status", "Keterangan"],
                     "rows": [[e.occurred_at.strftime("%d %b %H:%M"), e.status,
                               f"{e.description}{' · ' + e.location if e.location else ''}"] for e in ev]}}


@tool(name="retur_terbuka", description="Daftar retur yang masih diproses.", params={}, permission="returns:read",
      examples=["ada retur baru?", "retur yang belum selesai"])
async def retur_terbuka(s: AsyncSession, p, **_) -> dict:  # noqa: ANN001
    rows = (await s.execute(text("""
        SELECT r.number, r.status, r.reason_code, o.order_number, r.created_at
        FROM returns r JOIN orders o ON o.id = r.order_id
        WHERE r.tenant_id = :t AND r.status IN ('REQUESTED','APPROVED','RECEIVED','INSPECTED')
        ORDER BY r.created_at DESC LIMIT 15"""), {"t": p.tenant_id})).all()
    if not rows:
        return {"text": "Tidak ada retur yang sedang diproses."}
    return {"text": f"Ada {len(rows)} retur yang sedang diproses.",
            "card": {"type": "table", "title": "Retur terbuka", "columns": ["RMA", "Order", "Status", "Alasan"],
                     "rows": [[r.number, r.order_number, r.status, r.reason_code] for r in rows]}}


@tool(name="risiko_stok", description="SKU yang diperkirakan habis dan saran jumlah pesan ulang.",
      params={}, permission="ai:read", feature="ai",
      examples=["stok apa yang mau habis", "apa yang perlu dipesan ulang"])
async def risiko_stok(s: AsyncSession, p, **_) -> dict:  # noqa: ANN001
    st = await settings_svc.read(s, p.tenant_id)
    items = await ai_forecast.stockout_risk(s, p.tenant_id, None, lead_time=st.lead_time_days,
                                            service_level=float(st.service_level), cover_days=st.cover_days, limit=15)
    urgent = [x for x in items if x["risk"] != "aman"]
    if not urgent:
        return {"text": "Tidak ada stok yang berisiko habis dalam waktu dekat."}
    return {"text": f"{len(urgent)} SKU berisiko habis. Yang paling mendesak: {urgent[0]['sku_code']} "
                    f"({urgent[0]['days_until_out']} hari lagi).",
            "card": {"type": "table", "title": "Stok yang akan habis",
                     "columns": ["SKU", "Gudang", "Tersedia", "Habis dalam", "Saran pesan"],
                     "rows": [[x["sku_code"], x["warehouse"], x["available"],
                               "—" if x["days_until_out"] is None else f"{x['days_until_out']} hari",
                               x["suggested_order"]] for x in urgent]}}


@tool(name="kurir_terbaik", description="Kurir paling andal untuk kota tujuan tertentu.",
      params={"kota": {"type": "string"}}, permission="ai:read", feature="ai",
      examples=["kurir terbaik ke Bandung", "pakai kurir apa untuk Surabaya"])
async def kurir_terbaik(s: AsyncSession, p, kota: str | None = None, **_) -> dict:  # noqa: ANN001
    r = await ai_courier.recommend(s, p.tenant_id, kota)
    if not r["candidates"]:
        return {"text": r["note"]}
    top = r["candidates"][0]
    return {"text": f"Untuk {kota or 'semua kota'}: {top['courier_code'].upper()} {top['service_code']} — {top['reason']}. "
                    f"({r['note']})",
            "card": {"type": "table", "title": "Peringkat kurir", "columns": ["Kurir", "Layanan", "Skor", "Alasan"],
                     "rows": [[c["courier_code"].upper(), c["service_code"], c["score"], c["reason"]]
                              for c in r["candidates"]]}}


@tool(name="temuan_anomali", description="Temuan janggal yang belum ditindaklanjuti.", params={},
      permission="ai:read", feature="ai", examples=["ada yang aneh hari ini?", "temuan anomali"])
async def temuan_anomali(s: AsyncSession, p, **_) -> dict:  # noqa: ANN001
    rows = (await s.execute(text("""
        SELECT title, detail, severity, created_at FROM anomalies WHERE tenant_id = :t AND status = 'OPEN'
        ORDER BY created_at DESC LIMIT 10"""), {"t": p.tenant_id})).all()
    if not rows:
        return {"text": "Tidak ada temuan yang perlu dicek."}
    return {"text": f"Ada {len(rows)} temuan yang belum ditindaklanjuti.",
            "card": {"type": "table", "title": "Temuan", "columns": ["Waktu", "Temuan", "Tingkat"],
                     "rows": [[r.created_at.strftime("%d %b %H:%M"), r.title, r.severity] for r in rows]}}


# ------------------------------------------------------------------ perkakas perintah (wajib konfirmasi)
@tool(name="tandai_dibayar", description="Tandai satu order sudah dibayar sehingga stoknya dikunci.",
      params={"order_number": {"type": "string"}, "alasan": {"type": "string"}}, permission="order:write",
      writes=True, confirm_template="Tandai {order_number} sebagai sudah dibayar?",
      examples=["tandai SO-2609-000001 sudah dibayar"])
async def tandai_dibayar(s: AsyncSession, p, order_number: str, alasan: str | None = None, **_) -> dict:  # noqa: ANN001
    o = await _order_by_number(s, p, order_number, lock=True)
    await orders.run_action(s, Ctx.from_principal(p), o, "mark_paid", alasan or "Dikonfirmasi lewat asisten")
    return {"text": f"{o.order_number} sekarang berstatus {o.status}."}


@tool(name="mulai_picking", description="Mulai proses ambil barang untuk satu order.",
      params={"order_number": {"type": "string"}}, permission="order:fulfill", writes=True,
      confirm_template="Mulai ambil barang untuk {order_number}?", examples=["mulai picking SO-2609-000001"])
async def mulai_picking(s: AsyncSession, p, order_number: str, **_) -> dict:  # noqa: ANN001
    o = await _order_by_number(s, p, order_number, lock=True)
    await orders.run_action(s, Ctx.from_principal(p), o, "start_picking", "Dimulai lewat asisten")
    return {"text": f"{o.order_number} masuk tahap {o.status}. Tugas ambil barang sudah dibuat."}


@tool(name="batalkan_order", description="Batalkan satu order dan lepas reservasi stoknya.",
      params={"order_number": {"type": "string"}, "alasan": {"type": "string"}}, permission="order:write",
      writes=True, confirm_template="Batalkan {order_number}? Reservasi stoknya akan dilepas.",
      examples=["batalkan SO-2609-000002 karena pelanggan berubah pikiran"])
async def batalkan_order(s: AsyncSession, p, order_number: str, alasan: str | None = None, **_) -> dict:  # noqa: ANN001
    o = await _order_by_number(s, p, order_number, lock=True)
    await orders.run_action(s, Ctx.from_principal(p), o, "cancel", alasan or "Dibatalkan lewat asisten")
    return {"text": f"{o.order_number} dibatalkan. Stok yang tadi direservasi sudah kembali tersedia."}


def available(p) -> list[Tool]:  # noqa: ANN001
    """Perkakas yang boleh dipakai pengguna ini (izin peran + fitur paket)."""
    out = []
    for t in REGISTRY.values():
        if t.permission and not p.can(t.permission):
            continue
        if t.feature and not has_feature(p.entitlement, t.feature):
            continue
        out.append(t)
    return out


async def run(s: AsyncSession, p, name: str, args: dict) -> dict:  # noqa: ANN001
    t = REGISTRY.get(name)
    if t is None:
        raise AppError(422, "UNKNOWN_TOOL", f"Perintah {name} tidak dikenal")
    if t.permission and not p.can(t.permission):
        raise AppError(403, "FORBIDDEN", f"Peran Anda tidak punya izin untuk {name.replace('_', ' ')}")
    if t.feature and not has_feature(p.entitlement, t.feature):
        raise AppError(402, "FEATURE_NOT_IN_PLAN", f"{name.replace('_', ' ').capitalize()} tidak termasuk paket Anda")
    clean = {k: v for k, v in (args or {}).items() if k in t.params}
    return await t.handler(s, p, **clean)


