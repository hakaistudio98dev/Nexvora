import json

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, crypto, entitlements
from app.core.context import Ctx
from app.core.db import get_session, set_tenant_context
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.models import CourierAccount, CustomCourier, Manifest, Order, Shipment, TrackingEvent, Warehouse
from app.modules.shipping import providers, service
from app.modules.shipping.providers import BITESHIP_COURIERS, COURIERS
from app.modules.wms.common import next_doc_number, scan_event

router = APIRouter(prefix="/shipping", tags=["shipping"])


# ================================================================ akun kurir
class AccountIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    provider: str = Field(pattern="^(manual|simulator|biteship)$")
    couriers: list[str] = Field(default_factory=list)
    api_key: str | None = Field(default=None, max_length=300)
    is_default: bool = False


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    couriers: list[str] | None = None
    api_key: str | None = Field(default=None, max_length=300)
    is_active: bool | None = None
    is_default: bool | None = None


def _acc_out(a: CourierAccount) -> dict:
    return {"id": str(a.id), "name": a.name, "provider": a.provider, "couriers": a.couriers, "is_active": a.is_active,
            "is_default": a.is_default, "has_credentials": bool(a.credentials_enc), "created_at": a.created_at,
            "webhook_path": f"/ext/api/v1/shipping/webhooks/{a.provider}/{a.webhook_token}"
            if a.provider == "biteship" else None}


async def _check_couriers(s: AsyncSession, tenant_id: UUID, provider: str, codes: list[str]) -> list[str]:
    cat = await service.catalog(s, tenant_id)
    bad = [c for c in codes if c not in cat]
    if bad:
        raise AppError(422, "UNKNOWN_COURIER", f"Kurir tidak dikenal: {', '.join(bad)}")
    if provider == "biteship":
        unsupported = [c for c in codes if c not in BITESHIP_COURIERS]
        if unsupported:
            raise AppError(422, "COURIER_NOT_SUPPORTED", f"Tidak tersedia lewat Biteship: {', '.join(unsupported)}")
    return sorted(set(codes))


class CustomCourierIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{1,29}$", description="Kode singkat, mis. kurir_bdg")
    name: str = Field(min_length=2, max_length=80)
    services: list[str] = Field(default_factory=list, max_length=10, description="Nama layanan, mis. ['Reguler','Same day']")
    tracking_url_template: str | None = Field(default=None, max_length=300, pattern=r"^https://\S+$",
                                              description="Link lacak, gunakan {resi}")
    phone: str = Field(default="", max_length=40, pattern=r"^[0-9+ ()-]*$")


class CustomCourierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    services: list[str] | None = Field(default=None, max_length=10)
    tracking_url_template: str | None = Field(default=None, max_length=300, pattern=r"^(https://\S+)?$")
    phone: str | None = Field(default=None, max_length=40, pattern=r"^[0-9+ ()-]*$")
    is_active: bool | None = None


def _services(names: list[str]) -> list[dict]:
    out, seen = [], set()
    for n in names:
        n = n.strip()
        if not n:
            continue
        code = "".join(ch if ch.isalnum() else "_" for ch in n.lower()).strip("_")[:40] or "standard"
        if code not in seen:
            seen.add(code)
            out.append({"code": code, "name": n[:60]})
    return out or [{"code": "standard", "name": "Standar"}]


def _courier_rows(cat: dict) -> list[dict]:
    return [{"code": k, "name": v["name"], "custom": v["custom"], "is_active": v["is_active"], "phone": v["phone"],
             "tracking_url_template": v["tracking_url_template"], "auto_booking": k in BITESHIP_COURIERS,
             "services": [{"code": sc, "name": sn} for sc, sn in v["services"].items()]} for k, v in cat.items()]


@router.get("/couriers")
async def couriers(include_inactive: bool = False, p: Principal = Depends(require("shipping:read")),
                   s: AsyncSession = Depends(get_session)):
    return _courier_rows(await service.catalog(s, p.tenant_id, include_inactive=include_inactive))


