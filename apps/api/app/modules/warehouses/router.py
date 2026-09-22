from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, entitlements
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.core.schemas import ORM
from app.core.utils import snapshot
from app.models import Location, Warehouse
from app.models.warehouse import LOCATION_HIERARCHY

router = APIRouter(prefix="/warehouses", tags=["warehouses"])
W_FIELDS = ["code", "name", "address", "city", "postal_code", "phone", "contact_name", "timezone", "is_active"]


class WarehouseOut(ORM):
    id: UUID
    code: str
    name: str
    address: str
    city: str
    timezone: str
    postal_code: str
    phone: str
    contact_name: str
    is_active: bool


class WarehouseCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9-]{2,32}$")
    name: str = Field(min_length=2, max_length=200)
    address: str = Field(default="", max_length=1000)
    city: str = Field(default="", max_length=100)
    timezone: str = Field(default="Asia/Jakarta", pattern=r"^[A-Za-z_]+/[A-Za-z_]+$")
    postal_code: str = Field(default="", max_length=12, pattern=r"^[0-9A-Za-z -]*$")
    phone: str = Field(default="", max_length=40, pattern=r"^[0-9+ ()-]*$")
    contact_name: str = Field(default="", max_length=120)


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    address: str | None = Field(default=None, max_length=1000)
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=12, pattern=r"^[0-9A-Za-z -]*$")
    phone: str | None = Field(default=None, max_length=40, pattern=r"^[0-9+ ()-]*$")
    contact_name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class LocationOut(ORM):
    id: UUID
    warehouse_id: UUID
    parent_id: UUID | None
    type: str
    code: str
    full_code: str
    is_active: bool


class LocationCreate(BaseModel):
    type: str = Field(pattern=r"^(ZONE|RACK|SHELF|BIN)$")
    code: str = Field(pattern=r"^[A-Z0-9]{1,32}$")
    parent_id: UUID | None = None


class LocationUpdate(BaseModel):
    is_active: bool


async def _warehouse(s: AsyncSession, p: Principal, wid: UUID) -> Warehouse:
    w = await s.scalar(select(Warehouse).where(Warehouse.id == wid, Warehouse.tenant_id == p.tenant_id))
    if w is None:
        raise AppError(404, "NOT_FOUND", "Gudang tidak ditemukan")
    return w


@router.get("", response_model=list[WarehouseOut])
async def list_warehouses(p: Principal = Depends(require("warehouse:read")), s: AsyncSession = Depends(get_session)):
    return (await s.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant_id)
                            .order_by(Warehouse.code))).all()


@router.post("", response_model=WarehouseOut, status_code=201)
async def create_warehouse(body: WarehouseCreate, p: Principal = Depends(require("warehouse:write")),
                           s: AsyncSession = Depends(get_session)):
    await entitlements.enforce_limit(s, p, "warehouses")
    w = Warehouse(tenant_id=p.tenant_id, **body.model_dump())
    s.add(w)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="warehouse.created",
                       entity_type="warehouse", entity_id=w.id, after=snapshot(w, W_FIELDS),
                       correlation_id=p.correlation_id, ip=p.ip)
    return w


@router.patch("/{warehouse_id}", response_model=WarehouseOut)
async def update_warehouse(warehouse_id: UUID, body: WarehouseUpdate,
                           p: Principal = Depends(require("warehouse:write")), s: AsyncSession = Depends(get_session)):
    w = await _warehouse(s, p, warehouse_id)
    if body.is_active and not w.is_active:
        await entitlements.enforce_limit(s, p, "warehouses")
    before = snapshot(w, W_FIELDS)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(w, k, v)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="warehouse.updated",
                       entity_type="warehouse", entity_id=w.id, before=before, after=snapshot(w, W_FIELDS),
                       correlation_id=p.correlation_id, ip=p.ip)
    return w


@router.get("/{warehouse_id}/locations", response_model=list[LocationOut])
async def list_locations(warehouse_id: UUID, p: Principal = Depends(require("warehouse:read")),
                         s: AsyncSession = Depends(get_session)):
    w = await _warehouse(s, p, warehouse_id)
    return (await s.scalars(select(Location).where(Location.warehouse_id == w.id, Location.tenant_id == p.tenant_id)
                            .order_by(Location.full_code))).all()


@router.post("/{warehouse_id}/locations", response_model=LocationOut, status_code=201)
async def create_location(warehouse_id: UUID, body: LocationCreate,
                          p: Principal = Depends(require("warehouse:write")), s: AsyncSession = Depends(get_session)):
    w = await _warehouse(s, p, warehouse_id)
    expected_parent = LOCATION_HIERARCHY[body.type]
    if expected_parent is None:
        if body.parent_id is not None:
            raise AppError(400, "INVALID_HIERARCHY", "ZONE tidak boleh memiliki induk")
        full_code = body.code
    else:
        if body.parent_id is None:
            raise AppError(400, "INVALID_HIERARCHY", f"{body.type} wajib berada di dalam {expected_parent}")
        parent = await s.scalar(select(Location).where(Location.id == body.parent_id,
                                                       Location.tenant_id == p.tenant_id,
                                                       Location.warehouse_id == w.id))
        if parent is None:
            raise AppError(404, "NOT_FOUND", "Lokasi induk tidak ditemukan di gudang ini")
        if parent.type != expected_parent:
            raise AppError(400, "INVALID_HIERARCHY", f"{body.type} harus berada di dalam {expected_parent}, "
                                                     f"bukan {parent.type}")
        full_code = f"{parent.full_code}-{body.code}"
    loc = Location(tenant_id=p.tenant_id, warehouse_id=w.id, parent_id=body.parent_id, type=body.type,
                   code=body.code, full_code=full_code)
    s.add(loc)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="location.created",
                       entity_type="location", entity_id=loc.id,
                       after={"warehouse": w.code, "type": loc.type, "full_code": loc.full_code},
                       correlation_id=p.correlation_id, ip=p.ip)
    return loc


@router.patch("/{warehouse_id}/locations/{location_id}", response_model=LocationOut)
async def update_location(warehouse_id: UUID, location_id: UUID, body: LocationUpdate,
                          p: Principal = Depends(require("warehouse:write")), s: AsyncSession = Depends(get_session)):
    w = await _warehouse(s, p, warehouse_id)
    loc = await s.scalar(select(Location).where(Location.id == location_id, Location.warehouse_id == w.id,
                                                Location.tenant_id == p.tenant_id))
    if loc is None:
        raise AppError(404, "NOT_FOUND", "Lokasi tidak ditemukan")
    before = {"is_active": loc.is_active}
    loc.is_active = body.is_active
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="location.updated",
                       entity_type="location", entity_id=loc.id, before=before, after={"is_active": loc.is_active},
                       correlation_id=p.correlation_id, ip=p.ip)
    return loc
