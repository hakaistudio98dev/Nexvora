"""Phase 8: impor CSV massal & reservasi ulang otomatis

Revision ID: 0011_imports
Revises: 0010_assistant
Create Date: 2026-09-29
"""
import os
import re

from alembic import op

revision = "0011_imports"
down_revision = "0010_assistant"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")


def upgrade() -> None:
    op.execute(f"""
    -- Pekerjaan impor: file diperiksa dulu (PREVIEW), baru dijalankan setelah pengguna setuju
    CREATE TABLE import_jobs (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL REFERENCES tenants(id),
      kind         varchar(20) NOT NULL CHECK (kind IN ('products','stock')),
      filename     varchar(200) NOT NULL DEFAULT '',
      status       varchar(12) NOT NULL DEFAULT 'PREVIEW' CHECK (status IN ('PREVIEW','COMMITTED','FAILED','EXPIRED')),
      total_rows   integer NOT NULL DEFAULT 0,
      valid_rows   integer NOT NULL DEFAULT 0,
      rows         jsonb NOT NULL DEFAULT '[]',
      errors       jsonb NOT NULL DEFAULT '[]',
      result       jsonb NOT NULL DEFAULT '{{}}',
      created_by   uuid,
      created_at   timestamptz NOT NULL DEFAULT now(),
      committed_at timestamptz
    );
    CREATE INDEX ix_import_jobs_tenant ON import_jobs(tenant_id, created_at DESC);
    ALTER TABLE import_jobs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE import_jobs FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON import_jobs
      USING (tenant_id = app_current_tenant() OR app_is_superadmin())
      WITH CHECK (tenant_id = app_current_tenant() OR app_is_superadmin());
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON import_jobs TO {APP_ROLE};
      END IF;
    END $$;
    """)
    op.execute("INSERT INTO permissions(code, description) VALUES "
               "('import:run', 'Impor data massal dari file CSV')")
    for role in ("SUPER_ADMIN", "TENANT_ADMIN", "WAREHOUSE_MANAGER"):
        op.execute(f"INSERT INTO role_permissions(role_code, permission_code) VALUES ('{role}', 'import:run')")


def downgrade() -> None:
    op.execute("""
    DELETE FROM role_permissions WHERE permission_code = 'import:run';
    DELETE FROM permissions WHERE code = 'import:run';
    DROP TABLE IF EXISTS import_jobs CASCADE;
    """)
