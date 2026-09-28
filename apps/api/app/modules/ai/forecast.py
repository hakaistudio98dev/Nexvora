"""Ramalan permintaan & risiko kehabisan stok.

Metodenya sengaja sederhana dan bisa dijelaskan ke pengguna: rata-rata bergerak berbobot
(bobot hari terbaru lebih besar) dikali pola hari dalam seminggu. Tidak ada model kotak hitam —
setiap angka di layar bisa ditelusuri ke penjualan yang benar-benar terjadi.

    ramalan(hari) = laju_harian × faktor_hari[dow]
    titik_pesan_ulang = laju_harian × lead_time + z × sigma × √lead_time     (stok pengaman)
    saran_pesan       = laju_harian × (lead_time + cover) + stok_pengaman − tersedia − sedang_datang
"""
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import NormalDist
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

HISTORY_DAYS = 120
HALF_LIFE = 14.0          # penjualan 14 hari lalu berbobot setengah dari hari ini
MIN_DAYS_WEEKDAY = 28     # pola mingguan baru dipakai setelah ada 4 minggu data


@dataclass
class Forecast:
    warehouse_id: UUID
    sku_id: UUID
    method: str
    daily_rate: float
    sigma: float
    weekday_factor: list[float]
    history_days: int
    sold_30d: int

    def expected(self, day: date) -> float:
        f = self.weekday_factor[day.weekday()] if len(self.weekday_factor) == 7 else 1.0
        return max(0.0, self.daily_rate * f)


def _series(rows: list[tuple[date, int]], today: date, days: int) -> list[int]:
    got = {d: q for d, q in rows}
    return [got.get(today - timedelta(days=i), 0) for i in range(days - 1, -1, -1)]


def fit(rows: list[tuple[date, int]], today: date, first_sale: date | None) -> tuple[float, float, list[float], str, int]:
    """Kembalikan (laju harian, sigma, faktor hari, metode, jumlah hari data)."""
    span = min(HISTORY_DAYS, (today - first_sale).days + 1) if first_sale else 0
    if span <= 0 or not rows:
        return 0.0, 0.0, [1.0] * 7, "belum_ada_penjualan", 0
    series = _series(rows, today, span)
    # rata-rata berbobot eksponensial: hari terbaru paling berpengaruh
    decay = 0.5 ** (1 / HALF_LIFE)
    wsum = tot = 0.0
    for i, q in enumerate(reversed(series)):   # i=0 → hari ini
        w = decay ** i
        tot += w * q
        wsum += w
    rate = tot / wsum if wsum else 0.0

    factors = [1.0] * 7
    method = "rata_rata"
    if span >= MIN_DAYS_WEEKDAY and rate > 0:
        by_dow: dict[int, list[int]] = {d: [] for d in range(7)}
        for i, q in enumerate(series):
            by_dow[(today - timedelta(days=span - 1 - i)).weekday()].append(q)
        for d, qs in by_dow.items():
            if qs:
                raw = (sum(qs) / len(qs)) / rate if rate else 1.0
                # ditarik ke 1 bila datanya sedikit, agar pola tidak dibentuk oleh satu-dua hari
                k = len(qs) / (len(qs) + 4)
                factors[d] = max(0.2, min(3.0, 1 + k * (raw - 1)))
        avg = sum(factors) / 7
        factors = [f / avg for f in factors] if avg else factors
        method = "pola_mingguan"

    resid = [q - max(0.0, rate * factors[(today - timedelta(days=span - 1 - i)).weekday()])
             for i, q in enumerate(series)]
    sigma = math.sqrt(sum(r * r for r in resid) / len(resid)) if resid else 0.0
    return rate, sigma, factors, method, span


