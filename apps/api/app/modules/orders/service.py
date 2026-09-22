from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.config import get_settings
from app.core.context import Ctx
from app.core.errors import AppError
from app.models import (InventoryBalance, Order, OrderItem, OrderStatusHistory, Reservation, Sku,
                        Warehouse)
from app.modules.inventory import service as inv
from app.modules.orders.state import ACTIONS


def _now() -> datetime:
    return datetime.now(UTC)


async def next_order_number(session: AsyncSession, tenant_id: UUID) -> str:
    n = await session.scalar(text("""
        INSERT INTO order_counters(tenant_id, last_no) VALUES (:t, 1)
        ON CONFLICT (tenant_id) DO UPDATE SET last_no = order_counters.last_no + 1
        RETURNING last_no
    """), {"t": tenant_id})
    return f"SO-{_now():%y%m}-{n:06d}"


async def record_status(session: AsyncSession, ctx: Ctx, order: Order, to_status: str, reason: str | None,
                        *, initial: bool = False) -> None:
    from_status = None if initial else order.status
    session.add(OrderStatusHistory(tenant_id=order.tenant_id, order_id=order.id, from_status=from_status,
                                   to_status=to_status, reason=reason, actor_user_id=ctx.actor_user_id))
    before = order.status
    order.status = to_status
    order.status_reason = reason
    await audit.record(session, tenant_id=order.tenant_id, actor_user_id=ctx.actor_user_id,
                       action="order.status_changed", entity_type="order", entity_id=order.id,
                       before={"status": before}, after={"status": to_status, "reason": reason},
                       correlation_id=ctx.correlation_id, ip=ctx.ip)


# ------------------------------------------------------------------ allocation + reservation
async def _choose_warehouses(session: AsyncSession, order: Order, need: dict[UUID, int]) -> list[tuple[Warehouse, str]]:
    """Allocation engine v1: gudang aktif yang punya stok LENGKAP untuk semua item.
    Prioritas: kota tujuan sama → total stok tersedia terbanyak → kode gudang."""
    rows = (await session.execute(
        select(InventoryBalance.warehouse_id, InventoryBalance.sku_id, InventoryBalance.available)
        .where(InventoryBalance.tenant_id == order.tenant_id, InventoryBalance.sku_id.in_(need.keys()))
    )).all()
    avail: dict[UUID, dict[UUID, int]] = {}
    for w, s, a in rows:
        avail.setdefault(w, {})[s] = a
    whs = (await session.scalars(select(Warehouse).where(Warehouse.tenant_id == order.tenant_id,
                                                         Warehouse.is_active.is_(True)))).all()
    candidates = []
    for w in whs:
        stock = avail.get(w.id, {})
        if all(stock.get(s, 0) >= q for s, q in need.items()):
            same_city = bool(w.city) and w.city.strip().lower() == order.ship_city.strip().lower()
            total = sum(stock.get(s, 0) for s in need)
            why = "kota tujuan sama" if same_city else "stok tersedia terbanyak"
            candidates.append(((0 if same_city else 1, -total, w.code), w, why))
    candidates.sort(key=lambda c: c[0])
    return [(w, why) for _, w, why in candidates]


async def reserve_order(session: AsyncSession, ctx: Ctx, order: Order, *, ttl_minutes: int | None) -> bool:
    active = await session.scalar(select(func.count()).select_from(Reservation).where(
        Reservation.order_id == order.id, Reservation.status == "ACTIVE"))
    if active:
        return True
    need: dict[UUID, int] = {}
    for it in order.items:
        need[it.sku_id] = need.get(it.sku_id, 0) + it.quantity

    expires = _now() + timedelta(minutes=ttl_minutes) if ttl_minutes else None
    for wh, why in await _choose_warehouses(session, order, need):
        bals = await inv.lock_balances(session, order.tenant_id, [(wh.id, s) for s in need])
        # Cek ulang setelah dikunci: order lain mungkin baru saja mengambil stok yang sama
        if not all(bals[(wh.id, s)].on_hand - bals[(wh.id, s)].reserved >= q for s, q in need.items()):
            continue
        for it in order.items:
            await inv.apply(session, ctx, bals[(wh.id, it.sku_id)], "RESERVE", d_reserved=it.quantity,
                            reference_type="order", reference_id=order.order_number)
            session.add(Reservation(tenant_id=order.tenant_id, order_id=order.id, order_item_id=it.id,
                                    warehouse_id=wh.id, sku_id=it.sku_id, quantity=it.quantity,
                                    status="ACTIVE", expires_at=expires))
        order.warehouse_id = wh.id
        order.stock_status = "RESERVED"
        order.allocation_note = f"Gudang {wh.code} dipilih: {why}"
        await session.flush()
        return True

    order.stock_status = "OUT_OF_STOCK"
    order.warehouse_id = None
    order.allocation_note = "Tidak ada gudang dengan stok lengkap untuk semua item"
    await session.flush()
    return False


