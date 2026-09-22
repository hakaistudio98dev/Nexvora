"""Phase 2: OMS (order hub + state machine), inventory ledger, reservation, idempotency

Revision ID: 0002_oms_inventory
Revises: 0001_foundation
Create Date: 2026-09-21
"""
import os
import re

from alembic import op

revision = "0002_oms_inventory"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")

TENANT_TABLES = [
    "inventory_balances", "inventory_ledger", "reservations", "orders", "order_items",
    "order_status_history", "order_counters", "idempotency_keys",
]

ORDER_STATUSES = ("CREATED", "PAID", "ALLOCATED", "PICKING", "PACKING", "READY_TO_SHIP", "SHIPPED",
                  "DELIVERED", "CANCELLED", "FAILED", "RETURN_REQUESTED", "RETURNED", "REFUNDED")


def _in(values) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.execute(f"""
    ALTER TABLE skus ADD CONSTRAINT uq_skus_tenant_id UNIQUE (tenant_id, id);

    -- ===================== inventory balance (1 baris per gudang x SKU) =====================
    CREATE TABLE inventory_balances (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL,
      warehouse_id uuid NOT NULL,
      sku_id       uuid NOT NULL,
      on_hand      integer NOT NULL DEFAULT 0,
      reserved     integer NOT NULL DEFAULT 0,
      damaged      integer NOT NULL DEFAULT 0,
      in_transit   integer NOT NULL DEFAULT 0,
      returned     integer NOT NULL DEFAULT 0,
      available    integer GENERATED ALWAYS AS (on_hand - reserved) STORED,
      version      integer NOT NULL DEFAULT 0,
      updated_at   timestamptz NOT NULL DEFAULT now(),
      UNIQUE (warehouse_id, sku_id),
      UNIQUE (tenant_id, id),
      -- Invarian stok dijaga database: tidak pernah minus, reserved tidak melebihi fisik
      CHECK (on_hand >= 0 AND reserved >= 0 AND damaged >= 0 AND in_transit >= 0 AND returned >= 0),
      CHECK (reserved <= on_hand),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id, sku_id)       REFERENCES skus(tenant_id, id)       ON DELETE RESTRICT
    );
    CREATE INDEX ix_inv_bal_sku ON inventory_balances(tenant_id, sku_id);

    -- ===================== inventory ledger (immutable) =====================
    CREATE TABLE inventory_ledger (
      id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id        uuid NOT NULL,
      warehouse_id     uuid NOT NULL,
      sku_id           uuid NOT NULL,
      location_id      uuid,
      entry_type       varchar(20) NOT NULL CHECK (entry_type IN
                         ('RECEIPT','ADJUSTMENT','RESERVE','RELEASE','SHIP','DAMAGE','RETURN')),
      d_on_hand        integer NOT NULL DEFAULT 0,
      d_reserved       integer NOT NULL DEFAULT 0,
      d_damaged        integer NOT NULL DEFAULT 0,
      on_hand_after    integer NOT NULL,
      reserved_after   integer NOT NULL,
      damaged_after    integer NOT NULL,
      reason_code      varchar(40),
      note             text,
      reference_type   varchar(30),
      reference_id     varchar(64),
      actor_user_id    uuid,
      correlation_id   varchar(64),
      created_at       timestamptz NOT NULL DEFAULT now(),
      CHECK (d_on_hand <> 0 OR d_reserved <> 0 OR d_damaged <> 0),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id),
      FOREIGN KEY (tenant_id, sku_id)       REFERENCES skus(tenant_id, id),
      FOREIGN KEY (tenant_id, location_id)  REFERENCES locations(tenant_id, id)
    );
    CREATE INDEX ix_ledger_sku ON inventory_ledger(tenant_id, sku_id, id DESC);
    CREATE INDEX ix_ledger_wh  ON inventory_ledger(tenant_id, warehouse_id, id DESC);
    CREATE INDEX ix_ledger_ref ON inventory_ledger(tenant_id, reference_type, reference_id);

    CREATE OR REPLACE FUNCTION append_only() RETURNS trigger LANGUAGE plpgsql AS
    $$ BEGIN RAISE EXCEPTION '% bersifat append-only', TG_TABLE_NAME; END $$;
    CREATE TRIGGER trg_ledger_immutable BEFORE UPDATE OR DELETE ON inventory_ledger
      FOR EACH ROW EXECUTE FUNCTION append_only();
    CREATE TRIGGER trg_ledger_no_truncate BEFORE TRUNCATE ON inventory_ledger
      FOR EACH STATEMENT EXECUTE FUNCTION append_only();

    -- ===================== orders =====================
    CREATE TABLE order_counters (
      tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
      last_no   bigint NOT NULL DEFAULT 0
    );

    CREATE TABLE orders (
      id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id        uuid NOT NULL REFERENCES tenants(id),
      order_number     varchar(32) NOT NULL,
      channel          varchar(20) NOT NULL CHECK (channel IN
                         ('MANUAL','API','WEBSITE','SHOPEE','TOKOPEDIA','TIKTOK','LAZADA','OTHER')),
      external_ref     varchar(100),
      status           varchar(20) NOT NULL DEFAULT 'CREATED' CHECK (status IN {_in(ORDER_STATUSES)}),
      payment_status   varchar(20) NOT NULL DEFAULT 'UNPAID' CHECK (payment_status IN ('UNPAID','PAID','FAILED','REFUNDED')),
      stock_status     varchar(20) NOT NULL DEFAULT 'PENDING' CHECK (stock_status IN
                         ('PENDING','RESERVED','OUT_OF_STOCK','RELEASED','CONSUMED')),
      warehouse_id     uuid,
      allocation_note  varchar(300),
      customer_name    varchar(200) NOT NULL,
      customer_phone   varchar(40) NOT NULL DEFAULT '',
      customer_email   varchar(254) NOT NULL DEFAULT '',
      ship_address     text NOT NULL,
      ship_city        varchar(100) NOT NULL,
      ship_province    varchar(100) NOT NULL DEFAULT '',
      ship_postal_code varchar(12) NOT NULL DEFAULT '',
      ship_country     char(2) NOT NULL DEFAULT 'ID',
      currency         char(3) NOT NULL DEFAULT 'IDR',
      subtotal         numeric(14,2) NOT NULL CHECK (subtotal >= 0),
      shipping_fee     numeric(14,2) NOT NULL DEFAULT 0 CHECK (shipping_fee >= 0),
      discount         numeric(14,2) NOT NULL DEFAULT 0 CHECK (discount >= 0),
      total            numeric(14,2) NOT NULL CHECK (total >= 0),
      notes            text NOT NULL DEFAULT '',
      status_reason    varchar(300),
      placed_at        timestamptz NOT NULL DEFAULT now(),
      paid_at          timestamptz,
      allocated_at     timestamptz,
      shipped_at       timestamptz,
      delivered_at     timestamptz,
      cancelled_at     timestamptz,
      created_by       uuid,
      version          integer NOT NULL DEFAULT 0,
      created_at       timestamptz NOT NULL DEFAULT now(),
      updated_at       timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, order_number),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id)
    );
    -- Satu order marketplace hanya boleh masuk sekali
    CREATE UNIQUE INDEX ux_orders_external ON orders(tenant_id, channel, external_ref) WHERE external_ref IS NOT NULL;
    CREATE INDEX ix_orders_status ON orders(tenant_id, status, placed_at DESC);
    CREATE INDEX ix_orders_placed ON orders(tenant_id, placed_at DESC);

    CREATE TABLE order_items (
      id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id  uuid NOT NULL,
      order_id   uuid NOT NULL,
      sku_id     uuid NOT NULL,
      quantity   integer NOT NULL CHECK (quantity > 0),
      unit_price numeric(14,2) NOT NULL CHECK (unit_price >= 0),
      line_total numeric(14,2) NOT NULL CHECK (line_total >= 0),
      UNIQUE (order_id, sku_id),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id, sku_id)   REFERENCES skus(tenant_id, id)
    );

    CREATE TABLE order_status_history (
      id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id     uuid NOT NULL,
      order_id      uuid NOT NULL,
      from_status   varchar(20),
      to_status     varchar(20) NOT NULL,
      reason        varchar(300),
      actor_user_id uuid,
      created_at    timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, id)
    );
    CREATE INDEX ix_osh_order ON order_status_history(order_id, id);
    CREATE TRIGGER trg_osh_immutable BEFORE UPDATE OR DELETE ON order_status_history
      FOR EACH ROW EXECUTE FUNCTION append_only();

    -- ===================== reservations =====================
    CREATE TABLE reservations (
      id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id     uuid NOT NULL,
      order_id      uuid NOT NULL,
      order_item_id uuid NOT NULL,
      warehouse_id  uuid NOT NULL,
      sku_id        uuid NOT NULL,
      quantity      integer NOT NULL CHECK (quantity > 0),
      status        varchar(12) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','RELEASED','CONSUMED','EXPIRED')),
      expires_at    timestamptz,
      created_at    timestamptz NOT NULL DEFAULT now(),
      updated_at    timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, order_id)      REFERENCES orders(tenant_id, id),
      FOREIGN KEY (tenant_id, order_item_id) REFERENCES order_items(tenant_id, id),
      FOREIGN KEY (tenant_id, warehouse_id)  REFERENCES warehouses(tenant_id, id),
      FOREIGN KEY (tenant_id, sku_id)        REFERENCES skus(tenant_id, id)
    );
    -- Maksimal satu reservasi aktif per item order
    CREATE UNIQUE INDEX ux_res_active_item ON reservations(order_item_id) WHERE status = 'ACTIVE';
    CREATE INDEX ix_res_expiry ON reservations(expires_at) WHERE status = 'ACTIVE' AND expires_at IS NOT NULL;
    CREATE INDEX ix_res_order ON reservations(order_id);

    -- ===================== idempotency =====================
    CREATE TABLE idempotency_keys (
      tenant_id       uuid NOT NULL REFERENCES tenants(id),
      key             varchar(100) NOT NULL,
      endpoint        varchar(100) NOT NULL,
      request_hash    char(64) NOT NULL,
      response_status integer,
      response_body   jsonb,
      created_at      timestamptz NOT NULL DEFAULT now(),
      expires_at      timestamptz NOT NULL,
      PRIMARY KEY (tenant_id, endpoint, key)
    );
    CREATE INDEX ix_idem_expiry ON idempotency_keys(expires_at);
    """)

    for t in ["orders", "reservations"]:
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

    op.execute(f"""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON inventory_balances, orders, order_items, reservations, order_counters
          TO {APP_ROLE};
        GRANT SELECT, INSERT ON inventory_ledger, order_status_history TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE, DELETE ON idempotency_keys TO {APP_ROLE};
      END IF;
    END $$;
    """)

    perms = {
        "order:read": "Lihat order",
        "order:write": "Buat order, tandai bayar, batalkan",
        "order:fulfill": "Jalankan tahap fulfillment (picking s/d delivered)",
        "inventory:read": "Lihat stok dan ledger",
        "inventory:write": "Terima stok (receipt)",
        "inventory:adjust": "Penyesuaian stok (wajib alasan)",
    }
    role_perms = {
        "SUPER_ADMIN": list(perms),
        "TENANT_ADMIN": list(perms),
        "WAREHOUSE_MANAGER": ["order:read", "order:fulfill", "inventory:read", "inventory:write", "inventory:adjust"],
        "WAREHOUSE_OPERATOR": ["order:read", "order:fulfill", "inventory:read"],
        "FINANCE": ["order:read", "inventory:read"],
        "CUSTOMER_SERVICE": ["order:read", "order:write", "inventory:read"],
        "VIEWER": ["order:read", "inventory:read"],
    }
    for code, desc in perms.items():
        op.execute(f"INSERT INTO permissions(code, description) VALUES ('{code}', '{desc}')")
    for role, ps in role_perms.items():
        for p in ps:
            op.execute(f"INSERT INTO role_permissions(role_code, permission_code) VALUES ('{role}', '{p}')")


def downgrade() -> None:
    op.execute("""
    DELETE FROM role_permissions WHERE permission_code IN
      ('order:read','order:write','order:fulfill','inventory:read','inventory:write','inventory:adjust');
    DELETE FROM permissions WHERE code IN
      ('order:read','order:write','order:fulfill','inventory:read','inventory:write','inventory:adjust');
    DROP TABLE IF EXISTS idempotency_keys, reservations, order_status_history, order_items, orders,
      order_counters, inventory_ledger, inventory_balances CASCADE;
    DROP FUNCTION IF EXISTS append_only();
    ALTER TABLE skus DROP CONSTRAINT IF EXISTS uq_skus_tenant_id;
    """)