@router.post("/couriers", status_code=201)
async def add_courier(body: CustomCourierIn, p: Principal = Depends(require("shipping:manage")),
                      s: AsyncSession = Depends(get_session)):
    if body.code in COURIERS:
        raise AppError(409, "COURIER_EXISTS", f"Kode {body.code} sudah dipakai kurir bawaan ({COURIERS[body.code]['name']})")
    if body.tracking_url_template and "{resi}" not in body.tracking_url_template:
        raise AppError(422, "INVALID_TEMPLATE", "Link lacak harus memuat {resi}, mis. https://kurir.id/lacak?no={resi}")
    c = CustomCourier(tenant_id=p.tenant_id, code=body.code, name=body.name, services=_services(body.services),
                      tracking_url_template=body.tracking_url_template, phone=body.phone)
    s.add(c)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="courier.custom_created",
                       entity_type="custom_courier", entity_id=c.id, after={"code": c.code, "name": c.name},
                       correlation_id=p.correlation_id, ip=p.ip)
    return next(x for x in _courier_rows(await service.catalog(s, p.tenant_id)) if x["code"] == c.code)


@router.patch("/couriers/{code}")
async def update_courier(code: str, body: CustomCourierUpdate, p: Principal = Depends(require("shipping:manage")),
                         s: AsyncSession = Depends(get_session)):
    c = await s.scalar(select(CustomCourier).where(CustomCourier.tenant_id == p.tenant_id, CustomCourier.code == code)
                       .with_for_update())
    if c is None:
        raise AppError(404, "NOT_FOUND", "Kurir kustom tidak ditemukan (kurir bawaan tidak bisa diubah)")
    if body.name is not None:
        c.name = body.name
    if body.services is not None:
        c.services = _services(body.services)
    if body.tracking_url_template is not None:
        if body.tracking_url_template and "{resi}" not in body.tracking_url_template:
            raise AppError(422, "INVALID_TEMPLATE", "Link lacak harus memuat {resi}")
        c.tracking_url_template = body.tracking_url_template or None
    if body.phone is not None:
        c.phone = body.phone
    if body.is_active is not None:
        c.is_active = body.is_active
    await s.flush()
    return next(x for x in _courier_rows(await service.catalog(s, p.tenant_id, include_inactive=True)) if x["code"] == code)


@router.get("/accounts")
async def list_accounts(p: Principal = Depends(require("shipping:read")), s: AsyncSession = Depends(get_session)):
    await service.get_or_create_manual_account(s, p.tenant_id)
    return [_acc_out(a) for a in (await s.scalars(select(CourierAccount).where(CourierAccount.tenant_id == p.tenant_id)
                                                  .order_by(CourierAccount.created_at))).all()]


@router.post("/accounts", status_code=201)
async def create_account(body: AccountIn, p: Principal = Depends(require("shipping:manage")),
                         s: AsyncSession = Depends(get_session)):
    if body.provider == "biteship":
        if "shipping_integration" not in p.entitlement.features and not p.entitlement.unrestricted:
            raise AppError(402, "FEATURE_NOT_IN_PLAN", "Integrasi kurir otomatis tersedia mulai paket Growth")
        if not body.api_key:
            raise AppError(422, "API_KEY_REQUIRED", "Isi API key Biteship")
    a = CourierAccount(tenant_id=p.tenant_id, name=body.name, provider=body.provider,
                       couriers=await _check_couriers(s, p.tenant_id, body.provider, body.couriers or (
                           sorted(BITESHIP_COURIERS) if body.provider == "biteship" else list(COURIERS))),
                       credentials_enc=crypto.encrypt(json.dumps({"api_key": body.api_key})) if body.api_key else None,
                       webhook_token=service.new_webhook_token(), is_default=body.is_default)
    if body.is_default:
        await s.execute(update(CourierAccount).where(CourierAccount.tenant_id == p.tenant_id).values(is_default=False))
    s.add(a)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="courier_account.created",
                       entity_type="courier_account", entity_id=a.id, after={"name": a.name, "provider": a.provider},
                       correlation_id=p.correlation_id, ip=p.ip)
    return _acc_out(a)


