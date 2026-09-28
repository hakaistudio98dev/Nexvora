"""Deteksi anomali operasional.

Semua detektor memakai statistik yang tahan pencilan (median + MAD), bukan rata-rata, supaya satu
order raksasa tidak menggeser ambangnya sendiri. Setiap temuan punya dedup_key sehingga satu kejadian
hanya muncul sekali, dan selalu menyertakan angka pembanding agar pengguna bisa menilai sendiri.
"""
import json
from datetime import UTC, datetime
from statistics import median
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

KINDS = {
    "ORDER_VALUE": "Nilai order jauh di atas kebiasaan",
    "ORDER_QTY": "Jumlah pesanan satu SKU tidak wajar",
    "CHANNEL_SPIKE": "Lonjakan order pada satu channel",
    "CHANNEL_SILENT": "Channel berhenti mengirim order",
    "REPEAT_CUSTOMER": "Order berulang dari kontak yang sama",
    "RETURN_SPIKE": "Retur menumpuk pada satu SKU",
    "PICK_SLOW": "Proses ambil barang melambat",
}
MIN_HISTORY = 20


def mad(values: list[float], med: float) -> float:
    return median([abs(v - med) for v in values]) if values else 0.0


def robust_z(x: float, med: float, m: float) -> float:
    # 1.4826 membuat MAD setara simpangan baku pada data normal.
    # Bila semua data historis identik (MAD = 0), dipakai 20% median sebagai skala cadangan,
    # supaya pencilan besar tetap terdeteksi dan bukan malah dianggap normal.
    scale = 1.4826 * m if m > 0 else max(abs(med) * 0.2, 1.0)
    return (x - med) / scale


async def _record(s: AsyncSession, tenant_id: UUID, kind: str, title: str, detail: str, *, score: float,
                  dedup_key: str, severity: str = "WARNING", entity_type: str | None = None,
                  entity_id: UUID | None = None, entity_label: str | None = None, data: dict | None = None) -> bool:
    inserted = await s.scalar(text("""
        INSERT INTO anomalies(tenant_id, kind, severity, title, detail, score, entity_type, entity_id,
                              entity_label, data, dedup_key)
        VALUES (:t, :k, :sev, :ti, :de, :sc, :et, :ei, :el, CAST(:da AS jsonb), :dk)
        ON CONFLICT (tenant_id, dedup_key) DO NOTHING RETURNING id
    """), {"t": tenant_id, "k": kind, "sev": severity, "ti": title[:200], "de": detail, "sc": round(score, 2),
           "et": entity_type, "ei": entity_id, "el": entity_label, "da": json.dumps(data or {}), "dk": dedup_key})
    if inserted is None:
        return False
    from app.modules.notifications.service import notify  # noqa: PLC0415
    await notify(s, tenant_id, "AI_ANOMALY", title, detail, data=data or {}, link="/ai",
                 dedup_key=f"anom:{dedup_key}", severity=severity)
    return True


