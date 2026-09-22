from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.context import Ctx
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.models import (BinStock, CycleCount, CycleCountLine, InboundLine, InboundReceipt, InventoryBalance, Location,
                        Order, Package, Product, Sku, Wave, WmsException, WmsTask)
from app.modules.wms import common, counting, inbound, packing, picking

router = APIRouter(prefix="/wms", tags=["wms"])
EVENT_ID = Field(default=None, max_length=100, description="ID unik dari perangkat untuk antrean offline")


# =============================================================== helpers
async def _names(s: AsyncSession, tenant_id: UUID, sku_ids=(), loc_ids=(), order_ids=()) -> tuple[dict, dict, dict]:  # noqa: ANN001
    skus = {}
    if sku_ids:
        rows = (await s.execute(select(Sku, Product.name).join(Product, Product.id == Sku.product_id)
                                .where(Sku.tenant_id == tenant_id, Sku.id.in_(set(sku_ids))))).all()
        skus = {k.id: {"sku_code": k.sku_code, "barcode": k.barcode, "variant_name": k.variant_name, "product_name": n}
                for k, n in rows}
    locs = {}
    ids = {x for x in loc_ids if x}
    if ids:
        locs = {x.id: x.full_code for x in (await s.scalars(select(Location).where(Location.id.in_(ids)))).all()}
    orders = {}
    oids = {x for x in order_ids if x}
    if oids:
        orders = {o.id: o.order_number for o in (await s.scalars(select(Order).where(Order.id.in_(oids)))).all()}
    return skus, locs, orders


async def _tasks_out(s: AsyncSession, tenant_id: UUID, tasks: list[WmsTask]) -> list[dict]:
    skus, locs, orders = await _names(s, tenant_id, [t.sku_id for t in tasks],
                                      [t.from_location_id for t in tasks] + [t.to_location_id for t in tasks],
                                      [t.order_id for t in tasks])
    return [{
        "id": str(t.id), "task_type": t.task_type, "status": t.status, "quantity": t.quantity, "done_qty": t.done_qty,
        "remaining": t.remaining, "sku_id": str(t.sku_id), **skus.get(t.sku_id, {}),
        "from_location": locs.get(t.from_location_id), "to_location": locs.get(t.to_location_id),
        "order_id": str(t.order_id) if t.order_id else None, "order_number": orders.get(t.order_id),
        "wave_id": str(t.wave_id) if t.wave_id else None, "inbound_id": str(t.inbound_id) if t.inbound_id else None,
        "created_at": t.created_at,
    } for t in tasks]


async def _task(s: AsyncSession, p: Principal, task_id: UUID) -> WmsTask:
    t = await s.scalar(select(WmsTask).where(WmsTask.id == task_id, WmsTask.tenant_id == p.tenant_id)
                       .with_for_update())
    if t is None:
        raise AppError(404, "NOT_FOUND", "Task tidak ditemukan")
    return t


async def _order_by_ref(s: AsyncSession, p: Principal, ref: str, lock: bool = False) -> Order:
    ref = ref.strip()
    cond = [Order.order_number == ref.upper()]
    try:
        cond.append(Order.id == UUID(ref))
    except ValueError:
        pass
    stmt = select(Order).where(Order.tenant_id == p.tenant_id, or_(*cond))
    o = await s.scalar(stmt.with_for_update() if lock else stmt)
    if o is None:
        raise AppError(404, "NOT_FOUND", f"Order {ref} tidak ditemukan")
    return o


# =============================================================== ringkasan & scan lookup
@router.get("/stats")
async def stats(warehouse_id: UUID, p: Principal = Depends(require("wms:read")), s: AsyncSession = Depends(get_session)):
    def tasks(tt):  # noqa: ANN001, ANN202
        return select(func.count()).select_from(WmsTask).where(
            WmsTask.tenant_id == p.tenant_id, WmsTask.warehouse_id == warehouse_id, WmsTask.task_type == tt,
            WmsTask.status.in_(("OPEN", "IN_PROGRESS")))
    today = datetime.now(UTC) - timedelta(hours=24)
    return {
        "open_putaway": await s.scalar(tasks("PUTAWAY")),
        "open_picks": await s.scalar(tasks("PICK")),
        "orders_allocated": await s.scalar(select(func.count()).select_from(Order).where(
            Order.tenant_id == p.tenant_id, Order.warehouse_id == warehouse_id, Order.status == "ALLOCATED")),
        "orders_picking": await s.scalar(select(func.count()).select_from(Order).where(
            Order.tenant_id == p.tenant_id, Order.warehouse_id == warehouse_id, Order.status == "PICKING")),
        "orders_packing": await s.scalar(select(func.count()).select_from(Order).where(
            Order.tenant_id == p.tenant_id, Order.warehouse_id == warehouse_id, Order.status == "PACKING")),
        "ready_to_ship": await s.scalar(select(func.count()).select_from(Order).where(
            Order.tenant_id == p.tenant_id, Order.warehouse_id == warehouse_id, Order.status == "READY_TO_SHIP")),
        "packed_24h": await s.scalar(select(func.count()).select_from(Package).join(Order, Order.id == Package.order_id)
                                     .where(Package.tenant_id == p.tenant_id, Order.warehouse_id == warehouse_id,
                                            Package.created_at >= today)),
        "inbound_open": await s.scalar(select(func.count()).select_from(InboundReceipt).where(
            InboundReceipt.tenant_id == p.tenant_id, InboundReceipt.warehouse_id == warehouse_id,
            InboundReceipt.status == "RECEIVING")),
        "exceptions_open": await s.scalar(select(func.count()).select_from(WmsException).where(
            WmsException.tenant_id == p.tenant_id, WmsException.warehouse_id == warehouse_id,
            WmsException.status == "OPEN")),
    }


