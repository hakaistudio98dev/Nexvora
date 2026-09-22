from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Ctx
from app.core.errors import AppError
from app.models import BinStock, InboundLine, InboundReceipt, Location, WmsTask
from app.modules.inventory import service as inv
from app.modules.wms import common


async def receive(s: AsyncSession, ctx: Ctx, rec: InboundReceipt, sku_id, qty: int, damaged: bool) -> InboundLine:  # noqa: ANN001
    if rec.status != "RECEIVING":
        raise AppError(409, "INBOUND_CLOSED", f"Inbound {rec.number} sudah {rec.status}")
    line = await s.scalar(select(InboundLine).where(InboundLine.inbound_id == rec.id, InboundLine.sku_id == sku_id)
                          .with_for_update())
    if line is None:
        line = InboundLine(tenant_id=ctx.tenant_id, inbound_id=rec.id, sku_id=sku_id, expected_qty=0)
        s.add(line)
        await s.flush()
    if damaged:
        line.damaged_qty += qty
    else:
        line.received_qty += qty
    await s.flush()
    return line


async def suggest_bin(s: AsyncSession, tenant_id, warehouse_id, sku_id):  # noqa: ANN001, ANN201
    """Saran putaway: bin yang sudah berisi SKU ini (terbanyak) agar stok tidak tersebar."""
    return await s.scalar(select(BinStock.location_id).join(Location, Location.id == BinStock.location_id)
                          .where(BinStock.tenant_id == tenant_id, BinStock.warehouse_id == warehouse_id,
                                 BinStock.sku_id == sku_id, BinStock.quantity > 0, Location.is_active.is_(True))
                          .order_by(BinStock.quantity.desc()).limit(1))


async def complete(s: AsyncSession, ctx: Ctx, rec: InboundReceipt) -> dict:
    if rec.status != "RECEIVING":
        raise AppError(409, "INBOUND_CLOSED", f"Inbound {rec.number} sudah {rec.status}")
    lines = (await s.scalars(select(InboundLine).where(InboundLine.inbound_id == rec.id))).all()
    posted = [ln for ln in lines if ln.received_qty or ln.damaged_qty]
    if not posted:
        raise AppError(422, "NOTHING_RECEIVED", "Belum ada barang yang discan masuk")
    bals = await inv.lock_balances(s, ctx.tenant_id, [(rec.warehouse_id, ln.sku_id) for ln in posted])
    tasks = 0
    for ln in lines:
        if ln.received_qty or ln.damaged_qty:
            await inv.apply(s, ctx, bals[(rec.warehouse_id, ln.sku_id)], "RECEIPT", d_on_hand=ln.received_qty,
                            d_damaged=ln.damaged_qty, reference_type="inbound", reference_id=rec.number,
                            note=rec.supplier or None)
        if ln.received_qty:
            s.add(WmsTask(tenant_id=ctx.tenant_id, warehouse_id=rec.warehouse_id, task_type="PUTAWAY",
                          sku_id=ln.sku_id, quantity=ln.received_qty, inbound_id=rec.id,
                          to_location_id=await suggest_bin(s, ctx.tenant_id, rec.warehouse_id, ln.sku_id)))
            tasks += 1
        total = ln.received_qty + ln.damaged_qty
        if ln.expected_qty and total < ln.expected_qty:
            common.raise_exception(s, ctx, warehouse_id=rec.warehouse_id, exc_type="SHORT_RECEIPT", sku_id=ln.sku_id,
                                   inbound_id=rec.id, quantity=ln.expected_qty - total,
                                   note=f"{rec.number}: diterima {total} dari {ln.expected_qty}")
        elif total > ln.expected_qty:
            common.raise_exception(s, ctx, warehouse_id=rec.warehouse_id, exc_type="OVER_RECEIPT", sku_id=ln.sku_id,
                                   inbound_id=rec.id, quantity=total - ln.expected_qty,
                                   note=f"{rec.number}: diterima {total}, dokumen {ln.expected_qty}")
        if ln.damaged_qty:
            common.raise_exception(s, ctx, warehouse_id=rec.warehouse_id, exc_type="DAMAGED", sku_id=ln.sku_id,
                                   inbound_id=rec.id, quantity=ln.damaged_qty,
                                   note=f"{rec.number}: barang rusak saat diterima")
    rec.status = "COMPLETED"
    rec.completed_at = datetime.now(UTC)
    await s.flush()
    return {"putaway_tasks": tasks,
            "units_received": sum(ln.received_qty for ln in lines),
            "units_damaged": sum(ln.damaged_qty for ln in lines)}


async def putaway(s: AsyncSession, ctx: Ctx, *, warehouse_id, sku_id, location: Location, qty: int,  # noqa: ANN001
                  ref_type: str, ref_id) -> None:  # noqa: ANN001
    """Pindahkan stok yang belum punya bin (area penerimaan) ke bin. Dikunci lewat saldo gudang
    agar total isi bin tidak pernah melebihi stok fisik."""
    bal = (await inv.lock_balances(s, ctx.tenant_id, [(warehouse_id, sku_id)]))[(warehouse_id, sku_id)]
    unplaced = bal.on_hand - await common.sum_bins(s, ctx.tenant_id, warehouse_id, sku_id)
    if qty > unplaced:
        raise AppError(409, "NOTHING_TO_PUTAWAY",
                       f"Hanya {max(unplaced, 0)} unit yang belum ditempatkan di bin")
    b = await common.lock_bin(s, ctx.tenant_id, warehouse_id, location.id, sku_id)
    b.quantity += qty
    common.movement(s, ctx, warehouse_id=warehouse_id, sku_id=sku_id, qty=qty, kind="PUTAWAY", to_loc=location.id,
                    ref_type=ref_type, ref_id=ref_id)
    await s.flush()