@router.patch("/accounts/{account_id}")
async def update_account(account_id: UUID, body: AccountUpdate, p: Principal = Depends(require("shipping:manage")),
                         s: AsyncSession = Depends(get_session)):
    a = await s.scalar(select(CourierAccount).where(CourierAccount.id == account_id,
                                                    CourierAccount.tenant_id == p.tenant_id).with_for_update())
    if a is None:
        raise AppError(404, "NOT_FOUND", "Akun kurir tidak ditemukan")
    if body.name is not None:
        a.name = body.name
    if body.couriers is not None:
        a.couriers = await _check_couriers(s, p.tenant_id, a.provider, body.couriers)
    if body.api_key:
        a.credentials_enc = crypto.encrypt(json.dumps({"api_key": body.api_key}))
    if body.is_active is not None:
        a.is_active = body.is_active
    if body.is_default:
        await s.execute(update(CourierAccount).where(CourierAccount.tenant_id == p.tenant_id,
                                                     CourierAccount.id != a.id).values(is_default=False))
        a.is_default = True
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="courier_account.updated",
                       entity_type="courier_account", entity_id=a.id,
                       after={"name": a.name, "active": a.is_active, "key_changed": bool(body.api_key)},
                       correlation_id=p.correlation_id, ip=p.ip)
    return _acc_out(a)


async def _account(s: AsyncSession, p: Principal, account_id: UUID | None) -> CourierAccount:
    if account_id:
        a = await s.scalar(select(CourierAccount).where(CourierAccount.id == account_id,
                                                        CourierAccount.tenant_id == p.tenant_id))
        if a is None:
            raise AppError(404, "NOT_FOUND", "Akun kurir tidak ditemukan")
        return a
    a = await s.scalar(select(CourierAccount).where(CourierAccount.tenant_id == p.tenant_id,
                                                    CourierAccount.is_default.is_(True), CourierAccount.is_active.is_(True)))
    return a or await service.get_or_create_manual_account(s, p.tenant_id)


async def _order(s: AsyncSession, p: Principal, order_id: UUID, lock: bool = False) -> Order:
    stmt = select(Order).where(Order.id == order_id, Order.tenant_id == p.tenant_id)
    o = await s.scalar(stmt.with_for_update() if lock else stmt)
    if o is None:
        raise AppError(404, "NOT_FOUND", "Order tidak ditemukan")
    return o


# ================================================================ tarif & shipment
class ShipmentIn(BaseModel):
    order_id: UUID
    account_id: UUID | None = None
    courier_code: str = Field(max_length=30)
    service_code: str = Field(default="", max_length=40)
    tracking_number: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{6,60}$")
    cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)


class TrackingIn(BaseModel):
    status: str = Field(pattern="^(IN_TRANSIT|OUT_FOR_DELIVERY|DELIVERED|FAILED_DELIVERY|RETURNED_TO_SENDER)$")
    description: str = Field(min_length=3, max_length=500)
    location: str = Field(default="", max_length=200)


async def _ship_out(s: AsyncSession, sh: Shipment, events: bool = False, cat: dict | None = None) -> dict:
    cat = cat or await service.catalog(s, sh.tenant_id, include_inactive=True)
    order = await s.get(Order, sh.order_id)
    acc = await s.get(CourierAccount, sh.courier_account_id)
    mf = await s.get(Manifest, sh.manifest_id) if sh.manifest_id else None
    out = {"id": str(sh.id), "order_id": str(sh.order_id), "order_number": order.order_number,
           "customer_name": order.customer_name, "ship_city": order.ship_city, "status": sh.status,
           "provider": sh.provider, "account_name": acc.name, "courier_code": sh.courier_code,
           "courier_name": service.courier_name(cat, sh.courier_code),
           "tracking_url": service.tracking_url(cat, sh.courier_code, sh.tracking_number),
           "service_code": sh.service_code, "tracking_number": sh.tracking_number,
           "cost": str(sh.cost) if sh.cost is not None else None, "weight_g": sh.weight_g, "manifest_number": mf.number if mf else None,
           "label_printed_at": sh.label_printed_at, "handed_over_at": sh.handed_over_at,
           "delivered_at": sh.delivered_at, "created_at": sh.created_at, "last_tracked_at": sh.last_tracked_at}
    if events:
        evs = (await s.scalars(select(TrackingEvent).where(TrackingEvent.shipment_id == sh.id)
                               .order_by(TrackingEvent.occurred_at, TrackingEvent.id))).all()
        out["events"] = [{"status": e.status, "description": e.description, "location": e.location,
                          "source": e.source, "occurred_at": e.occurred_at} for e in evs]
    return out


async def _shipment(s: AsyncSession, p: Principal, sid: UUID, lock: bool = False) -> Shipment:
    stmt = select(Shipment).where(Shipment.id == sid, Shipment.tenant_id == p.tenant_id)
    sh = await s.scalar(stmt.with_for_update() if lock else stmt)
    if sh is None:
        raise AppError(404, "NOT_FOUND", "Pengiriman tidak ditemukan")
    return sh


