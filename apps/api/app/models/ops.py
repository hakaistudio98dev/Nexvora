from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class TenantSettings(Base):
    __tablename__ = "tenant_settings"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    sla_ship_hours: Mapped[int] = mapped_column(default=24)
    sla_risk_hours: Mapped[int] = mapped_column(default=4)
    low_stock_threshold: Mapped[int] = mapped_column(default=5)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Jakarta")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __mapper_args__ = {"eager_defaults": True}


class Notification(IdMixin, Base):
    __tablename__ = "notifications"
    tenant_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10), default="INFO")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    link: Mapped[str | None] = mapped_column(String(300))
    in_app: Mapped[bool] = mapped_column(default=True)
    dedup_key: Mapped[str | None] = mapped_column(String(200))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}


class NotificationChannel(IdMixin, Base):
    __tablename__ = "notification_channels"
    tenant_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(500))
    secret_enc: Mapped[str | None]
    events: Mapped[list] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}


class NotificationDelivery(IdMixin, Base):
    __tablename__ = "notification_deliveries"
    tenant_id: Mapped[UUID]
    notification_id: Mapped[UUID]
    channel_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(10), default="PENDING")
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    response_code: Mapped[int | None]
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}
