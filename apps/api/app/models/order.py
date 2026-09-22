from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, Identity, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin


class Order(IdMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    tenant_id: Mapped[UUID]
    order_number: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(20))
    external_ref: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="CREATED")
    payment_status: Mapped[str] = mapped_column(String(20), default="UNPAID")
    stock_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    warehouse_id: Mapped[UUID | None]
    allocation_note: Mapped[str | None] = mapped_column(String(300))
    customer_name: Mapped[str] = mapped_column(String(200))
    customer_phone: Mapped[str] = mapped_column(String(40), default="")
    customer_email: Mapped[str] = mapped_column(String(254), default="")
    ship_address: Mapped[str]
    ship_city: Mapped[str] = mapped_column(String(100))
    ship_province: Mapped[str] = mapped_column(String(100), default="")
    ship_postal_code: Mapped[str] = mapped_column(String(12), default="")
    ship_country: Mapped[str] = mapped_column(String(2), default="ID")
    currency: Mapped[str] = mapped_column(String(3), default="IDR")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    notes: Mapped[str] = mapped_column(default="")
    status_reason: Mapped[str | None] = mapped_column(String(300))
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allocated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID | None]
    version: Mapped[int] = mapped_column(default=0)

    items: Mapped[list["OrderItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan",
                                                    order_by="OrderItem.id")

    __mapper_args__ = {"version_id_col": version, "eager_defaults": True}  # optimistic locking


class OrderItem(IdMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"]),)
    tenant_id: Mapped[UUID]
    order_id: Mapped[UUID]
    sku_id: Mapped[UUID]
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tenant_id: Mapped[UUID]
    order_id: Mapped[UUID]
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(300))
    actor_user_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
