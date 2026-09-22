"""Kurir kustom per tenant (kurir lokal / kurir yang belum ada di daftar bawaan)

Revision ID: 0006_custom_couriers
Revises: 0005_shipping_returns
Create Date: 2026-09-25
"""
import os
import re

from alembic import op

revision = "0006_custom_couriers"
down_revision = "0005_shipping_returns"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE custom_couriers (
      id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id             uuid NOT NULL REFERENCES tenants(id),
      code                  varchar(30) NOT NULL CHECK (code ~ '^[a-z0-9][a-z0-9_]{{1,29}}$'),
      name                  varchar(80) NOT NULL,
      services              jsonb NOT NULL DEFAULT '[]',
      tracking_url_template varchar(300) CHECK (tracking_url_template IS NULL OR tracking_url_template ~ '^https://'),
      phone                 varchar(40) NOT NULL DEFAULT '',
      is_active             boolean NOT NULL DEFAULT true,
      created_at            timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, code)
    );
    ALTER TABLE custom_couriers ENABLE ROW LEVEL SECURITY;
    ALTER TABLE custom_couriers FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON custom_couriers
      USING (tenant_id = app_current_tenant() OR app_is_superadmin())
      WITH CHECK (tenant_id = app_current_tenant() OR app_is_superadmin());
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON custom_couriers TO {APP_ROLE};
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS custom_couriers CASCADE;")