@router.get("/scan")
async def lookup(warehouse_id: UUID, code: str = Query(min_length=1, max_length=100),
                 p: Principal = Depends(require("wms:read")), s: AsyncSession = Depends(get_session)):
    """Satu scan, sistem menebak jenisnya: bin, SKU, atau order."""
    loc = await s.scalar(select(Location).where(Location.tenant_id == p.tenant_id, Location.warehouse_id == warehouse_id,
                                                Location.full_code == code.strip().upper()))
    if loc:
        rows = (await s.scalars(select(BinStock).where(BinStock.location_id == loc.id, BinStock.quantity > 0))).all()
        skus, _, _ = await _names(s, p.tenant_id, [r.sku_id for r in rows])
        return {"type": "LOCATION", "location": {"id": str(loc.id), "full_code": loc.full_code, "type": loc.type,
                                                 "is_active": loc.is_active},
                "stock": [{"sku_id": str(r.sku_id), **skus[r.sku_id], "quantity": r.quantity, "allocated": r.allocated}
                          for r in rows]}
    try:
        sku = await common.resolve_sku(s, p.tenant_id, code)
    except AppError:
        sku = None
    if sku:
        rows = (await s.scalars(select(BinStock).where(BinStock.warehouse_id == warehouse_id, BinStock.sku_id == sku.id,
                                                       BinStock.quantity > 0))).all()
        _, locs, _ = await _names(s, p.tenant_id, loc_ids=[r.location_id for r in rows])
        bal = await s.scalar(select(InventoryBalance).where(InventoryBalance.warehouse_id == warehouse_id,
                                                            InventoryBalance.sku_id == sku.id))
        in_bins = sum(r.quantity for r in rows)
        skus, _, _ = await _names(s, p.tenant_id, [sku.id])
        return {"type": "SKU", "sku": {"id": str(sku.id), **skus[sku.id]},
                "on_hand": bal.on_hand if bal else 0, "available": bal.available if bal else 0,
                "unplaced": (bal.on_hand if bal else 0) - in_bins,
                "bins": sorted([{"location": locs[r.location_id], "quantity": r.quantity, "allocated": r.allocated}
                                for r in rows], key=lambda x: x["location"])}
    try:
        o = await _order_by_ref(s, p, code)
        return {"type": "ORDER", "order": {"id": str(o.id), "order_number": o.order_number, "status": o.status}}
    except AppError:
        raise AppError(404, "UNKNOWN_CODE", f"Kode {code} tidak dikenal sebagai bin, SKU, atau order") from None


@router.get("/bins")
async def bin_stock(warehouse_id: UUID, prefix: str = Query("", max_length=160), sku_id: UUID | None = None,
                    p: Principal = Depends(require("wms:read")), s: AsyncSession = Depends(get_session)):
    stmt = (select(BinStock, Location.full_code).join(Location, Location.id == BinStock.location_id)
            .where(BinStock.tenant_id == p.tenant_id, BinStock.warehouse_id == warehouse_id, BinStock.quantity > 0,
                   Location.full_code.startswith(prefix.upper()))
            .order_by(Location.full_code).limit(1000))
    if sku_id:
        stmt = stmt.where(BinStock.sku_id == sku_id)
    rows = (await s.execute(stmt)).all()
    skus, _, _ = await _names(s, p.tenant_id, [b.sku_id for b, _ in rows])
    return [{"location": code, "sku_id": str(b.sku_id), **skus[b.sku_id], "quantity": b.quantity,
             "allocated": b.allocated} for b, code in rows]


