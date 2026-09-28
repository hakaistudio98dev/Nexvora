"""Impor CSV massal: produk/SKU dan stok awal.

Alurnya selalu dua langkah — **periksa dulu, jalankan kemudian**:

    unggah file → PREVIEW (semua baris divalidasi, kesalahan ditunjukkan per nomor baris)
                → pengguna melihat ringkasan → COMMITTED (dijalankan dalam satu transaksi)

Baris yang salah tidak pernah "diperbaiki diam-diam". File dengan kesalahan tetap bisa dijalankan sebagian
hanya kalau pengguna memilihnya, dan baris yang dilewati dilaporkan kembali.
"""
import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Ctx
from app.core.errors import AppError
from app.models import Product, Sku, Warehouse
from app.modules.inventory import service as inv

MAX_ROWS = 5000


@dataclass
class Spec:
    kind: str
    label: str
    required: list[str]
    optional: list[str]
    permission: str
    example: list[dict]


SPECS: dict[str, Spec] = {
    "products": Spec(
        kind="products", label="Produk & SKU", permission="product:write",
        required=["kode_produk", "nama_produk", "kode_sku"],
        optional=["barcode", "varian", "satuan", "berat_gram", "batas_stok_menipis"],
        example=[{"kode_produk": "TSH", "nama_produk": "Kaos Polos", "kode_sku": "TSH-BLK-M", "barcode": "8991234567890",
                  "varian": "Hitam / M", "satuan": "PCS", "berat_gram": "200", "batas_stok_menipis": "10"}]),
    "stock": Spec(
        kind="stock", label="Stok awal", permission="inventory:write",
        required=["kode_gudang", "kode_sku", "jumlah"],
        optional=["catatan"],
        example=[{"kode_gudang": "JKT-01", "kode_sku": "TSH-BLK-M", "jumlah": "50", "catatan": "stok opname awal"}]),
}

ALIASES = {  # judul kolom yang sering dipakai orang → nama baku
    "sku": "kode_sku", "sku_code": "kode_sku", "kode": "kode_sku", "product_code": "kode_produk",
    "produk": "nama_produk", "product_name": "nama_produk", "nama": "nama_produk", "variant": "varian",
    "weight": "berat_gram", "berat": "berat_gram", "qty": "jumlah", "quantity": "jumlah", "stok": "jumlah",
    "gudang": "kode_gudang", "warehouse": "kode_gudang", "warehouse_code": "kode_gudang", "unit": "satuan",
    "reorder_point": "batas_stok_menipis", "note": "catatan",
}


def _norm(h: str) -> str:
    k = (h or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(k, k)


def parse_csv(raw: bytes, spec: Spec) -> tuple[list[dict], list[dict]]:
    """Kembalikan (baris, kesalahan). Menerima pemisah koma maupun titik koma."""
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:4000]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        raise AppError(422, "EMPTY_FILE", "File kosong atau bukan CSV")
    fields = [_norm(f) for f in reader.fieldnames]
    missing = [c for c in spec.required if c not in fields]
    if missing:
        raise AppError(422, "MISSING_COLUMNS",
                       f"Kolom wajib belum ada: {', '.join(missing)}. Kolom yang dibutuhkan: "
                       f"{', '.join(spec.required)}" + (f" (opsional: {', '.join(spec.optional)})" if spec.optional else ""))
    rows, errors = [], []
    for i, raw_row in enumerate(reader, start=2):        # baris 1 = judul kolom
        if i - 1 > MAX_ROWS:
            errors.append({"row": i, "message": f"File melebihi {MAX_ROWS:,} baris; bagi menjadi beberapa file"})
            break
        row = {_norm(k): (v or "").strip() for k, v in raw_row.items() if k is not None}
        if not any(row.get(c) for c in spec.required):
            continue                                     # baris kosong dilewati diam-diam
        kosong = [c for c in spec.required if not row.get(c)]
        if kosong:
            errors.append({"row": i, "message": f"{', '.join(kosong)} wajib diisi"})
            continue
        rows.append({"_row": i, **{k: v for k, v in row.items() if k in spec.required + spec.optional}})
    return rows, errors


def _int(v: str, field: str, row: int, errors: list, *, minimum: int = 0, maximum: int = 10_000_000) -> int | None:
    if v in ("", None):
        return None
    try:
        n = int(Decimal(str(v).replace(".", "").replace(",", ".")))
    except Exception:  # noqa: BLE001
        errors.append({"row": row, "message": f"{field} harus angka, bukan “{v}”"})
        return None
    if not minimum <= n <= maximum:
        errors.append({"row": row, "message": f"{field} harus antara {minimum} dan {maximum:,}"})
        return None
    return n


