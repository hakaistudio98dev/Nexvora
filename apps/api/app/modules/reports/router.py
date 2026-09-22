"""Ekspor CSV (UTF-8 BOM agar rapi di Excel). Sel teks yang diawali = + - @ diberi tanda kutip
tunggal untuk mencegah CSV/formula injection."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, get_principal
from app.core.entitlements import check_permission
from app.core.errors import AppError
from app.modules.analytics.service import RANGE, F, params

router = APIRouter(prefix="/reports", tags=["reports"])
MAX_ROWS = 100_000
start, end = RANGE

REPORTS: dict[str, dict] = {
    "orders": {"perm": "order:read", "label": "Order", "sql": f"""
        SELECT o.order_number, o.channel, o.external_ref, o.status, o.payment_status, o.stock_status, w.code gudang,
               o.customer_name, o.customer_phone, o.ship_city, o.ship_postal_code, o.subtotal, o.shipping_fee, o.discount,
               o.total, o.placed_at AT TIME ZONE :tz dibuat, o.paid_at AT TIME ZONE :tz dibayar,
               o.shipped_at AT TIME ZONE :tz dikirim, o.delivered_at AT TIME ZONE :tz diterima
        FROM orders o LEFT JOIN warehouses w ON w.id = o.warehouse_id
        WHERE {F} AND o.placed_at >= {start} AND o.placed_at < {end} ORDER BY o.placed_at"""},
    "order_items": {"perm": "order:read", "label": "Item order", "sql": f"""
        SELECT o.order_number, o.channel, o.status, k.sku_code, pr.name produk, k.variant_name, i.quantity, i.unit_price,
               i.line_total, o.placed_at AT TIME ZONE :tz dibuat
        FROM order_items i JOIN orders o ON o.id = i.order_id JOIN skus k ON k.id = i.sku_id JOIN products pr ON pr.id = k.product_id
        WHERE {F} AND o.placed_at >= {start} AND o.placed_at < {end} ORDER BY o.placed_at, k.sku_code"""},
    "sla": {"perm": "order:read", "label": "SLA pengiriman", "sql": f"""
        SELECT o.order_number, o.channel, w.code gudang, o.paid_at AT TIME ZONE :tz dibayar,
               o.shipped_at AT TIME ZONE :tz dikirim,
               round(extract(epoch FROM o.shipped_at - o.paid_at) / 3600, 2) jam_sampai_kirim, :sla batas_jam,
               CASE WHEN o.shipped_at <= o.paid_at + make_interval(hours => :sla) THEN 'tepat waktu' ELSE 'terlambat' END sla
        FROM orders o LEFT JOIN warehouses w ON w.id = o.warehouse_id
        WHERE {F} AND o.shipped_at >= {start} AND o.shipped_at < {end} AND o.paid_at IS NOT NULL ORDER BY o.shipped_at"""},
    "shipments": {"perm": "shipping:read", "label": "Pengiriman", "sql": f"""
        SELECT o.order_number, sh.courier_code kurir, sh.service_code layanan, sh.tracking_number resi, sh.status,
               sh.cost ongkir, sh.weight_g berat_gram, o.ship_city kota, sh.handed_over_at AT TIME ZONE :tz diserahkan,
               sh.delivered_at AT TIME ZONE :tz diterima
        FROM shipments sh JOIN orders o ON o.id = sh.order_id
        WHERE {F} AND sh.created_at >= {start} AND sh.created_at < {end} ORDER BY sh.created_at"""},
    "returns": {"perm": "returns:read", "label": "Retur", "sql": f"""
        SELECT r.number, o.order_number, r.status, r.reason_code alasan, r.resolution solusi, r.refund_amount refund,
               k.sku_code, l.quantity diretur, l.received_qty diterima, l.restock_qty layak_jual, l.damaged_qty rusak,
               r.created_at AT TIME ZONE :tz dibuat, r.closed_at AT TIME ZONE :tz selesai
        FROM returns r JOIN orders o ON o.id = r.order_id JOIN return_lines l ON l.return_id = r.id JOIN skus k ON k.id = l.sku_id
        WHERE {F} AND r.created_at >= {start} AND r.created_at < {end} ORDER BY r.created_at"""},
    "inventory": {"perm": "inventory:read", "label": "Saldo stok (saat ini)", "sql": """
        SELECT w.code gudang, k.sku_code, pr.name produk, k.variant_name, b.on_hand fisik, b.reserved direservasi,
               b.available tersedia, b.damaged rusak, b.returned retur_belum_inspeksi,
               COALESCE(k.reorder_point, ts.low_stock_threshold, 5) batas_menipis
        FROM inventory_balances b JOIN skus k ON k.id = b.sku_id JOIN products pr ON pr.id = k.product_id
        JOIN warehouses w ON w.id = b.warehouse_id LEFT JOIN tenant_settings ts ON ts.tenant_id = b.tenant_id
        WHERE b.tenant_id = :t AND (CAST(:wh AS uuid) IS NULL OR b.warehouse_id = CAST(:wh AS uuid))
          AND (CAST(:ch AS text) IS NULL OR true)
        ORDER BY w.code, k.sku_code"""},
    "ledger": {"perm": "inventory:read", "label": "Mutasi stok (ledger)", "sql": f"""
        SELECT l.id, l.created_at AT TIME ZONE :tz waktu, w.code gudang, k.sku_code, l.entry_type jenis, l.reason_code alasan,
               l.d_on_hand, l.d_reserved, l.d_damaged, l.d_returned, l.on_hand_after saldo_fisik, l.reference_type,
               l.reference_id referensi, l.note catatan
        FROM inventory_ledger l JOIN warehouses w ON w.id = l.warehouse_id JOIN skus k ON k.id = l.sku_id
        WHERE l.tenant_id = :t AND (CAST(:wh AS uuid) IS NULL OR l.warehouse_id = CAST(:wh AS uuid))
          AND l.created_at >= {start} AND l.created_at < {end} AND (CAST(:ch AS text) IS NULL OR true) ORDER BY l.id"""},
}


def _cell(v):  # noqa: ANN001, ANN202
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, (Decimal, int, float)):
        return str(v)
    v = str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@router.get("")
async def available(p: Principal = Depends(get_principal)):
    return [{"name": k, "label": v["label"], "allowed": p.can(v["perm"])} for k, v in REPORTS.items()]


@router.get("/{name}.csv")
async def export(name: str, date_from: date | None = None, date_to: date | None = None, warehouse_id: UUID | None = None,
                 channel: str | None = Query(None, max_length=20), sep: str = Query("comma", pattern="^(comma|semicolon)$"),
                 p: Principal = Depends(get_principal), s: AsyncSession = Depends(get_session)):
    rep = REPORTS.get(name)
    if rep is None:
        raise AppError(404, "NOT_FOUND", "Laporan tidak dikenal")
    for perm in ("analytics:read", rep["perm"]):
        if not p.can(perm):
            raise AppError(403, "FORBIDDEN", "Anda tidak memiliki izin untuk laporan ini")
        check_permission(p.entitlement, perm)
    prm = await params(s, p.tenant_id, date_from, date_to, warehouse_id, channel)
    res = await s.execute(text(rep["sql"] + f" LIMIT {MAX_ROWS + 1}"), prm)
    rows = res.all()
    if len(rows) > MAX_ROWS:
        raise AppError(413, "TOO_MANY_ROWS", f"Lebih dari {MAX_ROWS:,} baris; persempit rentang tanggal atau filter gudang")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";" if sep == "semicolon" else ",")
    w.writerow(list(res.keys()))
    for r in rows:
        w.writerow([_cell(v) for v in r])
    fname = f"nexvora-{name}-{prm['d_from']}_{prm['d_to']}.csv"
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


