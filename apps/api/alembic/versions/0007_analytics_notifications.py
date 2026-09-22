"""Phase 5: pengaturan SLA, reorder point, notifikasi (in-app, email, webhook), heartbeat sistem

Revision ID: 0007_analytics_notifications
Revises: 0006_custom_couriers
Create Date: 2026-09-26
"""
import os
import re

from alembic import op

revision = "0007_analytics_notifications"
down_revision = "0006_custom_couriers"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")

TENANT_TABLES = ["tenant_settings", "notifications", "notification_channels", "notification_deliveries"]


def upgrade() -> None:
    op.execute("""
    CREATE TABLE tenant_settings (
      tenant_id           uuid PRIMARY KEY REFERENCES tenants(id),
      sla_ship_hours      integer NOT NULL DEFAULT 24 CHECK (sla_ship_hours BETWEEN 1 AND 720),
      sla_risk_hours      integer NOT NULL DEFAULT 4 CHECK (sla_risk_hours BETWEEN 0 AND 168),
      low_stock_threshold integer NOT NULL DEFAULT 5 CHECK (low_stock_threshold >= 0),
      timezone            varchar(64) NOT NULL DEFAULT 'Asia/Jakarta',
      updated_at          timestamptz NOT NULL DEFAULT now()
    );

    -- Titik pesan ulang per SKU (menimpa batas stok menipis default tenant)
    ALTER TABLE skus ADD COLUMN reorder_point integer CHECK (reorder_point IS NULL OR reorder_point >= 0);

    CREATE TABLE notifications (
      id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id   uuid NOT NULL REFERENCES tenants(id),
      event_type  varchar(40) NOT NULL,
      severity    varchar(10) NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO','WARNING','CRITICAL')),
      title       varchar(200) NOT NULL,
      body        text NOT NULL DEFAULT '',
      data        jsonb NOT NULL DEFAULT '{}',
      link        varchar(300),
      in_app      boolean NOT NULL DEFAULT true,
      dedup_key   varchar(200),
      read_at     timestamptz,
      created_at  timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    );
    -- Notifikasi yang sama (mis. SLA order X terlambat) hanya dibuat sekali
    CREATE UNIQUE INDEX ux_notifications_dedup ON notifications(tenant_id, dedup_key) WHERE dedup_key IS NOT NULL;
    CREATE INDEX ix_notifications_inbox ON notifications(tenant_id, in_app, created_at DESC);

    CREATE TABLE notification_channels (
      id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id   uuid NOT NULL REFERENCES tenants(id),
      kind        varchar(10) NOT NULL CHECK (kind IN ('EMAIL','WEBHOOK')),
      name        varchar(100) NOT NULL,
      target      varchar(500) NOT NULL,
      secret_enc  text,
      events      jsonb NOT NULL DEFAULT '[]',
      is_active   boolean NOT NULL DEFAULT true,
      created_at  timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    );

    CREATE TABLE notification_deliveries (
      id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id       uuid NOT NULL,
      notification_id uuid NOT NULL,
      channel_id      uuid NOT NULL,
      status          varchar(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENT','FAILED')),
      attempts        integer NOT NULL DEFAULT 0,
      last_error      varchar(500),
      response_code   integer,
      next_attempt_at timestamptz NOT NULL DEFAULT now(),
      sent_at         timestamptz,
      created_at      timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, notification_id) REFERENCES notifications(tenant_id, id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id, channel_id)      REFERENCES notification_channels(tenant_id, id)
    );
    CREATE INDEX ix_deliveries_due ON notification_deliveries(next_attempt_at) WHERE status = 'PENDING';
    CREATE INDEX ix_deliveries_channel ON notification_deliveries(channel_id, created_at DESC);

    -- Detak worker (global, dibaca NOC)
    CREATE TABLE system_heartbeats (
      name       varchar(50) PRIMARY KEY,
      beat_at    timestamptz NOT NULL DEFAULT now(),
      info       jsonb NOT NULL DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS ix_orders_paid ON orders(tenant_id, paid_at);
    CREATE INDEX IF NOT EXISTS ix_orders_shipped ON orders(tenant_id, shipped_at);
    """)
    for t in TENANT_TABLES:
        op.execute(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant() OR app_is_superadmin())
          WITH CHECK (tenant_id = app_current_tenant() OR app_is_superadmin());
        """)
    op.execute(f"""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON tenant_settings, notifications, notification_channels,
          notification_deliveries, system_heartbeats TO {APP_ROLE};
      END IF;
    END $$;
    -- Analitik lanjutan & webhook = paket Growth ke atas
    UPDATE plans SET features = features || '["analytics","webhooks"]'::jsonb
      WHERE code IN ('growth','enterprise') AND NOT features ? 'analytics';
    """)
    perms = {"analytics:read": "Lihat dashboard analitik & laporan", "settings:manage": "Ubah pengaturan operasional (SLA, stok menipis)",
             "notification:read": "Lihat notifikasi", "notification:manage": "Kelola kanal notifikasi (email, webhook)"}
    role_perms = {
        "SUPER_ADMIN": list(perms), "TENANT_ADMIN": list(perms),
        "WAREHOUSE_MANAGER": ["analytics:read", "notification:read"], "WAREHOUSE_OPERATOR": ["notification:read"],
        "FINANCE": ["analytics:read", "notification:read"], "CUSTOMER_SERVICE": ["analytics:read", "notification:read"],
        "VIEWER": ["analytics:read", "notification:read"],
    }
    for code, desc in perms.items():
        op.execute(f"INSERT INTO permissions(code, description) VALUES ('{code}', '{desc}')")
    for role, ps in role_perms.items():
        for p in ps:
            op.execute(f"INSERT INTO role_permissions(role_code, permission_code) VALUES ('{role}', '{p}')")


def downgrade() -> None:
    op.execute("""
    DELETE FROM role_permissions WHERE permission_code IN ('analytics:read','settings:manage','notification:read','notification:manage');
    DELETE FROM permissions WHERE code IN ('analytics:read','settings:manage','notification:read','notification:manage');
    UPDATE plans SET features = (features - 'analytics') - 'webhooks';
    DROP TABLE IF EXISTS notification_deliveries, notification_channels, notifications, tenant_settings, system_heartbeats CASCADE;
    ALTER TABLE skus DROP COLUMN IF EXISTS reorder_point;
    DROP INDEX IF EXISTS ix_orders_paid; DROP INDEX IF EXISTS ix_orders_shipped;
    """)
