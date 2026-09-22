"""Perhitungan KPI (PRD §25) langsung dari data operasional — tidak ada tabel ringkasan yang bisa basi."""
import math
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.settings import service as settings_svc

SHIPPED = "('SHIPPED','DELIVERED','RETURN_REQUESTED','RETURNED','REFUNDED')"
DEAD = "('CANCELLED','FAILED')"
OPEN_FLOW = "('PAID','ALLOCATED','PICKING','PACKING','READY_TO_SHIP')"
F = ("o.tenant_id = :t AND (CAST(:wh AS uuid) IS NULL OR o.warehouse_id = CAST(:wh AS uuid)) "
     "AND (CAST(:ch AS text) IS NULL OR o.channel = CAST(:ch AS text))")
RANGE = ("CAST(:d_from AS date)::timestamp AT TIME ZONE :tz", "(CAST(:d_to AS date) + 1)::timestamp AT TIME ZONE :tz")


def ratio(a: float, b: float) -> float | None:
    return round(a * 100 / b, 1) if b else None


async def params(s: AsyncSession, tenant_id: UUID, d_from: date | None, d_to: date | None,
                 warehouse_id: UUID | None, channel: str | None) -> dict:
    st = await settings_svc.get(s, tenant_id)
    today = datetime.now(UTC).date()
    d_to = d_to or today
    d_from = d_from or d_to - timedelta(days=29)
    if d_from > d_to:
        raise AppError(422, "INVALID_RANGE", "Tanggal awal harus sebelum tanggal akhir")
    if (d_to - d_from).days > 366:
        raise AppError(422, "RANGE_TOO_LONG", "Rentang maksimal 1 tahun")
    return {"t": tenant_id, "wh": str(warehouse_id) if warehouse_id else None, "ch": channel, "d_from": d_from,
            "d_to": d_to, "tz": st.timezone, "sla": st.sla_ship_hours, "risk": st.sla_risk_hours}


