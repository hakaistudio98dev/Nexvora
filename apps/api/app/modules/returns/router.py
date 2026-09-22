
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Ctx
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.models import Order, Product, Return, ReturnLine, Sku
from app.modules.returns import service

router = APIRouter(prefix="/returns", tags=["returns"])
REASONS = {"DAMAGED": "Barang rusak", "WRONG_ITEM": "Barang salah", "NOT_AS_DESCRIBED": "Tidak sesuai deskripsi",
           "CHANGED_MIND": "Berubah pikiran", "UNDELIVERED": "Gagal kirim / dikembalikan kurir", "OTHER": "Lainnya"}


class LineIn(BaseModel):
    order_item_id: UUID
    quantity: int = Field(gt=0, le=100_000)


class ReturnCreate(BaseModel):
    order_id: UUID
    reason_code: str = Field(pattern="^(DAMAGED|WRONG_ITEM|NOT_AS_DESCRIBED|CHANGED_MIND|OTHER)$")
    note: str = Field(default="", max_length=2000)
    return_tracking: str | None = Field(default=None, max_length=60)
    lines: list[LineIn] = Field(min_length=1, max_length=200)


class DecideIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


class ReceiveLine(BaseModel):
    line_id: UUID
    received_qty: int = Field(ge=0, le=100_000)


class InspectLine(BaseModel):
    line_id: UUID
    restock_qty: int = Field(ge=0, le=100_000)
    damaged_qty: int = Field(ge=0, le=100_000)


class ResolveIn(BaseModel):
    resolution: str = Field(pattern="^(REFUND|REPLACEMENT|NONE)$")
    refund_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    refund_ref: str | None = Field(default=None, max_length=120)


async def _out(s: AsyncSession, r: Return) -> dict:
    order = await s.get(Order, r.order_id)
    lines = (await s.scalars(select(ReturnLine).where(ReturnLine.return_id == r.id))).all()
    skus = {k.id: (k, n) for k, n in (await s.execute(select(Sku, Product.name).join(Product, Product.id == Sku.product_id)
                                                      .where(Sku.id.in_([x.sku_id for x in lines])))).all()}
    repl = await s.get(Order, r.replacement_order_id) if r.replacement_order_id else None
    return {"id": str(r.id), "number": r.number, "status": r.status, "reason_code": r.reason_code,
            "reason_label": REASONS.get(r.reason_code), "note": r.note, "return_tracking": r.return_tracking,
            "order_id": str(order.id), "order_number": order.order_number, "customer_name": order.customer_name,
            "order_total": str(order.total), "resolution": r.resolution,
            "refund_amount": str(r.refund_amount) if r.refund_amount is not None else None,
            "refund_ref": r.refund_ref, "replacement_order_number": repl.order_number if repl else None,
            "created_at": r.created_at, "received_at": r.received_at, "closed_at": r.closed_at,
            "lines": [{"id": str(x.id), "sku_code": skus[x.sku_id][0].sku_code, "product_name": skus[x.sku_id][1],
                       "quantity": x.quantity, "received_qty": x.received_qty, "restock_qty": x.restock_qty,
                       "damaged_qty": x.damaged_qty} for x in lines]}


async def _get(s: AsyncSession, p: Principal, rid: UUID, lock: bool = False) -> Return:
    stmt = select(Return).where(Return.id == rid, Return.tenant_id == p.tenant_id)
    r = await s.scalar(stmt.with_for_update() if lock else stmt)
    if r is None:
        raise AppError(404, "NOT_FOUND", "Retur tidak ditemukan")
    return r


@router.get("/reasons")
async def reasons(p: Principal = Depends(require("returns:read"))):
    return [{"code": k, "label": v} for k, v in REASONS.items() if k != "UNDELIVERED"]


@router.get("")
async def list_returns(status: str | None = Query(None, max_length=12), q: str | None = Query(None, max_length=60),
                       p: Principal = Depends(require("returns:read")), s: AsyncSession = Depends(get_session)):
    stmt = (select(Return).join(Order, Order.id == Return.order_id).where(Return.tenant_id == p.tenant_id)
            .order_by(Return.created_at.desc()).limit(200))
    if status == "OPEN":
        stmt = stmt.where(Return.status.in_(service.OPEN))
    elif status:
        stmt = stmt.where(Return.status == status)
    if q:
        stmt = stmt.where(or_(Return.number == q.upper(), Order.order_number == q.upper()))
    return [await _out(s, r) for r in (await s.scalars(stmt)).all()]


@router.get("/{return_id}")
async def get_return(return_id: UUID, p: Principal = Depends(require("returns:read")), s: AsyncSession = Depends(get_session)):
    return await _out(s, await _get(s, p, return_id))


@router.post("", status_code=201)
async def create(body: ReturnCreate, p: Principal = Depends(require("returns:write")), s: AsyncSession = Depends(get_session)):
    order = await s.scalar(select(Order).where(Order.id == body.order_id, Order.tenant_id == p.tenant_id).with_for_update())
    if order is None:
        raise AppError(404, "NOT_FOUND", "Order tidak ditemukan")
    r = await service.create(s, Ctx.from_principal(p), order, [(x.order_item_id, x.quantity) for x in body.lines],
                             body.reason_code, body.note, body.return_tracking)
    return await _out(s, r)


@router.post("/{return_id}/decide")
async def decide(return_id: UUID, body: DecideIn, p: Principal = Depends(require("returns:write")),
                 s: AsyncSession = Depends(get_session)):
    r = await _get(s, p, return_id, lock=True)
    await service.decide(s, Ctx.from_principal(p), r, body.approve, body.note)
    return await _out(s, r)


@router.post("/{return_id}/receive")
async def receive(return_id: UUID, lines: list[ReceiveLine], p: Principal = Depends(require("returns:receive")),
                  s: AsyncSession = Depends(get_session)):
    r = await _get(s, p, return_id, lock=True)
    await service.receive(s, Ctx.from_principal(p), r, {x.line_id: x.received_qty for x in lines})
    return await _out(s, r)


@router.post("/{return_id}/inspect")
async def inspect(return_id: UUID, lines: list[InspectLine], p: Principal = Depends(require("returns:receive")),
                  s: AsyncSession = Depends(get_session)):
    r = await _get(s, p, return_id, lock=True)
    tasks = await service.inspect(s, Ctx.from_principal(p), r, {x.line_id: (x.restock_qty, x.damaged_qty) for x in lines})
    return {**await _out(s, r), "putaway_tasks": tasks}


@router.post("/{return_id}/resolve")
async def resolve(return_id: UUID, body: ResolveIn, p: Principal = Depends(require("returns:write")),
                  s: AsyncSession = Depends(get_session)):
    r = await _get(s, p, return_id, lock=True)
    await service.resolve(s, Ctx.from_principal(p), r, body.resolution, body.refund_amount, body.refund_ref)
    return await _out(s, r)


