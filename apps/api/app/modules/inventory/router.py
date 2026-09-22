from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.context import Ctx
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.core.schemas import Page
from app.core.utils import escape_like
from app.models import InventoryBalance, InventoryLedger, Location, Order, Product, Sku, Warehouse
from app.modules.inventory import service as inv
from app.modules.orders import service as order_service
from app.modules.settings import service as settings_svc
from app.modules.wms import common as wms_common

router = APIRouter(prefix="/inventory", tags=["inventory"])

ADJUST_REASONS = {
    "COUNT_CORRECTION": "Koreksi hasil hitung / stock opname",
    "FOUND": "Barang ditemukan",
    "LOST": "Barang hilang",
    "DAMAGED": "Pindahkan ke stok rusak",
    "WRITE_OFF_DAMAGED": "Musnahkan stok rusak",
    "OTHER": "Lainnya",
}


class BalanceOut(BaseModel):
    warehouse_id: UUID
    warehouse_code: str
    sku_id: UUID
    sku_code: str
    product_name: str
    variant_name: str
    on_hand: int
    reserved: int
    available: int
    damaged: int
    in_transit: int
    returned: int
    threshold: int
    updated_at: datetime


class LedgerOut(BaseModel):
    id: int
    warehouse_code: str
    sku_code: str
    entry_type: str
    d_on_hand: int
    d_reserved: int
    d_damaged: int
    d_returned: int
    on_hand_after: int
    reserved_after: int
    damaged_after: int
    returned_after: int
    reason_code: str | None
    note: str | None
    reference_type: str | None
    reference_id: str | None
    actor_user_id: UUID | None
    created_at: datetime


class ReceiptLine(BaseModel):
    sku_id: UUID
    quantity: int = Field(gt=0, le=1_000_000)
    location_id: UUID | None = None


class ReceiptIn(BaseModel):
    warehouse_id: UUID
    reference: str = Field(default="", max_length=64, description="No. PO / surat jalan")
    note: str = Field(default="", max_length=500)
    lines: list[ReceiptLine] = Field(min_length=1, max_length=500)


class AdjustIn(BaseModel):
    warehouse_id: UUID
    sku_id: UUID
    reason_code: str
    delta: int = Field(description="+/− untuk koreksi; positif untuk DAMAGED/WRITE_OFF_DAMAGED")
    note: str = Field(min_length=3, max_length=500)
    location_code: str | None = Field(default=None, max_length=160,
                                      description="Bin yang terdampak (wajib bila stok di luar bin tidak cukup)")

    @model_validator(mode="after")
    def _check(self):
        if self.reason_code not in ADJUST_REASONS:
            raise ValueError(f"reason_code harus salah satu dari {', '.join(ADJUST_REASONS)}")
        if self.delta == 0 or abs(self.delta) > 1_000_000:
            raise ValueError("delta tidak boleh 0")
        if self.reason_code in ("DAMAGED", "WRITE_OFF_DAMAGED", "FOUND") and self.delta < 0:
            raise ValueError(f"delta untuk {self.reason_code} harus positif")
        if self.reason_code == "LOST" and self.delta > 0:
            raise ValueError("delta untuk LOST harus negatif")
        return self


class ReserveIn(BaseModel):
    order_id: UUID


async def _warehouse(s: AsyncSession, p: Principal, wid: UUID) -> Warehouse:
    w = await s.scalar(select(Warehouse).where(Warehouse.id == wid, Warehouse.tenant_id == p.tenant_id))
    if w is None:
        raise AppError(404, "NOT_FOUND", "Gudang tidak ditemukan")
    if not w.is_active:
        raise AppError(409, "WAREHOUSE_INACTIVE", "Gudang nonaktif")
    return w


@router.get("/reasons")
async def reasons(p: Principal = Depends(require("inventory:read"))):
    return [{"code": k, "label": v} for k, v in ADJUST_REASONS.items()]


