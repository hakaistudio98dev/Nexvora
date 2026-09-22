import calendar
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.config import get_settings
from app.core.errors import AppError
from app.models import Invoice, Plan, Subscription
from app.modules.wms.common import next_doc_number


def now() -> datetime:
    return datetime.now(UTC)


def add_period(start: datetime, cycle: str) -> datetime:
    months = 12 if cycle == "YEARLY" else 1
    m = start.month - 1 + months
    y, m = start.year + m // 12, m % 12 + 1
    return start.replace(year=y, month=m, day=min(start.day, calendar.monthrange(y, m)[1]))


def price(plan: Plan, cycle: str) -> Decimal:
    return plan.price_yearly if cycle == "YEARLY" else plan.price_monthly


async def start_trial(s: AsyncSession, tenant_id: UUID, plan_code: str | None = None) -> Subscription:
    st = get_settings()
    code = plan_code or st.trial_plan
    plan = await s.get(Plan, code)
    if plan is None:
        raise AppError(422, "UNKNOWN_PLAN", f"Paket {code} tidak ada")
    sub = Subscription(tenant_id=tenant_id, plan_code=plan.code, status="TRIALING",
                       trial_ends_at=now() + timedelta(days=st.trial_days))
    s.add(sub)
    await s.flush()
    return sub


async def create_invoice(s: AsyncSession, tenant_id: UUID, plan: Plan, cycle: str, kind: str,
                         created_by: UUID | None, due_in_days: int = 3) -> Invoice:
    amount = price(plan, cycle)
    if amount <= 0:
        raise AppError(422, "CONTACT_SALES", f"Paket {plan.name} memakai harga kontrak. Hubungi tim sales.")
    inv = Invoice(tenant_id=tenant_id, number=await next_doc_number(s, tenant_id, "INV"), kind=kind,
                  plan_code=plan.code, billing_cycle=cycle, amount=amount, due_at=now() + timedelta(days=due_in_days),
                  provider=get_settings().billing_provider, created_by=created_by)
    s.add(inv)
    await s.flush()
    return inv


async def apply_payment(s: AsyncSession, inv: Invoice, *, ref: str | None, actor: UUID | None) -> Subscription:
    """Idempoten: invoice yang sudah PAID tidak memperpanjang langganan dua kali."""
    sub = await s.scalar(select(Subscription).where(Subscription.tenant_id == inv.tenant_id).with_for_update())
    if inv.status == "PAID":
        return sub
    if inv.status not in ("OPEN", "EXPIRED"):
        raise AppError(409, "INVOICE_CLOSED", f"Invoice {inv.number} berstatus {inv.status}")
    t = now()
    inv.status, inv.paid_at, inv.payment_ref = "PAID", t, ref
    before = {"plan": sub.plan_code, "status": sub.status, "period_end": str(sub.current_period_end)}
    renew_from_end = (inv.kind == "RENEWAL" and sub.status == "ACTIVE" and sub.current_period_end
                      and sub.current_period_end > t)
    start = sub.current_period_end if renew_from_end else t
    sub.plan_code, sub.billing_cycle = inv.plan_code, inv.billing_cycle
    sub.current_period_start, sub.current_period_end = start, add_period(start, inv.billing_cycle)
    sub.status, sub.grace_until, sub.cancel_at_period_end = "ACTIVE", None, False
    await audit.record(s, tenant_id=inv.tenant_id, actor_user_id=actor, action="subscription.paid",
                       entity_type="invoice", entity_id=inv.id, before=before,
                       after={"invoice": inv.number, "plan": sub.plan_code, "period_end": str(sub.current_period_end),
                              "ref": ref})
    await s.flush()
    return sub


async def run_lifecycle(s: AsyncSession) -> dict:
    """Dipanggil worker dengan konteks superadmin (lintas tenant)."""
    st, t = get_settings(), now()
    counts = {"trial_ended": 0, "past_due": 0, "suspended": 0, "cancelled": 0, "renewal_invoices": 0}
    subs = (await s.scalars(select(Subscription).where(
        Subscription.status.in_(("TRIALING", "ACTIVE", "PAST_DUE"))).with_for_update(skip_locked=True))).all()
    for sub in subs:
        if sub.status == "TRIALING" and sub.trial_ends_at and sub.trial_ends_at <= t:
            sub.status, sub.grace_until = "PAST_DUE", t + timedelta(days=st.grace_days)
            counts["trial_ended"] += 1
        elif sub.status == "ACTIVE" and sub.current_period_end and sub.current_period_end <= t:
            if sub.cancel_at_period_end:
                sub.status = "CANCELLED"
                counts["cancelled"] += 1
            else:
                sub.status, sub.grace_until = "PAST_DUE", sub.current_period_end + timedelta(days=st.grace_days)
                counts["past_due"] += 1
        elif sub.status == "PAST_DUE" and sub.grace_until and sub.grace_until <= t:
            sub.status = "SUSPENDED"
            counts["suspended"] += 1
        # Tagihan perpanjangan dibuat beberapa hari sebelum periode habis
        if (sub.status in ("ACTIVE", "PAST_DUE") and not sub.cancel_at_period_end and sub.current_period_end
                and sub.current_period_end - timedelta(days=st.renewal_notice_days) <= t):
            has_open = await s.scalar(select(Invoice.id).where(Invoice.tenant_id == sub.tenant_id,
                                                               Invoice.status == "OPEN").limit(1))
            plan = await s.get(Plan, sub.plan_code)
            if not has_open and plan and price(plan, sub.billing_cycle) > 0:
                await create_invoice(s, sub.tenant_id, plan, sub.billing_cycle, "RENEWAL", None,
                                     due_in_days=max(1, (sub.current_period_end - t).days))
                counts["renewal_invoices"] += 1
    await s.flush()
    return counts
