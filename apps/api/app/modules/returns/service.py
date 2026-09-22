from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.context import Ctx
from app.core.errors import AppError
from app.models import Order, Return, ReturnLine, Sku, WmsTask
from app.modules.inventory import service as inv
from app.modules.wms.common import next_doc_number

OPEN = ("REQUESTED", "APPROVED", "RECEIVED", "INSPECTED")


def now() -> datetime:
    return datetime.now(UTC)


async def _returned_before(s: AsyncSession, order_id) -> dict:  # noqa: ANN001
    rows = (await s.execute(select(ReturnLine.order_item_id, func.sum(ReturnLine.quantity))
                            .join(Return, Return.id == ReturnLine.return_id)
                            .where(Return.order_id == order_id, Return.status != "REJECTED")
                            .group_by(ReturnLine.order_item_id))).all()
    return {k: int(v) for k, v in rows}


async def create(s: AsyncSession, ctx: Ctx, order: Order, lines: list[tuple], reason: str, note: str,
                 return_tracking: str | None, *, auto_approve: bool = False) -> Return:
    from app.modules.orders.service import record_status  # noqa: PLC0415
    if order.status not in ("SHIPPED", "DELIVERED"):
        raise AppError(409, "NOT_RETURNABLE", "Retur hanya untuk order yang sudah dikirim atau diterima")
    if await s.scalar(select(Return.id).where(Return.order_id == order.id, Return.status.in_(OPEN)).limit(1)):
        raise AppError(409, "RETURN_OPEN", "Order ini masih punya retur yang sedang diproses")
    items = {i.id: i for i in order.items}
    before = await _returned_before(s, order.id)
    if not lines:
        raise AppError(422, "NO_LINES", "Pilih minimal satu item yang diretur")
    for item_id, qty in lines:
        it = items.get(item_id)
        if it is None:
            raise AppError(422, "UNKNOWN_ITEM", "Item tidak ada di order ini")
        if qty < 1 or qty > it.quantity - before.get(item_id, 0):
            raise AppError(422, "INVALID_QTY", f"Jumlah retur melebihi yang dikirim ({it.quantity - before.get(item_id, 0)})")
    r = Return(tenant_id=ctx.tenant_id, number=await next_doc_number(s, ctx.tenant_id, "RMA"), order_id=order.id,
               warehouse_id=order.warehouse_id, reason_code=reason, note=note, return_tracking=return_tracking,
               order_status_before=order.status, status="APPROVED" if auto_approve else "REQUESTED",
               created_by=ctx.actor_user_id, decided_by=ctx.actor_user_id if auto_approve else None)
    s.add(r)
    await s.flush()
    for item_id, qty in lines:
        s.add(ReturnLine(tenant_id=ctx.tenant_id, return_id=r.id, order_item_id=item_id,
                         sku_id=items[item_id].sku_id, quantity=qty))
    await record_status(s, ctx, order, "RETURN_REQUESTED", f"{r.number}: {reason}")
    await audit.record(s, tenant_id=ctx.tenant_id, actor_user_id=ctx.actor_user_id, action="return.created",
                       entity_type="return", entity_id=r.id,
                       after={"number": r.number, "order": order.order_number, "reason": reason},
                       correlation_id=ctx.correlation_id, ip=ctx.ip)
    await s.flush()
    return r


async def create_rts(s: AsyncSession, ctx: Ctx, order: Order, desc: str) -> Return | None:
    """Paket dikembalikan kurir (gagal kirim) → RMA otomatis, langsung disetujui, menunggu diterima gudang."""
    if order.status not in ("SHIPPED", "DELIVERED"):
        return None
    if await s.scalar(select(Return.id).where(Return.order_id == order.id, Return.status.in_(OPEN)).limit(1)):
        return None
    before = await _returned_before(s, order.id)
    lines = [(i.id, i.quantity - before.get(i.id, 0)) for i in order.items if i.quantity - before.get(i.id, 0) > 0]
    return await create(s, ctx, order, lines, "UNDELIVERED", f"Dikembalikan kurir: {desc}", None, auto_approve=True)


async def decide(s: AsyncSession, ctx: Ctx, r: Return, approve: bool, note: str | None) -> None:
    from app.modules.orders.service import record_status  # noqa: PLC0415
    if r.status != "REQUESTED":
        raise AppError(409, "INVALID_STATE", f"Retur berstatus {r.status}")
    r.status, r.decided_by = ("APPROVED" if approve else "REJECTED"), ctx.actor_user_id
    if not approve:
        r.note = (r.note + f"\nDitolak: {note}").strip() if note else r.note
        r.closed_at = now()
        order = await s.scalar(select(Order).where(Order.id == r.order_id).with_for_update())
        await record_status(s, ctx, order, r.order_status_before, f"Retur {r.number} ditolak: {note or '-'}")


