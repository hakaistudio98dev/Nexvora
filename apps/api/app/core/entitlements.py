"""Hak pakai berdasarkan paket langganan.

- Fitur dipetakan dari prefix izin (mis. semua `wms:*` butuh fitur `wms`), jadi endpoint baru
  otomatis ikut terkunci tanpa perlu diingat satu per satu.
- Langganan SUSPENDED/CANCELLED = mode baca-saja: semua aksi tulis ditolak kecuali billing,
  agar tenant tetap bisa melihat & mengekspor data serta membayar.
"""
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Plan, Sku, Subscription, User, Warehouse

FEATURE_BY_PREFIX = {"wms": "wms", "order": "oms", "inventory": "oms", "apikey": "api_keys",
                     "shipping": "oms", "returns": "oms", "analytics": "oms", "notification": "oms"}
FEATURE_LABEL = {"oms": "Order & Inventory", "wms": "Gudang (WMS) & scanner", "api_keys": "API key integrasi",
                 "shipping_integration": "Integrasi kurir otomatis",
                 "analytics": "Analitik lanjutan (SLA, produktivitas, kesehatan stok, NOC)",
                 "webhooks": "Webhook notifikasi",
                 "priority_support": "Dukungan prioritas"}
WRITE_ACTIONS = {"write", "manage", "operate", "adjust", "fulfill"}
READONLY_EXEMPT = {"billing"}
LIMIT_LABEL = {"warehouses": "gudang aktif", "users": "pengguna aktif", "skus": "SKU aktif",
               "orders_per_month": "order per bulan"}
FEATURE_MIN_PLAN = {"wms": "Growth", "api_keys": "Growth", "shipping_integration": "Growth", "analytics": "Growth",
                    "webhooks": "Growth"}


@dataclass
class Entitlement:
    plan_code: str | None
    plan_name: str
    status: str
    features: set[str] = field(default_factory=set)
    limits: dict = field(default_factory=dict)
    read_only: bool = False
    unrestricted: bool = False
    trial_ends_at: datetime | None = None
    current_period_end: datetime | None = None
    grace_until: datetime | None = None
    cancel_at_period_end: bool = False

    def as_dict(self) -> dict:
        return {"plan_code": self.plan_code, "plan_name": self.plan_name, "status": self.status,
                "features": sorted(self.features), "limits": self.limits, "read_only": self.read_only,
                "unrestricted": self.unrestricted, "trial_ends_at": self.trial_ends_at,
                "current_period_end": self.current_period_end, "grace_until": self.grace_until,
                "cancel_at_period_end": self.cancel_at_period_end}


UNRESTRICTED = Entitlement(plan_code=None, plan_name="Platform", status="ACTIVE",
                           features=set(FEATURE_LABEL), unrestricted=True)


async def load(session: AsyncSession, tenant_id: UUID, *, superadmin: bool) -> Entitlement:
    if superadmin:
        return UNRESTRICTED
    sub = await session.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    if sub is None:
        return Entitlement(plan_code=None, plan_name="Tanpa paket", status="NONE", read_only=True)
    plan = await session.get(Plan, sub.plan_code)
    return Entitlement(
        plan_code=plan.code, plan_name=plan.name, status=sub.status, features=set(plan.features or []),
        limits=dict(plan.limits or {}), read_only=sub.status in ("SUSPENDED", "CANCELLED"),
        trial_ends_at=sub.trial_ends_at, current_period_end=sub.current_period_end, grace_until=sub.grace_until,
        cancel_at_period_end=sub.cancel_at_period_end)


def check_permission(ent: Entitlement, perm: str) -> None:
    prefix, _, action = perm.partition(":")
    if ent.unrestricted:
        return
    feat = FEATURE_BY_PREFIX.get(prefix)
    if feat and feat not in ent.features:
        need = FEATURE_MIN_PLAN.get(feat)
        raise AppError(402, "FEATURE_NOT_IN_PLAN",
                       f"Fitur {FEATURE_LABEL.get(feat, feat)} tidak termasuk paket {ent.plan_name}."
                       + (f" Tersedia mulai paket {need}." if need else "") + " Upgrade di menu Langganan.")
    if ent.read_only and action in WRITE_ACTIONS and prefix not in READONLY_EXEMPT:
        raise AppError(402, "SUBSCRIPTION_INACTIVE",
                       "Langganan tidak aktif, akun dalam mode baca-saja. Selesaikan pembayaran di menu Langganan "
                       "untuk mengaktifkan kembali. Data Anda tetap aman.")


async def usage(session: AsyncSession, tenant_id: UUID) -> dict:
    orders_month = await session.scalar(text("""
        SELECT count(*) FROM orders WHERE tenant_id = :t
          AND placed_at >= date_trunc('month', now() AT TIME ZONE 'Asia/Jakarta') AT TIME ZONE 'Asia/Jakarta'
    """), {"t": tenant_id})
    return {
        "warehouses": await session.scalar(select(func.count()).select_from(Warehouse).where(
            Warehouse.tenant_id == tenant_id, Warehouse.is_active.is_(True))) or 0,
        "users": await session.scalar(select(func.count()).select_from(User).where(
            User.tenant_id == tenant_id, User.is_active.is_(True))) or 0,
        "skus": await session.scalar(select(func.count()).select_from(Sku).where(
            Sku.tenant_id == tenant_id, Sku.is_active.is_(True))) or 0,
        "orders_per_month": int(orders_month or 0),
    }


async def enforce_limit(session: AsyncSession, p, key: str, adding: int = 1) -> None:  # noqa: ANN001
    """Batas keras untuk data master (gudang, pengguna, SKU). Order TIDAK memakai ini: kuota order
    bersifat lunak agar order marketplace tidak pernah ditolak di tengah operasional."""
    ent: Entitlement = p.entitlement
    if ent.unrestricted:
        return
    limit = ent.limits.get(key)
    if limit is None:
        return
    current = (await usage(session, p.tenant_id))[key]
    if current + adding > limit:
        raise AppError(402, "PLAN_LIMIT",
                       f"Paket {ent.plan_name} dibatasi {limit} {LIMIT_LABEL[key]} (terpakai {current}). "
                       "Upgrade paket di menu Langganan atau nonaktifkan data yang tidak dipakai.")


def require_feature(p, feature: str) -> None:  # noqa: ANN001
    """Untuk endpoint yang fiturnya tidak bisa diturunkan dari prefix izin."""
    ent: Entitlement = p.entitlement
    if ent.unrestricted or feature in ent.features:
        return
    need = FEATURE_MIN_PLAN.get(feature)
    raise AppError(402, "FEATURE_NOT_IN_PLAN", f"Fitur {FEATURE_LABEL.get(feature, feature)} tidak termasuk paket "
                                               f"{ent.plan_name}." + (f" Tersedia mulai paket {need}." if need else ""))
