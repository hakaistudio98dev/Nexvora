from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Ctx
from app.core.errors import AppError
from app.models import BinStock, Location, Order, Wave, WmsException, WmsTask
from app.modules.wms import common


async def allocate_pick(s: AsyncSession, ctx: Ctx, *, order: Order, order_item_id: UUID, sku_id: UUID, qty: int,
                        wave_id: UUID | None, exclude: set[UUID] | None = None) -> list[WmsTask]:
    """Ambil stok dari bin (urut kode lokasi = jalur jalan picker). Sisa yang tidak ada di bin
    diambil dari area penerimaan (stok yang belum di-putaway)."""
    remaining, tasks = qty, []
    bins = (await s.execute(
        select(BinStock).join(Location, Location.id == BinStock.location_id)
        .where(BinStock.tenant_id == ctx.tenant_id, BinStock.warehouse_id == order.warehouse_id,
               BinStock.sku_id == sku_id, BinStock.quantity > BinStock.allocated, Location.is_active.is_(True),
               *( [BinStock.location_id.notin_(exclude)] if exclude else []))
        .order_by(Location.full_code).with_for_update(of=BinStock)
    )).scalars().all()
    for b in bins:
        if remaining == 0:
            break
        take = min(b.quantity - b.allocated, remaining)
        if take <= 0:
            continue
        b.allocated += take
        remaining -= take
        tasks.append(WmsTask(tenant_id=ctx.tenant_id, warehouse_id=order.warehouse_id, task_type="PICK", sku_id=sku_id,
                             quantity=take, from_location_id=b.location_id, order_id=order.id,
                             order_item_id=order_item_id, wave_id=wave_id))
    if remaining:
        tasks.append(WmsTask(tenant_id=ctx.tenant_id, warehouse_id=order.warehouse_id, task_type="PICK", sku_id=sku_id,
                             quantity=remaining, from_location_id=None, order_id=order.id,
                             order_item_id=order_item_id, wave_id=wave_id))
    s.add_all(tasks)
    await s.flush()  # autoflush mati: alokasi harus tertulis sebelum order berikutnya membaca bin
    return tasks


async def begin_picking(s: AsyncSession, ctx: Ctx, order: Order, wave: Wave) -> int:
    from app.modules.orders.service import record_status  # hindari import melingkar

    if order.status != "ALLOCATED" or order.stock_status != "RESERVED" or order.warehouse_id is None:
        raise AppError(409, "NOT_ALLOCATED", f"Order {order.order_number} belum siap dipicking")
    n = 0
    for it in order.items:
        n += len(await allocate_pick(s, ctx, order=order, order_item_id=it.id, sku_id=it.sku_id,
                                     qty=it.quantity, wave_id=wave.id))
    await record_status(s, ctx, order, "PICKING", f"Masuk wave {wave.number}")
    return n


async def create_wave(s: AsyncSession, ctx: Ctx, warehouse_id: UUID, orders: list[Order]) -> Wave:
    if not orders:
        raise AppError(422, "NO_ORDERS", "Tidak ada order ALLOCATED untuk dibuatkan wave di gudang ini")
    wave = Wave(tenant_id=ctx.tenant_id, warehouse_id=warehouse_id,
                number=await common.next_doc_number(s, ctx.tenant_id, "WV"), created_by=ctx.actor_user_id)
    s.add(wave)
    await s.flush()
    for o in orders:
        await begin_picking(s, ctx, o, wave)
    await s.flush()
    return wave


async def _close_wave_if_done(s: AsyncSession, wave_id: UUID | None) -> None:
    if not wave_id:
        return
    open_ = await s.scalar(select(func.count()).select_from(WmsTask).where(
        WmsTask.wave_id == wave_id, WmsTask.status.in_(("OPEN", "IN_PROGRESS"))))
    if not open_:
        w = await s.get(Wave, wave_id)
        if w and w.status == "OPEN":
            w.status = "DONE"


