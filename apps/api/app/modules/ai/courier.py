"""Rekomendasi kurir berdasarkan hasil pengiriman Anda sendiri, bukan klaim marketing kurir.

Untuk setiap kurir dihitung dari 120 hari terakhir: tingkat sampai, rata-rata lama antar,
tingkat gagal/dikembalikan, dan ongkir rata-rata. Skor akhir:

    skor = 55% keandalan + 25% kecepatan + 20% biaya

Data kota tujuan dipakai lebih dulu (kurir bisa bagus di Jakarta tapi lemah di Papua); kalau
pengalaman ke kota itu belum cukup, dipakai angka nasional dan ditandai di layar.
"""
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

WINDOW_DAYS = 120
MIN_CITY_SHIPMENTS = 5      # di bawah ini, statistik kota dianggap belum bisa dipercaya
MIN_SHIPMENTS = 3


async def _stats(s: AsyncSession, tenant_id: UUID, city: str | None) -> list[dict]:
    rows = (await s.execute(text(f"""
        SELECT sh.courier_code, sh.service_code, count(*) n,
               count(*) FILTER (WHERE sh.status = 'DELIVERED') delivered,
               count(*) FILTER (WHERE sh.status IN ('FAILED_DELIVERY','RETURNED_TO_SENDER')) failed,
               avg(EXTRACT(epoch FROM sh.delivered_at - sh.handed_over_at) / 86400.0)
                 FILTER (WHERE sh.status = 'DELIVERED' AND sh.handed_over_at IS NOT NULL) avg_days,
               avg(sh.cost) avg_cost
        FROM shipments sh JOIN orders o ON o.id = sh.order_id
        WHERE sh.tenant_id = :t AND sh.created_at >= now() - interval '{WINDOW_DAYS} days'
          AND sh.status <> 'CANCELLED'
          AND (CAST(:has_city AS boolean) IS FALSE OR lower(o.ship_city) = lower(CAST(:city AS varchar)))
        GROUP BY sh.courier_code, sh.service_code
    """), {"t": tenant_id, "city": city or "", "has_city": city is not None})).all()
    return [{"courier_code": r.courier_code, "service_code": r.service_code, "n": r.n, "delivered": r.delivered,
             "failed": r.failed, "avg_days": float(r.avg_days) if r.avg_days is not None else None,
             "avg_cost": float(r.avg_cost) if r.avg_cost is not None else None} for r in rows]


def _score(cands: list[dict]) -> list[dict]:
    days = [c["avg_days"] for c in cands if c["avg_days"] is not None]
    costs = [c["avg_cost"] for c in cands if c["avg_cost"] is not None]
    d_lo, d_hi = (min(days), max(days)) if days else (0, 0)
    c_lo, c_hi = (min(costs), max(costs)) if costs else (0, 0)
    for c in cands:
        settled = c["delivered"] + c["failed"]
        # keandalan: proporsi sampai dari pengiriman yang sudah selesai, dihaluskan (Laplace)
        # agar kurir dengan 1 pengiriman sukses tidak langsung tampak sempurna
        c["reliability"] = round((c["delivered"] + 1) / (settled + 2) * 100, 1)
        c["speed_days"] = round(c["avg_days"], 1) if c["avg_days"] is not None else None
        c["cost"] = round(c["avg_cost"]) if c["avg_cost"] is not None else None
        speed = 1.0 if c["avg_days"] is None or d_hi == d_lo else (d_hi - c["avg_days"]) / (d_hi - d_lo)
        cost = 1.0 if c["avg_cost"] is None or c_hi == c_lo else (c_hi - c["avg_cost"]) / (c_hi - c_lo)
        c["score"] = round((0.55 * (c["reliability"] / 100) + 0.25 * speed + 0.20 * cost) * 100, 1)
        reasons = []
        reasons.append(f"{c['reliability']}% paket sampai" if not c["failed"]
                       else f"{c['reliability']}% paket sampai ({c['failed']} dari {c['n']} gagal/dikembalikan)")
        if c["speed_days"] is not None:
            reasons.append(f"rata-rata {c['speed_days']} hari")
        if c["cost"] is not None:
            reasons.append(f"ongkir rata-rata Rp{c['cost']:,.0f}".replace(",", "."))
        if c["n"] < MIN_SHIPMENTS:
            reasons.append("data masih sedikit")
        c["reason"] = " · ".join(reasons)
    return sorted(cands, key=lambda x: -x["score"])


async def recommend(s: AsyncSession, tenant_id: UUID, city: str | None, limit: int = 5) -> dict:
    basis, cands = "kota", []
    if city:
        cands = await _stats(s, tenant_id, city)
    if sum(c["n"] for c in cands) < MIN_CITY_SHIPMENTS:
        cands, basis = await _stats(s, tenant_id, None), "semua kota"
    if not cands:
        return {"city": city, "basis": "belum ada data", "candidates": [],
                "note": "Belum ada riwayat pengiriman. Rekomendasi muncul setelah beberapa paket terkirim."}
    ranked = _score(cands)[:limit]
    return {"city": city, "basis": basis, "candidates": ranked,
            "note": ("Berdasarkan pengiriman Anda sendiri ke kota ini." if basis == "kota"
                     else "Pengiriman ke kota ini masih sedikit, jadi dipakai rata-rata semua kota.")}


async def recommend_for_order(s: AsyncSession, tenant_id: UUID, order_id: UUID) -> dict:
    city = await s.scalar(text("SELECT ship_city FROM orders WHERE id = :i AND tenant_id = :t"),
                          {"i": order_id, "t": tenant_id})
    return await recommend(s, tenant_id, city)
