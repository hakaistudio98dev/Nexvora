"""Phase 1 foundation: tenant, identity, RBAC, catalog, warehouse, audit + RLS

Revision ID: 0001_foundation
Revises:
Create Date: 2026-09-20
"""
import os
import re

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")

# Tabel yang datanya milik satu tenant -> wajib RLS.
TENANT_TABLES = [
    "users", "user_roles", "refresh_tokens",
    "products", "skus", "warehouses", "locations",
]


def upgrade() -> None:
    op.execute(r"""
    -- ===================== helper functions =====================
    CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
      LANGUAGE sql STABLE AS
    $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;

    CREATE OR REPLACE FUNCTION app_is_superadmin() RETURNS boolean
      LANGUAGE sql STABLE AS
    $$ SELECT coalesce(current_setting('app.is_superadmin', true), '') = 'on' $$;

    CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
      LANGUAGE plpgsql AS
    $$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

    -- ===================== tenants =====================
    CREATE TABLE tenants (
      id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      slug        varchar(63) NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
      name        varchar(200) NOT NULL,
      status      varchar(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUSPENDED')),
      created_at  timestamptz NOT NULL DEFAULT now(),
      updated_at  timestamptz NOT NULL DEFAULT now()
    );

    -- Lookup tenant saat login (sebelum konteks tenant ada), tanpa membuka RLS tabel tenants.
    CREATE OR REPLACE FUNCTION resolve_tenant_by_slug(p_slug text)
      RETURNS TABLE(id uuid, status varchar)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
    $$ SELECT t.id, t.status FROM tenants t WHERE t.slug = lower(p_slug) $$;
    REVOKE ALL ON FUNCTION resolve_tenant_by_slug(text) FROM PUBLIC;

    -- ===================== RBAC (katalog global, read-only untuk app) =====================
    CREATE TABLE roles (
      code        varchar(50) PRIMARY KEY,
      name        varchar(100) NOT NULL,
      description text NOT NULL DEFAULT ''
    );
    CREATE TABLE permissions (
      code        varchar(80) PRIMARY KEY,
      description text NOT NULL DEFAULT ''
    );
    CREATE TABLE role_permissions (
      role_code       varchar(50) REFERENCES roles(code) ON DELETE CASCADE,
      permission_code varchar(80) REFERENCES permissions(code) ON DELETE CASCADE,
      PRIMARY KEY (role_code, permission_code)
    );

    -- ===================== users =====================
    CREATE TABLE users (
      id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
      email              varchar(254) NOT NULL CHECK (email = lower(email)),
      full_name          varchar(200) NOT NULL,
      password_hash      text NOT NULL,
      is_active          boolean NOT NULL DEFAULT true,
      failed_login_count integer NOT NULL DEFAULT 0,
      locked_until       timestamptz,
      token_version      integer NOT NULL DEFAULT 0,
      last_login_at      timestamptz,
      created_at         timestamptz NOT NULL DEFAULT now(),
      updated_at         timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, email),
      UNIQUE (tenant_id, id)
    );

    CREATE TABLE user_roles (
      tenant_id  uuid NOT NULL,
      user_id    uuid NOT NULL,
      role_code  varchar(50) NOT NULL REFERENCES roles(code),
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (user_id, role_code),
      FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE
    );

    CREATE TABLE refresh_tokens (
      id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id   uuid NOT NULL,
      user_id     uuid NOT NULL,
      family_id   uuid NOT NULL,
      token_hash  char(64) NOT NULL UNIQUE,
      expires_at  timestamptz NOT NULL,
      revoked_at  timestamptz,
      replaced_by uuid,
      user_agent  varchar(300),
      ip          varchar(64),
      created_at  timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX ix_refresh_tokens_family ON refresh_tokens(family_id);

    -- ===================== catalog =====================
    CREATE TABLE products (
      id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
      code        varchar(64) NOT NULL,
      name        varchar(200) NOT NULL,
      description text NOT NULL DEFAULT '',
      is_active   boolean NOT NULL DEFAULT true,
      created_at  timestamptz NOT NULL DEFAULT now(),
      updated_at  timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, code),
      UNIQUE (tenant_id, id)
    );

    CREATE TABLE skus (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL,
      product_id   uuid NOT NULL,
      sku_code     varchar(64) NOT NULL,
      barcode      varchar(64),
      variant_name varchar(120) NOT NULL DEFAULT '',
      unit         varchar(20) NOT NULL DEFAULT 'PCS',
      length_mm    integer CHECK (length_mm IS NULL OR length_mm > 0),
      width_mm     integer CHECK (width_mm  IS NULL OR width_mm  > 0),
      height_mm    integer CHECK (height_mm IS NULL OR height_mm > 0),
      weight_g     integer CHECK (weight_g  IS NULL OR weight_g  > 0),
      is_active    boolean NOT NULL DEFAULT true,
      created_at   timestamptz NOT NULL DEFAULT now(),
      updated_at   timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, sku_code),
      UNIQUE (tenant_id, barcode),
      -- FK komposit: SKU tidak mungkin menunjuk produk milik tenant lain
      FOREIGN KEY (tenant_id, product_id) REFERENCES products(tenant_id, id) ON DELETE RESTRICT
    );

    -- ===================== warehouse =====================
    CREATE TABLE warehouses (
      id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
      code       varchar(32) NOT NULL CHECK (code ~ '^[A-Z0-9-]+$'),
      name       varchar(200) NOT NULL,
      address    text NOT NULL DEFAULT '',
      city       varchar(100) NOT NULL DEFAULT '',
      timezone   varchar(64) NOT NULL DEFAULT 'Asia/Jakarta',
      is_active  boolean NOT NULL DEFAULT true,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, code),
      UNIQUE (tenant_id, id)
    );

    CREATE TABLE locations (
      id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id     uuid NOT NULL,
      warehouse_id  uuid NOT NULL,
      parent_id     uuid,
      type          varchar(10) NOT NULL CHECK (type IN ('ZONE','RACK','SHELF','BIN')),
      code          varchar(32) NOT NULL CHECK (code ~ '^[A-Z0-9]+$'),
      full_code     varchar(160) NOT NULL,
      is_active     boolean NOT NULL DEFAULT true,
      created_at    timestamptz NOT NULL DEFAULT now(),
      updated_at    timestamptz NOT NULL DEFAULT now(),
      UNIQUE (warehouse_id, full_code),
      UNIQUE (tenant_id, id),
      CHECK ((type = 'ZONE') = (parent_id IS NULL)),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id, parent_id)    REFERENCES locations(tenant_id, id)  ON DELETE RESTRICT
    );
    CREATE INDEX ix_locations_parent ON locations(parent_id);

    -- ===================== audit (append-only) =====================
    CREATE TABLE audit_logs (
      id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id      uuid REFERENCES tenants(id),
      actor_user_id  uuid,
      action         varchar(80) NOT NULL,
      entity_type    varchar(60) NOT NULL,
      entity_id      varchar(64),
      before         jsonb,
      after          jsonb,
      correlation_id varchar(64),
      ip             varchar(64),
      created_at     timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_audit_tenant_time ON audit_logs(tenant_id, created_at DESC);

    CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger
      LANGUAGE plpgsql AS
    $$ BEGIN RAISE EXCEPTION 'audit_logs bersifat append-only'; END $$;
    CREATE TRIGGER trg_audit_logs_immutable
      BEFORE UPDATE OR DELETE ON audit_logs
      FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();
    CREATE TRIGGER trg_audit_logs_no_truncate
      BEFORE TRUNCATE ON audit_logs
      FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_immutable();
    """)

    # updated_at triggers
    for t in ["tenants", "users", "products", "skus", "warehouses", "locations"]:
        op.execute(
            f"CREATE TRIGGER trg_{t}_updated_at BEFORE UPDATE ON {t} "
            f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
        )

    # ===================== Row-Level Security =====================
    for t in TENANT_TABLES:
        op.execute(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant() OR app_is_superadmin())
          WITH CHECK (tenant_id = app_current_tenant() OR app_is_superadmin());
        """)

    op.execute("""
    ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
    ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_self ON tenants
      USING (id = app_current_tenant() OR app_is_superadmin())
      WITH CHECK (app_is_superadmin());

    ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
    CREATE POLICY audit_read ON audit_logs FOR SELECT
      USING (tenant_id = app_current_tenant() OR app_is_superadmin());
    CREATE POLICY audit_insert ON audit_logs FOR INSERT
      WITH CHECK (tenant_id = app_current_tenant() OR app_is_superadmin());
    """)

    # ===================== least-privilege grants =====================
    op.execute(f"""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON tenants TO {APP_ROLE};
        GRANT SELECT ON roles, permissions, role_permissions TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE, DELETE ON users, user_roles, refresh_tokens TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE ON products, skus, warehouses, locations TO {APP_ROLE};
        GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE};
        GRANT EXECUTE ON FUNCTION resolve_tenant_by_slug(text) TO {APP_ROLE};
      END IF;
    END $$;
    """)

    _seed_rbac()


ROLES = {
    "SUPER_ADMIN": ("Super Admin", "Operator platform lintas tenant"),
    "TENANT_ADMIN": ("Tenant Admin", "Admin penuh untuk satu tenant"),
    "WAREHOUSE_MANAGER": ("Warehouse Manager", "Kelola gudang, lokasi, dan operasi"),
    "WAREHOUSE_OPERATOR": ("Warehouse Operator", "Eksekusi task gudang"),
    "FINANCE": ("Finance", "Billing dan laporan biaya"),
    "CUSTOMER_SERVICE": ("Customer Service", "Lihat order dan produk"),
    "VIEWER": ("Viewer", "Hanya baca"),
}

PERMISSIONS = {
    "tenant:read": "Lihat profil tenant",
    "tenant:manage": "Buat dan ubah tenant (platform)",
    "user:read": "Lihat pengguna",
    "user:write": "Buat/ubah pengguna dan perannya",
    "role:read": "Lihat peran dan izin",
    "product:read": "Lihat produk dan SKU",
    "product:write": "Buat/ubah produk dan SKU",
    "warehouse:read": "Lihat gudang dan lokasi",
    "warehouse:write": "Buat/ubah gudang dan lokasi",
    "audit:read": "Lihat audit log",
}

ROLE_PERMS = {
    "SUPER_ADMIN": list(PERMISSIONS),
    "TENANT_ADMIN": [p for p in PERMISSIONS if p != "tenant:manage"],
    "WAREHOUSE_MANAGER": ["tenant:read", "product:read", "warehouse:read", "warehouse:write", "user:read"],
    "WAREHOUSE_OPERATOR": ["product:read", "warehouse:read"],
    "FINANCE": ["tenant:read", "product:read", "warehouse:read", "audit:read"],
    "CUSTOMER_SERVICE": ["tenant:read", "product:read", "warehouse:read"],
    "VIEWER": ["tenant:read", "product:read", "warehouse:read"],
}


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _seed_rbac() -> None:
    for code, (name, desc) in ROLES.items():
        op.execute(f"INSERT INTO roles(code, name, description) VALUES ({_q(code)}, {_q(name)}, {_q(desc)})")
    for code, desc in PERMISSIONS.items():
        op.execute(f"INSERT INTO permissions(code, description) VALUES ({_q(code)}, {_q(desc)})")
    for role, perms in ROLE_PERMS.items():
        for p in perms:
            op.execute(f"INSERT INTO role_permissions(role_code, permission_code) VALUES ({_q(role)}, {_q(p)})")


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS audit_logs, locations, warehouses, skus, products,
      refresh_tokens, user_roles, users, role_permissions, permissions, roles, tenants CASCADE;
    DROP FUNCTION IF EXISTS resolve_tenant_by_slug(text);
    DROP FUNCTION IF EXISTS audit_logs_immutable();
    DROP FUNCTION IF EXISTS set_updated_at();
    DROP FUNCTION IF EXISTS app_is_superadmin();
    DROP FUNCTION IF EXISTS app_current_tenant();
    """)