@router.get("/rates")
async def rates(order_id: UUID, account_id: UUID | None = None, p: Principal = Depends(require("shipping:read")),
                s: AsyncSession = Depends(get_session)):
    o = await _order(s, p, order_id)
    if o.warehouse_id is None:
        raise AppError(409, "NOT_ALLOCATED", "Order belum punya gudang asal")
    a = await _account(s, p, account_id)
    cat = await service.catalog(s, p.tenant_id)
    req = await service.build_request(s, o, "", "")
    return {"account": _acc_out(a), "weight_g": req.weight_g,
            "rates": await providers.get_provider(a.provider).rates(
                req, list(cat) if a.provider == "manual" else (a.couriers or list(cat)), service.credentials(a),
                catalog=cat)}


@router.post("/shipments", status_code=201)
async def create_shipment(body: ShipmentIn, p: Principal = Depends(require("shipping:write")),
                          s: AsyncSession = Depends(get_session)):
    o = await _order(s, p, body.order_id, lock=True)
    a = await _account(s, p, body.account_id)
    sh = await service.create_shipment(s, Ctx.from_principal(p), o, a, courier_code=body.courier_code,
                                       service_code=body.service_code, tracking_number=body.tracking_number,
                                       cost=body.cost)
    return await _ship_out(s, sh, events=True)


@router.get("/shipments")
async def list_shipments(status: str | None = Query(None, max_length=20), q: str | None = Query(None, max_length=60),
                         warehouse_id: UUID | None = None, limit: int = Query(100, ge=1, le=500),
                         p: Principal = Depends(require("shipping:read")), s: AsyncSession = Depends(get_session)):
    stmt = (select(Shipment).join(Order, Order.id == Shipment.order_id).where(Shipment.tenant_id == p.tenant_id)
            .order_by(Shipment.created_at.desc()).limit(limit))
    if status == "ACTIVE":
        stmt = stmt.where(Shipment.status.in_(("HANDED_OVER", "IN_TRANSIT", "OUT_FOR_DELIVERY", "FAILED_DELIVERY")))
    elif status:
        stmt = stmt.where(Shipment.status == status)
    if warehouse_id:
        stmt = stmt.where(Shipment.warehouse_id == warehouse_id)
    if q:
        stmt = stmt.where(or_(Shipment.tracking_number == q.strip().upper(), Order.order_number == q.strip().upper()))
    cat = await service.catalog(s, p.tenant_id, include_inactive=True)
    return [await _ship_out(s, sh, cat=cat) for sh in (await s.scalars(stmt)).all()]


@router.get("/shipments/by-order/{order_id}")
async def by_order(order_id: UUID, p: Principal = Depends(require("shipping:read")), s: AsyncSession = Depends(get_session)):
    await _order(s, p, order_id)
    sh = await service.active_shipment(s, order_id)
    return await _ship_out(s, sh, events=True) if sh else None


@router.get("/shipments/{shipment_id}")
async def get_shipment(shipment_id: UUID, p: Principal = Depends(require("shipping:read")),
                       s: AsyncSession = Depends(get_session)):
    return await _ship_out(s, await _shipment(s, p, shipment_id), events=True)


@router.get("/shipments/{shipment_id}/label")
async def label(shipment_id: UUID, p: Principal = Depends(require("shipping:read")), s: AsyncSession = Depends(get_session)):
    sh = await _shipment(s, p, shipment_id)
    o = await s.get(Order, sh.order_id)
    wh = await s.get(Warehouse, sh.warehouse_id)
    return {**await _ship_out(s, sh),
            "sender": {"name": wh.contact_name or wh.name, "phone": wh.phone, "address": wh.address, "city": wh.city,
                       "postal_code": wh.postal_code},
            "recipient": {"name": o.customer_name, "phone": o.customer_phone, "address": o.ship_address,
                          "city": o.ship_city, "province": o.ship_province, "postal_code": o.ship_postal_code},
            "items": sum(i.quantity for i in o.items), "notes": o.notes}


