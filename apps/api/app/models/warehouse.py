from uuid import UUID

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin

LOCATION_HIERARCHY = {"ZONE": None, "RACK": "ZONE", "SHELF": "RACK", "BIN": "SHELF"}


class Warehouse(IdMixin, TimestampMixin, Base):
    __tablename__ = "warehouses"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Jakarta")
    postal_code: Mapped[str] = mapped_column(String(12), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    contact_name: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(default=True)


class Location(IdMixin, TimestampMixin, Base):
    __tablename__ = "locations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "warehouse_id"], ["warehouses.tenant_id", "warehouses.id"]),
        ForeignKeyConstraint(["tenant_id", "parent_id"], ["locations.tenant_id", "locations.id"]),
    )
    tenant_id: Mapped[UUID]
    warehouse_id: Mapped[UUID]
    parent_id: Mapped[UUID | None]
    type: Mapped[str] = mapped_column(String(10))
    code: Mapped[str] = mapped_column(String(32))
    full_code: Mapped[str] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(default=True)
