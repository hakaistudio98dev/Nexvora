"""Koneksi database + konteks tenant untuk Row-Level Security.

Setiap request memakai SATU transaksi. Konteks tenant dipasang dengan
set_config(..., is_local=true) sehingga hanya berlaku di transaksi itu dan
otomatis hilang saat koneksi kembali ke pool — tidak bisa "bocor" ke request lain.
"""
from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

# Replika baca (opsional). Tanpa DATABASE_READ_URL, query analitik memakai database utama.
read_engine = create_async_engine(_settings.database_read_url, pool_size=_settings.db_pool_size,
                                  pool_pre_ping=True) if _settings.database_read_url else engine
ReadSessionLocal = async_sessionmaker(read_engine, expire_on_commit=False, autoflush=False)
HAS_READ_REPLICA = _settings.database_read_url is not None


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(text(f"SET LOCAL statement_timeout = {_settings.statement_timeout_ms}"))
            yield session


async def get_read_session() -> AsyncIterator[AsyncSession]:
    """Untuk analitik & laporan: transaksi read-only dengan batas waktu lebih longgar,
    diarahkan ke replika bila tersedia."""
    async with ReadSessionLocal() as session:
        async with session.begin():
            await session.execute(text(
                f"SET LOCAL statement_timeout = {_settings.read_statement_timeout_ms}"))
            await session.execute(text("SET TRANSACTION READ ONLY"))
            yield session


async def set_tenant_context(session: AsyncSession, tenant_id: UUID | None, *, superadmin: bool = False) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, true), set_config('app.is_superadmin', :s, true)"),
        {"t": str(tenant_id) if tenant_id else "", "s": "on" if superadmin else "off"},
    )
