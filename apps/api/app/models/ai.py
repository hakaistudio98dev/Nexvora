from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class DemandForecast(Base):
    __tablename__ = "demand_forecasts"
    warehouse_id: Mapped[UUID] = mapped_column(primary_key=True)
    sku_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    method: Mapped[str] = mapped_column(String(20))
    daily_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    sigma: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    weekday_factor: Mapped[list] = mapped_column(JSONB, default=list)
    history_days: Mapped[int] = mapped_column(default=0)
    sold_30d: Mapped[int] = mapped_column(default=0)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}


class Anomaly(IdMixin, Base):
    __tablename__ = "anomalies"
    tenant_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10), default="WARNING")
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(default="")
    score: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=0)
    entity_type: Mapped[str | None] = mapped_column(String(20))
    entity_id: Mapped[UUID | None]
    entity_label: Mapped[str | None] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="OPEN")
    dedup_key: Mapped[str] = mapped_column(String(200))
    decided_by: Mapped[UUID | None]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}