async def _settle_reservations(session: AsyncSession, ctx: Ctx, order: Order, mode: str,
                               new_status: str) -> None:
    """mode: RELEASE (kembalikan ke available) atau SHIP (kurangi fisik & reserved)."""
    res = (await session.scalars(select(Reservation).where(
        Reservation.order_id == order.id, Reservation.status == "ACTIVE").with_for_update())).all()
    if not res:
        return
    bals = await inv.lock_balances(session, order.tenant_id, [(r.warehouse_id, r.sku_id) for r in res])
    for r in res:
        b = bals[(r.warehouse_id, r.sku_id)]
        if mode == "SHIP":
            await inv.apply(session, ctx, b, "SHIP", d_on_hand=-r.quantity, d_reserved=-r.quantity,
                            reference_type="order", reference_id=order.order_number)
        else:
            await inv.apply(session, ctx, b, "RELEASE", d_reserved=-r.quantity,
                            reason_code=new_status, reference_type="order", reference_id=order.order_number)
        r.status = new_status
    order.stock_status = "CONSUMED" if mode == "SHIP" else "RELEASED"
    await session.flush()


# ------------------------------------------------------------------ transitions
async def run_action(session: AsyncSession, ctx: Ctx, order: Order, action_name: str,
                     reason: str | None = None) -> Order:
    action = ACTIONS.get(action_name)
    if action is None:
        raise AppError(404, "UNKNOWN_ACTION", "Aksi tidak dikenal")
    if order.status not in action.from_statuses:
        raise AppError(409, "INVALID_TRANSITION",
                       f"Aksi '{action.label}' tidak bisa dilakukan saat status {order.status}")
    now = _now()

    if action_name == "reserve":
        ttl = None if order.payment_status == "PAID" else get_settings().reservation_ttl_minutes
        if not await reserve_order(session, ctx, order, ttl_minutes=ttl):
            raise AppError(409, "OUT_OF_STOCK", order.allocation_note or "Stok tidak cukup")
        if order.status == "PAID":
            order.allocated_at = now
            await record_status(session, ctx, order, "ALLOCATED", order.allocation_note)
        return order

    if action_name == "mark_paid":
        order.payment_status, order.paid_at = "PAID", now
        await record_status(session, ctx, order, "PAID", reason)
        # Stok yang sudah dibayar tidak boleh kedaluwarsa
        await session.execute(text("UPDATE reservations SET expires_at = NULL "
                                   "WHERE order_id = :o AND status = 'ACTIVE'"), {"o": order.id})
        if await reserve_order(session, ctx, order, ttl_minutes=None):
            order.allocated_at = now
            await record_status(session, ctx, order, "ALLOCATED", order.allocation_note)
        return order

    if action_name == "allocate":
        if not await reserve_order(session, ctx, order, ttl_minutes=None):
            raise AppError(409, "OUT_OF_STOCK", order.allocation_note or "Stok tidak cukup")
        order.allocated_at = now
        await record_status(session, ctx, order, "ALLOCATED", order.allocation_note)
        return order

    if action_name in ("cancel", "mark_failed"):
        if action_name == "cancel" and not reason:
            raise AppError(400, "REASON_REQUIRED", "Alasan pembatalan wajib diisi")
        await _settle_reservations(session, ctx, order, "RELEASE", "RELEASED")
        if action_name == "mark_failed":
            order.payment_status = "FAILED"
        order.cancelled_at = now
        await record_status(session, ctx, order, action.to_status, reason)
        return order

    if action_name == "start_picking":
        from app.modules.wms.picking import create_wave  # noqa: PLC0415
        if order.stock_status != "RESERVED":
            raise AppError(409, "NOT_RESERVED", "Order belum memiliki reservasi stok")
        await create_wave(session, ctx, order.warehouse_id, [order])
        return order

    if action_name == "ship":
        from app.modules.shipping.service import handover  # noqa: PLC0415
        if order.stock_status != "RESERVED":
            raise AppError(409, "NOT_RESERVED", "Order belum memiliki reservasi stok")
        await handover(session, ctx, order)  # wajib sudah ada resi
        await _settle_reservations(session, ctx, order, "SHIP", "CONSUMED")
        order.shipped_at = now
    elif action_name == "deliver":
        from app.modules.shipping.service import mark_delivered_manually  # noqa: PLC0415
        if await mark_delivered_manually(session, ctx, order, reason):
            return order  # status order sudah diperbarui lewat event tracking
        order.delivered_at = now

    await record_status(session, ctx, order, action.to_status, reason)
    return order


