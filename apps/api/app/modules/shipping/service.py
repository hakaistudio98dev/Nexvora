import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, crypto
from app.core.context import Ctx
from app.core.errors import AppError
from urllib.parse import quote

from app.models import (CourierAccount, CustomCourier, Manifest, Order, Package, Shipment, Sku, TrackingEvent,
                        Warehouse)
from app.modules.shipping import providers
from app.modules.shipping.providers import BITESHIP_COURIERS, COURIERS, STATUS_RANK, TERMINAL, ShipRequest

ACTIVE = ("CREATED", "LABEL_READY", "HANDED_OVER", "IN_TRANSIT", "OUT_FOR_DELIVERY", "FAILED_DELIVERY")


def now() -> datetime:
    return datetime.now(UTC)


async def catalog(s: AsyncSession, tenant_id: UUID, *, include_inactive: bool = False) -> dict[str, dict]:
    """Kurir bawaan + kurir kustom milik tenant ini (kurir lokal, kurir yang belum ada di daftar)."""
    out = {k: {"name": v["name"], "services": dict(v["services"]), "custom": False, "is_active": True,
               "tracking_url_template": None, "phone": ""} for k, v in COURIERS.items()}
    stmt = select(CustomCourier).where(CustomCourier.tenant_id == tenant_id)
    if not include_inactive:
        stmt = stmt.where(CustomCourier.is_active.is_(True))
    for c in (await s.scalars(stmt)).all():
        out[c.code] = {"name": c.name, "services": {x["code"]: x["name"] for x in (c.services or [])}, "custom": True,
                       "is_active": c.is_active, "tracking_url_template": c.tracking_url_template, "phone": c.phone}
    return out


def courier_name(cat: dict, code: str) -> str:
    return cat.get(code, {}).get("name", code)


def tracking_url(cat: dict, code: str, resi: str | None) -> str | None:
    tpl = cat.get(code, {}).get("tracking_url_template")
    return tpl.replace("{resi}", quote(resi, safe="")) if tpl and resi else None


def credentials(acc: CourierAccount) -> dict:
    raw = crypto.decrypt(acc.credentials_enc)
    return json.loads(raw) if raw else {}


def new_webhook_token() -> str:
    return secrets.token_hex(20)


async def get_or_create_manual_account(s: AsyncSession, tenant_id: UUID) -> CourierAccount:
    acc = await s.scalar(select(CourierAccount).where(CourierAccount.tenant_id == tenant_id,
                                                      CourierAccount.provider == "manual").limit(1))
    if acc is None:
        acc = CourierAccount(tenant_id=tenant_id, name="Manual (input resi)", provider="manual",
                             couriers=list(COURIERS), webhook_token=new_webhook_token(),
                             is_default=not await s.scalar(select(CourierAccount.id).where(
                                 CourierAccount.tenant_id == tenant_id).limit(1)))
        s.add(acc)
        await s.flush()
    return acc


async def active_shipment(s: AsyncSession, order_id: UUID, lock: bool = False) -> Shipment | None:
    stmt = select(Shipment).where(Shipment.order_id == order_id, Shipment.status != "CANCELLED")
    return await s.scalar(stmt.with_for_update() if lock else stmt)


