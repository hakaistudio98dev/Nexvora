"""Console operator platform (super admin): paket, langganan tenant, invoice."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, entitlements
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.models import Invoice, Plan, Subscription, Tenant
from app.modules.billing import service
from app.modules.billing.router import InvoiceOut, PlanOut, inv_out, plan_out

router = APIRouter(prefix="/platform", tags=["platform"])
ADMIN = require("tenant:manage")


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=500)
    price_monthly: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    price_yearly: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    features: list[str] | None = None
    limits: dict[str, int | None] | None = None
    is_public: bool | None = None
    self_serve: bool | None = None

    @field_validator("features")
    @classmethod
    def _f(cls, v):  # noqa: ANN001, ANN206
        bad = set(v or []) - set(entitlements.FEATURE_LABEL)
        if bad:
            raise ValueError(f"Fitur tidak dikenal: {', '.join(sorted(bad))}")
        return v

    @field_validator("limits")
    @classmethod
    def _l(cls, v):  # noqa: ANN001, ANN206
        bad = set(v or {}) - set(entitlements.LIMIT_LABEL)
        if bad:
            raise ValueError(f"Batas tidak dikenal: {', '.join(sorted(bad))}")
        if any(x is not None and x < 0 for x in (v or {}).values()):
            raise ValueError("Batas tidak boleh negatif")
        return v


class SubOverride(BaseModel):
    plan_code: str | None = Field(default=None, max_length=30)
    status: str | None = Field(default=None, pattern="^(TRIALING|ACTIVE|PAST_DUE|SUSPENDED|CANCELLED)$")
    billing_cycle: str | None = Field(default=None, pattern="^(MONTHLY|YEARLY)$")
    current_period_end: datetime | None = None
    trial_ends_at: datetime | None = None
    note: str = Field(min_length=3, max_length=300)


class MarkPaidIn(BaseModel):
    payment_ref: str = Field(min_length=3, max_length=120, description="No. referensi transfer / mutasi")


@router.get("/plans", response_model=list[PlanOut])
async def all_plans(p: Principal = Depends(ADMIN), s: AsyncSession = Depends(get_session)):
    return [plan_out(x) for x in (await s.scalars(select(Plan).order_by(Plan.sort_order))).all()]


@router.patch("/plans/{code}", response_model=PlanOut)
async def update_plan(code: str, body: PlanUpdate, p: Principal = Depends(ADMIN), s: AsyncSession = Depends(get_session)):
    plan = await s.get(Plan, code)
    if plan is None:
        raise AppError(404, "NOT_FOUND", "Paket tidak ditemukan")
    before = plan_out(plan).model_dump(mode="json")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(plan, k, v)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="plan.updated", entity_type="plan",
                       entity_id=code, before=before, after=plan_out(plan).model_dump(mode="json"),
                       correlation_id=p.correlation_id, ip=p.ip)
    return plan_out(plan)


@router.get("/subscriptions")
async def subscriptions(status: str | None = Query(None, max_length=12), p: Principal = Depends(ADMIN),
                        s: AsyncSession = Depends(get_session)):
    stmt = (select(Subscription, Tenant).join(Tenant, Tenant.id == Subscription.tenant_id)
            .order_by(Tenant.created_at.desc()).limit(500))
    if status:
        stmt = stmt.where(Subscription.status == status)
    out = []
    for sub, t in (await s.execute(stmt)).all():
        out.append({"tenant_id": str(t.id), "slug": t.slug, "name": t.name, "tenant_status": t.status,
                    "plan_code": sub.plan_code, "status": sub.status, "billing_cycle": sub.billing_cycle,
                    "trial_ends_at": sub.trial_ends_at, "current_period_end": sub.current_period_end,
                    "grace_until": sub.grace_until, "cancel_at_period_end": sub.cancel_at_period_end,
                    "usage": await entitlements.usage(s, t.id)})
    return out


@router.patch("/tenants/{tenant_id}/subscription")
async def override_subscription(tenant_id: UUID, body: SubOverride, p: Principal = Depends(ADMIN),
                                s: AsyncSession = Depends(get_session)):
    """Untuk kontrak Enterprise, kompensasi, atau perpanjangan manual."""
    sub = await s.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id).with_for_update())
    if sub is None:
        raise AppError(404, "NOT_FOUND", "Tenant belum punya langganan")
    if body.plan_code and await s.get(Plan, body.plan_code) is None:
        raise AppError(422, "UNKNOWN_PLAN", "Paket tidak ada")
    before = {"plan": sub.plan_code, "status": sub.status, "period_end": str(sub.current_period_end)}
    for k in ("plan_code", "status", "billing_cycle", "current_period_end", "trial_ends_at"):
        v = getattr(body, k)
        if v is not None:
            setattr(sub, k, v)
    if body.status == "ACTIVE":
        sub.grace_until = None
        if sub.current_period_start is None:
            sub.current_period_start = service.now()
    await audit.record(s, tenant_id=tenant_id, actor_user_id=p.user_id, action="subscription.overridden",
                       entity_type="subscription", entity_id=sub.id, before=before,
                       after={"plan": sub.plan_code, "status": sub.status, "period_end": str(sub.current_period_end),
                              "note": body.note}, correlation_id=p.correlation_id, ip=p.ip)
    return {"plan_code": sub.plan_code, "status": sub.status, "current_period_end": sub.current_period_end}


@router.get("/invoices", response_model=list[InvoiceOut])
async def invoices(status: str | None = Query("OPEN", max_length=8), p: Principal = Depends(ADMIN),
                   s: AsyncSession = Depends(get_session)):
    stmt = select(Invoice).order_by(Invoice.created_at.desc()).limit(500)
    if status:
        stmt = stmt.where(Invoice.status == status)
    return [inv_out(i) for i in (await s.scalars(stmt)).all()]


@router.post("/invoices/{invoice_id}/mark-paid", response_model=InvoiceOut)
async def mark_paid(invoice_id: UUID, body: MarkPaidIn, p: Principal = Depends(ADMIN),
                    s: AsyncSession = Depends(get_session)):
    inv = await s.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
    if inv is None:
        raise AppError(404, "NOT_FOUND", "Invoice tidak ditemukan")
    await service.apply_payment(s, inv, ref=body.payment_ref, actor=p.user_id)
    return inv_out(inv)


@router.get("/noc")
async def platform_noc(p: Principal = Depends(ADMIN), s: AsyncSession = Depends(get_session)):
    """Kesehatan sistem lintas tenant untuk operator platform."""
    import time  # noqa: PLC0415

    from sqlalchemy import text  # noqa: PLC0415

    from app.core import ratelimit  # noqa: PLC0415
    t0 = time.perf_counter()
    await s.execute(text("SELECT 1"))
    db_ms = round((time.perf_counter() - t0) * 1000, 1)
    redis_status = "not_configured"
    r = ratelimit._get_redis()
    if r is not None:
        try:
            await r.ping()
            redis_status = "ok"
        except Exception:  # noqa: BLE001
            redis_status = "down"
    hb = (await s.execute(text("SELECT beat_at, info FROM system_heartbeats WHERE name = 'worker'"))).first()
    now = service.now()
    q1 = lambda sql: s.execute(text(sql))  # noqa: E731
    subs = dict((await q1("SELECT status, count(*) FROM subscriptions GROUP BY status")).all())
    deliv = (await q1("""SELECT count(*) FILTER (WHERE status = 'PENDING') pending,
                                count(*) FILTER (WHERE status = 'FAILED' AND created_at > now() - interval '24 hours') failed
                         FROM notification_deliveries""")).one()
    orders24 = (await q1("SELECT count(*) FROM orders WHERE placed_at > now() - interval '24 hours'")).scalar()
    overdue_res = (await q1("""SELECT count(*) FROM reservations WHERE status = 'ACTIVE' AND expires_at < now() - interval '5 minutes'""")).scalar()
    worker_age = (now - hb.beat_at).total_seconds() if hb else None
    return {
        "database": {"status": "ok", "latency_ms": db_ms},
        "redis": {"status": redis_status},
        "worker": {"status": "ok" if worker_age is not None and worker_age < 120 else "down",
                   "seconds_since_beat": int(worker_age) if worker_age is not None else None,
                   "last_run": hb.info if hb else None},
        "queues": {"notification_pending": deliv.pending, "notification_failed_24h": deliv.failed,
                   "reservations_expiry_backlog": overdue_res},
        "tenants": {"by_subscription": subs, "total": sum(subs.values())},
        "orders_24h": orders24,
    }
