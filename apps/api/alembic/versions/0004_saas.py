"""SaaS: paket, langganan, tagihan, API key

Revision ID: 0004_saas
Revises: 0003_wms
Create Date: 2026-09-23
"""
import json
import os
import re

from alembic import op

revision = "0004_saas"
down_revision = "0003_wms"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")

# Harga CONTOH — ubah dari console super admin (Admin platform → Paket) tanpa deploy ulang.
PLANS = [
    {"code": "starter", "name": "Starter", "sort": 1, "monthly": 299000, "yearly": 2990000,
     "description": "Order & stok untuk brand yang mulai tumbuh",
     "features": ["oms"], "limits": {"warehouses": 1, "users": 3, "skus": 500, "orders_per_month": 1000}},
    {"code": "growth", "name": "Growth", "sort": 2, "monthly": 899000, "yearly": 8990000,
     "description": "Gudang terstruktur dengan scanner, untuk brand multi-channel",
     "features": ["oms", "wms", "api_keys"],
     "limits": {"warehouses": 3, "users": 15, "skus": 5000, "orders_per_month": 10000}},
    {"code": "enterprise", "name": "Enterprise", "sort": 3, "monthly": 0, "yearly": 0,
     "description": "Multi-gudang tanpa batas, integrasi & dukungan prioritas (harga sesuai kontrak)",
     "features": ["oms", "wms", "api_keys", "priority_support"],
     "limits": {"warehouses": None, "users": None, "skus": None, "orders_per_month": None}},
]

TENANT_TABLES = ["subscriptions", "invoices", "api_keys"]


