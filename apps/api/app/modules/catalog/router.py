from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, entitlements
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.core.schemas import ORM, Page
from app.core.utils import escape_like, paginate, snapshot
from app.models import Product, Sku

router = APIRouter(tags=["catalog"])
CODE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
P_FIELDS = ["code", "name", "description", "is_active"]
S_FIELDS = ["sku_code", "barcode", "variant_name", "unit", "length_mm", "width_mm", "height_mm", "weight_g",
            "reorder_point", "is_active"]


class SkuOut(ORM):
    id: UUID
    product_id: UUID
    sku_code: str
    barcode: str | None
    variant_name: str
    unit: str
    length_mm: int | None
    width_mm: int | None
    height_mm: int | None
    weight_g: int | None
    reorder_point: int | None = None
    is_active: bool


class ProductOut(ORM):
    id: UUID
    code: str
    name: str
    description: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductDetail(ProductOut):
    skus: list[SkuOut]


class ProductCreate(BaseModel):
    code: str = Field(pattern=CODE)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    is_active: bool | None = None


class SkuBase(BaseModel):
    barcode: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{4,64}$")
    variant_name: str = Field(default="", max_length=120)
    unit: str = Field(default="PCS", pattern=r"^[A-Z]{1,20}$")
    length_mm: int | None = Field(default=None, gt=0, le=100000)
    width_mm: int | None = Field(default=None, gt=0, le=100000)
    height_mm: int | None = Field(default=None, gt=0, le=100000)
    weight_g: int | None = Field(default=None, gt=0, le=10_000_000)
    reorder_point: int | None = Field(default=None, ge=0, le=10_000_000, description="Batas stok menipis khusus SKU ini")


class SkuCreate(SkuBase):
    sku_code: str = Field(pattern=CODE)


class SkuUpdate(BaseModel):
    barcode: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{4,64}$")
    variant_name: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, pattern=r"^[A-Z]{1,20}$")
    length_mm: int | None = Field(default=None, gt=0, le=100000)
    width_mm: int | None = Field(default=None, gt=0, le=100000)
    height_mm: int | None = Field(default=None, gt=0, le=100000)
    weight_g: int | None = Field(default=None, gt=0, le=10_000_000)
    reorder_point: int | None = Field(default=None, ge=0, le=10_000_000)
    is_active: bool | None = None


async def _product(s: AsyncSession, p: Principal, product_id: UUID) -> Product:
    prod = await s.scalar(select(Product).where(Product.id == product_id, Product.tenant_id == p.tenant_id))
    if prod is None:
        raise AppError(404, "NOT_FOUND", "Produk tidak ditemukan")
    return prod


@router.get("/products", response_model=Page[ProductOut])
async def list_products(q: str | None = Query(default=None, max_length=100),
                        active: bool | None = None,
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                        p: Principal = Depends(require("product:read")), s: AsyncSession = Depends(get_session)):
    stmt = select(Product).where(Product.tenant_id == p.tenant_id).order_by(Product.code)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Product.code.ilike(like, escape="\\"), Product.name.ilike(like, escape="\\")))
    if active is not None:
        stmt = stmt.where(Product.is_active == active)
    items, total = await paginate(s, stmt, limit, offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(body: ProductCreate, p: Principal = Depends(require("product:write")),
                         s: AsyncSession = Depends(get_session)):
    prod = Product(tenant_id=p.tenant_id, **body.model_dump())
    s.add(prod)
    await s.flush()
    await s.refresh(prod)
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="product.created",
                       entity_type="product", entity_id=prod.id, after=snapshot(prod, P_FIELDS),
                       correlation_id=p.correlation_id, ip=p.ip)
    return prod


@router.get("/products/{product_id}", response_model=ProductDetail)
async def get_product(product_id: UUID, p: Principal = Depends(require("product:read")),
                      s: AsyncSession = Depends(get_session)):
    prod = await _product(s, p, product_id)
    skus = (await s.scalars(select(Sku).where(Sku.product_id == prod.id, Sku.tenant_id == p.tenant_id)
                            .order_by(Sku.sku_code))).all()
    base = ProductOut.model_validate(prod).model_dump()
    return ProductDetail(**base, skus=[SkuOut.model_validate(x) for x in skus])


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(product_id: UUID, body: ProductUpdate, p: Principal = Depends(require("product:write")),
                         s: AsyncSession = Depends(get_session)):
    prod = await _product(s, p, product_id)
    before = snapshot(prod, P_FIELDS)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(prod, k, v)
    await s.flush()
    await s.refresh(prod)
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="product.updated",
                       entity_type="product", entity_id=prod.id, before=before, after=snapshot(prod, P_FIELDS),
                       correlation_id=p.correlation_id, ip=p.ip)
    return prod


@router.post("/products/{product_id}/skus", response_model=SkuOut, status_code=201)
async def create_sku(product_id: UUID, body: SkuCreate, p: Principal = Depends(require("product:write")),
                     s: AsyncSession = Depends(get_session)):
    prod = await _product(s, p, product_id)
    await entitlements.enforce_limit(s, p, "skus")
    sku = Sku(tenant_id=p.tenant_id, product_id=prod.id, **body.model_dump())
    s.add(sku)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="sku.created", entity_type="sku",
                       entity_id=sku.id, after=snapshot(sku, S_FIELDS), correlation_id=p.correlation_id, ip=p.ip)
    return sku


@router.get("/skus", response_model=Page[SkuOut])
async def search_skus(q: str | None = Query(default=None, max_length=64), barcode: str | None = Query(None, max_length=64),
                      limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                      p: Principal = Depends(require("product:read")), s: AsyncSession = Depends(get_session)):
    stmt = select(Sku).where(Sku.tenant_id == p.tenant_id).order_by(Sku.sku_code)
    if barcode:
        stmt = stmt.where(Sku.barcode == barcode)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Sku.sku_code.ilike(like, escape="\\"), Sku.variant_name.ilike(like, escape="\\")))
    items, total = await paginate(s, stmt, limit, offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.patch("/skus/{sku_id}", response_model=SkuOut)
async def update_sku(sku_id: UUID, body: SkuUpdate, p: Principal = Depends(require("product:write")),
                     s: AsyncSession = Depends(get_session)):
    sku = await s.scalar(select(Sku).where(Sku.id == sku_id, Sku.tenant_id == p.tenant_id))
    if sku is None:
        raise AppError(404, "NOT_FOUND", "SKU tidak ditemukan")
    before = snapshot(sku, S_FIELDS)
    if body.is_active and not sku.is_active:
        await entitlements.enforce_limit(s, p, "skus")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(sku, k, v)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="sku.updated", entity_type="sku",
                       entity_id=sku.id, before=before, after=snapshot(sku, S_FIELDS),
                       correlation_id=p.correlation_id, ip=p.ip)
    return sku