async def scan(s: AsyncSession) -> int:
    """Dijalankan worker. Jendela waktunya pendek supaya murah, dedup mencegah pengulangan."""
    found = 0
    q = lambda sql, **kw: s.execute(text(sql), kw)  # noqa: E731
    now = datetime.now(UTC)
    day = f"{now:%Y%m%d}"

    # 1 & 2. Order dengan nilai / jumlah jauh di atas kebiasaan tenant
    hist = (await q("""
        SELECT tenant_id, array_agg(total ORDER BY total) vals FROM orders
        WHERE status NOT IN ('CANCELLED','FAILED') AND placed_at BETWEEN now() - interval '90 days' AND now() - interval '1 day'
        GROUP BY tenant_id HAVING count(*) >= :m
    """, m=MIN_HISTORY)).all()
    baseline = {h.tenant_id: [float(v) for v in h.vals] for h in hist}
    for r in (await q("""
        SELECT id, tenant_id, order_number, customer_name, customer_phone, channel, total, placed_at
        FROM orders WHERE status NOT IN ('CANCELLED','FAILED') AND placed_at >= now() - interval '2 days'
    """)).all():
        vals = baseline.get(r.tenant_id)
        if not vals:
            continue
        med = median(vals)
        z = robust_z(float(r.total), med, mad(vals, med))
        if z >= 6 and float(r.total) >= med * 3:
            if await _record(s, r.tenant_id, "ORDER_VALUE",
                             f"{r.order_number} bernilai jauh di atas biasanya",
                             f"Nilai Rp{float(r.total):,.0f} — median order Anda Rp{med:,.0f}. "
                             "Cek keaslian pesanan sebelum diproses atau dikirim.".replace(",", "."),
                             score=z, dedup_key=f"val:{r.id}", entity_type="order", entity_id=r.id,
                             entity_label=r.order_number, severity="CRITICAL" if z >= 12 else "WARNING",
                             data={"order_number": r.order_number, "total": str(r.total), "median": str(round(med)),
                                   "channel": r.channel}):
                found += 1

    sku_hist = {k: [] for k in ()}
    for h in (await q("""
        SELECT i.tenant_id, i.sku_id, array_agg(i.quantity) vals
        FROM order_items i JOIN orders o ON o.id = i.order_id
        WHERE o.status NOT IN ('CANCELLED','FAILED') AND o.placed_at BETWEEN now() - interval '90 days' AND now() - interval '1 day'
        GROUP BY i.tenant_id, i.sku_id HAVING count(*) >= :m
    """, m=MIN_HISTORY)).all():
        sku_hist[(h.tenant_id, h.sku_id)] = [float(v) for v in h.vals]
    for r in (await q("""
        SELECT i.id, i.tenant_id, i.sku_id, i.quantity, o.id order_id, o.order_number, k.sku_code
        FROM order_items i JOIN orders o ON o.id = i.order_id JOIN skus k ON k.id = i.sku_id
        WHERE o.status NOT IN ('CANCELLED','FAILED') AND o.placed_at >= now() - interval '2 days'
    """)).all():
        vals = sku_hist.get((r.tenant_id, r.sku_id))
        if not vals:
            continue
        med = median(vals)
        if r.quantity >= max(10, med * 8):
            if await _record(s, r.tenant_id, "ORDER_QTY",
                             f"{r.order_number}: {r.sku_code} dipesan {r.quantity} unit",
                             f"Biasanya {med:.0f} unit per order. Pastikan stok cukup dan pesanan benar.",
                             score=r.quantity / max(1.0, med), dedup_key=f"qty:{r.id}", entity_type="order",
                             entity_id=r.order_id, entity_label=r.order_number,
                             data={"sku_code": r.sku_code, "quantity": r.quantity, "median": med}):
                found += 1

    # 3 & 4. Lonjakan atau berhentinya order per channel
    for r in (await q("""
        WITH d AS (
          SELECT o.tenant_id, o.channel, (o.placed_at AT TIME ZONE COALESCE(ts.timezone,'Asia/Jakarta'))::date dd, count(*) n
          FROM orders o LEFT JOIN tenant_settings ts ON ts.tenant_id = o.tenant_id
          WHERE o.placed_at >= now() - interval '21 days' GROUP BY 1, 2, 3)
        SELECT tenant_id, channel,
               max(n) FILTER (WHERE dd = (now() AT TIME ZONE 'Asia/Jakarta')::date) today,
               array_agg(n) FILTER (WHERE dd < (now() AT TIME ZONE 'Asia/Jakarta')::date) prev
        FROM d GROUP BY tenant_id, channel
    """)).all():
        prev = [float(x) for x in (r.prev or [])]
        if len(prev) < 7:
            continue
        med = median(prev)
        today = float(r.today or 0)
        z = robust_z(today, med, mad(prev, med))
        if z >= 5 and today >= med * 2 and today >= 5:
            if await _record(s, r.tenant_id, "CHANNEL_SPIKE", f"Order dari {r.channel} melonjak hari ini",
                             f"{int(today)} order hari ini, biasanya sekitar {med:.0f}. Pastikan stok dan kapasitas "
                             "gudang cukup, dan cek apakah ada order kembar dari integrasi.",
                             score=z, dedup_key=f"spike:{r.channel}:{day}", severity="INFO",
                             data={"channel": r.channel, "today": int(today), "median": med}):
                found += 1
        if med >= 5 and today == 0 and now.hour >= 12:
            if await _record(s, r.tenant_id, "CHANNEL_SILENT", f"Belum ada order dari {r.channel} hari ini",
                             f"Biasanya sekitar {med:.0f} order per hari. Biasanya ini tanda integrasi terputus — "
                             "cek menu Integrasi (API) dan status toko Anda.",
                             score=med, dedup_key=f"silent:{r.channel}:{day}", severity="CRITICAL",
                             data={"channel": r.channel, "median": med}):
                found += 1

    # 5. Kontak yang sama memesan berulang dalam sehari
    for r in (await q("""
        SELECT tenant_id, customer_phone, count(*) n, sum(total) total, min(customer_name) nm
        FROM orders WHERE placed_at >= now() - interval '24 hours' AND customer_phone <> ''
          AND status NOT IN ('CANCELLED','FAILED')
        GROUP BY tenant_id, customer_phone HAVING count(*) >= 4
    """)).all():
        if await _record(s, r.tenant_id, "REPEAT_CUSTOMER", f"{r.n} order dari nomor yang sama dalam 24 jam",
                         f"{r.nm} ({r.customer_phone}), total Rp{float(r.total):,.0f}. Bisa jadi pesanan kembar dari "
                         "integrasi, atau perlu diverifikasi sebelum dikirim.".replace(",", "."),
                         score=r.n, dedup_key=f"repeat:{r.customer_phone}:{day}", severity="INFO",
                         data={"phone": r.customer_phone, "orders": r.n}):
            found += 1

    # 6. Retur menumpuk pada satu SKU
    for r in (await q("""
        SELECT rl.tenant_id, rl.sku_id, k.sku_code, sum(rl.quantity) qty, count(DISTINCT r.id) n
        FROM return_lines rl JOIN returns r ON r.id = rl.return_id JOIN skus k ON k.id = rl.sku_id
        WHERE r.status <> 'REJECTED' AND r.created_at >= now() - interval '7 days'
        GROUP BY 1, 2, 3 HAVING count(DISTINCT r.id) >= 3
    """)).all():
        if await _record(s, r.tenant_id, "RETURN_SPIKE", f"{r.sku_code} diretur {r.n} kali minggu ini",
                         f"Total {r.qty} unit. Periksa kualitas batch, foto produk, atau deskripsi ukurannya.",
                         score=r.n, dedup_key=f"ret:{r.sku_id}:{now:%Y%W}", entity_type="sku", entity_id=r.sku_id,
                         entity_label=r.sku_code, data={"sku_code": r.sku_code, "returns": r.n, "qty": int(r.qty)}):
            found += 1

    # 7. Proses ambil barang melambat dibanding dua minggu terakhir
    for r in (await q("""
        WITH t AS (
          SELECT tenant_id, warehouse_id, completed_at,
                 EXTRACT(epoch FROM completed_at - created_at) / 60 mins
          FROM wms_tasks WHERE task_type = 'PICK' AND status = 'DONE'
            AND completed_at >= now() - interval '14 days')
        SELECT tenant_id, warehouse_id,
               array_agg(mins) FILTER (WHERE completed_at >= now() - interval '1 day') today,
               array_agg(mins) FILTER (WHERE completed_at < now() - interval '1 day') prev
        FROM t GROUP BY tenant_id, warehouse_id
    """)).all():
        today_v = [float(x) for x in (r.today or [])]
        prev_v = [float(x) for x in (r.prev or [])]
        if len(today_v) < 10 or len(prev_v) < 30:
            continue
        t_med, p_med = median(today_v), median(prev_v)
        if p_med > 0 and t_med >= p_med * 2.5 and t_med - p_med >= 5:
            if await _record(s, r.tenant_id, "PICK_SLOW", "Ambil barang hari ini lebih lambat dari biasanya",
                             f"Median {t_med:.0f} menit per tugas, biasanya {p_med:.0f} menit. Cek jumlah petugas, "
                             "penataan rak, atau apakah ada stok yang sulit ditemukan.",
                             score=t_med / p_med, dedup_key=f"pick:{r.warehouse_id}:{day}",
                             data={"today_minutes": round(t_med), "usual_minutes": round(p_med)}):
                found += 1

    await s.flush()
    return found