async def validate(s: AsyncSession, tenant_id: UUID, kind: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Periksa isi baris terhadap data yang sudah ada. Tidak mengubah apa pun."""
    errors: list[dict] = []
    ok: list[dict] = []
    seen: set = set()

    if kind == "products":
        existing = {r[0].upper(): r[1] for r in (await s.execute(
            select(Sku.sku_code, Sku.id).where(Sku.tenant_id == tenant_id))).all()}
        barcodes = {r[0] for r in (await s.execute(
            select(Sku.barcode).where(Sku.tenant_id == tenant_id, Sku.barcode.isnot(None)))).all()}
        for r in rows:
            n = r["_row"]
            sku = r["kode_sku"].upper()
            if sku in seen:
                errors.append({"row": n, "message": f"Kode SKU {sku} muncul dua kali di file ini"})
                continue
            seen.add(sku)
            bc = r.get("barcode") or None
            if bc and bc in barcodes:
                errors.append({"row": n, "message": f"Barcode {bc} sudah dipakai SKU lain"})
                continue
            berat = _int(r.get("berat_gram", ""), "berat_gram", n, errors, minimum=1)
            batas = _int(r.get("batas_stok_menipis", ""), "batas_stok_menipis", n, errors)
            ok.append({"_row": n, "kode_produk": r["kode_produk"].strip(), "nama_produk": r["nama_produk"].strip(),
                       "kode_sku": sku, "barcode": bc, "varian": r.get("varian", ""), "satuan": (r.get("satuan") or "PCS").upper(),
                       "berat_gram": berat, "batas_stok_menipis": batas, "_update": sku in existing})
    else:
        wh = {w.code.upper(): str(w.id) for w in (await s.scalars(
            select(Warehouse).where(Warehouse.tenant_id == tenant_id, Warehouse.is_active.is_(True)))).all()}
        skus = {k.sku_code.upper(): str(k.id) for k in (await s.scalars(
            select(Sku).where(Sku.tenant_id == tenant_id, Sku.is_active.is_(True)))).all()}
        for r in rows:
            n = r["_row"]
            g, sku = r["kode_gudang"].upper(), r["kode_sku"].upper()
            if g not in wh:
                errors.append({"row": n, "message": f"Gudang {g} tidak ada atau nonaktif"})
                continue
            if sku not in skus:
                errors.append({"row": n, "message": f"SKU {sku} belum terdaftar — impor produknya dulu"})
                continue
            if (g, sku) in seen:
                errors.append({"row": n, "message": f"{sku} di {g} muncul dua kali di file ini"})
                continue
            qty = _int(r["jumlah"], "jumlah", n, errors, minimum=1)
            if qty is None:
                continue
            seen.add((g, sku))
            ok.append({"_row": n, "warehouse_id": wh[g], "sku_id": skus[sku], "kode_gudang": g, "kode_sku": sku,
                       "jumlah": qty, "catatan": r.get("catatan", "")})
    return ok, errors


async def commit(s: AsyncSession, ctx: Ctx, kind: str, rows: list[dict]) -> dict:
    """Jalankan impor. Dipanggil di dalam satu transaksi: kalau satu baris gagal, semuanya dibatalkan."""
    if kind == "products":
        produk: dict[str, Product] = {}
        for p in (await s.scalars(select(Product).where(Product.tenant_id == ctx.tenant_id))).all():
            produk[p.code.upper()] = p
        existing = {k.sku_code.upper(): k for k in (await s.scalars(
            select(Sku).where(Sku.tenant_id == ctx.tenant_id))).all()}
        dibuat = diperbarui = produk_baru = 0
        for r in rows:
            code = r["kode_produk"].upper()
            prod = produk.get(code)
            if prod is None:
                prod = Product(tenant_id=ctx.tenant_id, code=r["kode_produk"], name=r["nama_produk"])
                s.add(prod)
                await s.flush()
                produk[code] = prod
                produk_baru += 1
            sku = existing.get(r["kode_sku"])
            if sku is None:
                sku = Sku(tenant_id=ctx.tenant_id, product_id=prod.id, sku_code=r["kode_sku"])
                s.add(sku)
                dibuat += 1
            else:
                diperbarui += 1
            sku.variant_name = r["varian"] or sku.variant_name or ""
            sku.unit = r["satuan"] or "PCS"
            if r["barcode"]:
                sku.barcode = r["barcode"]
            if r["berat_gram"]:
                sku.weight_g = r["berat_gram"]
            if r["batas_stok_menipis"] is not None:
                sku.reorder_point = r["batas_stok_menipis"]
        await s.flush()
        return {"produk_baru": produk_baru, "sku_baru": dibuat, "sku_diperbarui": diperbarui}

    pairs = [(UUID(r["warehouse_id"]), UUID(r["sku_id"])) for r in rows]
    bals: dict = await inv.lock_balances(s, ctx.tenant_id, pairs)
    total = 0
    for r in rows:
        key = (UUID(r["warehouse_id"]), UUID(r["sku_id"]))
        await inv.apply(s, ctx, bals[key], "RECEIPT", d_on_hand=r["jumlah"], reason_code="IMPORT",
                        reference_type="import", reference_id=None, note=r.get("catatan") or "Impor stok awal")
        total += r["jumlah"]
    await s.flush()
    return {"baris": len(rows), "unit_masuk": total}


def template_csv(kind: str) -> str:
    spec = SPECS[kind]
    cols = spec.required + spec.optional
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for row in spec.example:
        w.writerow({c: row.get(c, "") for c in cols})
    return "\ufeff" + buf.getvalue()


def now() -> datetime:
    return datetime.now(UTC)