async def receive(s: AsyncSession, ctx: Ctx, r: Return, received: dict) -> None:
    """Barang retur tiba di gudang → masuk bucket 'returned' (belum bisa dijual sebelum diinspeksi)."""
    from app.modules.orders.service import record_status  # noqa: PLC0415
    if r.status != "APPROVED":
        raise AppError(409, "INVALID_STATE", "Retur harus disetujui sebelum barang diterima")
    lines = (await s.scalars(select(ReturnLine).where(ReturnLine.return_id == r.id))).all()
    if not any(received.get(ln.id, 0) for ln in lines):
        raise AppError(422, "NOTHING_RECEIVED", "Isi jumlah barang yang diterima")
    bals = await inv.lock_balances(s, ctx.tenant_id, [(r.warehouse_id, ln.sku_id) for ln in lines])
    for ln in lines:
        q = received.get(ln.id, 0)
        if q < 0 or q > ln.quantity:
            raise AppError(422, "INVALID_QTY", "Jumlah diterima melebihi jumlah retur")
        ln.received_qty = q
        if q:
            await inv.apply(s, ctx, bals[(r.warehouse_id, ln.sku_id)], "RETURN", d_returned=q,
                            reason_code="RETURN_RECEIVED", reference_type="return", reference_id=r.number)
    r.status, r.received_at = "RECEIVED", now()
    order = await s.scalar(select(Order).where(Order.id == r.order_id).with_for_update())
    await record_status(s, ctx, order, "RETURNED", f"Barang {r.number} diterima gudang")
    await s.flush()


async def inspect(s: AsyncSession, ctx: Ctx, r: Return, decisions: dict) -> int:
    """Per baris: berapa yang layak jual (restock → stok tersedia + task putaway) dan berapa rusak."""
    if r.status != "RECEIVED":
        raise AppError(409, "INVALID_STATE", "Barang retur harus diterima dulu")
    lines = (await s.scalars(select(ReturnLine).where(ReturnLine.return_id == r.id))).all()
    bals = await inv.lock_balances(s, ctx.tenant_id, [(r.warehouse_id, ln.sku_id) for ln in lines])
    tasks = 0
    for ln in lines:
        restock, damaged = decisions.get(ln.id, (0, 0))
        if restock < 0 or damaged < 0 or restock + damaged != ln.received_qty:
            sku = await s.get(Sku, ln.sku_id)
            raise AppError(422, "INVALID_INSPECTION",
                           f"{sku.sku_code}: layak jual + rusak harus sama dengan diterima ({ln.received_qty})")
        ln.restock_qty, ln.damaged_qty = restock, damaged
        b = bals[(r.warehouse_id, ln.sku_id)]
        if restock:
            await inv.apply(s, ctx, b, "RETURN", d_returned=-restock, d_on_hand=restock,
                            reason_code="RETURN_RESTOCK", reference_type="return", reference_id=r.number)
            s.add(WmsTask(tenant_id=ctx.tenant_id, warehouse_id=r.warehouse_id, task_type="PUTAWAY",
                          sku_id=ln.sku_id, quantity=restock))
            tasks += 1
        if damaged:
            await inv.apply(s, ctx, b, "RETURN", d_returned=-damaged, d_damaged=damaged,
                            reason_code="RETURN_DAMAGED", reference_type="return", reference_id=r.number)
    r.status = "INSPECTED"
    await s.flush()
    return tasks


async def resolve(s: AsyncSession, ctx: Ctx, r: Return, resolution: str, amount: Decimal | None,
                  ref: str | None) -> Order | None:
    from app.modules.orders.service import create_order, record_status  # noqa: PLC0415
    if r.status != "INSPECTED":
        raise AppError(409, "INVALID_STATE", "Inspeksi barang retur dulu sebelum diselesaikan")
    order = await s.scalar(select(Order).where(Order.id == r.order_id).with_for_update())
    replacement = None
    if resolution == "REFUND":
        refunded = await s.scalar(select(func.coalesce(func.sum(Return.refund_amount), 0)).where(
            Return.order_id == order.id, Return.resolution == "REFUND")) or Decimal("0")
        if amount is None or amount <= 0 or amount > order.total - refunded:
            raise AppError(422, "INVALID_REFUND", f"Nominal refund harus 1 – {order.total - refunded}")
        r.refund_amount, r.refund_ref = amount, ref
        order.payment_status = "REFUNDED"
        await record_status(s, ctx, order, "REFUNDED", f"Refund {amount} via {r.number}")
    elif resolution == "REPLACEMENT":
        lines = (await s.scalars(select(ReturnLine).where(ReturnLine.return_id == r.id))).all()
        items = {i.id: i for i in order.items}
        skus = {k.id: k for k in (await s.scalars(select(Sku).where(Sku.id.in_([ln.sku_id for ln in lines])))).all()}
        replacement = await create_order(
            s, ctx, channel=order.channel, external_ref=None, customer_name=order.customer_name,
            customer_phone=order.customer_phone, customer_email=order.customer_email, ship_address=order.ship_address,
            ship_city=order.ship_city, ship_province=order.ship_province, ship_postal_code=order.ship_postal_code,
            ship_country=order.ship_country, notes=f"Pengganti untuk {order.order_number} ({r.number})", paid=True,
            items=[(skus[ln.sku_id], ln.quantity, Decimal("0"))
                   for ln in lines])
        r.replacement_order_id = replacement.id
    r.resolution, r.status, r.closed_at = resolution, "CLOSED", now()
    await audit.record(s, tenant_id=ctx.tenant_id, actor_user_id=ctx.actor_user_id, action="return.closed",
                       entity_type="return", entity_id=r.id,
                       after={"number": r.number, "resolution": resolution, "refund": str(amount) if amount else None,
                              "replacement": replacement.order_number if replacement else None},
                       correlation_id=ctx.correlation_id, ip=ctx.ip)
    await s.flush()
    return replacement
