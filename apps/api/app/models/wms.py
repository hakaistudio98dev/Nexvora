from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class _Ts:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class BinStock(IdMixin, Base):
    __tablename__ = "bin_stock"
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    location_id: Mapped[UUID]
    sku_id: Mapped[UUID]
    quantity: Mapped[int] = mapped_column(default=0)
    allocated: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __mapper_args__ = {"eager_defaults": True}


class BinMovement(Base):
    __tablename__ = "bin_movements"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    sku_id: Mapped[UUID]
    from_location_id: Mapped[UUID | None]
    to_location_id: Mapped[UUID | None]
    quantity: Mapped[int]
    movement_type: Mapped[str] = mapped_column(String(20))
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[str | None] = mapped_column(String(64))
    actor_user_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InboundReceipt(IdMixin, _Ts, Base):
    __tablename__ = "inbound_receipts"
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    number: Mapped[str] = mapped_column(String(32))
    supplier: Mapped[str] = mapped_column(String(200), default="")
    reference: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(12), default="RECEIVING")
    notes: Mapped[str] = mapped_column(default="")
    created_by: Mapped[UUID | None]
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __mapper_args__ = {"eager_defaults": True}


class InboundLine(IdMixin, Base):
    __tablename__ = "inbound_lines"
    tenant_id: Mapped[UUID]
    inbound_id: Mapped[UUID]
    sku_id: Mapped[UUID]
    expected_qty: Mapped[int] = mapped_column(default=0)
    received_qty: Mapped[int] = mapped_column(default=0)
    damaged_qty: Mapped[int] = mapped_column(default=0)


class Wave(IdMixin, _Ts, Base):
    __tablename__ = "waves"
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    number: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(12), default="OPEN")
    created_by: Mapped[UUID | None]
    __mapper_args__ = {"eager_defaults": True}


class WmsTask(IdMixin, _Ts, Base):
    __tablename__ = "wms_tasks"
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    task_type: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12), default="OPEN")
    sku_id: Mapped[UUID]
    quantity: Mapped[int]
    done_qty: Mapped[int] = mapped_column(default=0)
    from_location_id: Mapped[UUID | None]
    to_location_id: Mapped[UUID | None]
    order_id: Mapped[UUID | None]
    order_item_id: Mapped[UUID | None]
    wave_id: Mapped[UUID | None]
    inbound_id: Mapped[UUID | None]
    assigned_to: Mapped[UUID | None]
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __mapper_args__ = {"eager_defaults": True}

    @property
    def remaining(self) -> int:
        return self.quantity - self.done_qty


class PackProgress(Base):
    __tablename__ = "pack_progress"
    order_item_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    order_id: Mapped[UUID]
    scanned_qty: Mapped[int] = mapped_column(default=0)


class Package(IdMixin, Base):
    __tablename__ = "packages"
    tenant_id: Mapped[UUID]
    order_id: Mapped[UUID]
    weight_g: Mapped[int]
    expected_weight_g: Mapped[int | None]
    length_mm: Mapped[int | None]
    width_mm: Mapped[int | None]
    height_mm: Mapped[int | None]
    override_reason: Mapped[str | None] = mapped_column(String(300))
    packed_by: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}


class CycleCount(IdMixin, _Ts, Base):
    __tablename__ = "cycle_counts"
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    number: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(12), default="OPEN")
    created_by: Mapped[UUID | None]
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __mapper_args__ = {"eager_defaults": True}


class CycleCountLine(IdMixin, Base):
    __tablename__ = "cycle_count_lines"
    tenant_id: Mapped[UUID]
    count_id: Mapped[UUID]
    location_id: Mapped[UUID]
    sku_id: Mapped[UUID]
    system_qty: Mapped[int]
    counted_qty: Mapped[int | None]
    counted_by: Mapped[UUID | None]
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WmsException(IdMixin, Base):
    __tablename__ = "wms_exceptions"
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    exc_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), default="OPEN")
    sku_id: Mapped[UUID | None]
    location_id: Mapped[UUID | None]
    order_id: Mapped[UUID | None]
    task_id: Mapped[UUID | None]
    inbound_id: Mapped[UUID | None]
    quantity: Mapped[int | None]
    note: Mapped[str] = mapped_column(default="")
    reported_by: Mapped[UUID | None]
    resolution: Mapped[str | None]
    resolved_by: Mapped[UUID | None]
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {"eager_defaults": True}
