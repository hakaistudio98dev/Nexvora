from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, entitlements, idempotency
from app.core.config import get_settings
from app.core.context import Ctx
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.core.schemas import Page
from app.core.utils import escape_like, paginate
from app.models import Order, OrderStatusHistory, Product, Reservation, Sku, Warehouse
from app.modules.orders import service
from app.modules.orders.state import ACTIONS, allowed_actions

router = APIRouter(prefix="/orders", tags=["orders"])
Money = Decimal
CHANNELS = r"^(MANUAL|API|WEBSITE|SHOPEE|TOKOPEDIA|TIKTOK|LAZADA|OTHER)$"


# ------------------------------------------------------------------ schemas
class Customer(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    phone: str = Field(default="", max_length=40, pattern=r"^[0-9+ ()-]*$")
    email: EmailStr | None = None


class Shipping(BaseModel):
    address: str = Field(min_length=5, max_length=1000)
    city: str = Field(min_length=2, max_length=100)
    province: str = Field(default="", max_length=100)
    postal_code: str = Field(default="", max_length=12, pattern=r"^[0-9A-Za-z -]*$")
    country: str = Field(default="ID", pattern=r"^[A-Z]{2}$")


class ItemIn(BaseModel):
    sku_id: UUID | None = None
    sku_code: str | None = Field(default=None, max_length=64)
    quantity: int = Field(gt=0, le=100000)
    unit_price: Money = Field(ge=0, le=Decimal("1000000000"), max_digits=14, decimal_places=2)

    @model_validator(mode="after")
    def _one_ref(self):
        if not self.sku_id and not self.sku_code:
            raise ValueError("Isi sku_id atau sku_code")
        return self


class OrderCreate(BaseModel):
    channel: str = Field(default="MANUAL", pattern=CHANNELS)
    external_ref: str | None = Field(default=None, min_length=1, max_length=100)
    customer: Customer
    shipping: Shipping
    items: list[ItemIn] = Field(min_length=1, max_length=200)
    shipping_fee: Money = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    discount: Money = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    notes: str = Field(default="", max_length=2000)
    paid: bool = Field(default=False, description="True bila pembayaran sudah terverifikasi (umum untuk marketplace)")


class ActionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class OrderSummary(BaseModel):
    id: UUID
    order_number: str
    channel: str
    external_ref: str | None
    status: str
    payment_status: str
    stock_status: str
    customer_name: str
    ship_city: str
    total: Money
    item_count: int
    warehouse_code: str | None
    placed_at: datetime


class ItemOut(BaseModel):
    id: UUID
    sku_id: UUID
    sku_code: str
    product_name: str
    variant_name: str
    quantity: int
    unit_price: Money
    line_total: Money


class ReservationOut(BaseModel):
    id: UUID
    sku_code: str
    warehouse_code: str
    quantity: int
    status: str
    expires_at: datetime | None


class HistoryOut(BaseModel):
    from_status: str | None
    to_status: str
    reason: str | None
    actor_user_id: UUID | None
    created_at: datetime


class ActionOut(BaseModel):
    name: str
    label: str
    needs_reason: bool


class OrderDetail(OrderSummary):
    allocation_note: str | None
    status_reason: str | None
    customer_phone: str
    customer_email: str
    ship_address: str
    ship_province: str
    ship_postal_code: str
    ship_country: str
    currency: str
    subtotal: Money
    shipping_fee: Money
    discount: Money
    notes: str
    paid_at: datetime | None
    allocated_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    items: list[ItemOut]
    reservations: list[ReservationOut]
    history: list[HistoryOut]
    actions: list[ActionOut]


# ------------------------------------------------------------------ helpers
async def _load(s: AsyncSession, p: Principal, order_id: UUID, lock: bool = False) -> Order:
    stmt = select(Order).where(Order.id == order_id, Order.tenant_id == p.tenant_id)
    if lock:
        stmt = stmt.with_for_update()
    o = await s.scalar(stmt)
    if o is None:
        raise AppError(404, "NOT_FOUND", "Order tidak ditemukan")
    return o


async def _detail(s: AsyncSession, p: Principal, o: Order) -> OrderDetail:
    skus = {x.id: x for x in (await s.scalars(select(Sku).where(
        Sku.tenant_id == p.tenant_id, Sku.id.in_([i.sku_id for i in o.items])))).all()}
    prods = {x.id: x for x in (await s.scalars(select(Product).where(
        Product.tenant_id == p.tenant_id, Product.id.in_({k.product_id for k in skus.values()})))).all()}
    whs = {w.id: w for w in (await s.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant_id))).all()}
    res = (await s.scalars(select(Reservation).where(Reservation.order_id == o.id)
                           .order_by(Reservation.created_at))).all()
    hist = (await s.scalars(select(OrderStatusHistory).where(OrderStatusHistory.order_id == o.id)
                            .order_by(OrderStatusHistory.id))).all()
    items = [ItemOut(id=i.id, sku_id=i.sku_id, sku_code=skus[i.sku_id].sku_code,
                     product_name=prods[skus[i.sku_id].product_id].name, variant_name=skus[i.sku_id].variant_name,
                     quantity=i.quantity, unit_price=i.unit_price, line_total=i.line_total) for i in o.items]
    return OrderDetail(
        id=o.id, order_number=o.order_number, channel=o.channel, external_ref=o.external_ref, status=o.status,
        payment_status=o.payment_status, stock_status=o.stock_status, customer_name=o.customer_name,
        ship_city=o.ship_city, total=o.total, item_count=sum(i.quantity for i in o.items),
        warehouse_code=whs[o.warehouse_id].code if o.warehouse_id in whs else None, placed_at=o.placed_at,
        allocation_note=o.allocation_note, status_reason=o.status_reason, customer_phone=o.customer_phone,
        customer_email=o.customer_email, ship_address=o.ship_address, ship_province=o.ship_province,
        ship_postal_code=o.ship_postal_code, ship_country=o.ship_country, currency=o.currency,
        subtotal=o.subtotal, shipping_fee=o.shipping_fee, discount=o.discount, notes=o.notes, paid_at=o.paid_at,
        allocated_at=o.allocated_at, shipped_at=o.shipped_at, delivered_at=o.delivered_at,
        cancelled_at=o.cancelled_at, items=items,
        reservations=[ReservationOut(id=r.id, sku_code=skus[r.sku_id].sku_code, warehouse_code=whs[r.warehouse_id].code,
                                     quantity=r.quantity, status=r.status, expires_at=r.expires_at) for r in res],
        history=[HistoryOut(from_status=h.from_status, to_status=h.to_status, reason=h.reason,
                            actor_user_id=h.actor_user_id, created_at=h.created_at) for h in hist],
        actions=[ActionOut(name=a.name, label=a.label, needs_reason=a.name in ("cancel", "mark_failed"))
                 for a in allowed_actions(o.status, o.stock_status, p.permissions)],
    )


