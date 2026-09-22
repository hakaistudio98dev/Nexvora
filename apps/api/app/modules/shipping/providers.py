"""Adapter kurir. Semua provider mengembalikan status dalam kosakata internal yang sama:
CREATED, LABEL_READY, HANDED_OVER, IN_TRANSIT, OUT_FOR_DELIVERY, DELIVERED, FAILED_DELIVERY,
RETURNED_TO_SENDER, CANCELLED.

- manual    : resi diisi operator (resi dari marketplace / drop-off counter). Tracking diperbarui manual.
- simulator : resi & tracking palsu yang bergerak otomatis — HANYA untuk demo/uji coba.
- biteship  : aggregator kurir Indonesia (JNE, J&T, SiCepat, AnterAja, dll.) via API.
"""
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

import httpx

from app.core.config import get_settings
from app.core.errors import AppError

COURIERS: dict[str, dict] = {
    "jne": {"name": "JNE", "services": {"reg": "Reguler", "yes": "YES (besok sampai)", "oke": "OKE (ekonomis)"}},
    "jnt": {"name": "J&T Express", "services": {"ez": "EZ Reguler"}},
    "sicepat": {"name": "SiCepat", "services": {"reg": "REG", "best": "BEST", "gokil": "GOKIL (kargo)"}},
    "anteraja": {"name": "AnterAja", "services": {"reg": "Reguler", "next_day": "Next Day"}},
    "pos": {"name": "Pos Indonesia", "services": {"kilat": "Pos Kilat Khusus"}},
    "idexpress": {"name": "ID Express", "services": {"reg": "Reguler"}},
    "ninja": {"name": "Ninja Xpress", "services": {"standard": "Standard"}},
    "lion": {"name": "Lion Parcel", "services": {"reg_pack": "REGPACK"}},
    "tiki": {"name": "TIKI", "services": {"reg": "REG", "ons": "ONS (besok sampai)", "eco": "ECO"}},
    "wahana": {"name": "Wahana", "services": {"normal": "Normal"}},
    "sap": {"name": "SAP Express", "services": {"reg": "Reguler", "ods": "One Day Service"}},
    "rpx": {"name": "RPX", "services": {"rgp": "Regular Package"}},
    "paxel": {"name": "Paxel", "services": {"same_day": "Same Day", "next_day": "Next Day"}},
    "lalamove": {"name": "Lalamove", "services": {"motorcycle": "Motor", "car": "Mobil"}},
    "borzo": {"name": "Borzo", "services": {"instant": "Instant"}},
    "gojek": {"name": "GoSend", "services": {"instant": "Instant", "same_day": "Same Day"}},
    "grab": {"name": "GrabExpress", "services": {"instant": "Instant", "same_day": "Same Day"}},
    "own": {"name": "Kurir toko sendiri", "services": {"standard": "Antar sendiri"}},
}
# Kurir yang bisa dipesan otomatis lewat Biteship (kurir lain tetap bisa dipakai lewat akun Manual)
BITESHIP_COURIERS = {"jne", "jnt", "sicepat", "anteraja", "pos", "idexpress", "ninja", "lion", "gojek", "grab",
                     "tiki", "wahana", "sap", "rpx", "paxel", "lalamove", "borzo"}
STATUS_RANK = {"CREATED": 0, "LABEL_READY": 1, "HANDED_OVER": 2, "IN_TRANSIT": 3, "OUT_FOR_DELIVERY": 4,
               "FAILED_DELIVERY": 4, "DELIVERED": 9, "RETURNED_TO_SENDER": 9, "CANCELLED": 9}
TERMINAL = {"DELIVERED", "RETURNED_TO_SENDER", "CANCELLED"}


@dataclass
class ShipRequest:
    order_number: str
    courier_code: str
    service_code: str
    weight_g: int
    value: Decimal
    origin: dict            # name, phone, address, city, postal_code
    destination: dict       # name, phone, address, city, postal_code
    items: list[dict] = field(default_factory=list)  # name, quantity, value, weight


@dataclass
class ShipResult:
    tracking_number: str | None
    provider_ref: str | None
    status: str = "LABEL_READY"
    cost: Decimal | None = None


@dataclass
class TrackEvent:
    status: str
    description: str
    location: str = ""
    occurred_at: datetime | None = None