async def confirm_pick(s: AsyncSession, ctx: Ctx, task: WmsTask, *, location_code: str | None, barcode: str,
                       qty: int) -> WmsTask:
    if task.task_type != "PICK" or task.status not in ("OPEN", "IN_PROGRESS"):
        raise AppError(409, "TASK_CLOSED", "Task picking ini sudah selesai atau dibatalkan")
    if task.from_location_id:
        loc = await common.resolve_bin(s, ctx.tenant_id, task.warehouse_id, location_code or "")
        if loc.id != task.from_location_id:
            expected = await s.get(Location, task.from_location_id)
            raise AppError(409, "WRONG_LOCATION", f"Salah lokasi: ambil dari {expected.full_code}, bukan {loc.full_code}")
    sku = await common.resolve_sku(s, ctx.tenant_id, barcode)
    if sku.id != task.sku_id:
        raise AppError(409, "WRONG_SKU", f"Salah barang: yang discan {sku.sku_code}")
    if not 1 <= qty <= task.remaining:
        raise AppError(422, "INVALID_QTY", f"Jumlah harus 1–{task.remaining}")
    if task.from_location_id:
        b = await common.lock_bin(s, ctx.tenant_id, task.warehouse_id, task.from_location_id, task.sku_id)
        if b.allocated < qty or b.quantity < qty:
            raise AppError(409, "BIN_SHORT", "Stok di bin tidak cukup; laporkan barang kurang")
        b.quantity -= qty
        b.allocated -= qty
        common.movement(s, ctx, warehouse_id=task.warehouse_id, sku_id=task.sku_id, qty=qty, kind="PICK",
                        from_loc=task.from_location_id, ref_type="order", ref_id=task.order_id)
    task.done_qty += qty
    task.assigned_to = ctx.actor_user_id
    task.status = "DONE" if task.remaining == 0 else "IN_PROGRESS"
    if task.status == "DONE":
        task.completed_at = datetime.now(UTC)
    await s.flush()
    await _close_wave_if_done(s, task.wave_id)
    return task


async def short_pick(s: AsyncSession, ctx: Ctx, task: WmsTask, note: str) -> WmsException:
    """Barang di bin tidak ada/kurang. Sisa alokasi dilepas dan dicatat ke exception queue."""
    if task.task_type != "PICK" or task.status not in ("OPEN", "IN_PROGRESS"):
        raise AppError(409, "TASK_CLOSED", "Task picking ini sudah selesai atau dibatalkan")
    missing = task.remaining
    if task.from_location_id:
        b = await common.lock_bin(s, ctx.tenant_id, task.warehouse_id, task.from_location_id, task.sku_id)
        b.allocated = max(0, b.allocated - missing)
    task.status = "SHORT"
    task.assigned_to = ctx.actor_user_id
    task.completed_at = datetime.now(UTC)
    exc = common.raise_exception(s, ctx, warehouse_id=task.warehouse_id, exc_type="MISSING", sku_id=task.sku_id,
                                 location_id=task.from_location_id, order_id=task.order_id, task_id=task.id,
                                 quantity=missing, note=note or "Barang tidak ditemukan saat picking")
    await s.flush()
    await _close_wave_if_done(s, task.wave_id)
    return exc


async def repick(s: AsyncSession, ctx: Ctx, exc: WmsException) -> list[WmsTask]:
    """Buat task pengganti dari bin lain untuk jumlah yang kurang."""
    task = await s.get(WmsTask, exc.task_id) if exc.task_id else None
    if task is None or task.task_type != "PICK" or exc.exc_type != "MISSING":
        raise AppError(422, "NOT_REPICKABLE", "Hanya exception barang kurang saat picking yang bisa di-pick ulang")
    order = await s.get(Order, task.order_id)
    if order is None or order.status not in ("PICKING", "PACKING"):
        raise AppError(409, "ORDER_NOT_PICKING", "Order sudah tidak dalam proses picking")
    exclude = {task.from_location_id} if task.from_location_id else None
    new = await allocate_pick(s, ctx, order=order, order_item_id=task.order_item_id, sku_id=task.sku_id,
                              qty=exc.quantity or task.quantity - task.done_qty, wave_id=task.wave_id,
                              exclude=exclude)
    if task.wave_id:
        w = await s.get(Wave, task.wave_id)
        if w and w.status == "DONE":
            w.status = "OPEN"
    await s.flush()
    return new


async def picked_by_item(s: AsyncSession, order_id: UUID) -> dict[UUID, int]:
    rows = (await s.execute(select(WmsTask.order_item_id, func.sum(WmsTask.done_qty))
                            .where(WmsTask.order_id == order_id, WmsTask.task_type == "PICK",
                                   WmsTask.status != "CANCELLED")
                            .group_by(WmsTask.order_item_id))).all()
    return {k: int(v or 0) for k, v in rows}


async def open_pick_tasks(s: AsyncSession, order_id: UUID) -> int:
    return int(await s.scalar(select(func.count()).select_from(WmsTask).where(
        WmsTask.order_id == order_id, WmsTask.task_type == "PICK", WmsTask.status.in_(("OPEN", "IN_PROGRESS")))) or 0)