# ------------------------------------------------------------------ endpoints
@router.get("", response_model=Page[OrderSummary])
async def list_orders(status: str | None = Query(None, max_length=20), channel: str | None = Query(None, max_length=20),
                      stock_status: str | None = Query(None, max_length=20), q: str | None = Query(None, max_length=100),
                      limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
                      p: Principal = Depends(require("order:read")), s: AsyncSession = Depends(get_session)):
    stmt = select(Order).where(Order.tenant_id == p.tenant_id).order_by(Order.placed_at.desc())
    if status:
        stmt = stmt.where(Order.status == status)
    if channel:
        stmt = stmt.where(Order.channel == channel)
    if stock_status:
        stmt = stmt.where(Order.stock_status == stock_status)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Order.order_number.ilike(like, escape="\\"), Order.external_ref.ilike(like, escape="\\"),
                              Order.customer_name.ilike(like, escape="\\")))
    rows, total = await paginate(s, stmt, limit, offset)
    whs = {w.id: w.code for w in (await s.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant_id))).all()}
    items = [OrderSummary(id=o.id, order_number=o.order_number, channel=o.channel, external_ref=o.external_ref,
                          status=o.status, payment_status=o.payment_status, stock_status=o.stock_status,
                          customer_name=o.customer_name, ship_city=o.ship_city, total=o.total,
                          item_count=sum(i.quantity for i in o.items), warehouse_code=whs.get(o.warehouse_id),
                          placed_at=o.placed_at) for o in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/stats")
async def order_stats(p: Principal = Depends(require("order:read")), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(Order.status, func.count()).where(Order.tenant_id == p.tenant_id)
                            .group_by(Order.status))).all()
    oos = await s.scalar(select(func.count()).select_from(Order).where(
        Order.tenant_id == p.tenant_id, Order.stock_status == "OUT_OF_STOCK",
        Order.status.in_(("CREATED", "PAID"))))
    return {"by_status": {k: v for k, v in rows}, "out_of_stock": oos or 0}


