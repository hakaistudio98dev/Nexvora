from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class CourierAccount(IdMixin, Base):
    __tablename__ = "courier_accounts"
    tenant_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(20))
    couriers: Mapped[list] = mapped_column(JSONB, default=list)
    credentials_enc: Mapped[str | None]
    webhook_token: Mapped[str] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}


class Manifest(IdMixin, Base):
    __tablename__ = "manifests"
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    number: Mapped[str] = mapped_column(String(32))
    courier_code: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(12), default="OPEN")
    driver_name: Mapped[str | None] = mapped_column(String(120))
    vehicle_plate: Mapped[str | None] = mapped_column(String(20))
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}


class Shipment(IdMixin, Base):
    __tablename__ = "shipments"
    tenant_id: Mapped[UUID]
    order_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    courier_account_id: Mapped[UUID]
    provider: Mapped[str] = mapped_column(String(20))
    courier_code: Mapped[str] = mapped_column(String(30))
    service_code: Mapped[str] = mapped_column(String(40), default="")
    provider_ref: Mapped[str | None] = mapped_column(String(100))
    tracking_number: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="CREATED")
    cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    weight_g: Mapped[int | None]
    manifest_id: Mapped[UUID | None]
    label_printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_tracked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __mapper_args__ = {"eager_defaults": True}


class TrackingEvent(Base):
    __tablename__ = "tracking_events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tenant_id: Mapped[UUID]
    shipment_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}


class Return(IdMixin, Base):
    __tablename__ = "returns"
    tenant_id: Mapped[UUID]
    number: Mapped[str] = mapped_column(String(32))
    order_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(12), default="REQUESTED")
    reason_code: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(default="")
    return_tracking: Mapped[str | None] = mapped_column(String(60))
    order_status_before: Mapped[str] = mapped_column(String(20))
    resolution: Mapped[str | None] = mapped_column(String(12))
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    refund_ref: Mapped[str | None] = mapped_column(String(120))
    replacement_order_id: Mapped[UUID | None]
    created_by: Mapped[UUID | None]
    decided_by: Mapped[UUID | None]
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __mapper_args__ = {"eager_defaults": True}


class ReturnLine(IdMixin, Base):
    __tablename__ = "return_lines"
    tenant_id: Mapped[UUID]
    return_id: Mapped[UUID]
    order_item_id: Mapped[UUID]
    sku_id: Mapped[UUID]
    quantity: Mapped[int]
    received_qty: Mapped[int] = mapped_column(default=0)
    restock_qty: Mapped[int] = mapped_column(default=0)
    damaged_qty: Mapped[int] = mapped_column(default=0)


class CustomCourier(IdMixin, Base):
    __tablename__ = "custom_couriers"
    tenant_id: Mapped[UUID]
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(80))
    services: Mapped[list] = mapped_column(JSONB, default=list)
    tracking_url_template: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str] = mapped_column(String(40), default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}