# ------------------------------------------------------------------ expiry (dipanggil worker)
async def expire_reservations(session: AsyncSession, limit: int = 200) -> int:
    """Lepas reservasi order BELUM DIBAYAR yang kedaluwarsa dan batalkan ordernya.
    Dijalankan dengan konteks superadmin (lintas tenant) oleh worker."""
    order_ids = (await session.scalars(text("""
        SELECT DISTINCT r.order_id FROM reservations r
        WHERE r.status = 'ACTIVE' AND r.expires_at IS NOT NULL AND r.expires_at < now()
        LIMIT :lim
    """), {"lim": limit})).all()
    done = 0
    for oid in order_ids:
        order = await session.scalar(select(Order).where(Order.id == oid)
                                     .with_for_update(skip_locked=True))
        if order is None or order.status != "CREATED" or order.payment_status == "PAID":
            continue
        ctx = Ctx(tenant_id=order.tenant_id)
        await _settle_reservations(session, ctx, order, "RELEASE", "EXPIRED")
        order.cancelled_at = _now()
        await record_status(session, ctx, order, "CANCELLED",
                            "Reservasi kedaluwarsa: order belum dibayar dalam batas waktu")
        done += 1
    return done


async def resolve_items(session: AsyncSession, tenant_id: UUID, items: list) -> list[tuple[Sku, int, Decimal]]:
    """Terima sku_id atau sku_code (untuk integrasi marketplace)."""
    ids = [i.sku_id for i in items if i.sku_id]
    codes = [i.sku_code for i in items if i.sku_code]
    conds = []
    if ids:
        conds.append(Sku.id.in_(ids))
    if codes:
        conds.append(Sku.sku_code.in_(codes))
    found = (await session.scalars(select(Sku).where(Sku.tenant_id == tenant_id, or_(*conds)))).all()
    by_id = {s.id: s for s in found}
    by_code = {s.sku_code: s for s in found}
    out, seen = [], set()
    for i in items:
        sku = by_id.get(i.sku_id) if i.sku_id else by_code.get(i.sku_code)
        if sku is None:
            raise AppError(422, "UNKNOWN_SKU", f"SKU tidak ditemukan: {i.sku_id or i.sku_code}")
        if not sku.is_active:
            raise AppError(422, "INACTIVE_SKU", f"SKU {sku.sku_code} nonaktif")
        if sku.id in seen:
            raise AppError(422, "DUPLICATE_SKU", f"SKU {sku.sku_code} muncul lebih dari sekali; gabungkan quantity-nya")
        seen.add(sku.id)
        out.append((sku, i.quantity, i.unit_price))
    return out


def new_item(order: Order, sku: Sku, qty: int, price: Decimal) -> OrderItem:
    return OrderItem(tenant_id=order.tenant_id, sku_id=sku.id, quantity=qty, unit_price=price,
                     line_total=(price * qty).quantize(Decimal("0.01")))


async def create_order(session: AsyncSession, ctx: Ctx, *, channel: str, external_ref: str | None, customer_name: str,
                       customer_phone: str, customer_email: str, ship_address: str, ship_city: str,
                       ship_province: str, ship_postal_code: str, ship_country: str,
                       items: list[tuple[Sku, int, Decimal]], shipping_fee: Decimal = Decimal("0"),
                       discount: Decimal = Decimal("0"), notes: str = "", paid: bool = False) -> Order:
    subtotal = sum((price * qty for _, qty, price in items), Decimal("0")).quantize(Decimal("0.01"))
    total = subtotal + shipping_fee - discount
    if total < 0:
        raise AppError(422, "INVALID_TOTAL", "Diskon melebihi subtotal + ongkir")
    o = Order(tenant_id=ctx.tenant_id, order_number=await next_order_number(session, ctx.tenant_id), channel=channel,
              external_ref=external_ref, customer_name=customer_name, customer_phone=customer_phone,
              customer_email=customer_email, ship_address=ship_address, ship_city=ship_city,
              ship_province=ship_province, ship_postal_code=ship_postal_code, ship_country=ship_country,
              subtotal=subtotal, shipping_fee=shipping_fee, discount=discount, total=total, notes=notes,
              created_by=ctx.actor_user_id, status="CREATED")
    o.items = [new_item(o, sku, qty, price) for sku, qty, price in items]
    session.add(o)
    await session.flush()
    await record_status(session, ctx, o, "CREATED", f"Order masuk dari {channel}", initial=True)
    await reserve_order(session, ctx, o, ttl_minutes=None if paid else get_settings().reservation_ttl_minutes)
    if paid:
        await run_action(session, ctx, o, "mark_paid", "Pembayaran terverifikasi saat order masuk")
    return o