@router.post("", response_model=OrderDetail, status_code=201,
             responses={200: {"description": "Replay dari Idempotency-Key yang sama"}})
async def create_order(body: OrderCreate, p: Principal = Depends(require("order:write")),
                       s: AsyncSession = Depends(get_session),
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    key = None
    if idempotency_key:
        key = idempotency.validate_key(idempotency_key)
        replay = await idempotency.begin(s, p.tenant_id, "POST /orders", key,
                                         idempotency.body_hash(body.model_dump(mode="json")))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"],
                                headers={"Idempotent-Replayed": "true"})

    if body.external_ref:
        dup = await s.scalar(select(Order).where(Order.tenant_id == p.tenant_id, Order.channel == body.channel,
                                                 Order.external_ref == body.external_ref))
        if dup:
            raise AppError(409, "DUPLICATE_ORDER",
                           f"Order {body.channel} {body.external_ref} sudah ada sebagai {dup.order_number}")

    resolved = await service.resolve_items(s, p.tenant_id, body.items)

    ctx = Ctx.from_principal(p)
    o = await service.create_order(
        s, ctx, channel=body.channel, external_ref=body.external_ref, customer_name=body.customer.name,
        customer_phone=body.customer.phone, customer_email=(body.customer.email or "").lower(),
        ship_address=body.shipping.address, ship_city=body.shipping.city, ship_province=body.shipping.province,
        ship_postal_code=body.shipping.postal_code, ship_country=body.shipping.country, items=resolved,
        shipping_fee=body.shipping_fee, discount=body.discount, notes=body.notes, paid=body.paid)
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="order.created", entity_type="order",
                       entity_id=o.id, after={"order_number": o.order_number, "channel": o.channel,
                                              "external_ref": o.external_ref, "total": str(o.total),
                                              "stock_status": o.stock_status},
                       correlation_id=p.correlation_id, ip=p.ip)
    await s.flush()
    detail = await _detail(s, p, o)
    if key:
        await idempotency.complete(s, p.tenant_id, "POST /orders", key, 201, detail.model_dump(mode="json"))
    return detail


@router.get("/{order_id}", response_model=OrderDetail)
async def get_order(order_id: UUID, p: Principal = Depends(require("order:read")),
                    s: AsyncSession = Depends(get_session)):
    return await _detail(s, p, await _load(s, p, order_id))


@router.post("/{order_id}/actions/{action}", response_model=OrderDetail)
async def order_action(order_id: UUID, action: str, body: ActionIn | None = None,
                       p: Principal = Depends(require("order:read")), s: AsyncSession = Depends(get_session)):
    a = ACTIONS.get(action)
    if a is None:
        raise AppError(404, "UNKNOWN_ACTION", "Aksi tidak dikenal")
    if not p.can(a.permission):
        raise AppError(403, "FORBIDDEN", "Anda tidak memiliki izin untuk aksi ini")
    entitlements.check_permission(p.entitlement, a.permission)
    o = await _load(s, p, order_id, lock=True)
    await service.run_action(s, Ctx.from_principal(p), o, action, (body.reason if body else None))
    await s.flush()
    return await _detail(s, p, o)
