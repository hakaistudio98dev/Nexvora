"""Phase 6: outbox event, indeks analitik, kesiapan skala

Revision ID: 0008_scale
Revises: 0007_analytics_notifications
Create Date: 2026-09-27
"""
import os
import re

from alembic import op

revision = "0008_scale"
down_revision = "0007_analytics_notifications"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")


def upgrade() -> None:
    op.execute("""
    -- Outbox: event bisnis ditulis dalam TRANSAKSI YANG SAMA dengan perubahan datanya,
    -- lalu dikirim keluar oleh worker dispatcher. Tidak ada event yang hilang saat proses gagal,
    -- dan urutannya terjaga lewat id yang menaik.
    CREATE TABLE outbox_events (
      id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id      uuid NOT NULL REFERENCES tenants(id),
      event_type     varchar(60) NOT NULL,
      aggregate_type varchar(40) NOT NULL,
      aggregate_id   varchar(64) NOT NULL,
      payload        jsonb NOT NULL DEFAULT '{}',
      status         varchar(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PUBLISHED','FAILED')),
      attempts       integer NOT NULL DEFAULT 0,
      last_error     varchar(500),
      available_at   timestamptz NOT NULL DEFAULT now(),
      published_at   timestamptz,
      correlation_id varchar(64),
      created_at     timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_outbox_due ON outbox_events(available_at, id) WHERE status = 'PENDING';
    CREATE INDEX ix_outbox_tenant ON outbox_events(tenant_id, created_at DESC);

    ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON outbox_events
      USING (tenant_id = app_current_tenant() OR app_is_superadmin())
      WITH CHECK (tenant_id = app_current_tenant() OR app_is_superadmin());

    -- Indeks untuk query analitik & laporan pada rentang tanggal
    CREATE INDEX IF NOT EXISTS ix_orders_placed_range ON orders(tenant_id, placed_at DESC);
    CREATE INDEX IF NOT EXISTS ix_order_items_order ON order_items(order_id);
    CREATE INDEX IF NOT EXISTS ix_order_items_sku ON order_items(tenant_id, sku_id);
    CREATE INDEX IF NOT EXISTS ix_shipments_handover ON shipments(tenant_id, handed_over_at);
    CREATE INDEX IF NOT EXISTS ix_packages_created ON packages(tenant_id, created_at);
    CREATE INDEX IF NOT EXISTS ix_tasks_completed ON wms_tasks(tenant_id, task_type, completed_at);
    CREATE INDEX IF NOT EXISTS ix_count_lines_counted ON cycle_count_lines(tenant_id, counted_at);
    CREATE INDEX IF NOT EXISTS ix_returns_created ON returns(tenant_id, created_at);
    CREATE INDEX IF NOT EXISTS ix_exceptions_order ON wms_exceptions(tenant_id, order_id);
    CREATE INDEX IF NOT EXISTS ix_ledger_time ON inventory_ledger(tenant_id, created_at);
    """)
    op.execute(f"""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON outbox_events TO {APP_ROLE};
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS outbox_events CASCADE;
    DROP INDEX IF EXISTS ix_orders_placed_range; DROP INDEX IF EXISTS ix_order_items_order;
    DROP INDEX IF EXISTS ix_order_items_sku; DROP INDEX IF EXISTS ix_shipments_handover;
    DROP INDEX IF EXISTS ix_packages_created; DROP INDEX IF EXISTS ix_tasks_completed;
    DROP INDEX IF EXISTS ix_count_lines_counted; DROP INDEX IF EXISTS ix_returns_created;
    DROP INDEX IF EXISTS ix_exceptions_order; DROP INDEX IF EXISTS ix_ledger_time;
    """)
