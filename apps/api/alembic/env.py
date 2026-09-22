"""Alembic env — migrasi dijalankan dengan role OWNER (bukan role app runtime)."""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

url = os.environ.get("DATABASE_MIGRATION_URL")
if not url:
    raise RuntimeError("DATABASE_MIGRATION_URL wajib di-set untuk migrasi")
# Alembic memakai driver sync
url = url.replace("+asyncpg", "+psycopg") if "+asyncpg" in url else url


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
