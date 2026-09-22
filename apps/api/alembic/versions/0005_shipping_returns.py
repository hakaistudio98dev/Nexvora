"""Phase 4: pengiriman (akun kurir, shipment, tracking, manifest serah terima) & retur (RMA)

Revision ID: 0005_shipping_returns
Revises: 0004_saas
Create Date: 2026-09-24
"""
import os
import re

from alembic import op

revision = "0005_shipping_returns"
down_revision = "0004_saas"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")

TENANT_TABLES = ["courier_accounts", "manifests", "shipments", "tracking_events", "returns", "return_lines"]


def upgrade() -> None:
    op.execute("""
    -- Alamat asal pengiriman
    ALTER TABLE warehouses ADD COLUMN postal_code varchar(12) NOT NULL DEFAULT '',
                           ADD COLUMN phone varchar(40) NOT NULL DEFAULT '',
                           ADD COLUMN contact_name varchar(120) NOT NULL DEFAULT '';

    -- Bucket "returned" ikut dilacak ledger (barang retur diterima, menunggu inspeksi)
    ALTER TABLE inventory_ledger ADD COLUMN d_returned integer NOT NULL DEFAULT 0,
                                 ADD COLUMN returned_after integer NOT NULL DEFAULT 0;
    ALTER TABLE inventory_ledger DROP CONSTRAINT inventory_ledger_check;
    -- NOT VALID: baris lama sudah lolos constraint sebelumnya; semua baris baru tetap diperiksa
    ALTER TABLE inventory_ledger ADD CONSTRAINT inventory_ledger_nonzero
      CHECK (d_on_hand <> 0 OR d_reserved <> 0 OR d_damaged <> 0 OR d_returned <> 0) NOT VALID;

    CREATE TABLE courier_accounts (
      id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id        uuid NOT NULL REFERENCES tenants(id),
      name             varchar(100) NOT NULL,
      provider         varchar(20) NOT NULL CHECK (provider IN ('manual','simulator','biteship')),
      couriers         jsonb NOT NULL DEFAULT '[]',
      credentials_enc  text,
      webhook_token    char(40) NOT NULL UNIQUE,
      is_active        boolean NOT NULL DEFAULT true,
      is_default       boolean NOT NULL DEFAULT false,
      created_at       timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    );

    CREATE TABLE manifests (
      id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id      uuid NOT NULL,
      warehouse_id   uuid NOT NULL,
      number         varchar(32) NOT NULL,
      courier_code   varchar(30) NOT NULL,
      status         varchar(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','HANDED_OVER','CANCELLED')),
      driver_name    varchar(120),
      vehicle_plate  varchar(20),
      handed_over_at timestamptz,
      created_by     uuid,
      created_at     timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id)
    );

    CREATE TABLE shipments (
      id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id          uuid NOT NULL,
      order_id           uuid NOT NULL,
      warehouse_id       uuid NOT NULL,
      courier_account_id uuid NOT NULL,
      provider           varchar(20) NOT NULL,
      courier_code       varchar(30) NOT NULL,
      service_code       varchar(40) NOT NULL DEFAULT '',
      provider_ref       varchar(100),
      tracking_number    varchar(60),
      status             varchar(20) NOT NULL DEFAULT 'CREATED' CHECK (status IN ('CREATED','LABEL_READY','HANDED_OVER',
                           'IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERED','FAILED_DELIVERY','RETURNED_TO_SENDER','CANCELLED')),
      cost               numeric(14,2),
      weight_g           integer,
      manifest_id        uuid,
      label_printed_at   timestamptz,
      handed_over_at     timestamptz,
      delivered_at       timestamptz,
      last_tracked_at    timestamptz,
      created_by         uuid,
      created_at         timestamptz NOT NULL DEFAULT now(),
      updated_at         timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, order_id)           REFERENCES orders(tenant_id, id),
      FOREIGN KEY (tenant_id, warehouse_id)       REFERENCES warehouses(tenant_id, id),
      FOREIGN KEY (tenant_id, courier_account_id) REFERENCES courier_accounts(tenant_id, id),
      FOREIGN KEY (tenant_id, manifest_id)        REFERENCES manifests(tenant_id, id)
    );
    -- Satu pengiriman aktif per order; nomor resi unik per tenant
    CREATE UNIQUE INDEX ux_shipments_order_active ON shipments(order_id) WHERE status <> 'CANCELLED';
    CREATE UNIQUE INDEX ux_shipments_resi ON shipments(tenant_id, courier_code, tracking_number)
      WHERE tracking_number IS NOT NULL AND status <> 'CANCELLED';
    CREATE INDEX ix_shipments_status ON shipments(tenant_id, status);
    CREATE INDEX ix_shipments_provider_ref ON shipments(provider, provider_ref);

    CREATE TABLE tracking_events (
      id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id   uuid NOT NULL,
      shipment_id uuid NOT NULL,
      status      varchar(20) NOT NULL,
      description text NOT NULL DEFAULT '',
      location    varchar(200) NOT NULL DEFAULT '',
      source      varchar(20) NOT NULL,
      occurred_at timestamptz NOT NULL DEFAULT now(),
      created_at  timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, shipment_id) REFERENCES shipments(tenant_id, id)
    );
    CREATE INDEX ix_tracking_shipment ON tracking_events(shipment_id, occurred_at);
    CREATE TRIGGER trg_tracking_immutable BEFORE UPDATE OR DELETE ON tracking_events
      FOR EACH ROW EXECUTE FUNCTION append_only();

    -- ===================== retur / RMA =====================
    CREATE TABLE returns (
      id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id            uuid NOT NULL,
      number               varchar(32) NOT NULL,
      order_id             uuid NOT NULL,
      warehouse_id         uuid NOT NULL,
      status               varchar(12) NOT NULL DEFAULT 'REQUESTED' CHECK (status IN
                             ('REQUESTED','APPROVED','REJECTED','RECEIVED','INSPECTED','CLOSED')),
      reason_code          varchar(20) NOT NULL CHECK (reason_code IN
                             ('DAMAGED','WRONG_ITEM','NOT_AS_DESCRIBED','CHANGED_MIND','UNDELIVERED','OTHER')),
      note                 text NOT NULL DEFAULT '',
      return_tracking      varchar(60),
      order_status_before  varchar(20) NOT NULL,
      resolution           varchar(12) CHECK (resolution IN ('REFUND','REPLACEMENT','NONE')),
      refund_amount        numeric(14,2) CHECK (refund_amount IS NULL OR refund_amount >= 0),
      refund_ref           varchar(120),
      replacement_order_id uuid,
      created_by           uuid,
      decided_by           uuid,
      received_at          timestamptz,
      closed_at            timestamptz,
      created_at           timestamptz NOT NULL DEFAULT now(),
      updated_at           timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, order_id)     REFERENCES orders(tenant_id, id),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id)
    );
    CREATE INDEX ix_returns_order ON returns(order_id);
    CREATE TABLE return_lines (
      id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id     uuid NOT NULL,
      return_id     uuid NOT NULL,
      order_item_id uuid NOT NULL,
      sku_id        uuid NOT NULL,
      quantity      integer NOT NULL CHECK (quantity > 0),
      received_qty  integer NOT NULL DEFAULT 0 CHECK (received_qty >= 0),
      restock_qty   integer NOT NULL DEFAULT 0 CHECK (restock_qty >= 0),
      damaged_qty   integer NOT NULL DEFAULT 0 CHECK (damaged_qty >= 0),
      CHECK (received_qty <= quantity AND restock_qty + damaged_qty <= received_qty),
      UNIQUE (return_id, order_item_id),
      FOREIGN KEY (tenant_id, return_id)     REFERENCES returns(tenant_id, id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id, order_item_id) REFERENCES order_items(tenant_id, id),
      FOREIGN KEY (tenant_id, sku_id)        REFERENCES skus(tenant_id, id)
    );
    """)
    for t in ["shipments", "returns"]:
        op.execute(f"CREATE TRIGGER trg_{t}_updated_at BEFORE UPDATE ON {t} FOR EACH ROW EXECUTE FUNCTION set_updated_at();")
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
        GRANT SELECT, INSERT, UPDATE ON courier_accounts, manifests, shipments, returns, return_lines TO {APP_ROLE};
        GRANT SELECT, INSERT ON tracking_events TO {APP_ROLE};
      END IF;
    END $$;
    -- Integrasi kurir otomatis (aggregator) = fitur paket Growth ke atas
    UPDATE plans SET features = features || '["shipping_integration"]'::jsonb
      WHERE code IN ('growth','enterprise') AND NOT features ? 'shipping_integration';
    """)
    perms = {
        "shipping:read": "Lihat pengiriman & tracking",
        "shipping:write": "Buat resi, cetak label, serah terima kurir, update tracking",
        "shipping:manage": "Kelola akun kurir",
        "returns:read": "Lihat retur",
        "returns:write": "Ajukan, setujui, dan selesaikan retur",
        "returns:receive": "Terima & inspeksi barang retur di gudang",
    }
    role_perms = {
        "SUPER_ADMIN": list(perms), "TENANT_ADMIN": list(perms),
        "WAREHOUSE_MANAGER": ["shipping:read", "shipping:write", "returns:read", "returns:receive"],
        "WAREHOUSE_OPERATOR": ["shipping:read", "shipping:write", "returns:read", "returns:receive"],
        "CUSTOMER_SERVICE": ["shipping:read", "returns:read", "returns:write"],
        "FINANCE": ["shipping:read", "returns:read"], "VIEWER": ["shipping:read", "returns:read"],
    }
    for code, desc in perms.items():
        op.execute(f"INSERT INTO permissions(code, description) VALUES ('{code}', '{desc}')")
    for role, ps in role_perms.items():
        for p in ps:
            op.execute(f"INSERT INTO role_permissions(role_code, permission_code) VALUES ('{role}', '{p}')")


def downgrade() -> None:
    op.execute("""
    DELETE FROM role_permissions WHERE permission_code LIKE 'shipping:%' OR permission_code LIKE 'returns:%';
    DELETE FROM permissions WHERE code LIKE 'shipping:%' OR code LIKE 'returns:%';
    UPDATE plans SET features = features - 'shipping_integration';
    DROP TABLE IF EXISTS return_lines, returns, tracking_events, shipments, manifests, courier_accounts CASCADE;
    ALTER TABLE inventory_ledger DROP CONSTRAINT IF EXISTS inventory_ledger_nonzero;
    ALTER TABLE inventory_ledger DROP COLUMN IF EXISTS d_returned, DROP COLUMN IF EXISTS returned_after;
    -- NOT VALID: baris retur lama tetap disimpan (ledger tidak boleh dihapus), constraint berlaku untuk baris baru
    ALTER TABLE inventory_ledger ADD CONSTRAINT inventory_ledger_check
      CHECK (d_on_hand <> 0 OR d_reserved <> 0 OR d_damaged <> 0) NOT VALID;
    ALTER TABLE warehouses DROP COLUMN IF EXISTS postal_code, DROP COLUMN IF EXISTS phone, DROP COLUMN IF EXISTS contact_name;
    """)