@router.get("/unplaced")
async def unplaced(warehouse_id: UUID, p: Principal = Depends(require("wms:read")),
                   s: AsyncSession = Depends(get_session)):
    """Stok yang tercatat di gudang tetapi belum punya bin (baru diterima / data Fase 2)."""
    in_bins = (select(BinStock.sku_id, func.sum(BinStock.quantity).label("q"))
               .where(BinStock.tenant_id == p.tenant_id, BinStock.warehouse_id == warehouse_id)
               .group_by(BinStock.sku_id).subquery())
    rows = (await s.execute(select(InventoryBalance.sku_id, InventoryBalance.on_hand, func.coalesce(in_bins.c.q, 0))
                            .outerjoin(in_bins, in_bins.c.sku_id == InventoryBalance.sku_id)
                            .where(InventoryBalance.tenant_id == p.tenant_id,
                                   InventoryBalance.warehouse_id == warehouse_id))).all()
    rows = [(sid, oh, int(q)) for sid, oh, q in rows if oh - int(q) > 0]
    skus, _, _ = await _names(s, p.tenant_id, [r[0] for r in rows])
    return [{"sku_id": str(sid), **skus[sid], "on_hand": oh, "in_bins": q, "unplaced": oh - q} for sid, oh, q in rows]


# =============================================================== inbound
class InboundLineIn(BaseModel):
    sku_id: UUID
    expected_qty: int = Field(ge=0, le=1_000_000)


class InboundCreate(BaseModel):
    warehouse_id: UUID
    supplier: str = Field(default="", max_length=200)
    reference: str = Field(default="", max_length=64)
    notes: str = Field(default="", max_length=1000)
    lines: list[InboundLineIn] = Field(default_factory=list, max_length=500)


class ReceiveIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=100)
    quantity: int = Field(default=1, gt=0, le=100_000)
    damaged: bool = False
    client_event_id: str | None = EVENT_ID


async def _inbound_out(s: AsyncSession, p: Principal, r: InboundReceipt) -> dict:
    lines = (await s.scalars(select(InboundLine).where(InboundLine.inbound_id == r.id))).all()
    skus, _, _ = await _names(s, p.tenant_id, [x.sku_id for x in lines])
    return {"id": str(r.id), "number": r.number, "warehouse_id": str(r.warehouse_id), "supplier": r.supplier,
            "reference": r.reference, "status": r.status, "notes": r.notes, "created_at": r.created_at,
            "completed_at": r.completed_at,
            "lines": sorted([{"sku_id": str(x.sku_id), **skus[x.sku_id], "expected_qty": x.expected_qty,
                              "received_qty": x.received_qty, "damaged_qty": x.damaged_qty} for x in lines],
                            key=lambda z: z["sku_code"])}


async def _inbound(s: AsyncSession, p: Principal, iid: UUID, lock: bool = False) -> InboundReceipt:
    stmt = select(InboundReceipt).where(InboundReceipt.id == iid, InboundReceipt.tenant_id == p.tenant_id)
    r = await s.scalar(stmt.with_for_update() if lock else stmt)
    if r is None:
        raise AppError(404, "NOT_FOUND", "Dokumen inbound tidak ditemukan")
    return r


@router.get("/inbound")
async def list_inbound(warehouse_id: UUID, status: str | None = Query(None, max_length=12),
                       p: Principal = Depends(require("wms:read")), s: AsyncSession = Depends(get_session)):
    stmt = (select(InboundReceipt).where(InboundReceipt.tenant_id == p.tenant_id,
                                         InboundReceipt.warehouse_id == warehouse_id)
            .order_by(InboundReceipt.created_at.desc()).limit(100))
    if status:
        stmt = stmt.where(InboundReceipt.status == status)
    return [await _inbound_out(s, p, r) for r in (await s.scalars(stmt)).all()]


@router.post("/inbound", status_code=201)
async def create_inbound(body: InboundCreate, p: Principal = Depends(require("wms:manage")),
                         s: AsyncSession = Depends(get_session)):
    await common.get_warehouse(s, p.tenant_id, body.warehouse_id)
    ids = [x.sku_id for x in body.lines]
    if len(ids) != len(set(ids)):
        raise AppError(422, "DUPLICATE_SKU", "SKU yang sama muncul lebih dari sekali")
    if ids:
        found = await s.scalar(select(func.count()).select_from(Sku).where(Sku.tenant_id == p.tenant_id, Sku.id.in_(ids)))
        if found != len(ids):
            raise AppError(422, "UNKNOWN_SKU", "Ada SKU yang tidak ditemukan")
    r = InboundReceipt(tenant_id=p.tenant_id, warehouse_id=body.warehouse_id, supplier=body.supplier,
                       reference=body.reference, notes=body.notes, created_by=p.user_id,
                       number=await common.next_doc_number(s, p.tenant_id, "IN"))
    s.add(r)
    await s.flush()
    for x in body.lines:
        s.add(InboundLine(tenant_id=p.tenant_id, inbound_id=r.id, sku_id=x.sku_id, expected_qty=x.expected_qty))
    await s.flush()
    return await _inbound_out(s, p, r)


@router.get("/inbound/{inbound_id}")
async def get_inbound(inbound_id: UUID, p: Principal = Depends(require("wms:read")),
                      s: AsyncSession = Depends(get_session)):
    return await _inbound_out(s, p, await _inbound(s, p, inbound_id))


