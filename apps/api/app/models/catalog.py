from uuid import UUID

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class Product(IdMixin, TimestampMixin, Base):
    __tablename__ = "products"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(default="")
    is_active: Mapped[bool] = mapped_column(default=True)


class Sku(IdMixin, TimestampMixin, Base):
    __tablename__ = "skus"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "product_id"], ["products.tenant_id", "products.id"]),)
    tenant_id: Mapped[UUID]
    product_id: Mapped[UUID]
    sku_code: Mapped[str] = mapped_column(String(64))
    barcode: Mapped[str | None] = mapped_column(String(64))
    variant_name: Mapped[str] = mapped_column(String(120), default="")
    unit: Mapped[str] = mapped_column(String(20), default="PCS")
    length_mm: Mapped[int | None]
    width_mm: Mapped[int | None]
    height_mm: Mapped[int | None]
    weight_g: Mapped[int | None]
    reorder_point: Mapped[int | None]
    is_active: Mapped[bool] = mapped_column(default=True)