async def overview(s: AsyncSession, p: dict) -> dict:
    start, end = RANGE
    one = lambda sql: s.execute(text(sql), p)  # noqa: E731
    o = (await one(f"""
        SELECT count(*) total, count(*) FILTER (WHERE status IN {DEAD}) cancelled,
               count(*) FILTER (WHERE status IN {SHIPPED}) shipped,
               count(*) FILTER (WHERE stock_status = 'OUT_OF_STOCK' AND status IN ('CREATED','PAID')) on_hold,
               COALESCE(sum(total) FILTER (WHERE status NOT IN {DEAD}), 0) revenue
        FROM orders o WHERE {F} AND placed_at >= {start} AND placed_at < {end}""")).one()
    sla = (await one(f"""
        SELECT count(*) n, count(*) FILTER (WHERE shipped_at <= paid_at + make_interval(hours => :sla)) ok,
               avg(extract(epoch FROM shipped_at - paid_at)) / 3600 avg_h,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM shipped_at - paid_at)) / 3600 p90_h
        FROM orders o WHERE {F} AND shipped_at >= {start} AND shipped_at < {end} AND paid_at IS NOT NULL""")).one()
    shipped_in_range = (await one(f"SELECT count(*) FROM orders o WHERE {F} AND shipped_at >= {start} AND shipped_at < {end}")).scalar()
    pick = (await one(f"""
        SELECT count(*) FILTER (WHERE status = 'DONE') done, count(*) FILTER (WHERE status = 'SHORT') short
        FROM wms_tasks WHERE tenant_id = :t AND task_type = 'PICK' AND completed_at >= {start} AND completed_at < {end}
          AND (CAST(:wh AS uuid) IS NULL OR warehouse_id = CAST(:wh AS uuid))""")).one()
    pack = (await one(f"""
        SELECT count(*) n, count(*) FILTER (WHERE p.override_reason IS NOT NULL) mismatch
        FROM packages p JOIN orders o ON o.id = p.order_id WHERE {F} AND p.created_at >= {start} AND p.created_at < {end}""")).one()
    inv = (await one(f"""
        SELECT count(*) n, count(*) FILTER (WHERE l.counted_qty = l.system_qty) matched
        FROM cycle_count_lines l JOIN cycle_counts c ON c.id = l.count_id
        WHERE c.tenant_id = :t AND c.status = 'APPROVED' AND c.approved_at >= {start} AND c.approved_at < {end}
          AND (CAST(:wh AS uuid) IS NULL OR c.warehouse_id = CAST(:wh AS uuid))""")).one()
    exc_orders = (await one(f"""
        SELECT count(DISTINCT e.order_id) FROM wms_exceptions e JOIN orders o ON o.id = e.order_id
        WHERE {F} AND o.placed_at >= {start} AND o.placed_at < {end}""")).scalar()
    returns = (await one(f"""
        SELECT count(*) FROM returns r JOIN orders o ON o.id = r.order_id
        WHERE {F} AND r.status <> 'REJECTED' AND r.created_at >= {start} AND r.created_at < {end}""")).scalar()
    cost = (await one(f"""
        SELECT avg(sh.cost), sum(sh.cost), count(sh.cost) FROM shipments sh JOIN orders o ON o.id = sh.order_id
        WHERE {F} AND sh.handed_over_at >= {start} AND sh.handed_over_at < {end} AND sh.cost IS NOT NULL""")).one()
    by_channel = (await one(f"""
        SELECT channel, count(*) n, COALESCE(sum(total) FILTER (WHERE status NOT IN {DEAD}), 0) revenue
        FROM orders o WHERE {F} AND placed_at >= {start} AND placed_at < {end} GROUP BY channel ORDER BY n DESC""")).all()
    by_status = (await one(f"""
        SELECT status, count(*) n FROM orders o WHERE {F} AND placed_at >= {start} AND placed_at < {end}
        GROUP BY status ORDER BY n DESC""")).all()
    live = o.total - o.cancelled
    return {
        "range": {"from": p["d_from"], "to": p["d_to"], "timezone": p["tz"], "sla_ship_hours": p["sla"]},
        "orders": {"total": o.total, "cancelled": o.cancelled, "shipped": o.shipped, "on_hold": o.on_hold,
                   "revenue": str(o.revenue), "aov": str(round(o.revenue / live, 2)) if live else None},
        "kpi": {
            "fulfillment_rate": ratio(o.shipped, live),
            "sla_compliance": ratio(sla.ok, sla.n),
            "avg_hours_to_ship": round(sla.avg_h, 1) if sla.avg_h is not None else None,
            "p90_hours_to_ship": round(sla.p90_h, 1) if sla.p90_h is not None else None,
            "pick_accuracy": ratio(pick.done, pick.done + pick.short),
            "pack_accuracy": ratio(pack.n - pack.mismatch, pack.n),
            "inventory_accuracy": ratio(inv.matched, inv.n),
            "exception_rate": ratio(exc_orders or 0, o.total),
            "return_rate": ratio(returns or 0, shipped_in_range or 0),
            "cost_per_order": str(round(cost[0], 0)) if cost[0] is not None else None,
        },
        "samples": {"sla_orders": sla.n, "pick_tasks": pick.done + pick.short, "packages": pack.n,
                    "count_lines": inv.n, "shipments_with_cost": cost[2], "shipped_in_range": shipped_in_range or 0},
        "by_channel": [{"channel": r.channel, "orders": r.n, "revenue": str(r.revenue)} for r in by_channel],
        "by_status": [{"status": r.status, "orders": r.n} for r in by_status],
    }


async def timeseries(s: AsyncSession, p: dict) -> list[dict]:
    start, end = RANGE
    rows = (await s.execute(text(f"""
        WITH days AS (SELECT generate_series(CAST(:d_from AS date), CAST(:d_to AS date), interval '1 day')::date d),
        placed AS (SELECT (placed_at AT TIME ZONE :tz)::date d, count(*) c,
                          COALESCE(sum(total) FILTER (WHERE status NOT IN {DEAD}), 0) rev
                   FROM orders o WHERE {F} AND placed_at >= {start} AND placed_at < {end} GROUP BY 1),
        shipped AS (SELECT (shipped_at AT TIME ZONE :tz)::date d, count(*) c,
                           count(*) FILTER (WHERE paid_at IS NOT NULL AND shipped_at <= paid_at + make_interval(hours => :sla)) ok
                    FROM orders o WHERE {F} AND shipped_at >= {start} AND shipped_at < {end} GROUP BY 1)
        SELECT days.d, COALESCE(placed.c, 0) placed, COALESCE(placed.rev, 0) revenue,
               COALESCE(shipped.c, 0) shipped, COALESCE(shipped.ok, 0) on_time
        FROM days LEFT JOIN placed USING (d) LEFT JOIN shipped USING (d) ORDER BY days.d"""), p)).all()
    return [{"date": r.d, "placed": r.placed, "revenue": str(r.revenue), "shipped": r.shipped, "on_time": r.on_time}
            for r in rows]


