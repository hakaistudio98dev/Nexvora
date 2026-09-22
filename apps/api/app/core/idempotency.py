"""Idempotency-Key untuk endpoint pembuat data (mis. POST /orders).

Baris kunci di-INSERT di awal transaksi request. Request kedua dengan kunci sama akan
menunggu (unique index) sampai request pertama commit, lalu menerima respons yang sama.
"""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError


def body_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def validate_key(key: str) -> str:
    key = key.strip()
    if not (8 <= len(key) <= 100) or not all(c.isalnum() or c in "-_:." for c in key):
        raise AppError(400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key harus 8–100 karakter alfanumerik/-_:.")
    return key


async def begin(session: AsyncSession, tenant_id: UUID, endpoint: str, key: str, req_hash: str) -> dict | None:
    """None = request baru, lanjutkan proses. dict = respons tersimpan untuk di-replay."""
    ttl = timedelta(hours=get_settings().idempotency_ttl_hours)
    res = await session.execute(text("""
        INSERT INTO idempotency_keys(tenant_id, key, endpoint, request_hash, expires_at)
        VALUES (:t, :k, :e, :h, :x) ON CONFLICT DO NOTHING
    """), {"t": tenant_id, "k": key, "e": endpoint, "h": req_hash, "x": datetime.now(UTC) + ttl})
    if res.rowcount == 1:
        return None
    row = (await session.execute(text("""
        SELECT request_hash, response_status, response_body FROM idempotency_keys
        WHERE tenant_id = :t AND endpoint = :e AND key = :k
    """), {"t": tenant_id, "e": endpoint, "k": key})).first()
    if row is None:  # kunci lama sudah dibersihkan tepat saat ini — anggap baru
        return None
    if row.request_hash != req_hash:
        raise AppError(422, "IDEMPOTENCY_CONFLICT",
                       "Idempotency-Key ini sudah dipakai untuk request dengan isi berbeda")
    if row.response_status is None:
        raise AppError(409, "IDEMPOTENCY_IN_PROGRESS", "Request dengan kunci ini sedang diproses")
    return {"status": row.response_status, "body": row.response_body}


async def complete(session: AsyncSession, tenant_id: UUID, endpoint: str, key: str, status: int, body: Any) -> None:
    await session.execute(text("""
        UPDATE idempotency_keys SET response_status = :s, response_body = CAST(:b AS jsonb)
        WHERE tenant_id = :t AND endpoint = :e AND key = :k
    """), {"s": status, "b": json.dumps(body, default=str), "t": tenant_id, "e": endpoint, "k": key})