@router.post("/inbound/{inbound_id}/receive")
async def receive_scan(inbound_id: UUID, body: ReceiveIn, p: Principal = Depends(require("wms:operate")),
                       s: AsyncSession = Depends(get_session)):
    async def run():  # noqa: ANN202
        r = await _inbound(s, p, inbound_id, lock=True)
        sku = await common.resolve_sku(s, p.tenant_id, body.barcode)
        ln = await inbound.receive(s, Ctx.from_principal(p), r, sku.id, body.quantity, body.damaged)
        return {"sku_code": sku.sku_code, "received_qty": ln.received_qty, "damaged_qty": ln.damaged_qty,
                "expected_qty": ln.expected_qty, "over": ln.received_qty + ln.damaged_qty > ln.expected_qty}
    return await common.scan_event(s, p.tenant_id, f"inbound-receive:{inbound_id}", body.client_event_id,
                                   body.model_dump(exclude={"client_event_id"}, mode="json"), run)


@router.post("/inbound/{inbound_id}/complete")
async def complete_inbound(inbound_id: UUID, p: Principal = Depends(require("wms:manage")),
                           s: AsyncSession = Depends(get_session)):
    r = await _inbound(s, p, inbound_id, lock=True)
    result = await inbound.complete(s, Ctx.from_principal(p), r)
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="inbound.completed",
                       entity_type="inbound", entity_id=r.id, after={"number": r.number, **result},
                       correlation_id=p.correlation_id, ip=p.ip)
    return {**await _inbound_out(s, p, r), "result": result}


@router.post("/inbound/{inbound_id}/cancel")
async def cancel_inbound(inbound_id: UUID, p: Principal = Depends(require("wms:manage")),
                         s: AsyncSession = Depends(get_session)):
    r = await _inbound(s, p, inbound_id, lock=True)
    if r.status != "RECEIVING":
        raise AppError(409, "INBOUND_CLOSED", f"Inbound {r.number} sudah {r.status}")
    r.status = "CANCELLED"
    return await _inbound_out(s, p, r)


# =============================================================== tasks: putaway & picking
class PutawayIn(BaseModel):
    location_code: str = Field(min_length=1, max_length=160)
    quantity: int = Field(gt=0, le=1_000_000)
    client_event_id: str | None = EVENT_ID


class FreePutawayIn(PutawayIn):
    warehouse_id: UUID
    barcode: str = Field(min_length=1, max_length=100)


class PickIn(BaseModel):
    location_code: str | None = Field(default=None, max_length=160)
    barcode: str = Field(min_length=1, max_length=100)
    quantity: int = Field(default=1, gt=0, le=100_000)
    client_event_id: str | None = EVENT_ID


class ShortIn(BaseModel):
    note: str = Field(default="", max_length=500)
    client_event_id: str | None = EVENT_ID


@router.get("/tasks")
async def list_tasks(warehouse_id: UUID, task_type: str | None = Query(None, pattern="^(PUTAWAY|PICK)$"),
                     status: str = Query("ACTIVE", max_length=12), wave_id: UUID | None = None,
                     order_id: UUID | None = None, limit: int = Query(200, ge=1, le=500),
                     p: Principal = Depends(require("wms:read")), s: AsyncSession = Depends(get_session)):
    stmt = select(WmsTask).where(WmsTask.tenant_id == p.tenant_id, WmsTask.warehouse_id == warehouse_id)
    if task_type:
        stmt = stmt.where(WmsTask.task_type == task_type)
    if status == "ACTIVE":
        stmt = stmt.where(WmsTask.status.in_(("OPEN", "IN_PROGRESS")))
    elif status != "ALL":
        stmt = stmt.where(WmsTask.status == status)
    if wave_id:
        stmt = stmt.where(WmsTask.wave_id == wave_id)
    if order_id:
        stmt = stmt.where(WmsTask.order_id == order_id)
    tasks = (await s.scalars(stmt.order_by(WmsTask.created_at).limit(limit))).all()
    out = await _tasks_out(s, p.tenant_id, list(tasks))
    # Urutkan sesuai jalur jalan: lokasi asal (pick) / tujuan (putaway); area penerimaan di akhir
    out.sort(key=lambda t: ((t["from_location"] or t["to_location"] or "~"), t["order_number"] or ""))
    return out