async def sla_board(s: AsyncSession, p: dict) -> dict:
    rows = (await s.execute(text(f"""
        SELECT o.id, o.order_number, o.status, o.channel, o.customer_name, o.paid_at, w.code wh,
               o.paid_at + make_interval(hours => :sla) due_at
        FROM orders o LEFT JOIN warehouses w ON w.id = o.warehouse_id
        WHERE {F} AND o.status IN {OPEN_FLOW} AND o.paid_at IS NOT NULL ORDER BY due_at LIMIT 300"""), p)).all()
    now = datetime.now(UTC)
    out, counts = [], {"overdue": 0, "at_risk": 0, "on_track": 0}
    for r in rows:
        hours_left = (r.due_at - now).total_seconds() / 3600
        state = "overdue" if hours_left < 0 else "at_risk" if hours_left <= p["risk"] else "on_track"
        counts[state] += 1
        out.append({"order_id": str(r.id), "order_number": r.order_number, "status": r.status, "channel": r.channel,
                    "customer_name": r.customer_name, "warehouse": r.wh, "paid_at": r.paid_at, "due_at": r.due_at,
                    "hours_left": round(hours_left, 1), "state": state})
    by_stage = {}
    for x in out:
        by_stage.setdefault(x["status"], {"overdue": 0, "at_risk": 0, "on_track": 0})[x["state"]] += 1
    return {"sla_ship_hours": p["sla"], "risk_hours": p["risk"], "counts": counts, "by_stage": by_stage,
            "orders": [x for x in out if x["state"] != "on_track"][:100]}


async def productivity(s: AsyncSession, p: dict) -> list[dict]:
    start, end = RANGE
    wh = "(CAST(:wh AS uuid) IS NULL OR warehouse_id = CAST(:wh AS uuid))"
    rows: dict = {}

    def add(uid, key, val):  # noqa: ANN001, ANN202
        if uid:
            rows.setdefault(uid, {"picks": 0, "units_picked": 0, "short_picks": 0, "putaways": 0, "units_putaway": 0,
                                  "packages": 0, "count_lines": 0})[key] += int(val or 0)
    for r in (await s.execute(text(f"""
        SELECT assigned_to, task_type, count(*) FILTER (WHERE status = 'DONE') done, count(*) FILTER (WHERE status = 'SHORT') short,
               sum(done_qty) units FROM wms_tasks WHERE tenant_id = :t AND {wh} AND completed_at >= {start}
               AND completed_at < {end} GROUP BY assigned_to, task_type"""), p)).all():
        if r.task_type == "PICK":
            add(r.assigned_to, "picks", r.done)
            add(r.assigned_to, "short_picks", r.short)
            add(r.assigned_to, "units_picked", r.units)
        else:
            add(r.assigned_to, "putaways", r.done)
            add(r.assigned_to, "units_putaway", r.units)
    for r in (await s.execute(text(f"""
        SELECT pk.packed_by, count(*) n FROM packages pk JOIN orders o ON o.id = pk.order_id
        WHERE {F} AND pk.created_at >= {start} AND pk.created_at < {end} GROUP BY pk.packed_by"""), p)).all():
        add(r.packed_by, "packages", r.n)
    for r in (await s.execute(text(f"""
        SELECT l.counted_by, count(*) n FROM cycle_count_lines l JOIN cycle_counts c ON c.id = l.count_id
        WHERE c.tenant_id = :t AND (CAST(:wh AS uuid) IS NULL OR c.warehouse_id = CAST(:wh AS uuid))
          AND l.counted_at >= {start} AND l.counted_at < {end} GROUP BY l.counted_by"""), p)).all():
        add(r.counted_by, "count_lines", r.n)
    if not rows:
        return []
    names = {r.id: (r.full_name, r.email) for r in (await s.execute(
        text("SELECT id, full_name, email FROM users WHERE id = ANY(:ids)"), {"ids": list(rows)})).all()}
    out = []
    for uid, v in rows.items():
        out.append({"user_id": str(uid), "name": names.get(uid, ("?", ""))[0], "email": names.get(uid, ("", ""))[1], **v,
                    "pick_accuracy": ratio(v["picks"], v["picks"] + v["short_picks"])})
    return sorted(out, key=lambda x: -(x["units_picked"] + x["packages"] * 3 + x["units_putaway"]))


