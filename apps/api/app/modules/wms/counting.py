from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Ctx
from app.core.errors import AppError
from app.models import BinStock, CycleCount, CycleCountLine, Location
from app.modules.inventory import service as inv
from app.modules.wms import common


async def create(s: AsyncSession, ctx: Ctx, warehouse_id, prefix: str) -> CycleCount:  # noqa: ANN001
    prefix = prefix.strip().upper()
    bins = (await s.scalars(select(Location).where(
        Location.tenant_id == ctx.tenant_id, Location.warehouse_id == warehouse_id, Location.type == "BIN",
        Location.is_active.is_(True), Location.full_code.startswith(prefix)))).all()
    if not bins:
        raise AppError(422, "NO_BINS", f"Tidak ada bin aktif dengan awalan '{prefix}'")
    cc = CycleCount(tenant_id=ctx.tenant_id, warehouse_id=warehouse_id, scope=prefix,
                    number=await common.next_doc_number(s, ctx.tenant_id, "CC"), created_by=ctx.actor_user_id)
    s.add(cc)
    await s.flush()
    stock = (await s.scalars(select(BinStock).where(BinStock.location_id.in_([b.id for b in bins]),
                                                    BinStock.quantity > 0))).all()
    for b in stock:
        s.add(CycleCountLine(tenant_id=ctx.tenant_id, count_id=cc.id, location_id=b.location_id, sku_id=b.sku_id,
                             system_qty=b.quantity))
    await s.flush()
    return cc


async def record(s: AsyncSession, ctx: Ctx, cc: CycleCount, location: Location, sku_id, counted: int) -> CycleCountLine:  # noqa: ANN001,E501
    if cc.status != "OPEN":
        raise AppError(409, "COUNT_CLOSED", f"Hitungan {cc.number} sudah {cc.status}")
    if not location.full_code.startswith(cc.scope):
        raise AppError(422, "OUT_OF_SCOPE", f"Bin {location.full_code} di luar cakupan hitungan ({cc.scope or 'semua'})")
    line = await s.scalar(select(CycleCountLine).where(CycleCountLine.count_id == cc.id,
                                                       CycleCountLine.location_id == location.id,
                                                       CycleCountLine.sku_id == sku_id).with_for_update())
    if line is None:
        cur = await s.scalar(select(BinStock.quantity).where(BinStock.location_id == location.id,
                                                             BinStock.sku_id == sku_id)) or 0
        line = CycleCountLine(tenant_id=ctx.tenant_id, count_id=cc.id, location_id=location.id, sku_id=sku_id,
                              system_qty=cur)
        s.add(line)
    line.counted_qty = counted
    line.counted_by = ctx.actor_user_id
    line.counted_at = datetime.now(UTC)
    await s.flush()
    return line


async def submit(s: AsyncSession, cc: CycleCount) -> None:
    if cc.status != "OPEN":
        raise AppError(409, "COUNT_CLOSED", f"Hitungan {cc.number} sudah {cc.status}")
    lines = (await s.scalars(select(CycleCountLine).where(CycleCountLine.count_id == cc.id))).all()
    pending = [ln for ln in lines if ln.counted_qty is None]
    if pending:
        raise AppError(409, "COUNT_INCOMPLETE", f"{len(pending)} baris belum dihitung. Isi 0 bila bin kosong.")
    if not lines:
        raise AppError(422, "COUNT_EMPTY", "Belum ada hasil hitung")
    cc.status = "SUBMITTED"
    cc.submitted_at = datetime.now(UTC)


async def approve(s: AsyncSession, ctx: Ctx, cc: CycleCount) -> dict:
    if cc.status != "SUBMITTED":
        raise AppError(409, "COUNT_NOT_SUBMITTED", "Hitungan harus disubmit dulu sebelum disetujui")
    lines = (await s.scalars(select(CycleCountLine).where(CycleCountLine.count_id == cc.id))).all()
    bals = await inv.lock_balances(s, ctx.tenant_id, [(cc.warehouse_id, ln.sku_id) for ln in lines])
    adjusted = matched = 0
    for ln in sorted(lines, key=lambda x: (str(x.location_id), str(x.sku_id))):
        if ln.counted_qty == ln.system_qty:
            matched += 1
        b = await common.lock_bin(s, ctx.tenant_id, cc.warehouse_id, ln.location_id, ln.sku_id)
        delta = ln.counted_qty - b.quantity  # dibandingkan dengan isi bin SAAT disetujui
        if delta == 0:
            continue
        if ln.counted_qty < b.allocated:
            raise AppError(409, "BIN_ALLOCATED", "Ada stok di bin ini yang sedang dialokasikan untuk picking; "
                                                 "selesaikan picking dulu lalu setujui ulang")
        await inv.apply(s, ctx, bals[(cc.warehouse_id, ln.sku_id)], "ADJUSTMENT", d_on_hand=delta,
                        reason_code="CYCLE_COUNT", reference_type="cycle_count", reference_id=cc.number,
                        location_id=ln.location_id)
        b.quantity = ln.counted_qty
        common.movement(s, ctx, warehouse_id=cc.warehouse_id, sku_id=ln.sku_id, qty=abs(delta), kind="COUNT_ADJUST",
                        from_loc=ln.location_id if delta < 0 else None, to_loc=ln.location_id if delta > 0 else None,
                        ref_type="cycle_count", ref_id=cc.number)
        adjusted += 1
    cc.status = "APPROVED"
    cc.approved_by = ctx.actor_user_id
    cc.approved_at = datetime.now(UTC)
    await s.flush()
    return {"lines": len(lines), "matched": matched, "adjusted": adjusted,
            "accuracy_pct": round(matched * 100 / len(lines), 1) if lines else 100.0}