@router.get("", response_model=Page[BalanceOut])
async def list_balances(warehouse_id: UUID | None = None, sku_id: UUID | None = None,
                        q: str | None = Query(None, max_length=100), low_stock: int | None = Query(None, ge=0),
                        low_only: bool = Query(False, description="Hanya SKU di bawah batas menipis (per SKU / default)"),
                        limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                        p: Principal = Depends(require("inventory:read")), s: AsyncSession = Depends(get_session)):
    default_thr = (await settings_svc.get(s, p.tenant_id)).low_stock_threshold
    thr = func.coalesce(Sku.reorder_point, default_thr)
    stmt = (select(InventoryBalance, Warehouse.code, Sku.sku_code, Sku.variant_name, Product.name, thr.label("thr"))
            .join(Warehouse, Warehouse.id == InventoryBalance.warehouse_id)
            .join(Sku, Sku.id == InventoryBalance.sku_id)
            .join(Product, Product.id == Sku.product_id)
            .where(InventoryBalance.tenant_id == p.tenant_id)
            .order_by(Warehouse.code, Sku.sku_code))
    if warehouse_id:
        stmt = stmt.where(InventoryBalance.warehouse_id == warehouse_id)
    if sku_id:
        stmt = stmt.where(InventoryBalance.sku_id == sku_id)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Sku.sku_code.ilike(like, escape="\\"), Product.name.ilike(like, escape="\\")))
    if low_stock is not None:
        stmt = stmt.where(InventoryBalance.available <= low_stock)
    if low_only:
        stmt = stmt.where(InventoryBalance.available <= thr)
    total = await s.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = (await s.execute(stmt.limit(limit).offset(offset))).all()
    items = [BalanceOut(warehouse_id=b.warehouse_id, warehouse_code=wc, sku_id=b.sku_id, sku_code=sc,
                        product_name=pn, variant_name=vn, on_hand=b.on_hand, reserved=b.reserved,
                        available=b.available, damaged=b.damaged, in_transit=b.in_transit, returned=b.returned,
                        threshold=t, updated_at=b.updated_at) for b, wc, sc, vn, pn, t in rows]
    return Page(items=items, total=int(total or 0), limit=limit, offset=offset)


@router.get("/ledger", response_model=list[LedgerOut])
async def ledger(warehouse_id: UUID | None = None, sku_id: UUID | None = None,
                 reference_id: str | None = Query(None, max_length=64), entry_type: str | None = Query(None, max_length=20),
                 before_id: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=500),
                 p: Principal = Depends(require("inventory:read")), s: AsyncSession = Depends(get_session)):
    stmt = (select(InventoryLedger, Warehouse.code, Sku.sku_code)
            .join(Warehouse, Warehouse.id == InventoryLedger.warehouse_id)
            .join(Sku, Sku.id == InventoryLedger.sku_id)
            .where(InventoryLedger.tenant_id == p.tenant_id)
            .order_by(InventoryLedger.id.desc()).limit(limit))
    if warehouse_id:
        stmt = stmt.where(InventoryLedger.warehouse_id == warehouse_id)
    if sku_id:
        stmt = stmt.where(InventoryLedger.sku_id == sku_id)
    if reference_id:
        stmt = stmt.where(InventoryLedger.reference_id == reference_id)
    if entry_type:
        stmt = stmt.where(InventoryLedger.entry_type == entry_type)
    if before_id:
        stmt = stmt.where(InventoryLedger.id < before_id)
    rows = (await s.execute(stmt)).all()
    return [LedgerOut(id=e.id, warehouse_code=wc, sku_code=sc, entry_type=e.entry_type, d_on_hand=e.d_on_hand,
                      d_reserved=e.d_reserved, d_damaged=e.d_damaged, d_returned=e.d_returned,
                      on_hand_after=e.on_hand_after, reserved_after=e.reserved_after, damaged_after=e.damaged_after,
                      returned_after=e.returned_after, reason_code=e.reason_code,
                      note=e.note, reference_type=e.reference_type, reference_id=e.reference_id,
                      actor_user_id=e.actor_user_id, created_at=e.created_at) for e, wc, sc in rows]


@router.post("/receipts", status_code=201)
async def receive(body: ReceiptIn, p: Principal = Depends(require("inventory:write")),
                  s: AsyncSession = Depends(get_session)):
    w = await _warehouse(s, p, body.warehouse_id)
    sku_ids = {line.sku_id for line in body.lines}
    skus = {k.id: k for k in (await s.scalars(select(Sku).where(Sku.tenant_id == p.tenant_id,
                                                                Sku.id.in_(sku_ids)))).all()}
    missing = sku_ids - skus.keys()
    if missing:
        raise AppError(422, "UNKNOWN_SKU", "Ada SKU yang tidak ditemukan")
    loc_ids = {line.location_id for line in body.lines if line.location_id}
    locs_by_id = {}
    if loc_ids:
        locs = (await s.scalars(select(Location).where(Location.tenant_id == p.tenant_id, Location.id.in_(loc_ids),
                                                       Location.warehouse_id == w.id, Location.type == "BIN"))).all()
        if len(locs) != len(loc_ids):
            raise AppError(422, "INVALID_LOCATION", "Lokasi harus BIN di gudang ini")
        locs_by_id = {x.id: x for x in locs}
    ctx = Ctx.from_principal(p)
    bals = await inv.lock_balances(s, p.tenant_id, [(w.id, sid) for sid in sku_ids])
    for line in body.lines:
        await inv.apply(s, ctx, bals[(w.id, line.sku_id)], "RECEIPT", d_on_hand=line.quantity,
                        note=body.note or None, reference_type="receipt", reference_id=body.reference or None,
                        location_id=line.location_id)
        if line.location_id:  # langsung ditempatkan di bin
            b = await wms_common.lock_bin(s, p.tenant_id, w.id, line.location_id, line.sku_id)
            b.quantity += line.quantity
            wms_common.movement(s, ctx, warehouse_id=w.id, sku_id=line.sku_id, qty=line.quantity, kind="PUTAWAY",
                                to_loc=locs_by_id[line.location_id].id, ref_type="receipt",
                                ref_id=body.reference or None)
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="inventory.received",
                       entity_type="warehouse", entity_id=w.id,
                       after={"reference": body.reference, "lines": [
                           {"sku": skus[x.sku_id].sku_code, "qty": x.quantity} for x in body.lines]},
                       correlation_id=p.correlation_id, ip=p.ip)
    return {"received_lines": len(body.lines), "units": sum(x.quantity for x in body.lines)}