class ManualProvider:
    code = "manual"

    async def create(self, req: ShipRequest, *, tracking_number: str | None, credentials: dict) -> ShipResult:
        if not tracking_number:
            raise AppError(422, "TRACKING_REQUIRED", "Isi nomor resi dari kurir/marketplace untuk pengiriman manual")
        return ShipResult(tracking_number=tracking_number.strip().upper(), provider_ref=None)

    async def cancel(self, shipment, credentials: dict) -> None:  # noqa: ANN001
        return None

    async def track(self, shipment, credentials: dict, now: datetime) -> list[TrackEvent]:  # noqa: ANN001
        return []

    async def rates(self, req: ShipRequest, couriers: list[str], credentials: dict,
                    catalog: dict | None = None) -> list[dict]:
        cat = catalog or COURIERS
        return [{"courier_code": c, "courier_name": cat[c]["name"], "service_code": sc, "service_name": sn,
                 "price": None, "etd": None} for c in couriers if c in cat for sc, sn in cat[c]["services"].items()]


class SimulatorProvider(ManualProvider):
    code = "simulator"
    STEPS = [("IN_TRANSIT", "Paket diproses di hub sortir", "Hub Jakarta"),
             ("OUT_FOR_DELIVERY", "Kurir menuju alamat penerima", ""),
             ("DELIVERED", "Paket diterima oleh penerima", "")]

    async def create(self, req: ShipRequest, *, tracking_number: str | None, credentials: dict) -> ShipResult:
        resi = f"SIM{req.courier_code.upper()[:3]}{secrets.randbelow(10**10):010d}"
        return ShipResult(tracking_number=resi, provider_ref=resi, cost=Decimal(9000 + (req.weight_g // 1000) * 7000))

    async def track(self, shipment, credentials: dict, now: datetime) -> list[TrackEvent]:  # noqa: ANN001
        if not shipment.handed_over_at:
            return []
        step = timedelta(seconds=get_settings().simulator_step_seconds)
        return [TrackEvent(st, desc, loc or shipment_city(shipment), shipment.handed_over_at + step * (i + 1))
                for i, (st, desc, loc) in enumerate(self.STEPS) if now >= shipment.handed_over_at + step * (i + 1)]

    async def rates(self, req: ShipRequest, couriers: list[str], credentials: dict,
                    catalog: dict | None = None) -> list[dict]:
        out = await super().rates(req, couriers, credentials, catalog)
        for i, r in enumerate(out):
            r["price"] = str(9000 + (req.weight_g // 1000) * 7000 + i * 1000)
            r["etd"] = "1-3 hari"
        return out


def shipment_city(shipment) -> str:  # noqa: ANN001
    return getattr(shipment, "_dest_city", "") or ""


BITESHIP_STATUS = {
    "confirmed": "LABEL_READY", "allocated": "LABEL_READY", "picking_up": "LABEL_READY",
    "picked": "IN_TRANSIT", "dropping_off": "OUT_FOR_DELIVERY", "delivered": "DELIVERED",
    "return_in_transit": "IN_TRANSIT", "returned": "RETURNED_TO_SENDER", "on_hold": "IN_TRANSIT",
    "rejected": "CANCELLED", "cancelled": "CANCELLED", "courier_not_found": "CANCELLED", "disposed": "FAILED_DELIVERY",
}


class BiteshipProvider:
    """Catatan: skema diambil dari dokumentasi publik Biteship v1. Verifikasi di akun sandbox
    sebelum dipakai produksi; semua pemanggilan diisolasi di kelas ini."""
    code = "biteship"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    def _client(self, credentials: dict) -> httpx.AsyncClient:
        key = credentials.get("api_key")
        if not key:
            raise AppError(422, "COURIER_NOT_CONFIGURED", "API key Biteship belum diisi di akun kurir")
        return httpx.AsyncClient(base_url=get_settings().biteship_base_url, timeout=20, transport=self._transport,
                                 headers={"Authorization": key, "Content-Type": "application/json"})

    @staticmethod
    def _check(r: httpx.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 300 or data.get("success") is False:
            raise AppError(502, "COURIER_API_ERROR", f"Kurir menolak permintaan: {data.get('error') or r.status_code}")
        return data

    async def create(self, req: ShipRequest, *, tracking_number: str | None, credentials: dict) -> ShipResult:
        body = {
            "shipper_contact_name": req.origin["name"], "shipper_contact_phone": req.origin["phone"],
            "origin_contact_name": req.origin["name"], "origin_contact_phone": req.origin["phone"],
            "origin_address": req.origin["address"], "origin_postal_code": int(req.origin["postal_code"] or 0),
            "destination_contact_name": req.destination["name"], "destination_contact_phone": req.destination["phone"],
            "destination_address": req.destination["address"],
            "destination_postal_code": int(req.destination["postal_code"] or 0),
            "courier_company": req.courier_code, "courier_type": req.service_code, "delivery_type": "now",
            "reference_id": req.order_number, "items": req.items,
        }
        try:
            async with self._client(credentials) as c:
                data = self._check(await c.post("/v1/orders", json=body))
        except httpx.HTTPError:
            raise AppError(502, "COURIER_API_DOWN", "Layanan kurir sedang tidak bisa dihubungi") from None
        courier = data.get("courier") or {}
        waybill = courier.get("waybill_id")
        return ShipResult(tracking_number=waybill, provider_ref=data.get("id"),
                          status="LABEL_READY" if waybill else "CREATED",
                          cost=Decimal(str(data["price"])) if data.get("price") is not None else None)

    async def cancel(self, shipment, credentials: dict) -> None:  # noqa: ANN001
        if not shipment.provider_ref:
            return
        try:
            async with self._client(credentials) as c:
                self._check(await c.post(f"/v1/orders/{shipment.provider_ref}/cancel",
                                         json={"cancellation_reason": "Dibatalkan penjual"}))
        except httpx.HTTPError:
            raise AppError(502, "COURIER_API_DOWN", "Layanan kurir sedang tidak bisa dihubungi") from None

    async def track(self, shipment, credentials: dict, now: datetime) -> list[TrackEvent]:  # noqa: ANN001
        if not shipment.provider_ref:
            return []
        try:
            async with self._client(credentials) as c:
                data = self._check(await c.get(f"/v1/orders/{shipment.provider_ref}"))
        except httpx.HTTPError:
            return []
        courier = data.get("courier") or {}
        if courier.get("waybill_id") and not shipment.tracking_number:
            shipment.tracking_number = courier["waybill_id"]
        events = []
        for h in courier.get("history") or []:
            st = BITESHIP_STATUS.get(str(h.get("status", "")).lower())
            if st:
                events.append(TrackEvent(st, h.get("note") or st, "", _parse_ts(h.get("updated_at"))))
        top = BITESHIP_STATUS.get(str(data.get("status", "")).lower())
        if top and not any(e.status == top for e in events):
            events.append(TrackEvent(top, f"Status kurir: {data.get('status')}", "", now))
        return events

    async def rates(self, req: ShipRequest, couriers: list[str], credentials: dict,
                    catalog: dict | None = None) -> list[dict]:
        couriers = [c for c in couriers if c in BITESHIP_COURIERS]
        body = {"origin_postal_code": int(req.origin["postal_code"] or 0),
                "destination_postal_code": int(req.destination["postal_code"] or 0),
                "couriers": ",".join(couriers), "items": req.items}
        try:
            async with self._client(credentials) as c:
                data = self._check(await c.post("/v1/rates/couriers", json=body))
        except httpx.HTTPError:
            raise AppError(502, "COURIER_API_DOWN", "Layanan kurir sedang tidak bisa dihubungi") from None
        return [{"courier_code": p.get("courier_code"), "courier_name": p.get("courier_name"),
                 "service_code": p.get("courier_service_code"), "service_name": p.get("courier_service_name"),
                 "price": str(p.get("price")), "etd": p.get("duration")} for p in data.get("pricing", [])]


def _parse_ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


_BITESHIP_TRANSPORT: httpx.AsyncBaseTransport | None = None  # dipakai tes untuk MockTransport


def get_provider(code: str):  # noqa: ANN201
    if code == "manual":
        return ManualProvider()
    if code == "simulator":
        return SimulatorProvider()
    if code == "biteship":
        return BiteshipProvider(_BITESHIP_TRANSPORT)
    raise AppError(422, "UNKNOWN_PROVIDER", f"Provider {code} tidak dikenal")
