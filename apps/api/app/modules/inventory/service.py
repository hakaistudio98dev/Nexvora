"""Inti inventory: SEMUA perubahan stok lewat apply(), yang mengunci baris saldo,
memvalidasi invarian, memperbarui saldo, dan menulis satu baris ledger immutable
dalam transaksi yang sama. Saldo selalu bisa direkonstruksi dari ledger."""
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Ctx
from app.core.errors import AppError
from app.models import InventoryBalance, InventoryLedger


async def lock_balances(session: AsyncSession, tenant_id: UUID,
                        pairs: Iterable[tuple[UUID, UUID]]) -> dict[tuple[UUID, UUID], InventoryBalance]:
    """Kunci (FOR UPDATE) saldo untuk pasangan (warehouse_id, sku_id); buat baris 0 bila belum ada.
    Urutan penguncian selalu sama (terurut) untuk mencegah deadlock."""
    keys = sorted(set(pairs), key=lambda x: (str(x[0]), str(x[1])))
    if not keys:
        return {}
    await session.execute(
        pg_insert(InventoryBalance)
        .values([{"tenant_id": tenant_id, "warehouse_id": w, "sku_id": s} for w, s in keys])
        .on_conflict_do_nothing(index_elements=["warehouse_id", "sku_id"])
    )
    out: dict[tuple[UUID, UUID], InventoryBalance] = {}
    for w, s in keys:
        bal = await session.scalar(
            select(InventoryBalance)
            .where(InventoryBalance.tenant_id == tenant_id, InventoryBalance.warehouse_id == w,
                   InventoryBalance.sku_id == s)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        out[(w, s)] = bal
    return out


async def apply(session: AsyncSession, ctx: Ctx, bal: InventoryBalance, entry_type: str, *,
                d_on_hand: int = 0, d_reserved: int = 0, d_damaged: int = 0, d_returned: int = 0,
                reason_code: str | None = None, note: str | None = None,
                reference_type: str | None = None, reference_id: object = None,
                location_id: UUID | None = None) -> InventoryLedger:
    on_hand = bal.on_hand + d_on_hand
    reserved = bal.reserved + d_reserved
    damaged = bal.damaged + d_damaged
    returned = bal.returned + d_returned
    if on_hand < 0 or damaged < 0 or reserved < 0 or returned < 0:
        raise AppError(409, "INSUFFICIENT_STOCK", "Stok tidak mencukupi untuk mutasi ini")
    if reserved > on_hand:
        raise AppError(409, "STOCK_RESERVED",
                       f"Stok fisik tidak boleh lebih kecil dari stok yang sudah direservasi ({bal.reserved})")
    bal.on_hand, bal.reserved, bal.damaged, bal.returned = on_hand, reserved, damaged, returned
    bal.version += 1
    entry = InventoryLedger(
        tenant_id=ctx.tenant_id, warehouse_id=bal.warehouse_id, sku_id=bal.sku_id, location_id=location_id,
        entry_type=entry_type, d_on_hand=d_on_hand, d_reserved=d_reserved, d_damaged=d_damaged, d_returned=d_returned,
        on_hand_after=on_hand, reserved_after=reserved, damaged_after=damaged, returned_after=returned,
        reason_code=reason_code, note=note, reference_type=reference_type,
        reference_id=str(reference_id) if reference_id is not None else None,
        actor_user_id=ctx.actor_user_id, correlation_id=ctx.correlation_id,
    )
    session.add(entry)
    return entry


async def reconcile(session: AsyncSession, tenant_id: UUID, warehouse_id: UUID | None = None) -> dict:
    """Bandingkan saldo tersimpan dengan penjumlahan ledger. Harus selalu nol selisih."""
    rows = (await session.execute(text("""
        WITH l AS (
          SELECT warehouse_id, sku_id, SUM(d_on_hand) oh, SUM(d_reserved) rs, SUM(d_damaged) dm, SUM(d_returned) rt
          FROM inventory_ledger WHERE tenant_id = :t GROUP BY warehouse_id, sku_id)
        SELECT b.warehouse_id, b.sku_id, b.on_hand, b.reserved, b.damaged, b.returned,
               COALESCE(l.oh,0) l_on_hand, COALESCE(l.rs,0) l_reserved, COALESCE(l.dm,0) l_damaged,
               COALESCE(l.rt,0) l_returned
        FROM inventory_balances b LEFT JOIN l USING (warehouse_id, sku_id)
        WHERE b.tenant_id = :t AND (CAST(:w AS uuid) IS NULL OR b.warehouse_id = CAST(:w AS uuid))
    """), {"t": tenant_id, "w": str(warehouse_id) if warehouse_id else None})).all()
    bins = {(r.warehouse_id, r.sku_id): int(r.q) for r in (await session.execute(text("""
        SELECT warehouse_id, sku_id, SUM(quantity) q FROM bin_stock WHERE tenant_id = :t GROUP BY warehouse_id, sku_id
    """), {"t": tenant_id})).all()}
    over_binned = [{"warehouse_id": str(r.warehouse_id), "sku_id": str(r.sku_id), "on_hand": r.on_hand,
                    "in_bins": bins.get((r.warehouse_id, r.sku_id), 0)}
                   for r in rows if bins.get((r.warehouse_id, r.sku_id), 0) > r.on_hand]
    mismatches = [
        {"warehouse_id": str(r.warehouse_id), "sku_id": str(r.sku_id),
         "balance": {"on_hand": r.on_hand, "reserved": r.reserved, "damaged": r.damaged, "returned": r.returned},
         "ledger": {"on_hand": int(r.l_on_hand), "reserved": int(r.l_reserved), "damaged": int(r.l_damaged),
                    "returned": int(r.l_returned)}}
        for r in rows
        if (r.on_hand, r.reserved, r.damaged, r.returned) != (r.l_on_hand, r.l_reserved, r.l_damaged, r.l_returned)
    ]
    return {"checked": len(rows), "mismatches": mismatches, "bin_overflow": over_binned,
            "ok": not mismatches and not over_binned}