@router.post("/tasks/{task_id}/putaway")
async def putaway_task(task_id: UUID, body: PutawayIn, p: Principal = Depends(require("wms:operate")),
                       s: AsyncSession = Depends(get_session)):
    async def run():  # noqa: ANN202
        t = await _task(s, p, task_id)
        if t.task_type != "PUTAWAY" or t.status not in ("OPEN", "IN_PROGRESS"):
            raise AppError(409, "TASK_CLOSED", "Task putaway ini sudah selesai")
        if body.quantity > t.remaining:
            raise AppError(422, "INVALID_QTY", f"Maksimal {t.remaining}")
        loc = await common.resolve_bin(s, p.tenant_id, t.warehouse_id, body.location_code)
        await inbound.putaway(s, Ctx.from_principal(p), warehouse_id=t.warehouse_id, sku_id=t.sku_id, location=loc,
                              qty=body.quantity, ref_type="putaway_task", ref_id=t.id)
        t.done_qty += body.quantity
        t.assigned_to = p.user_id
        t.status = "DONE" if t.remaining == 0 else "IN_PROGRESS"
        if t.status == "DONE":
            t.completed_at = datetime.now(UTC)
        await s.flush()
        return (await _tasks_out(s, p.tenant_id, [t]))[0] | {"placed_in": loc.full_code}
    return await common.scan_event(s, p.tenant_id, f"putaway:{task_id}", body.client_event_id,
                                   body.model_dump(exclude={"client_event_id"}, mode="json"), run)


@router.post("/putaway")
async def free_putaway(body: FreePutawayIn, p: Principal = Depends(require("wms:operate")),
                       s: AsyncSession = Depends(get_session)):
    """Tempatkan stok yang belum punya bin tanpa task (mis. stok hasil penerimaan Fase 2)."""
    async def run():  # noqa: ANN202
        await common.get_warehouse(s, p.tenant_id, body.warehouse_id)
        sku = await common.resolve_sku(s, p.tenant_id, body.barcode)
        loc = await common.resolve_bin(s, p.tenant_id, body.warehouse_id, body.location_code)
        await inbound.putaway(s, Ctx.from_principal(p), warehouse_id=body.warehouse_id, sku_id=sku.id, location=loc,
                              qty=body.quantity, ref_type="putaway", ref_id=None)
        return {"sku_code": sku.sku_code, "placed_in": loc.full_code, "quantity": body.quantity}
    return await common.scan_event(s, p.tenant_id, "putaway-free", body.client_event_id,
                                   body.model_dump(exclude={"client_event_id"}, mode="json"), run)


@router.post("/tasks/{task_id}/pick")
async def pick_task(task_id: UUID, body: PickIn, p: Principal = Depends(require("wms:operate")),
                    s: AsyncSession = Depends(get_session)):
    async def run():  # noqa: ANN202
        t = await _task(s, p, task_id)
        await picking.confirm_pick(s, Ctx.from_principal(p), t, location_code=body.location_code,
                                   barcode=body.barcode, qty=body.quantity)
        return (await _tasks_out(s, p.tenant_id, [t]))[0]
    return await common.scan_event(s, p.tenant_id, f"pick:{task_id}", body.client_event_id,
                                   body.model_dump(exclude={"client_event_id"}, mode="json"), run)


@router.post("/tasks/{task_id}/short")
async def short_task(task_id: UUID, body: ShortIn, p: Principal = Depends(require("wms:operate")),
                     s: AsyncSession = Depends(get_session)):
    async def run():  # noqa: ANN202
        t = await _task(s, p, task_id)
        exc = await picking.short_pick(s, Ctx.from_principal(p), t, body.note)
        return {"task": (await _tasks_out(s, p.tenant_id, [t]))[0], "exception_id": str(exc.id)}
    return await common.scan_event(s, p.tenant_id, f"short:{task_id}", body.client_event_id,
                                   body.model_dump(exclude={"client_event_id"}, mode="json"), run)


# =============================================================== waves
class WaveCreate(BaseModel):
    warehouse_id: UUID
    order_ids: list[UUID] | None = Field(default=None, max_length=200)
    max_orders: int = Field(default=20, ge=1, le=200)


async def _wave_out(s: AsyncSession, p: Principal, w: Wave) -> dict:
    rows = (await s.execute(select(WmsTask.status, func.count(), func.sum(WmsTask.quantity), func.sum(WmsTask.done_qty),
                                   func.count(func.distinct(WmsTask.order_id)))
                            .where(WmsTask.wave_id == w.id).group_by(WmsTask.status))).all()
    total = sum(r[1] for r in rows)
    done = sum(r[1] for r in rows if r[0] in ("DONE", "SHORT", "CANCELLED"))
    orders = await s.scalar(select(func.count(func.distinct(WmsTask.order_id))).where(WmsTask.wave_id == w.id))
    units = sum(int(r[2] or 0) for r in rows)
    picked = sum(int(r[3] or 0) for r in rows)
    return {"id": str(w.id), "number": w.number, "status": w.status, "created_at": w.created_at,
            "orders": orders, "tasks": total, "tasks_done": done, "units": units, "units_picked": picked,
            "short": sum(r[1] for r in rows if r[0] == "SHORT")}


@router.get("/waves")
async def list_waves(warehouse_id: UUID, p: Principal = Depends(require("wms:read")),
                     s: AsyncSession = Depends(get_session)):
    ws = (await s.scalars(select(Wave).where(Wave.tenant_id == p.tenant_id, Wave.warehouse_id == warehouse_id)
                          .order_by(Wave.created_at.desc()).limit(50))).all()
    return [await _wave_out(s, p, w) for w in ws]


