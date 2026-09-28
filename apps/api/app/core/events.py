"""Outbox: event bisnis dicatat bersama perubahan datanya, lalu dikirim keluar oleh worker.

Tujuannya: (1) tidak ada event yang hilang bila proses mati di tengah jalan, (2) urutan terjaga,
(3) integrasi luar (webhook, antrean pesan) bisa ditambah tanpa mengubah kode bisnis.
"""
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.events import OutboxEvent


def emit(session: AsyncSession, *, tenant_id: UUID, event_type: str, aggregate_type: str, aggregate_id: Any,
         payload: dict, correlation_id: str | None = None) -> None:
    session.add(OutboxEvent(tenant_id=tenant_id, event_type=event_type, aggregate_type=aggregate_type,
                            aggregate_id=str(aggregate_id), payload=payload, correlation_id=correlation_id))