@router.post("/shipments/{shipment_id}/label-printed")
async def label_printed(shipment_id: UUID, p: Principal = Depends(require("shipping:write")),
                        s: AsyncSession = Depends(get_session)):
    sh = await _shipment(s, p, shipment_id, lock=True)
    sh.label_printed_at = service.now()
    return {"ok": True}


@router.post("/shipments/{shipment_id}/cancel")
async def cancel(shipment_id: UUID, p: Principal = Depends(require("shipping:write")), s: AsyncSession = Depends(get_session)):
    sh = await _shipment(s, p, shipment_id, lock=True)
    await service.cancel_shipment(s, Ctx.from_principal(p), sh)
    return await _ship_out(s, sh, events=True)


@router.post("/shipments/{shipment_id}/tracking")
async def manual_tracking(shipment_id: UUID, body: TrackingIn, p: Principal = Depends(require("shipping:write")),
                          s: AsyncSession = Depends(get_session)):
    sh = await _shipment(s, p, shipment_id, lock=True)
    if sh.provider == "biteship":
        raise AppError(409, "AUTO_TRACKED", "Status kurir terintegrasi diperbarui otomatis; gunakan tombol perbarui")
    if not await service.apply_status(s, Ctx.from_principal(p), sh, body.status, body.description, source="MANUAL",
                                      location=body.location):
        raise AppError(409, "STATUS_NOT_ADVANCED", f"Status {sh.status} tidak bisa diubah ke {body.status}")
    return await _ship_out(s, sh, events=True)


@router.post("/shipments/{shipment_id}/refresh")
async def refresh(shipment_id: UUID, p: Principal = Depends(require("shipping:read")), s: AsyncSession = Depends(get_session)):
    sh = await _shipment(s, p, shipment_id, lock=True)
    n = await service.refresh(s, Ctx.from_principal(p), sh)
    return {"new_events": n, **await _ship_out(s, sh, events=True)}


# ================================================================ manifest serah terima kurir
class ManifestIn(BaseModel):
    warehouse_id: UUID
    courier_code: str = Field(max_length=30)


class ScanIn(BaseModel):
    code: str = Field(min_length=3, max_length=60)
    client_event_id: str | None = Field(default=None, max_length=100)


class HandoverIn(BaseModel):
    driver_name: str = Field(min_length=2, max_length=120)
    vehicle_plate: str | None = Field(default=None, max_length=20)


async def _manifest(s: AsyncSession, p: Principal, mid: UUID, lock: bool = False) -> Manifest:
    stmt = select(Manifest).where(Manifest.id == mid, Manifest.tenant_id == p.tenant_id)
    m = await s.scalar(stmt.with_for_update() if lock else stmt)
    if m is None:
        raise AppError(404, "NOT_FOUND", "Manifest tidak ditemukan")
    return m


async def _manifest_out(s: AsyncSession, m: Manifest) -> dict:
    ships = (await s.scalars(select(Shipment).where(Shipment.manifest_id == m.id).order_by(Shipment.updated_at))).all()
    cat = await service.catalog(s, m.tenant_id, include_inactive=True)
    return {"id": str(m.id), "number": m.number, "courier_code": m.courier_code,
            "courier_name": service.courier_name(cat, m.courier_code), "status": m.status,
            "driver_name": m.driver_name, "vehicle_plate": m.vehicle_plate, "handed_over_at": m.handed_over_at,
            "created_at": m.created_at, "packages": [await _ship_out(s, x, cat=cat) for x in ships]}


@router.get("/manifests")
async def list_manifests(warehouse_id: UUID, p: Principal = Depends(require("shipping:read")),
                         s: AsyncSession = Depends(get_session)):
    ms = (await s.scalars(select(Manifest).where(Manifest.tenant_id == p.tenant_id, Manifest.warehouse_id == warehouse_id)
                          .order_by(Manifest.created_at.desc()).limit(50))).all()
    return [await _manifest_out(s, m) for m in ms]


@router.post("/manifests", status_code=201)
async def create_manifest(body: ManifestIn, p: Principal = Depends(require("shipping:write")),
                          s: AsyncSession = Depends(get_session)):
    if body.courier_code not in await service.catalog(s, p.tenant_id):
        raise AppError(422, "UNKNOWN_COURIER", "Kurir tidak dikenal atau nonaktif")
    wh = await s.scalar(select(Warehouse).where(Warehouse.id == body.warehouse_id, Warehouse.tenant_id == p.tenant_id))
    if wh is None:
        raise AppError(404, "NOT_FOUND", "Gudang tidak ditemukan")
    m = Manifest(tenant_id=p.tenant_id, warehouse_id=wh.id, courier_code=body.courier_code, created_by=p.user_id,
                 number=await next_doc_number(s, p.tenant_id, "MF"))
    s.add(m)
    await s.flush()
    return await _manifest_out(s, m)