def upgrade() -> None:
    op.execute("""
    CREATE TABLE plans (
      code           varchar(30) PRIMARY KEY CHECK (code ~ '^[a-z0-9_-]+$'),
      name           varchar(60) NOT NULL,
      description    text NOT NULL DEFAULT '',
      price_monthly  numeric(14,2) NOT NULL DEFAULT 0 CHECK (price_monthly >= 0),
      price_yearly   numeric(14,2) NOT NULL DEFAULT 0 CHECK (price_yearly >= 0),
      features       jsonb NOT NULL DEFAULT '[]',
      limits         jsonb NOT NULL DEFAULT '{}',
      is_public      boolean NOT NULL DEFAULT true,
      self_serve     boolean NOT NULL DEFAULT true,
      sort_order     integer NOT NULL DEFAULT 0,
      updated_at     timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE subscriptions (
      id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id            uuid NOT NULL UNIQUE REFERENCES tenants(id),
      plan_code            varchar(30) NOT NULL REFERENCES plans(code),
      status               varchar(12) NOT NULL CHECK (status IN ('TRIALING','ACTIVE','PAST_DUE','SUSPENDED','CANCELLED')),
      billing_cycle        varchar(8) NOT NULL DEFAULT 'MONTHLY' CHECK (billing_cycle IN ('MONTHLY','YEARLY')),
      trial_ends_at        timestamptz,
      current_period_start timestamptz,
      current_period_end   timestamptz,
      grace_until          timestamptz,
      cancel_at_period_end boolean NOT NULL DEFAULT false,
      created_at           timestamptz NOT NULL DEFAULT now(),
      updated_at           timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE invoices (
      id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id       uuid NOT NULL REFERENCES tenants(id),
      number          varchar(32) NOT NULL,
      kind            varchar(10) NOT NULL CHECK (kind IN ('NEW','RENEWAL','CHANGE')),
      plan_code       varchar(30) NOT NULL REFERENCES plans(code),
      billing_cycle   varchar(8) NOT NULL CHECK (billing_cycle IN ('MONTHLY','YEARLY')),
      amount          numeric(14,2) NOT NULL CHECK (amount >= 0),
      currency        char(3) NOT NULL DEFAULT 'IDR',
      status          varchar(8) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','PAID','VOID','EXPIRED')),
      due_at          timestamptz NOT NULL,
      paid_at         timestamptz,
      provider        varchar(20) NOT NULL DEFAULT 'manual',
      payment_url     text,
      payment_ref     varchar(120),
      created_by      uuid,
      created_at      timestamptz NOT NULL DEFAULT now(),
      updated_at      timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number)
    );
    CREATE INDEX ix_invoices_tenant ON invoices(tenant_id, created_at DESC);

    CREATE TABLE api_keys (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL REFERENCES tenants(id),
      name         varchar(100) NOT NULL,
      prefix       varchar(16) NOT NULL,
      key_hash     char(64) NOT NULL UNIQUE,
      scopes       jsonb NOT NULL DEFAULT '[]',
      created_by   uuid,
      last_used_at timestamptz,
      expires_at   timestamptz,
      revoked_at   timestamptz,
      created_at   timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_api_keys_tenant ON api_keys(tenant_id);
    """)

    for t in ["plans", "subscriptions", "invoices"]:
        op.execute(f"CREATE TRIGGER trg_{t}_updated_at BEFORE UPDATE ON {t} "
                   f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();")
    for t in TENANT_TABLES:
        op.execute(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant() OR app_is_superadmin())
          WITH CHECK (tenant_id = app_current_tenant() OR app_is_superadmin());
        """)

    # Katalog paket boleh dibaca siapa saja (halaman harga publik); hanya super admin yang boleh mengubah
    op.execute("""
    ALTER TABLE plans ENABLE ROW LEVEL SECURITY;
    ALTER TABLE plans FORCE ROW LEVEL SECURITY;
    CREATE POLICY plans_read ON plans FOR SELECT USING (true);
    CREATE POLICY plans_admin_insert ON plans FOR INSERT WITH CHECK (app_is_superadmin());
    CREATE POLICY plans_admin_update ON plans FOR UPDATE USING (app_is_superadmin()) WITH CHECK (app_is_superadmin());
    """)

    for p in PLANS:
        op.execute(
            "INSERT INTO plans(code, name, description, price_monthly, price_yearly, features, limits, sort_order, "
            f"self_serve) VALUES ('{p['code']}', '{p['name']}', '{p['description']}', {p['monthly']}, {p['yearly']}, "
            f"'{json.dumps(p['features'])}', '{json.dumps(p['limits'])}', {p['sort']}, "
            f"{'false' if p['code'] == 'enterprise' else 'true'})")

    # Tenant yang sudah ada (selain platform) mendapat trial Growth 14 hari agar tidak ada yang terputus
    op.execute("""
    INSERT INTO subscriptions(tenant_id, plan_code, status, trial_ends_at)
    SELECT id, 'growth', 'TRIALING', now() + interval '14 days' FROM tenants WHERE slug <> 'platform'
    ON CONFLICT (tenant_id) DO NOTHING;
    """)

    op.execute(f"""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON plans, subscriptions, invoices, api_keys TO {APP_ROLE};
      END IF;
    END $$;
    """)

    perms = {"billing:read": "Lihat paket, pemakaian & tagihan", "billing:manage": "Ubah paket & bayar tagihan",
             "apikey:manage": "Kelola API key integrasi"}
    role_perms = {"SUPER_ADMIN": list(perms), "TENANT_ADMIN": list(perms), "FINANCE": ["billing:read"]}
    for code, desc in perms.items():
        op.execute(f"INSERT INTO permissions(code, description) VALUES ('{code}', '{desc}')")
    for role, ps in role_perms.items():
        for pc in ps:
            op.execute(f"INSERT INTO role_permissions(role_code, permission_code) VALUES ('{role}', '{pc}')")


def downgrade() -> None:
    op.execute("""
    DELETE FROM role_permissions WHERE permission_code IN ('billing:read','billing:manage','apikey:manage');
    DELETE FROM permissions WHERE code IN ('billing:read','billing:manage','apikey:manage');
    DROP TABLE IF EXISTS api_keys, invoices, subscriptions, plans CASCADE;
    """)