@router.post("/waves", status_code=201)
async def create_wave(body: WaveCreate, p: Principal = Depends(require("wms:manage")),
                      s: AsyncSession = Depends(get_session)):
    await common.get_warehouse(s, p.tenant_id, body.warehouse_id)
    stmt = (select(Order).where(Order.tenant_id == p.tenant_id, Order.warehouse_id == body.warehouse_id,
                                Order.status == "ALLOCATED", Order.stock_status == "RESERVED")
            .order_by(Order.placed_at).with_for_update(skip_locked=True))
    if body.order_ids:
        stmt = stmt.where(Order.id.in_(body.order_ids))
    else:
        stmt = stmt.limit(body.max_orders)
    orders = list((await s.scalars(stmt)).all())
    w = await picking.create_wave(s, Ctx.from_principal(p), body.warehouse_id, orders)
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="wave.created", entity_type="wave",
                       entity_id=w.id, after={"number": w.number, "orders": [o.order_number for o in orders]},
                       correlation_id=p.correlation_id, ip=p.ip)
    return await _wave_out(s, p, w)


# =============================================================== packing station
class PackScanIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=100)
    quantity: int = Field(default=1, gt=0, le=10_000)


class PackCompleteIn(BaseModel):
    weight_g: int = Field(gt=0, le=1_000_000)
    length_mm: int | None = Field(default=None, gt=0, le=10_000)
    width_mm: int | None = Field(default=None, gt=0, le=10_000)
    height_mm: int | None = Field(default=None, gt=0, le=10_000)
    override_reason: str | None = Field(default=None, max_length=300)


@router.get("/pack-queue")
async def pack_queue(warehouse_id: UUID, p: Principal = Depends(require("wms:read")),
                     s: AsyncSession = Depends(get_session)):
    orders = (await s.scalars(select(Order).where(Order.tenant_id == p.tenant_id, Order.warehouse_id == warehouse_id,
                                                  Order.status.in_(("PICKING", "PACKING")))
                              .order_by(Order.placed_at).limit(200))).all()
    out = []
    for o in orders:
        picked = await picking.picked_by_item(s, o.id)
        ready = all(picked.get(i.id, 0) >= i.quantity for i in o.items)
        out.append({"order_id": str(o.id), "order_number": o.order_number, "status": o.status,
                    "customer_name": o.customer_name, "items": sum(i.quantity for i in o.items),
                    "picking_complete": ready, "open_pick_tasks": await picking.open_pick_tasks(s, o.id)})
    return out


@router.get("/pack/{order_ref}")
async def pack_view(order_ref: str, p: Principal = Depends(require("wms:read")), s: AsyncSession = Depends(get_session)):
    return await packing.view(s, p.tenant_id, await _order_by_ref(s, p, order_ref))


@router.post("/pack/{order_ref}/scan")
async def pack_scan(order_ref: str, body: PackScanIn, p: Principal = Depends(require("wms:operate")),
                    s: AsyncSession = Depends(get_session)):
    o = await _order_by_ref(s, p, order_ref, lock=True)
    r = await packing.scan(s, Ctx.from_principal(p), o, body.barcode, body.quantity)
    return {**r, "view": await packing.view(s, p.tenant_id, o)}


@router.post("/pack/{order_ref}/complete")
async def pack_complete(order_ref: str, body: PackCompleteIn, p: Principal = Depends(require("wms:operate")),
                        s: AsyncSession = Depends(get_session)):
    o = await _order_by_ref(s, p, order_ref, lock=True)
    await packing.complete(s, Ctx.from_principal(p), o, weight_g=body.weight_g,
                           dims=(body.length_mm, body.width_mm, body.height_mm),
                           override_reason=(body.override_reason or "").strip() or None)
    return await packing.view(s, p.tenant_id, o)


# =============================================================== cycle count
class CountCreate(BaseModel):
    warehouse_id: UUID
    prefix: str = Field(default="", max_length=160, description="Awalan kode lokasi, mis. 'A-03' atau kosong = semua bin")


class CountLineIn(BaseModel):
    location_code: str = Field(min_length=1, max_length=160)
    barcode: str = Field(min_length=1, max_length=100)
    counted_qty: int = Field(ge=0, le=1_000_000)
    client_event_id: str | None = EVENT_ID


async def _count(s: AsyncSession, p: Principal, cid: UUID, lock: bool = False) -> CycleCount:
    stmt = select(CycleCount).where(CycleCount.id == cid, CycleCount.tenant_id == p.tenant_id)
    cc = await s.scalar(stmt.with_for_update() if lock else stmt)
    if cc is None:
        raise AppError(404, "NOT_FOUND", "Cycle count tidak ditemukan")
    return cc


