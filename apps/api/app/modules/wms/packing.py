from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.context import Ctx
from app.core.errors import AppError
from app.models import Order, Package, PackProgress, Sku
from app.modules.wms import common, picking


async def view(s: AsyncSession, tenant_id, order: Order) -> dict:  # noqa: ANN001
    skus = {k.id: k for k in (await s.scalars(select(Sku).where(
        Sku.tenant_id == tenant_id, Sku.id.in_([i.sku_id for i in order.items])))).all()}
    picked = await picking.picked_by_item(s, order.id)
    prog = {p.order_item_id: p.scanned_qty for p in (await s.scalars(
        select(PackProgress).where(PackProgress.order_id == order.id))).all()}
    items, weight_known, expected = [], True, 0
    for it in order.items:
        k = skus[it.sku_id]
        if k.weight_g:
            expected += k.weight_g * it.quantity
        else:
            weight_known = False
        items.append({"order_item_id": str(it.id), "sku_code": k.sku_code, "barcode": k.barcode,
                      "variant_name": k.variant_name, "quantity": it.quantity,
                      "picked_qty": picked.get(it.id, 0), "scanned_qty": prog.get(it.id, 0)})
    pkg = await s.scalar(select(Package).where(Package.order_id == order.id))
    return {
        "order_id": str(order.id), "order_number": order.order_number, "status": order.status,
        "customer_name": order.customer_name, "ship_city": order.ship_city, "items": items,
        "picking_complete": all(i["picked_qty"] >= i["quantity"] for i in items),
        "open_pick_tasks": await picking.open_pick_tasks(s, order.id),
        "all_scanned": all(i["scanned_qty"] >= i["quantity"] for i in items),
        "expected_weight_g": expected if weight_known else None,
        "tolerance_pct": get_settings().pack_weight_tolerance_pct,
        "package": {"weight_g": pkg.weight_g, "created_at": pkg.created_at} if pkg else None,
    }


async def scan(s: AsyncSession, ctx: Ctx, order: Order, barcode: str, qty: int = 1) -> dict:
    from app.modules.orders.service import record_status

    if order.status == "PICKING":
        picked = await picking.picked_by_item(s, order.id)
        if any(picked.get(i.id, 0) < i.quantity for i in order.items):
            raise AppError(409, "PICK_INCOMPLETE", "Picking order ini belum lengkap")
        await record_status(s, ctx, order, "PACKING", "Mulai di packing station")
    elif order.status != "PACKING":
        raise AppError(409, "NOT_PACKABLE", f"Order berstatus {order.status}, tidak bisa dipacking")
    sku = await common.resolve_sku(s, ctx.tenant_id, barcode)
    item = next((i for i in order.items if i.sku_id == sku.id), None)
    if item is None:
        raise AppError(409, "WRONG_SKU", f"{sku.sku_code} tidak ada di order ini — jangan dimasukkan ke paket")
    await s.execute(pg_insert(PackProgress).values(order_item_id=item.id, tenant_id=ctx.tenant_id, order_id=order.id)
                    .on_conflict_do_nothing(index_elements=["order_item_id"]))
    pp = await s.scalar(select(PackProgress).where(PackProgress.order_item_id == item.id).with_for_update()
                        .execution_options(populate_existing=True))
    if pp.scanned_qty + qty > item.quantity:
        raise AppError(409, "OVER_SCAN", f"{sku.sku_code} sudah lengkap ({item.quantity}); jangan tambahkan lagi")
    pp.scanned_qty += qty
    await s.flush()
    return {"sku_code": sku.sku_code, "scanned_qty": pp.scanned_qty, "quantity": item.quantity}


async def complete(s: AsyncSession, ctx: Ctx, order: Order, *, weight_g: int, dims: tuple, override_reason: str | None) -> Package:  # noqa: E501
    from app.modules.orders.service import record_status

    if order.status != "PACKING":
        raise AppError(409, "NOT_PACKING", "Scan item terlebih dulu untuk memulai packing")
    v = await view(s, ctx.tenant_id, order)
    missing = [f"{i['sku_code']} ({i['scanned_qty']}/{i['quantity']})" for i in v["items"] if i["scanned_qty"] < i["quantity"]]
    if missing:
        raise AppError(409, "ITEMS_NOT_VERIFIED", "Item belum lengkap discan: " + ", ".join(missing))
    exp = v["expected_weight_g"]
    tol = get_settings().pack_weight_tolerance_pct
    mismatch = exp is not None and abs(weight_g - exp) > exp * tol / 100
    if mismatch and not override_reason:
        raise AppError(422, "WEIGHT_MISMATCH",
                       f"Berat {weight_g} g di luar toleransi ±{tol}% dari perkiraan {exp} g. Cek isi paket, "
                       f"atau isi alasan bila beratnya memang benar.")
    pkg = Package(tenant_id=ctx.tenant_id, order_id=order.id, weight_g=weight_g, expected_weight_g=exp,
                  length_mm=dims[0], width_mm=dims[1], height_mm=dims[2],
                  override_reason=override_reason if mismatch else None, packed_by=ctx.actor_user_id)
    s.add(pkg)
    if mismatch:
        common.raise_exception(s, ctx, warehouse_id=order.warehouse_id, exc_type="WEIGHT_MISMATCH", order_id=order.id,
                               quantity=weight_g - exp, note=f"Berat {weight_g} g vs perkiraan {exp} g: {override_reason}")
    await record_status(s, ctx, order, "READY_TO_SHIP", f"Paket {weight_g} g terverifikasi")
    await s.flush()
    return pkg
