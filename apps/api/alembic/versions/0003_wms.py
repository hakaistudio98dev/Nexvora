"""Phase 3: WMS — stok per bin, inbound/receiving, putaway, wave picking, packing, cycle count, exception

Revision ID: 0003_wms
Revises: 0002_oms_inventory
Create Date: 2026-09-22
"""
import os
import re

from alembic import op

revision = "0003_wms"
down_revision = "0002_oms_inventory"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_USER", "nexvora_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise RuntimeError("APP_DB_USER tidak valid")

TENANT_TABLES = ["bin_stock", "bin_movements", "inbound_receipts", "inbound_lines", "waves", "wms_tasks",
                 "pack_progress", "packages", "cycle_counts", "cycle_count_lines", "wms_exceptions",
                 "doc_counters"]


def upgrade() -> None:
    op.execute("""
    -- Nomor dokumen per tenant (IN-, WV-, CC-)
    CREATE TABLE doc_counters (
      tenant_id uuid NOT NULL REFERENCES tenants(id),
      doc_type  varchar(10) NOT NULL,
      last_no   bigint NOT NULL DEFAULT 0,
      PRIMARY KEY (tenant_id, doc_type)
    );

    -- ===================== stok per bin =====================
    CREATE TABLE bin_stock (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL,
      warehouse_id uuid NOT NULL,
      location_id  uuid NOT NULL,
      sku_id       uuid NOT NULL,
      quantity     integer NOT NULL DEFAULT 0 CHECK (quantity >= 0),
      allocated    integer NOT NULL DEFAULT 0 CHECK (allocated >= 0),
      updated_at   timestamptz NOT NULL DEFAULT now(),
      CHECK (allocated <= quantity),
      UNIQUE (location_id, sku_id),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id),
      FOREIGN KEY (tenant_id, location_id)  REFERENCES locations(tenant_id, id),
      FOREIGN KEY (tenant_id, sku_id)       REFERENCES skus(tenant_id, id)
    );
    CREATE INDEX ix_bin_stock_sku ON bin_stock(tenant_id, warehouse_id, sku_id);

    CREATE TABLE bin_movements (
      id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id        uuid NOT NULL,
      warehouse_id     uuid NOT NULL,
      sku_id           uuid NOT NULL,
      from_location_id uuid,
      to_location_id   uuid,
      quantity         integer NOT NULL CHECK (quantity > 0),
      movement_type    varchar(20) NOT NULL CHECK (movement_type IN
                         ('PUTAWAY','PICK','MOVE','COUNT_ADJUST','ADJUST')),
      reference_type   varchar(30),
      reference_id     varchar(64),
      actor_user_id    uuid,
      created_at       timestamptz NOT NULL DEFAULT now(),
      CHECK (from_location_id IS NOT NULL OR to_location_id IS NOT NULL),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id),
      FOREIGN KEY (tenant_id, sku_id)       REFERENCES skus(tenant_id, id)
    );
    CREATE INDEX ix_bin_mov_loc ON bin_movements(tenant_id, warehouse_id, id DESC);
    CREATE TRIGGER trg_bin_mov_immutable BEFORE UPDATE OR DELETE ON bin_movements
      FOR EACH ROW EXECUTE FUNCTION append_only();

    -- ===================== inbound / receiving =====================
    CREATE TABLE inbound_receipts (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL,
      warehouse_id uuid NOT NULL,
      number       varchar(32) NOT NULL,
      supplier     varchar(200) NOT NULL DEFAULT '',
      reference    varchar(64) NOT NULL DEFAULT '',
      status       varchar(12) NOT NULL DEFAULT 'RECEIVING' CHECK (status IN ('RECEIVING','COMPLETED','CANCELLED')),
      notes        text NOT NULL DEFAULT '',
      created_by   uuid,
      completed_at timestamptz,
      created_at   timestamptz NOT NULL DEFAULT now(),
      updated_at   timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id)
    );
    CREATE TABLE inbound_lines (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL,
      inbound_id   uuid NOT NULL,
      sku_id       uuid NOT NULL,
      expected_qty integer NOT NULL DEFAULT 0 CHECK (expected_qty >= 0),
      received_qty integer NOT NULL DEFAULT 0 CHECK (received_qty >= 0),
      damaged_qty  integer NOT NULL DEFAULT 0 CHECK (damaged_qty >= 0),
      UNIQUE (inbound_id, sku_id),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, inbound_id) REFERENCES inbound_receipts(tenant_id, id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id, sku_id)     REFERENCES skus(tenant_id, id)
    );

    -- ===================== waves & tasks =====================
    CREATE TABLE waves (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL,
      warehouse_id uuid NOT NULL,
      number       varchar(32) NOT NULL,
      status       varchar(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','DONE','CANCELLED')),
      created_by   uuid,
      created_at   timestamptz NOT NULL DEFAULT now(),
      updated_at   timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id)
    );

    CREATE TABLE wms_tasks (
      id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id        uuid NOT NULL,
      warehouse_id     uuid NOT NULL,
      task_type        varchar(10) NOT NULL CHECK (task_type IN ('PUTAWAY','PICK')),
      status           varchar(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','IN_PROGRESS','DONE','SHORT','CANCELLED')),
      sku_id           uuid NOT NULL,
      quantity         integer NOT NULL CHECK (quantity > 0),
      done_qty         integer NOT NULL DEFAULT 0 CHECK (done_qty >= 0),
      from_location_id uuid,
      to_location_id   uuid,
      order_id         uuid,
      order_item_id    uuid,
      wave_id          uuid,
      inbound_id       uuid,
      assigned_to      uuid,
      completed_at     timestamptz,
      created_at       timestamptz NOT NULL DEFAULT now(),
      updated_at       timestamptz NOT NULL DEFAULT now(),
      CHECK (done_qty <= quantity),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, warehouse_id)     REFERENCES warehouses(tenant_id, id),
      FOREIGN KEY (tenant_id, sku_id)           REFERENCES skus(tenant_id, id),
      FOREIGN KEY (tenant_id, from_location_id) REFERENCES locations(tenant_id, id),
      FOREIGN KEY (tenant_id, to_location_id)   REFERENCES locations(tenant_id, id),
      FOREIGN KEY (tenant_id, order_id)         REFERENCES orders(tenant_id, id),
      FOREIGN KEY (tenant_id, order_item_id)    REFERENCES order_items(tenant_id, id),
      FOREIGN KEY (tenant_id, wave_id)          REFERENCES waves(tenant_id, id),
      FOREIGN KEY (tenant_id, inbound_id)       REFERENCES inbound_receipts(tenant_id, id)
    );
    CREATE INDEX ix_tasks_queue ON wms_tasks(tenant_id, warehouse_id, task_type, status);
    CREATE INDEX ix_tasks_order ON wms_tasks(order_id);
    CREATE INDEX ix_tasks_wave  ON wms_tasks(wave_id);

    -- ===================== packing =====================
    CREATE TABLE pack_progress (
      order_item_id uuid PRIMARY KEY,
      tenant_id     uuid NOT NULL,
      order_id      uuid NOT NULL,
      scanned_qty   integer NOT NULL DEFAULT 0 CHECK (scanned_qty >= 0),
      FOREIGN KEY (tenant_id, order_item_id) REFERENCES order_items(tenant_id, id),
      FOREIGN KEY (tenant_id, order_id)      REFERENCES orders(tenant_id, id)
    );
    CREATE TABLE packages (
      id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id         uuid NOT NULL,
      order_id          uuid NOT NULL UNIQUE,
      weight_g          integer NOT NULL CHECK (weight_g > 0),
      expected_weight_g integer,
      length_mm         integer CHECK (length_mm IS NULL OR length_mm > 0),
      width_mm          integer CHECK (width_mm IS NULL OR width_mm > 0),
      height_mm         integer CHECK (height_mm IS NULL OR height_mm > 0),
      override_reason   varchar(300),
      packed_by         uuid,
      created_at        timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, id)
    );

    -- ===================== cycle count =====================
    CREATE TABLE cycle_counts (
      id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id    uuid NOT NULL,
      warehouse_id uuid NOT NULL,
      number       varchar(32) NOT NULL,
      scope        varchar(160) NOT NULL DEFAULT '',
      status       varchar(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','SUBMITTED','APPROVED','CANCELLED')),
      created_by   uuid,
      submitted_at timestamptz,
      approved_by  uuid,
      approved_at  timestamptz,
      created_at   timestamptz NOT NULL DEFAULT now(),
      updated_at   timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id)
    );
    CREATE TABLE cycle_count_lines (
      id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id   uuid NOT NULL,
      count_id    uuid NOT NULL,
      location_id uuid NOT NULL,
      sku_id      uuid NOT NULL,
      system_qty  integer NOT NULL CHECK (system_qty >= 0),
      counted_qty integer CHECK (counted_qty IS NULL OR counted_qty >= 0),
      counted_by  uuid,
      counted_at  timestamptz,
      UNIQUE (count_id, location_id, sku_id),
      FOREIGN KEY (tenant_id, count_id)    REFERENCES cycle_counts(tenant_id, id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id, location_id) REFERENCES locations(tenant_id, id),
      FOREIGN KEY (tenant_id, sku_id)      REFERENCES skus(tenant_id, id)
    );

    -- ===================== exception queue =====================
    CREATE TABLE wms_exceptions (
      id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id     uuid NOT NULL,
      warehouse_id  uuid NOT NULL,
      exc_type      varchar(20) NOT NULL CHECK (exc_type IN
                      ('MISSING','DAMAGED','WRONG_SKU','WRONG_LOCATION','WEIGHT_MISMATCH','OVER_RECEIPT','SHORT_RECEIPT','OTHER')),
      status        varchar(10) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED')),
      sku_id        uuid,
      location_id   uuid,
      order_id      uuid,
      task_id       uuid,
      inbound_id    uuid,
      quantity      integer,
      note          text NOT NULL DEFAULT '',
      reported_by   uuid,
      resolution    text,
      resolved_by   uuid,
      resolved_at   timestamptz,
      created_at    timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, warehouse_id) REFERENCES warehouses(tenant_id, id)
    );
    CREATE INDEX ix_exc_open ON wms_exceptions(tenant_id, status, created_at DESC);
    """)

    for t in ["inbound_receipts", "waves", "wms_tasks", "cycle_counts"]:
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
        GRANT SELECT, INSERT, UPDATE ON bin_stock, inbound_receipts, inbound_lines, waves, wms_tasks,
          pack_progress, packages, cycle_counts, cycle_count_lines, wms_exceptions, doc_counters TO {APP_ROLE};
        GRANT SELECT, INSERT ON bin_movements TO {APP_ROLE};
      END IF;
    END $$;
    """)

    perms = {
        "wms:read": "Lihat antrean dan dokumen gudang",
        "wms:operate": "Kerjakan task gudang: terima, putaway, picking, packing, hitung",
        "wms:manage": "Buat inbound, wave, cycle count; setujui hitungan; selesaikan exception",
    }
    role_perms = {
        "SUPER_ADMIN": list(perms), "TENANT_ADMIN": list(perms), "WAREHOUSE_MANAGER": list(perms),
        "WAREHOUSE_OPERATOR": ["wms:read", "wms:operate"],
        "FINANCE": ["wms:read"], "CUSTOMER_SERVICE": ["wms:read"], "VIEWER": ["wms:read"],
    }
    for code, desc in perms.items():
        op.execute(f"INSERT INTO permissions(code, description) VALUES ('{code}', '{desc}')")
    for role, ps in role_perms.items():
        for p in ps:
            op.execute(f"INSERT INTO role_permissions(role_code, permission_code) VALUES ('{role}', '{p}')")


def downgrade() -> None:
    op.execute("""
    DELETE FROM role_permissions WHERE permission_code IN ('wms:read','wms:operate','wms:manage');
    DELETE FROM permissions WHERE code IN ('wms:read','wms:operate','wms:manage');
    DROP TABLE IF EXISTS wms_exceptions, cycle_count_lines, cycle_counts, packages, pack_progress, wms_tasks,
      waves, inbound_lines, inbound_receipts, bin_movements, bin_stock, doc_counters CASCADE;
    """)
