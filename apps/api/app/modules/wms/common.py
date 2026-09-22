"""Helper bersama modul WMS: nomor dokumen, resolusi barcode, stok per bin, exception."""
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import idempotency
from app.core.context import Ctx
from app.core.errors import AppError
from app.models import BinMovement, BinStock, Location, Sku, Warehouse, WmsException


async def next_doc_number(s: AsyncSession, tenant_id: UUID, prefix: str) -> str:
    n = await s.scalar(text("""
        INSERT INTO doc_counters(tenant_id, doc_type, last_no) VALUES (:t, :d, 1)
        ON CONFLICT (tenant_id, doc_type) DO UPDATE SET last_no = doc_counters.last_no + 1
        RETURNING last_no
    """), {"t": tenant_id, "d": prefix})
    return f"{prefix}-{n:06d}"


async def get_warehouse(s: AsyncSession, tenant_id: UUID, warehouse_id: UUID) -> Warehouse:
    w = await s.scalar(select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.tenant_id == tenant_id))
    if w is None:
        raise AppError(404, "NOT_FOUND", "Gudang tidak ditemukan")
    if not w.is_active:
        raise AppError(409, "WAREHOUSE_INACTIVE", "Gudang nonaktif")
    return w


async def resolve_sku(s: AsyncSession, tenant_id: UUID, code: str) -> Sku:
    """Barcode label atau kode SKU (tidak peka huruf besar/kecil)."""
    code = (code or "").strip()
    if not code:
        raise AppError(422, "EMPTY_SCAN", "Barcode kosong")
    sku = await s.scalar(select(Sku).where(Sku.tenant_id == tenant_id,
                                           or_(Sku.barcode == code, func.upper(Sku.sku_code) == code.upper()))
                         .limit(1))
    if sku is None:
        raise AppError(404, "UNKNOWN_BARCODE", f"Barcode {code} tidak dikenal")
    return sku


async def resolve_bin(s: AsyncSession, tenant_id: UUID, warehouse_id: UUID, code: str) -> Location:
    code = (code or "").strip().upper()
    loc = await s.scalar(select(Location).where(Location.tenant_id == tenant_id, Location.warehouse_id == warehouse_id,
                                                Location.full_code == code))
    if loc is None:
        raise AppError(404, "UNKNOWN_LOCATION", f"Lokasi {code} tidak ada di gudang ini")
    if loc.type != "BIN":
        raise AppError(422, "NOT_A_BIN", f"{code} adalah {loc.type}; scan label BIN")
    if not loc.is_active:
        raise AppError(409, "LOCATION_INACTIVE", f"Bin {code} nonaktif")
    return loc


def sku_matches(sku: Sku, code: str) -> bool:
    code = (code or "").strip()
    return code == (sku.barcode or "") or code.upper() == sku.sku_code.upper()


async def lock_bin(s: AsyncSession, tenant_id: UUID, warehouse_id: UUID, location_id: UUID, sku_id: UUID) -> BinStock:
    await s.execute(pg_insert(BinStock).values(tenant_id=tenant_id, warehouse_id=warehouse_id,
                                               location_id=location_id, sku_id=sku_id)
                    .on_conflict_do_nothing(index_elements=["location_id", "sku_id"]))
    return await s.scalar(select(BinStock).where(BinStock.location_id == location_id, BinStock.sku_id == sku_id)
                          .with_for_update().execution_options(populate_existing=True))


async def sum_bins(s: AsyncSession, tenant_id: UUID, warehouse_id: UUID, sku_id: UUID) -> int:
    return int(await s.scalar(select(func.coalesce(func.sum(BinStock.quantity), 0)).where(
        BinStock.tenant_id == tenant_id, BinStock.warehouse_id == warehouse_id, BinStock.sku_id == sku_id)) or 0)


def movement(s: AsyncSession, ctx: Ctx, *, warehouse_id: UUID, sku_id: UUID, qty: int, kind: str,
             from_loc: UUID | None = None, to_loc: UUID | None = None, ref_type: str | None = None,
             ref_id: Any = None) -> None:
    s.add(BinMovement(tenant_id=ctx.tenant_id, warehouse_id=warehouse_id, sku_id=sku_id, from_location_id=from_loc,
                      to_location_id=to_loc, quantity=qty, movement_type=kind, reference_type=ref_type,
                      reference_id=str(ref_id) if ref_id is not None else None, actor_user_id=ctx.actor_user_id))


def raise_exception(s: AsyncSession, ctx: Ctx, *, warehouse_id: UUID, exc_type: str, note: str = "",
                    **kw: Any) -> WmsException:
    e = WmsException(tenant_id=ctx.tenant_id, warehouse_id=warehouse_id, exc_type=exc_type, note=note,
                     reported_by=ctx.actor_user_id, **kw)
    s.add(e)
    return e


async def scan_event(s: AsyncSession, tenant_id: UUID, endpoint: str, client_event_id: str | None,
                     payload: dict, handler) -> Any:  # noqa: ANN001
    """Jadikan aksi scan idempoten: aplikasi mobile yang mengirim ulang antrean offline dengan
    client_event_id yang sama akan menerima hasil yang sama, tanpa mengulang mutasi stok."""
    if not client_event_id:
        return await handler()
    key = idempotency.validate_key(client_event_id)
    replay = await idempotency.begin(s, tenant_id, endpoint, key, idempotency.body_hash(payload))
    if replay:
        return replay["body"]
    result = await handler()
    await idempotency.complete(s, tenant_id, endpoint, key, 200, jsonable_encoder(result))
    return result