async def rebuild(s: AsyncSession, tenant_id: UUID | None = None) -> int:
    """Hitung ulang ramalan untuk setiap SKU yang punya saldo stok. Dipanggil worker."""
    today = datetime.now(UTC).date()
    rows = (await s.execute(text("""
        SELECT b.tenant_id, b.warehouse_id, b.sku_id,
               (o.placed_at AT TIME ZONE COALESCE(ts.timezone, 'Asia/Jakarta'))::date d,
               sum(i.quantity) q
        FROM inventory_balances b
        JOIN skus k ON k.id = b.sku_id AND k.is_active
        LEFT JOIN tenant_settings ts ON ts.tenant_id = b.tenant_id
        LEFT JOIN orders o ON o.warehouse_id = b.warehouse_id AND o.status NOT IN ('CANCELLED','FAILED')
             AND o.placed_at >= now() - make_interval(days => :hist)
        LEFT JOIN order_items i ON i.order_id = o.id AND i.sku_id = b.sku_id
        WHERE (CAST(:t AS uuid) IS NULL OR b.tenant_id = CAST(:t AS uuid))
        GROUP BY b.tenant_id, b.warehouse_id, b.sku_id, d
    """), {"hist": HISTORY_DAYS, "t": str(tenant_id) if tenant_id else None})).all()

    per_sku: dict[tuple, list[tuple[date, int]]] = {}
    owner: dict[tuple, UUID] = {}
    for r in rows:
        key = (r.warehouse_id, r.sku_id)
        owner[key] = r.tenant_id
        per_sku.setdefault(key, [])
        if r.d is not None and r.q:
            per_sku[key].append((r.d, int(r.q)))

    n = 0
    for (wh, sku), hist in per_sku.items():
        first = min((d for d, _ in hist), default=None)
        rate, sigma, factors, method, span = fit(hist, today, first)
        sold30 = sum(q for d, q in hist if (today - d).days < 30)
        await s.execute(text("""
            INSERT INTO demand_forecasts(tenant_id, warehouse_id, sku_id, method, daily_rate, sigma,
                                         weekday_factor, history_days, sold_30d, generated_at)
            VALUES (:t, :w, :s, :m, :r, :sg, CAST(:f AS jsonb), :h, :s30, now())
            ON CONFLICT (warehouse_id, sku_id) DO UPDATE SET
              method = EXCLUDED.method, daily_rate = EXCLUDED.daily_rate, sigma = EXCLUDED.sigma,
              weekday_factor = EXCLUDED.weekday_factor, history_days = EXCLUDED.history_days,
              sold_30d = EXCLUDED.sold_30d, generated_at = now()
        """), {"t": owner[(wh, sku)], "w": wh, "s": sku, "m": method, "r": round(rate, 4),
               "sg": round(sigma, 4), "f": __import__("json").dumps([round(x, 3) for x in factors]),
               "h": span, "s30": sold30})
        n += 1
    return n


def z_for(service_level: float) -> float:
    return NormalDist().inv_cdf(min(0.999, max(0.5, service_level)))


def days_until_out(f: Forecast, available: int, today: date, horizon: int = 120) -> int | None:
    """Simulasi hari per hari memakai pola mingguan; None berarti tidak habis dalam horizon."""
    left = float(available)
    if left <= 0:
        return 0
    for i in range(horizon):
        left -= f.expected(today + timedelta(days=i))
        if left <= 0:
            return i + 1
    return None


async def stockout_risk(s: AsyncSession, tenant_id: UUID, warehouse_id: UUID | None, *, lead_time: int,
                        service_level: float, cover_days: int, limit: int = 300) -> list[dict]:
    today = datetime.now(UTC).date()
    z = z_for(service_level)
    rows = (await s.execute(text("""
        SELECT f.warehouse_id, f.sku_id, f.method, f.daily_rate, f.sigma, f.weekday_factor, f.history_days,
               f.sold_30d, f.generated_at, b.available, b.on_hand, b.reserved, k.sku_code, p.name product_name,
               w.code wh_code, COALESCE(inb.qty, 0) incoming
        FROM demand_forecasts f
        JOIN inventory_balances b ON b.warehouse_id = f.warehouse_id AND b.sku_id = f.sku_id
        JOIN skus k ON k.id = f.sku_id JOIN products p ON p.id = k.product_id
        JOIN warehouses w ON w.id = f.warehouse_id
        LEFT JOIN (
          SELECT l.sku_id, d.warehouse_id, sum(GREATEST(l.expected_qty - l.received_qty, 0)) qty
          FROM inbound_lines l JOIN inbound_receipts d ON d.id = l.inbound_id
          WHERE d.status = 'RECEIVING' GROUP BY 1, 2
        ) inb ON inb.sku_id = f.sku_id AND inb.warehouse_id = f.warehouse_id
        WHERE f.tenant_id = :t AND (CAST(:w AS uuid) IS NULL OR f.warehouse_id = CAST(:w AS uuid))
    """), {"t": tenant_id, "w": str(warehouse_id) if warehouse_id else None})).all()

    out = []
    for r in rows:
        f = Forecast(r.warehouse_id, r.sku_id, r.method, float(r.daily_rate), float(r.sigma),
                     list(r.weekday_factor or []), r.history_days, r.sold_30d)
        if f.daily_rate <= 0:
            continue
        days = days_until_out(f, r.available, today)
        safety = z * f.sigma * math.sqrt(max(1, lead_time))
        reorder_point = f.daily_rate * lead_time + safety
        target = f.daily_rate * (lead_time + cover_days) + safety
        suggested = max(0, math.ceil(target - r.available - float(r.incoming)))
        risk = ("habis" if r.available <= 0 else "kritis" if days is not None and days <= lead_time
                else "waspada" if days is not None and days <= lead_time + 7 else "aman")
        out.append({
            "warehouse": r.wh_code, "warehouse_id": str(r.warehouse_id), "sku_id": str(r.sku_id),
            "sku_code": r.sku_code, "product_name": r.product_name, "available": r.available,
            "on_hand": r.on_hand, "reserved": r.reserved, "incoming": int(r.incoming),
            "daily_rate": round(f.daily_rate, 2), "sold_30d": f.sold_30d, "method": f.method,
            "history_days": f.history_days, "days_until_out": days,
            "stockout_date": (today + timedelta(days=days)).isoformat() if days else None,
            "safety_stock": math.ceil(safety), "reorder_point": math.ceil(reorder_point),
            "suggested_order": suggested if risk != "aman" or r.available < reorder_point else 0,
            "risk": risk, "generated_at": r.generated_at,
        })
    order = {"habis": 0, "kritis": 1, "waspada": 2, "aman": 3}
    out.sort(key=lambda x: (order[x["risk"]], x["days_until_out"] if x["days_until_out"] is not None else 9999))
    return out[:limit]


