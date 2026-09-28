"""Phase 8: asisten dalam aplikasi (tanya data & jalankan perintah)

Revision ID: 0010_assistant
Revises: 0009_ai
Create Date: 2026-09-28
"""
import os
import re

from alembic import op

revision = "0010_assistant"
down_revision = "0009_ai"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")


def upgrade() -> None:
    op.execute(f"""
    -- Riwayat percakapan asisten: dipakai untuk konteks lanjutan & jejak audit siapa memerintah apa
    CREATE TABLE assistant_messages (
      id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id   uuid NOT NULL REFERENCES tenants(id),
      user_id     uuid NOT NULL,
      role        varchar(10) NOT NULL CHECK (role IN ('user','assistant')),
      content     text NOT NULL,
      tool_calls  jsonb NOT NULL DEFAULT '[]',
      engine      varchar(10),
      created_at  timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_assistant_thread ON assistant_messages(tenant_id, user_id, created_at DESC);
    ALTER TABLE assistant_messages ENABLE ROW LEVEL SECURITY;
    ALTER TABLE assistant_messages FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON assistant_messages
      USING (tenant_id = app_current_tenant() OR app_is_superadmin())
      WITH CHECK (tenant_id = app_current_tenant() OR app_is_superadmin());
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, DELETE ON assistant_messages TO {APP_ROLE};
        GRANT USAGE ON SEQUENCE assistant_messages_id_seq TO {APP_ROLE};
      END IF;
    END $$;
    """)
    op.execute("INSERT INTO permissions(code, description) VALUES "
               "('assistant:use', 'Bertanya & memberi perintah lewat asisten')")
    for role in ("SUPER_ADMIN", "TENANT_ADMIN", "WAREHOUSE_MANAGER", "WAREHOUSE_OPERATOR",
                 "CUSTOMER_SERVICE", "FINANCE", "VIEWER"):
        op.execute(f"INSERT INTO role_permissions(role_code, permission_code) VALUES ('{role}', 'assistant:use')")


def downgrade() -> None:
    op.execute("""
    DELETE FROM role_permissions WHERE permission_code = 'assistant:use';
    DELETE FROM permissions WHERE code = 'assistant:use';
    DROP TABLE IF EXISTS assistant_messages CASCADE;
    """)
