"""Phase 7: ramalan permintaan, risiko kehabisan stok, rekomendasi kurir, deteksi anomali

Revision ID: 0009_ai
Revises: 0008_scale
Create Date: 2026-09-27
"""
import os
import re

from alembic import op

revision = "0009_ai"
down_revision = "0008_scale"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")


def upgrade() -> None:
    op.execute("""
    -- Parameter pesan ulang dipakai untuk menghitung titik pesan ulang & stok pengaman
    ALTER TABLE tenant_settings
      ADD COLUMN lead_time_days integer NOT NULL DEFAULT 7 CHECK (lead_time_days BETWEEN 0 AND 180),
      ADD COLUMN service_level numeric(4,3) NOT NULL DEFAULT 0.950 CHECK (service_level BETWEEN 0.500 AND 0.999),
      ADD COLUMN cover_days integer NOT NULL DEFAULT 30 CHECK (cover_days BETWEEN 1 AND 365);

    -- Hasil ramalan disimpan supaya halaman cepat dibuka; dihitung ulang berkala oleh worker.
    CREATE TABLE demand_forecasts (
      tenant_id      uuid NOT NULL REFERENCES tenants(id),
      warehouse_id   uuid NOT NULL,
      sku_id         uuid NOT NULL,
      method         varchar(20) NOT NULL,
      daily_rate     numeric(12,4) NOT NULL,
      sigma          numeric(12,4) NOT NULL DEFAULT 0,
      weekday_factor jsonb NOT NULL DEFAULT '[]',
      history_days   integer NOT NULL DEFAULT 0,
      sold_30d       integer NOT NULL DEFAULT 0,
      generated_at   timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (warehouse_id, sku_id),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id),
      FOREIGN KEY (tenant_id, sku_id) REFERENCES skus(tenant_id, id)
    );
    CREATE INDEX ix_forecast_tenant ON demand_forecasts(tenant_id, generated_at);

    -- Temuan mesin deteksi anomali (order janggal, lonjakan, proses gudang melambat)
    CREATE TABLE anomalies (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL REFERENCES tenants(id),
      kind         varchar(30) NOT NULL,
      severity     varchar(10) NOT NULL DEFAULT 'WARNING' CHECK (severity IN ('INFO','WARNING','CRITICAL')),
      title        varchar(200) NOT NULL,
      detail       text NOT NULL DEFAULT '',
      score        numeric(8,2) NOT NULL DEFAULT 0,
      entity_type  varchar(20),
      entity_id    uuid,
      entity_label varchar(80),
      data         jsonb NOT NULL DEFAULT '{}',
      status       varchar(10) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACK','DISMISSED')),
      dedup_key    varchar(200) NOT NULL,
      decided_by   uuid,
      decided_at   timestamptz,
      created_at   timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, dedup_key)
    );
    CREATE INDEX ix_anomalies_open ON anomalies(tenant_id, status, created_at DESC);
    """)
    for t in ["demand_forecasts", "anomalies"]:
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
        GRANT SELECT, INSERT, UPDATE, DELETE ON demand_forecasts TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE ON anomalies TO {APP_ROLE};
      END IF;
    END $$;
    -- Fitur AI = paket Enterprise (bisa diubah super admin dari console tanpa deploy)
    UPDATE plans SET features = features || '["ai"]'::jsonb WHERE code = 'enterprise' AND NOT features ? 'ai';
    """)
    perms = {"ai:read": "Lihat ramalan permintaan, risiko stok, rekomendasi kurir & anomali",
             "ai:manage": "Tindak lanjuti temuan anomali"}
    roles = {"SUPER_ADMIN": list(perms), "TENANT_ADMIN": list(perms),
             "WAREHOUSE_MANAGER": ["ai:read", "ai:manage"], "FINANCE": ["ai:read"],
             "CUSTOMER_SERVICE": ["ai:read"], "VIEWER": ["ai:read"]}
    for code, desc in perms.items():
        op.execute(f"INSERT INTO permissions(code, description) VALUES ('{code}', '{desc}')")
    for role, ps in roles.items():
        for p in ps:
            op.execute(f"INSERT INTO role_permissions(role_code, permission_code) VALUES ('{role}', '{p}')")


def downgrade() -> None:
    op.execute("""
    DELETE FROM role_permissions WHERE permission_code LIKE 'ai:%';
    DELETE FROM permissions WHERE code LIKE 'ai:%';
    UPDATE plans SET features = features - 'ai';
    DROP TABLE IF EXISTS demand_forecasts, anomalies CASCADE;
    ALTER TABLE tenant_settings DROP COLUMN IF EXISTS lead_time_days, DROP COLUMN IF EXISTS service_level,
      DROP COLUMN IF EXISTS cover_days;
    """)