async def top_skus(s: AsyncSession, p: dict, limit: int) -> list[dict]:
    start, end = RANGE
    rows = (await s.execute(text(f"""
        SELECT k.sku_code, pr.name, k.variant_name, sum(i.quantity) units, sum(i.line_total) revenue, count(DISTINCT o.id) orders
        FROM order_items i JOIN orders o ON o.id = i.order_id JOIN skus k ON k.id = i.sku_id JOIN products pr ON pr.id = k.product_id
        WHERE {F} AND o.placed_at >= {start} AND o.placed_at < {end} AND o.status NOT IN {DEAD}
        GROUP BY k.sku_code, pr.name, k.variant_name ORDER BY units DESC LIMIT :lim"""), {**p, "lim": limit})).all()
    return [{"sku_code": r.sku_code, "product_name": r.name, "variant_name": r.variant_name, "units": int(r.units),
             "revenue": str(r.revenue), "orders": r.orders} for r in rows]


async def inventory_health(s: AsyncSession, p: dict) -> dict:
    rows = (await s.execute(text("""
        WITH sales AS (
          SELECT o.warehouse_id, i.sku_id, sum(i.quantity) u FROM order_items i JOIN orders o ON o.id = i.order_id
          WHERE o.tenant_id = :t AND o.status NOT IN ('CANCELLED','FAILED') AND o.warehouse_id IS NOT NULL
            AND o.placed_at >= now() - interval '30 days' GROUP BY 1, 2)
        SELECT w.code wh, k.sku_code, pr.name, b.on_hand, b.reserved, b.available, b.damaged,
               COALESCE(k.reorder_point, ts.low_stock_threshold, 5) thr, COALESCE(sa.u, 0) sold30
        FROM inventory_balances b JOIN skus k ON k.id = b.sku_id JOIN products pr ON pr.id = k.product_id
        JOIN warehouses w ON w.id = b.warehouse_id LEFT JOIN tenant_settings ts ON ts.tenant_id = b.tenant_id
        LEFT JOIN sales sa ON sa.warehouse_id = b.warehouse_id AND sa.sku_id = b.sku_id
        WHERE b.tenant_id = :t AND k.is_active AND (CAST(:wh AS uuid) IS NULL OR b.warehouse_id = CAST(:wh AS uuid))"""), p)).all()
    items, counts = [], {"out": 0, "low": 0, "slow": 0, "ok": 0}
    for r in rows:
        daily = r.sold30 / 30
        cover = round(r.available / daily, 1) if daily > 0 else None
        state = ("out" if r.available <= 0 and r.sold30 > 0 else "low" if r.available <= r.thr
                 else "slow" if r.sold30 == 0 and r.on_hand > 0 else "ok")
        counts[state] += 1
        items.append({"warehouse": r.wh, "sku_code": r.sku_code, "product_name": r.name, "on_hand": r.on_hand,
                      "reserved": r.reserved, "available": r.available, "damaged": r.damaged, "threshold": r.thr,
                      "sold_30d": int(r.sold30), "avg_daily": round(daily, 2), "days_of_cover": cover, "state": state,
                      "suggested_reorder": max(0, math.ceil(daily * 30 - r.available)) if daily > 0 else 0})
    order = {"out": 0, "low": 1, "slow": 2, "ok": 3}
    items.sort(key=lambda x: (order[x["state"]], x["days_of_cover"] if x["days_of_cover"] is not None else 1e9))
    return {"counts": counts, "items": items[:500]}