async def _count_out(s: AsyncSession, p: Principal, cc: CycleCount) -> dict:
    lines = (await s.scalars(select(CycleCountLine).where(CycleCountLine.count_id == cc.id))).all()
    skus, locs, _ = await _names(s, p.tenant_id, [x.sku_id for x in lines], [x.location_id for x in lines])
    blind = not p.can("wms:manage") and cc.status == "OPEN"  # operator menghitung tanpa melihat angka sistem
    return {"id": str(cc.id), "number": cc.number, "scope": cc.scope, "status": cc.status, "created_at": cc.created_at,
            "submitted_at": cc.submitted_at, "approved_at": cc.approved_at, "blind": blind,
            "lines": sorted([{"id": str(x.id), "location": locs[x.location_id], "sku_id": str(x.sku_id),
                              **skus[x.sku_id], "system_qty": None if blind else x.system_qty,
                              "counted_qty": x.counted_qty,
                              "variance": None if blind or x.counted_qty is None else x.counted_qty - x.system_qty}
                             for x in lines], key=lambda z: (z["location"], z["sku_code"]))}


@router.get("/counts")
async def list_counts(warehouse_id: UUID, p: Principal = Depends(require("wms:read")),
                      s: AsyncSession = Depends(get_session)):
    ccs = (await s.scalars(select(CycleCount).where(CycleCount.tenant_id == p.tenant_id,
                                                    CycleCount.warehouse_id == warehouse_id)
                           .order_by(CycleCount.created_at.desc()).limit(50))).all()
    out = []
    for cc in ccs:
        n = await s.scalar(select(func.count()).select_from(CycleCountLine).where(CycleCountLine.count_id == cc.id))
        done = await s.scalar(select(func.count()).select_from(CycleCountLine).where(
            CycleCountLine.count_id == cc.id, CycleCountLine.counted_qty.is_not(None)))
        out.append({"id": str(cc.id), "number": cc.number, "scope": cc.scope, "status": cc.status,
                    "created_at": cc.created_at, "lines": n, "counted": done})
    return out


@router.post("/counts", status_code=201)
async def create_count(body: CountCreate, p: Principal = Depends(require("wms:manage")),
                       s: AsyncSession = Depends(get_session)):
    await common.get_warehouse(s, p.tenant_id, body.warehouse_id)
    return await _count_out(s, p, await counting.create(s, Ctx.from_principal(p), body.warehouse_id, body.prefix))


@router.get("/counts/{count_id}")
async def get_count(count_id: UUID, p: Principal = Depends(require("wms:read")), s: AsyncSession = Depends(get_session)):
    return await _count_out(s, p, await _count(s, p, count_id))


@router.post("/counts/{count_id}/lines")
async def count_line(count_id: UUID, body: CountLineIn, p: Principal = Depends(require("wms:operate")),
                     s: AsyncSession = Depends(get_session)):
    async def run():  # noqa: ANN202
        cc = await _count(s, p, count_id, lock=True)
        loc = await common.resolve_bin(s, p.tenant_id, cc.warehouse_id, body.location_code)
        sku = await common.resolve_sku(s, p.tenant_id, body.barcode)
        await counting.record(s, Ctx.from_principal(p), cc, loc, sku.id, body.counted_qty)
        return {"location": loc.full_code, "sku_code": sku.sku_code, "counted_qty": body.counted_qty}
    return await common.scan_event(s, p.tenant_id, f"count:{count_id}", body.client_event_id,
                                   body.model_dump(exclude={"client_event_id"}, mode="json"), run)


@router.post("/counts/{count_id}/submit")
async def submit_count(count_id: UUID, p: Principal = Depends(require("wms:operate")),
                       s: AsyncSession = Depends(get_session)):
    cc = await _count(s, p, count_id, lock=True)
    await counting.submit(s, cc)
    return await _count_out(s, p, cc)


@router.post("/counts/{count_id}/approve")
async def approve_count(count_id: UUID, p: Principal = Depends(require("wms:manage", "inventory:adjust")),
                        s: AsyncSession = Depends(get_session)):
    cc = await _count(s, p, count_id, lock=True)
    result = await counting.approve(s, Ctx.from_principal(p), cc)
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="cycle_count.approved",
                       entity_type="cycle_count", entity_id=cc.id, after={"number": cc.number, **result},
                       correlation_id=p.correlation_id, ip=p.ip)
    return {**await _count_out(s, p, cc), "result": result}


@router.post("/counts/{count_id}/cancel")
async def cancel_count(count_id: UUID, p: Principal = Depends(require("wms:manage")),
                       s: AsyncSession = Depends(get_session)):
    cc = await _count(s, p, count_id, lock=True)
    if cc.status not in ("OPEN", "SUBMITTED"):
        raise AppError(409, "COUNT_CLOSED", f"Hitungan {cc.number} sudah {cc.status}")
    cc.status = "CANCELLED"
    return await _count_out(s, p, cc)