@router.post("/adjustments", status_code=201)
async def adjust(body: AdjustIn, p: Principal = Depends(require("inventory:adjust")),
                 s: AsyncSession = Depends(get_session)):
    w = await _warehouse(s, p, body.warehouse_id)
    sku = await s.scalar(select(Sku).where(Sku.id == body.sku_id, Sku.tenant_id == p.tenant_id))
    if sku is None:
        raise AppError(404, "NOT_FOUND", "SKU tidak ditemukan")
    bal = (await inv.lock_balances(s, p.tenant_id, [(w.id, sku.id)]))[(w.id, sku.id)]
    before = {"on_hand": bal.on_hand, "reserved": bal.reserved, "damaged": bal.damaged}
    ctx = Ctx.from_principal(p)
    if body.reason_code == "DAMAGED":
        entry = await inv.apply(s, ctx, bal, "DAMAGE", d_on_hand=-body.delta, d_damaged=body.delta,
                                reason_code=body.reason_code, note=body.note, reference_type="adjustment")
    elif body.reason_code == "WRITE_OFF_DAMAGED":
        entry = await inv.apply(s, ctx, bal, "ADJUSTMENT", d_damaged=-body.delta,
                                reason_code=body.reason_code, note=body.note, reference_type="adjustment")
    else:
        entry = await inv.apply(s, ctx, bal, "ADJUSTMENT", d_on_hand=body.delta,
                                reason_code=body.reason_code, note=body.note, reference_type="adjustment")
    # Jaga konsistensi dengan stok per bin: isi bin tidak boleh melebihi stok fisik gudang
    phys_delta = body.delta if body.reason_code not in ("DAMAGED", "WRITE_OFF_DAMAGED") else \
        (-body.delta if body.reason_code == "DAMAGED" else 0)
    await s.flush()
    if body.location_code:
        loc = await wms_common.resolve_bin(s, p.tenant_id, w.id, body.location_code)
        b = await wms_common.lock_bin(s, p.tenant_id, w.id, loc.id, sku.id)
        if b.quantity + phys_delta < b.allocated:
            raise AppError(409, "BIN_ALLOCATED", "Stok di bin ini sedang dialokasikan untuk picking")
        b.quantity += phys_delta
        if phys_delta:
            wms_common.movement(s, ctx, warehouse_id=w.id, sku_id=sku.id, qty=abs(phys_delta), kind="ADJUST",
                                from_loc=loc.id if phys_delta < 0 else None, to_loc=loc.id if phys_delta > 0 else None,
                                ref_type="adjustment", ref_id=entry.id)
    elif phys_delta < 0:
        if bal.on_hand < await wms_common.sum_bins(s, p.tenant_id, w.id, sku.id):
            raise AppError(409, "LOCATION_REQUIRED",
                           "Stok di luar bin tidak cukup; pilih bin (location_code) yang dikurangi")
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="inventory.adjusted",
                       entity_type="sku", entity_id=sku.id, before=before,
                       after={"on_hand": bal.on_hand, "reserved": bal.reserved, "damaged": bal.damaged,
                              "warehouse": w.code, "reason": body.reason_code, "note": body.note},
                       correlation_id=p.correlation_id, ip=p.ip)
    await s.flush()
    return {"ledger_id": entry.id, "on_hand": bal.on_hand, "reserved": bal.reserved,
            "available": bal.on_hand - bal.reserved, "damaged": bal.damaged}


@router.post("/reserve")
async def reserve(body: ReserveIn, p: Principal = Depends(require("order:write")),
                  s: AsyncSession = Depends(get_session)):
    """PRD §14: POST /inventory/reserve — coba (ulang) reservasi stok untuk sebuah order."""
    o = await s.scalar(select(Order).where(Order.id == body.order_id, Order.tenant_id == p.tenant_id)
                       .with_for_update())
    if o is None:
        raise AppError(404, "NOT_FOUND", "Order tidak ditemukan")
    await order_service.run_action(s, Ctx.from_principal(p), o, "reserve")
    return {"order_id": str(o.id), "status": o.status, "stock_status": o.stock_status,
            "allocation_note": o.allocation_note}


@router.get("/reconcile")
async def reconcile(warehouse_id: UUID | None = None, p: Principal = Depends(require("inventory:read")),
                    s: AsyncSession = Depends(get_session)):
    return await inv.reconcile(s, p.tenant_id, warehouse_id)