async def build_request(s: AsyncSession, order: Order, courier_code: str, service_code: str) -> ShipRequest:
    wh = await s.get(Warehouse, order.warehouse_id)
    pkg = await s.scalar(select(Package).where(Package.order_id == order.id))
    skus = {k.id: k for k in (await s.scalars(select(Sku).where(Sku.id.in_([i.sku_id for i in order.items])))).all()}
    weight = pkg.weight_g if pkg else sum((skus[i.sku_id].weight_g or 0) * i.quantity for i in order.items) or 1000
    return ShipRequest(
        order_number=order.order_number, courier_code=courier_code, service_code=service_code, weight_g=weight,
        value=order.subtotal,
        origin={"name": wh.contact_name or wh.name, "phone": wh.phone, "address": wh.address, "city": wh.city,
                "postal_code": wh.postal_code},
        destination={"name": order.customer_name, "phone": order.customer_phone, "address": order.ship_address,
                     "city": order.ship_city, "postal_code": order.ship_postal_code},
        items=[{"name": skus[i.sku_id].sku_code, "quantity": i.quantity, "value": int(i.unit_price),
                "weight": skus[i.sku_id].weight_g or max(1, weight // max(1, len(order.items)))} for i in order.items])


async def create_shipment(s: AsyncSession, ctx: Ctx, order: Order, acc: CourierAccount, *, courier_code: str,
                          service_code: str, tracking_number: str | None, cost: Decimal | None) -> Shipment:
    if order.status != "READY_TO_SHIP":
        raise AppError(409, "NOT_READY_TO_SHIP", "Resi hanya bisa dibuat untuk order berstatus Siap kirim")
    if await active_shipment(s, order.id):
        raise AppError(409, "SHIPMENT_EXISTS", "Order ini sudah punya pengiriman aktif; batalkan dulu bila ingin ganti")
    if not acc.is_active:
        raise AppError(409, "ACCOUNT_INACTIVE", "Akun kurir nonaktif")
    cat = await catalog(s, ctx.tenant_id)
    if courier_code not in cat:
        raise AppError(422, "UNKNOWN_COURIER", f"Kurir {courier_code} tidak dikenal atau sudah dinonaktifkan")
    if acc.provider == "biteship" and courier_code not in BITESHIP_COURIERS:
        raise AppError(422, "COURIER_NOT_SUPPORTED", f"{courier_name(cat, courier_code)} tidak tersedia lewat Biteship; "
                                                     "pakai akun Manual (input resi)")
    if acc.provider != "manual" and acc.couriers and courier_code not in acc.couriers:
        raise AppError(422, "COURIER_NOT_ENABLED", f"Kurir {courier_name(cat, courier_code)} tidak aktif di akun {acc.name}")
    if service_code and cat[courier_code]["services"] and service_code not in cat[courier_code]["services"]:
        raise AppError(422, "UNKNOWN_SERVICE", f"Layanan {service_code} tidak ada untuk {courier_name(cat, courier_code)}")
    req = await build_request(s, order, courier_code, service_code)
    res = await providers.get_provider(acc.provider).create(req, tracking_number=tracking_number,
                                                            credentials=credentials(acc))
    sh = Shipment(tenant_id=ctx.tenant_id, order_id=order.id, warehouse_id=order.warehouse_id,
                  courier_account_id=acc.id, provider=acc.provider, courier_code=courier_code,
                  service_code=service_code, provider_ref=res.provider_ref, tracking_number=res.tracking_number,
                  status=res.status, weight_g=req.weight_g,
                  cost=None if (cost if cost is not None else res.cost) is None
                  else Decimal(cost if cost is not None else res.cost).quantize(Decimal("0.01")),
                  created_by=ctx.actor_user_id)
    s.add(sh)
    await s.flush()
    add_event(s, ctx, sh, res.status, f"Resi dibuat ({courier_name(cat, courier_code)})", source="SYSTEM")
    await audit.record(s, tenant_id=ctx.tenant_id, actor_user_id=ctx.actor_user_id, action="shipment.created",
                       entity_type="order", entity_id=order.id,
                       after={"courier": courier_code, "service": service_code, "resi": sh.tracking_number,
                              "provider": acc.provider}, correlation_id=ctx.correlation_id, ip=ctx.ip)
    await s.flush()  # agar event pertama ikut tampil di respons
    return sh


def add_event(s: AsyncSession, ctx: Ctx, sh: Shipment, status: str, desc: str, *, source: str, location: str = "",
              occurred_at: datetime | None = None) -> None:
    s.add(TrackingEvent(tenant_id=sh.tenant_id, shipment_id=sh.id, status=status, description=desc[:1000],
                        location=location[:200], source=source, occurred_at=occurred_at or now()))


async def apply_status(s: AsyncSession, ctx: Ctx, sh: Shipment, status: str, desc: str, *, source: str,
                       location: str = "", occurred_at: datetime | None = None) -> bool:
    """Catat event tracking & majukan status. Status tidak mundur, kecuali percobaan kirim gagal
    yang kemudian dikirim ulang. Efek ke order: DELIVERED → order diterima; RETURNED_TO_SENDER → RMA otomatis."""
    if sh.status in TERMINAL or status == sh.status:
        return False
    if STATUS_RANK[status] < STATUS_RANK[sh.status] and not (sh.status == "FAILED_DELIVERY" and status in
                                                              ("IN_TRANSIT", "OUT_FOR_DELIVERY")):
        return False
    if status in ("IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED", "FAILED_DELIVERY", "RETURNED_TO_SENDER") \
            and sh.status in ("CREATED", "LABEL_READY"):
        raise AppError(409, "NOT_HANDED_OVER", "Paket belum diserahkan ke kurir")
    add_event(s, ctx, sh, status, desc, source=source, location=location, occurred_at=occurred_at)
    sh.status = status
    sh.last_tracked_at = now()
    order = await s.scalar(select(Order).where(Order.id == sh.order_id).with_for_update())
    from app.modules.orders.service import record_status  # noqa: PLC0415
    if status == "DELIVERED":
        sh.delivered_at = occurred_at or now()
        if order.status == "SHIPPED":
            order.delivered_at = sh.delivered_at
            await record_status(s, ctx, order, "DELIVERED", f"Diterima ({source.lower()}): {desc}")
    elif status == "RETURNED_TO_SENDER":
        from app.modules.returns.service import create_rts  # noqa: PLC0415
        await create_rts(s, ctx, order, desc)
    await s.flush()
    return True


async def handover(s: AsyncSession, ctx: Ctx, order: Order) -> Shipment:
    sh = await active_shipment(s, order.id, lock=True)
    if sh is None or not sh.tracking_number:
        raise AppError(409, "NO_SHIPMENT", "Buat resi pengiriman dulu sebelum diserahkan ke kurir")
    if sh.status != "LABEL_READY":
        raise AppError(409, "SHIPMENT_NOT_READY", f"Pengiriman berstatus {sh.status}")
    sh.status, sh.handed_over_at = "HANDED_OVER", now()
    add_event(s, ctx, sh, "HANDED_OVER", "Paket diserahkan ke kurir", source="SYSTEM")
    await s.flush()
    return sh


async def mark_delivered_manually(s: AsyncSession, ctx: Ctx, order: Order, reason: str | None) -> bool:
    sh = await active_shipment(s, order.id, lock=True)
    if sh is None:
        return False
    return await apply_status(s, ctx, sh, "DELIVERED", reason or "Dikonfirmasi diterima", source="MANUAL")


async def cancel_shipment(s: AsyncSession, ctx: Ctx, sh: Shipment) -> None:
    if sh.status not in ("CREATED", "LABEL_READY"):
        raise AppError(409, "CANNOT_CANCEL", "Pengiriman yang sudah diserahkan ke kurir tidak bisa dibatalkan di sini")
    if sh.manifest_id:
        raise AppError(409, "IN_MANIFEST", "Keluarkan dulu dari manifest serah terima")
    acc = await s.get(CourierAccount, sh.courier_account_id)
    await providers.get_provider(sh.provider).cancel(sh, credentials(acc))
    sh.status = "CANCELLED"
    add_event(s, ctx, sh, "CANCELLED", "Resi dibatalkan", source="MANUAL")
    await s.flush()


async def refresh(s: AsyncSession, ctx: Ctx, sh: Shipment) -> int:
    if sh.status in TERMINAL:
        return 0
    acc = await s.get(CourierAccount, sh.courier_account_id)
    order = await s.get(Order, sh.order_id)
    sh._dest_city = order.ship_city  # untuk lokasi event simulasi
    events = await providers.get_provider(sh.provider).track(sh, credentials(acc), now())
    n = 0
    for e in sorted(events, key=lambda x: STATUS_RANK.get(x.status, 0)):
        try:
            if await apply_status(s, ctx, sh, e.status, e.description, source=sh.provider.upper(),
                                  location=e.location, occurred_at=e.occurred_at):
                n += 1
        except AppError:
            continue
    sh.last_tracked_at = now()
    return n


async def poll_all(s: AsyncSession, limit: int = 100) -> int:
    """Worker: tarik status terbaru untuk pengiriman aktif (simulator & biteship)."""
    ships = (await s.scalars(select(Shipment).where(
        Shipment.status.in_(("HANDED_OVER", "IN_TRANSIT", "OUT_FOR_DELIVERY", "FAILED_DELIVERY", "CREATED")),
        Shipment.provider.in_(("simulator", "biteship")))
        .order_by(Shipment.last_tracked_at.asc().nulls_first()).limit(limit).with_for_update(skip_locked=True))).all()
    total = 0
    for sh in ships:
        total += await refresh(s, Ctx(tenant_id=sh.tenant_id), sh)
    return total


# ------------------------------------------------------------------ manifest serah terima
async def manifest_scan(s: AsyncSession, ctx: Ctx, mf: Manifest, code: str) -> Shipment:
    if mf.status != "OPEN":
        raise AppError(409, "MANIFEST_CLOSED", f"Manifest {mf.number} sudah {mf.status}")
    code = code.strip().upper()
    sh = await s.scalar(select(Shipment).where(Shipment.tenant_id == ctx.tenant_id, Shipment.status != "CANCELLED",
                                               Shipment.tracking_number == code).with_for_update())
    if sh is None:
        order = await s.scalar(select(Order).where(Order.tenant_id == ctx.tenant_id, Order.order_number == code))
        sh = await active_shipment(s, order.id, lock=True) if order else None
    if sh is None:
        raise AppError(404, "UNKNOWN_PACKAGE", f"{code} bukan resi atau nomor order yang punya pengiriman")
    if sh.courier_code != mf.courier_code:
        cat = await catalog(s, ctx.tenant_id, include_inactive=True)
        raise AppError(409, "WRONG_COURIER", f"Paket ini untuk {courier_name(cat, sh.courier_code)}, bukan "
                                             f"{courier_name(cat, mf.courier_code)} — pisahkan")
    if sh.warehouse_id != mf.warehouse_id:
        raise AppError(409, "WRONG_WAREHOUSE", "Paket ini dari gudang lain")
    if sh.status != "LABEL_READY":
        raise AppError(409, "SHIPMENT_NOT_READY", f"Pengiriman berstatus {sh.status}")
    if sh.manifest_id and sh.manifest_id != mf.id:
        raise AppError(409, "IN_OTHER_MANIFEST", "Paket sudah masuk manifest lain")
    if sh.manifest_id == mf.id:
        raise AppError(409, "ALREADY_SCANNED", "Paket sudah discan di manifest ini")
    sh.manifest_id = mf.id
    await s.flush()
    return sh


async def manifest_handover(s: AsyncSession, ctx: Ctx, mf: Manifest, driver: str, plate: str | None) -> int:
    from app.modules.orders.service import run_action  # noqa: PLC0415
    if mf.status != "OPEN":
        raise AppError(409, "MANIFEST_CLOSED", f"Manifest {mf.number} sudah {mf.status}")
    ships = (await s.scalars(select(Shipment).where(Shipment.manifest_id == mf.id, Shipment.status == "LABEL_READY")
                             .order_by(Shipment.id))).all()
    if not ships:
        raise AppError(422, "MANIFEST_EMPTY", "Belum ada paket yang discan")
    for sh in ships:
        order = await s.scalar(select(Order).where(Order.id == sh.order_id).with_for_update())
        await run_action(s, ctx, order, "ship", f"Serah terima {mf.number} ke {driver}")
    mf.status, mf.driver_name, mf.vehicle_plate, mf.handed_over_at = "HANDED_OVER", driver, plate, now()
    await audit.record(s, tenant_id=ctx.tenant_id, actor_user_id=ctx.actor_user_id, action="manifest.handed_over",
                       entity_type="manifest", entity_id=mf.id,
                       after={"number": mf.number, "packages": len(ships), "driver": driver, "plate": plate},
                       correlation_id=ctx.correlation_id, ip=ctx.ip)
    await s.flush()
    return len(ships)
