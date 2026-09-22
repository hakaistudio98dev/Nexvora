from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Computed, DateTime, ForeignKeyConstraint, Identity, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class InventoryBalance(IdMixin, Base):
    __tablename__ = "inventory_balances"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "warehouse_id"], ["warehouses.tenant_id", "warehouses.id"]),
        ForeignKeyConstraint(["tenant_id", "sku_id"], ["skus.tenant_id", "skus.id"]),
    )
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    sku_id: Mapped[UUID]
    on_hand: Mapped[int] = mapped_column(default=0)
    reserved: Mapped[int] = mapped_column(default=0)
    damaged: Mapped[int] = mapped_column(default=0)
    in_transit: Mapped[int] = mapped_column(default=0)
    returned: Mapped[int] = mapped_column(default=0)
    available: Mapped[int] = mapped_column(Computed("on_hand - reserved"))
    version: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    __mapper_args__ = {"eager_defaults": True}


class InventoryLedger(Base):
    __tablename__ = "inventory_ledger"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    sku_id: Mapped[UUID]
    location_id: Mapped[UUID | None]
    entry_type: Mapped[str] = mapped_column(String(20))
    d_on_hand: Mapped[int] = mapped_column(default=0)
    d_reserved: Mapped[int] = mapped_column(default=0)
    d_damaged: Mapped[int] = mapped_column(default=0)
    d_returned: Mapped[int] = mapped_column(default=0)
    on_hand_after: Mapped[int]
    reserved_after: Mapped[int]
    damaged_after: Mapped[int]
    returned_after: Mapped[int] = mapped_column(default=0)
    reason_code: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None]
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[str | None] = mapped_column(String(64))
    actor_user_id: Mapped[UUID | None]
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reservation(IdMixin, Base):
    __tablename__ = "reservations"
    tenant_id: Mapped[UUID]
    order_id: Mapped[UUID]
    order_item_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    sku_id: Mapped[UUID]
    quantity: Mapped[int]
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
