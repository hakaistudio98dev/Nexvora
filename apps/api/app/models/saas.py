from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class Plan(Base):
    __tablename__ = "plans"
    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(default="")
    price_monthly: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    price_yearly: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    features: Mapped[list] = mapped_column(JSONB, default=list)
    limits: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_public: Mapped[bool] = mapped_column(default=True)
    self_serve: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __mapper_args__ = {"eager_defaults": True}


class Subscription(IdMixin, Base):
    __tablename__ = "subscriptions"
    tenant_id: Mapped[UUID]
    plan_code: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(12))
    billing_cycle: Mapped[str] = mapped_column(String(8), default="MONTHLY")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __mapper_args__ = {"eager_defaults": True}


class Invoice(IdMixin, Base):
    __tablename__ = "invoices"
    tenant_id: Mapped[UUID]
    number: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(10))
    plan_code: Mapped[str] = mapped_column(String(30))
    billing_cycle: Mapped[str] = mapped_column(String(8))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="IDR")
    status: Mapped[str] = mapped_column(String(8), default="OPEN")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(20), default="manual")
    payment_url: Mapped[str | None]
    payment_ref: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __mapper_args__ = {"eager_defaults": True}


class ApiKey(IdMixin, Base):
    __tablename__ = "api_keys"
    tenant_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[UUID | None]
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}