async def daily_forecast(s: AsyncSession, tenant_id: UUID, warehouse_id: UUID, sku_id: UUID,
                         horizon: int) -> dict | None:
    today = datetime.now(UTC).date()
    r = (await s.execute(text("""
        SELECT f.*, k.sku_code, p.name product_name, w.code wh_code, b.available
        FROM demand_forecasts f JOIN skus k ON k.id = f.sku_id JOIN products p ON p.id = k.product_id
        JOIN warehouses w ON w.id = f.warehouse_id
        LEFT JOIN inventory_balances b ON b.warehouse_id = f.warehouse_id AND b.sku_id = f.sku_id
        WHERE f.tenant_id = :t AND f.warehouse_id = :w AND f.sku_id = :s
    """), {"t": tenant_id, "w": warehouse_id, "s": sku_id})).first()
    if r is None:
        return None
    f = Forecast(warehouse_id, sku_id, r.method, float(r.daily_rate), float(r.sigma),
                 list(r.weekday_factor or []), r.history_days, r.sold_30d)
    history = (await s.execute(text("""
        SELECT (o.placed_at AT TIME ZONE COALESCE(ts.timezone,'Asia/Jakarta'))::date d, sum(i.quantity) q
        FROM order_items i JOIN orders o ON o.id = i.order_id
        LEFT JOIN tenant_settings ts ON ts.tenant_id = o.tenant_id
        WHERE o.tenant_id = :t AND o.warehouse_id = :w AND i.sku_id = :s
          AND o.status NOT IN ('CANCELLED','FAILED') AND o.placed_at >= now() - interval '60 days'
        GROUP BY d ORDER BY d
    """), {"t": tenant_id, "w": warehouse_id, "s": sku_id})).all()
    got = {h.d: int(h.q) for h in history}
    return {
        "sku_code": r.sku_code, "product_name": r.product_name, "warehouse": r.wh_code,
        "available": r.available or 0, "method": f.method, "daily_rate": round(f.daily_rate, 2),
        "sigma": round(f.sigma, 2), "history_days": f.history_days, "sold_30d": f.sold_30d,
        "generated_at": r.generated_at,
        "history": [{"date": (today - timedelta(days=i)).isoformat(),
                     "actual": got.get(today - timedelta(days=i), 0)} for i in range(29, -1, -1)],
        "forecast": [{"date": (today + timedelta(days=i + 1)).isoformat(),
                      "expected": round(f.expected(today + timedelta(days=i + 1)), 1),
                      "low": round(max(0.0, f.expected(today + timedelta(days=i + 1)) - f.sigma), 1),
                      "high": round(f.expected(today + timedelta(days=i + 1)) + f.sigma, 1)}
                     for i in range(horizon)],
    }
