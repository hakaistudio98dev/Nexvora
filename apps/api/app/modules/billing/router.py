from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, entitlements
from app.core.config import get_settings
from app.core.db import get_session, set_tenant_context
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.models import Invoice, Plan, Subscription, User
from app.modules.billing import midtrans, service

router = APIRouter(prefix="/billing", tags=["billing"])


class PlanOut(BaseModel):
    code: str
    name: str
    description: str
    price_monthly: Decimal
    price_yearly: Decimal
    features: list[str]
    limits: dict
    self_serve: bool


class InvoiceOut(BaseModel):
    id: UUID
    number: str
    kind: str
    plan_code: str
    billing_cycle: str
    amount: Decimal
    currency: str
    status: str
    due_at: datetime
    paid_at: datetime | None
    provider: str
    payment_url: str | None
    payment_ref: str | None
    created_at: datetime


class CheckoutIn(BaseModel):
    plan_code: str = Field(max_length=30)
    billing_cycle: str = Field(default="MONTHLY", pattern="^(MONTHLY|YEARLY)$")


def plan_out(p: Plan) -> PlanOut:
    return PlanOut(code=p.code, name=p.name, description=p.description, price_monthly=p.price_monthly,
                   price_yearly=p.price_yearly, features=list(p.features or []), limits=dict(p.limits or {}),
                   self_serve=p.self_serve)


def inv_out(i: Invoice) -> InvoiceOut:
    return InvoiceOut.model_validate(i, from_attributes=True)


async def public_plans(s: AsyncSession) -> list[PlanOut]:
    return [plan_out(p) for p in (await s.scalars(select(Plan).where(Plan.is_public.is_(True))
                                                   .order_by(Plan.sort_order))).all()]


@router.get("/plans", response_model=list[PlanOut])
async def plans(p: Principal = Depends(require("billing:read")), s: AsyncSession = Depends(get_session)):
    return await public_plans(s)


@router.get("/subscription")
async def subscription(p: Principal = Depends(require("billing:read")), s: AsyncSession = Depends(get_session)):
    ent = p.entitlement
    use = await entitlements.usage(s, p.tenant_id)
    warnings = []
    for k, v in use.items():
        lim = ent.limits.get(k)
        if lim:
            if v > lim:
                warnings.append(f"Pemakaian {entitlements.LIMIT_LABEL[k]} melebihi kuota ({v}/{lim}).")
            elif v >= lim * 0.8:
                warnings.append(f"Pemakaian {entitlements.LIMIT_LABEL[k]} sudah {round(v * 100 / lim)}% dari kuota.")
    open_inv = (await s.scalars(select(Invoice).where(Invoice.tenant_id == p.tenant_id, Invoice.status == "OPEN")
                                .order_by(Invoice.created_at.desc()))).all()
    return {"subscription": ent.as_dict(), "usage": use, "warnings": warnings,
            "open_invoices": [inv_out(i) for i in open_inv],
            "provider": get_settings().billing_provider, "bank_transfer_info": get_settings().bank_transfer_info}


@router.get("/invoices", response_model=list[InvoiceOut])
async def invoices(p: Principal = Depends(require("billing:read")), s: AsyncSession = Depends(get_session)):
    return [inv_out(i) for i in (await s.scalars(select(Invoice).where(Invoice.tenant_id == p.tenant_id)
                                                 .order_by(Invoice.created_at.desc()).limit(100))).all()]


async def _payment_link(s: AsyncSession, p: Principal, inv: Invoice) -> None:
    if inv.provider == "midtrans" and not inv.payment_url:
        user = await s.get(User, p.user_id)
        plan = await s.get(Plan, inv.plan_code)
        inv.payment_url = await midtrans.create_payment(inv, plan_name=plan.name, email=user.email, name=user.full_name)