@router.get("/manifests/{manifest_id}")
async def get_manifest(manifest_id: UUID, p: Principal = Depends(require("shipping:read")),
                       s: AsyncSession = Depends(get_session)):
    return await _manifest_out(s, await _manifest(s, p, manifest_id))


@router.post("/manifests/{manifest_id}/scan")
async def manifest_scan(manifest_id: UUID, body: ScanIn, p: Principal = Depends(require("shipping:write")),
                        s: AsyncSession = Depends(get_session)):
    async def run():  # noqa: ANN202
        m = await _manifest(s, p, manifest_id, lock=True)
        sh = await service.manifest_scan(s, Ctx.from_principal(p), m, body.code)
        return {"added": await _ship_out(s, sh), "count": len((await _manifest_out(s, m))["packages"])}
    return await scan_event(s, p.tenant_id, f"manifest:{manifest_id}", body.client_event_id,
                            body.model_dump(exclude={"client_event_id"}), run)


@router.post("/manifests/{manifest_id}/remove/{shipment_id}")
async def manifest_remove(manifest_id: UUID, shipment_id: UUID, p: Principal = Depends(require("shipping:write")),
                          s: AsyncSession = Depends(get_session)):
    m = await _manifest(s, p, manifest_id, lock=True)
    if m.status != "OPEN":
        raise AppError(409, "MANIFEST_CLOSED", "Manifest sudah ditutup")
    sh = await _shipment(s, p, shipment_id, lock=True)
    if sh.manifest_id == m.id:
        sh.manifest_id = None
    return await _manifest_out(s, m)


@router.post("/manifests/{manifest_id}/handover")
async def manifest_handover(manifest_id: UUID, body: HandoverIn, p: Principal = Depends(require("shipping:write")),
                            s: AsyncSession = Depends(get_session)):
    entitlements.check_permission(p.entitlement, "order:fulfill")
    m = await _manifest(s, p, manifest_id, lock=True)
    n = await service.manifest_handover(s, Ctx.from_principal(p), m, body.driver_name, body.vehicle_plate)
    return {**await _manifest_out(s, m), "handed_over": n}


@router.post("/manifests/{manifest_id}/cancel")
async def manifest_cancel(manifest_id: UUID, p: Principal = Depends(require("shipping:write")),
                          s: AsyncSession = Depends(get_session)):
    m = await _manifest(s, p, manifest_id, lock=True)
    if m.status != "OPEN":
        raise AppError(409, "MANIFEST_CLOSED", "Manifest sudah ditutup")
    await s.execute(update(Shipment).where(Shipment.manifest_id == m.id).values(manifest_id=None))
    m.status = "CANCELLED"
    return await _manifest_out(s, m)


# ================================================================ webhook kurir
@router.post("/webhooks/{provider}/{token}", include_in_schema=False)
async def webhook(provider: str, token: str, payload: dict, s: AsyncSession = Depends(get_session)):
    """Token rahasia di URL mengidentifikasi akun kurir. Isi payload TIDAK dipercaya: sistem menarik
    status terbaru langsung dari API kurir (pull-on-notify)."""
    if len(token) != 40:
        raise AppError(404, "NOT_FOUND", "Tidak ditemukan")
    await set_tenant_context(s, None, superadmin=True)
    acc = await s.scalar(select(CourierAccount).where(CourierAccount.webhook_token == token,
                                                      CourierAccount.provider == provider))
    if acc is None:
        raise AppError(404, "NOT_FOUND", "Tidak ditemukan")
    await set_tenant_context(s, acc.tenant_id)
    ref = str(payload.get("order_id") or payload.get("id") or "")
    sh = await s.scalar(select(Shipment).where(Shipment.courier_account_id == acc.id, Shipment.provider_ref == ref)
                        .with_for_update()) if ref else None
    if sh is not None:
        await service.refresh(s, Ctx(tenant_id=acc.tenant_id), sh)
    return {"ok": True}