# =============================================================== exceptions
class ExceptionIn(BaseModel):
    warehouse_id: UUID
    exc_type: str = Field(pattern="^(MISSING|DAMAGED|WRONG_SKU|WRONG_LOCATION|OTHER)$")
    location_code: str | None = Field(default=None, max_length=160)
    barcode: str | None = Field(default=None, max_length=100)
    quantity: int | None = Field(default=None, ge=0, le=1_000_000)
    note: str = Field(min_length=3, max_length=1000)
    client_event_id: str | None = EVENT_ID


class ResolveIn(BaseModel):
    action: str = Field(default="NOTE", pattern="^(NOTE|REPICK)$")
    note: str = Field(min_length=3, max_length=1000)


async def _exc_out(s: AsyncSession, p: Principal, rows: list[WmsException]) -> list[dict]:
    skus, locs, orders = await _names(s, p.tenant_id, [e.sku_id for e in rows if e.sku_id],
                                      [e.location_id for e in rows], [e.order_id for e in rows])
    return [{"id": str(e.id), "exc_type": e.exc_type, "status": e.status, "quantity": e.quantity, "note": e.note,
             "sku_code": skus.get(e.sku_id, {}).get("sku_code"), "location": locs.get(e.location_id),
             "order_number": orders.get(e.order_id), "can_repick": e.exc_type == "MISSING" and e.task_id is not None,
             "resolution": e.resolution, "created_at": e.created_at, "resolved_at": e.resolved_at} for e in rows]


@router.get("/exceptions")
async def list_exceptions(warehouse_id: UUID, status: str = Query("OPEN", pattern="^(OPEN|RESOLVED|ALL)$"),
                          p: Principal = Depends(require("wms:read")), s: AsyncSession = Depends(get_session)):
    stmt = select(WmsException).where(WmsException.tenant_id == p.tenant_id, WmsException.warehouse_id == warehouse_id)
    if status != "ALL":
        stmt = stmt.where(WmsException.status == status)
    rows = (await s.scalars(stmt.order_by(WmsException.created_at.desc()).limit(200))).all()
    return await _exc_out(s, p, list(rows))


@router.post("/exceptions", status_code=201)
async def report_exception(body: ExceptionIn, p: Principal = Depends(require("wms:operate")),
                           s: AsyncSession = Depends(get_session)):
    async def run():  # noqa: ANN202
        await common.get_warehouse(s, p.tenant_id, body.warehouse_id)
        loc = await common.resolve_bin(s, p.tenant_id, body.warehouse_id, body.location_code) if body.location_code else None
        sku = await common.resolve_sku(s, p.tenant_id, body.barcode) if body.barcode else None
        e = common.raise_exception(s, Ctx.from_principal(p), warehouse_id=body.warehouse_id, exc_type=body.exc_type,
                                   location_id=loc.id if loc else None, sku_id=sku.id if sku else None,
                                   quantity=body.quantity, note=body.note)
        await s.flush()
        return (await _exc_out(s, p, [e]))[0]
    return await common.scan_event(s, p.tenant_id, "exception-report", body.client_event_id,
                                   body.model_dump(exclude={"client_event_id"}, mode="json"), run)


@router.post("/exceptions/{exc_id}/resolve")
async def resolve_exception(exc_id: UUID, body: ResolveIn, p: Principal = Depends(require("wms:manage")),
                            s: AsyncSession = Depends(get_session)):
    e = await s.scalar(select(WmsException).where(WmsException.id == exc_id, WmsException.tenant_id == p.tenant_id)
                       .with_for_update())
    if e is None:
        raise AppError(404, "NOT_FOUND", "Exception tidak ditemukan")
    if e.status != "OPEN":
        raise AppError(409, "ALREADY_RESOLVED", "Exception ini sudah diselesaikan")
    extra = ""
    if body.action == "REPICK":
        tasks = await picking.repick(s, Ctx.from_principal(p), e)
        extra = f" — {len(tasks)} task picking pengganti dibuat"
    e.status = "RESOLVED"
    e.resolution = body.note + extra
    e.resolved_by = p.user_id
    e.resolved_at = datetime.now(UTC)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="wms_exception.resolved",
                       entity_type="wms_exception", entity_id=e.id, after={"action": body.action, "note": body.note},
                       correlation_id=p.correlation_id, ip=p.ip)
    return (await _exc_out(s, p, [e]))[0]


@router.get("/labels/bins")
async def bin_labels(warehouse_id: UUID, prefix: str = Query("", max_length=160),
                     p: Principal = Depends(require("wms:read")), s: AsyncSession = Depends(get_session)):
    locs = (await s.scalars(select(Location).where(Location.tenant_id == p.tenant_id,
                                                   Location.warehouse_id == warehouse_id, Location.type == "BIN",
                                                   Location.full_code.startswith(prefix.upper()))
                            .order_by(Location.full_code).limit(1000))).all()
    return [{"full_code": x.full_code, "is_active": x.is_active} for x in locs]