@router.post("/checkout", response_model=InvoiceOut, status_code=201)
async def checkout(body: CheckoutIn, p: Principal = Depends(require("billing:manage")),
                   s: AsyncSession = Depends(get_session)):
    if not p.is_human or p.entitlement.unrestricted:
        raise AppError(422, "NOT_APPLICABLE", "Akun platform tidak berlangganan")
    plan = await s.get(Plan, body.plan_code)
    if plan is None or not plan.is_public:
        raise AppError(404, "UNKNOWN_PLAN", "Paket tidak ditemukan")
    if not plan.self_serve:
        raise AppError(422, "CONTACT_SALES", f"Paket {plan.name} memakai kontrak. Hubungi tim sales.")
    use = await entitlements.usage(s, p.tenant_id)
    over = [f"{entitlements.LIMIT_LABEL[k]} {use[k]}/{lim}" for k, lim in (plan.limits or {}).items()
            if lim is not None and k != "orders_per_month" and use.get(k, 0) > lim]
    if over:
        raise AppError(409, "USAGE_EXCEEDS_PLAN", f"Pemakaian saat ini melebihi batas paket {plan.name}: "
                                                  + ", ".join(over) + ". Nonaktifkan data yang tidak dipakai dulu.")
    sub = await s.scalar(select(Subscription).where(Subscription.tenant_id == p.tenant_id).with_for_update())
    kind = "NEW"
    if sub.status == "ACTIVE":
        kind = "RENEWAL" if (sub.plan_code, sub.billing_cycle) == (plan.code, body.billing_cycle) else "CHANGE"
    # Invoice terbuka sebelumnya digantikan oleh pilihan terbaru
    await s.execute(update(Invoice).where(Invoice.tenant_id == p.tenant_id, Invoice.status == "OPEN")
                    .values(status="VOID"))
    inv = await service.create_invoice(s, p.tenant_id, plan, body.billing_cycle, kind, p.user_id)
    await _payment_link(s, p, inv)
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="billing.checkout",
                       entity_type="invoice", entity_id=inv.id,
                       after={"number": inv.number, "plan": plan.code, "cycle": body.billing_cycle,
                              "amount": str(inv.amount), "kind": kind}, correlation_id=p.correlation_id, ip=p.ip)
    return inv_out(inv)


@router.post("/invoices/{invoice_id}/pay", response_model=InvoiceOut)
async def pay_invoice(invoice_id: UUID, p: Principal = Depends(require("billing:manage")),
                      s: AsyncSession = Depends(get_session)):
    inv = await s.scalar(select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == p.tenant_id)
                         .with_for_update())
    if inv is None:
        raise AppError(404, "NOT_FOUND", "Invoice tidak ditemukan")
    if inv.status != "OPEN":
        raise AppError(409, "INVOICE_CLOSED", f"Invoice {inv.number} berstatus {inv.status}")
    await _payment_link(s, p, inv)
    return inv_out(inv)


@router.post("/cancel")
async def cancel(p: Principal = Depends(require("billing:manage")), s: AsyncSession = Depends(get_session)):
    sub = await s.scalar(select(Subscription).where(Subscription.tenant_id == p.tenant_id).with_for_update())
    if sub is None or sub.status != "ACTIVE":
        raise AppError(409, "NOT_ACTIVE", "Hanya langganan aktif yang bisa dihentikan di akhir periode")
    sub.cancel_at_period_end = True
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="subscription.cancel_requested",
                       entity_type="subscription", entity_id=sub.id, correlation_id=p.correlation_id, ip=p.ip)
    return {"cancel_at_period_end": True, "current_period_end": sub.current_period_end}


@router.post("/resume")
async def resume(p: Principal = Depends(require("billing:manage")), s: AsyncSession = Depends(get_session)):
    sub = await s.scalar(select(Subscription).where(Subscription.tenant_id == p.tenant_id).with_for_update())
    if sub is None or sub.status != "ACTIVE":
        raise AppError(409, "NOT_ACTIVE", "Langganan tidak aktif")
    sub.cancel_at_period_end = False
    return {"cancel_at_period_end": False}


@router.post("/webhooks/midtrans", include_in_schema=False)
async def midtrans_webhook(request: Request, s: AsyncSession = Depends(get_session)):
    """Dipanggil server Midtrans. Keaslian dicek lewat signature SHA-512 + nominal harus sama."""
    payload = await request.json()
    if not midtrans.verify_signature(payload):
        raise AppError(403, "BAD_SIGNATURE", "Signature tidak valid")
    try:
        inv_id = UUID(str(payload.get("order_id")))
    except ValueError:
        return {"ok": True}
    await set_tenant_context(s, None, superadmin=True)
    inv = await s.scalar(select(Invoice).where(Invoice.id == inv_id).with_for_update())
    if inv is None:
        return {"ok": True}
    await set_tenant_context(s, inv.tenant_id)  # sisa transaksi dibatasi ke tenant pemilik invoice
    result = midtrans.outcome(payload)
    if result == "PAID":
        if Decimal(str(payload.get("gross_amount", "0"))) != inv.amount:
            raise AppError(422, "AMOUNT_MISMATCH", "Nominal pembayaran tidak sesuai invoice")
        await service.apply_payment(s, inv, ref=str(payload.get("transaction_id", "")), actor=None)
    elif result == "FAILED" and inv.status == "OPEN":
        inv.status, inv.payment_url = "EXPIRED", None
    return {"ok": True}
