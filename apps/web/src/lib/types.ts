// Diselaraskan dengan skema OpenAPI FastAPI (jalankan `npm run gen:types` untuk versi otomatis).
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export type Me = {
  id: string;
  email: string;
  full_name: string;
  roles: string[];
  permissions: string[];
  tenant: { id: string; slug: string; name: string };
  subscription: SubscriptionInfo;
};

export type SubscriptionInfo = {
  plan_code: string | null; plan_name: string; status: "TRIALING" | "ACTIVE" | "PAST_DUE" | "SUSPENDED" | "CANCELLED" | "NONE";
  features: string[]; limits: Record<string, number | null>; read_only: boolean; unrestricted: boolean;
  trial_ends_at: string | null; current_period_end: string | null; grace_until: string | null; cancel_at_period_end: boolean;
};

export type Plan = {
  code: string; name: string; description: string; price_monthly: string; price_yearly: string;
  features: string[]; limits: Record<string, number | null>; self_serve: boolean;
};

export type Invoice = {
  id: string; number: string; kind: string; plan_code: string; billing_cycle: "MONTHLY" | "YEARLY"; amount: string;
  currency: string; status: "OPEN" | "PAID" | "VOID" | "EXPIRED"; due_at: string; paid_at: string | null;
  provider: string; payment_url: string | null; payment_ref: string | null; created_at: string;
};

export type Product = {
  id: string; code: string; name: string; description: string; is_active: boolean;
  created_at: string; updated_at: string;
};

export type Sku = {
  id: string; product_id: string; sku_code: string; barcode: string | null; variant_name: string; unit: string;
  length_mm: number | null; width_mm: number | null; height_mm: number | null; weight_g: number | null;
  reorder_point?: number | null; is_active: boolean;
};

export type ProductDetail = Product & { skus: Sku[] };

export type Warehouse = {
  id: string; code: string; name: string; address: string; city: string; timezone: string; is_active: boolean;
  postal_code?: string; phone?: string; contact_name?: string;
};

export type LocationType = "ZONE" | "RACK" | "SHELF" | "BIN";
export type Location = {
  id: string; warehouse_id: string; parent_id: string | null; type: LocationType;
  code: string; full_code: string; is_active: boolean;
};

export type User = {
  id: string; email: string; full_name: string; is_active: boolean; role_codes: string[];
  last_login_at: string | null; created_at: string;
};

export type Role = { code: string; name: string; description: string; permissions: string[] };

export type AuditLog = {
  id: number; actor_user_id: string | null; action: string; entity_type: string; entity_id: string | null;
  before: Record<string, unknown> | null; after: Record<string, unknown> | null;
  correlation_id: string | null; ip: string | null; created_at: string;
};

export type ApiErrorBody = {
  error: { code: string; message: string; correlation_id?: string; details?: { loc: (string | number)[]; msg: string }[] };
};

// ---------------- Fase 2: OMS & Inventory ----------------
export type OrderStatus =
  | "CREATED" | "PAID" | "ALLOCATED" | "PICKING" | "PACKING" | "READY_TO_SHIP" | "SHIPPED" | "DELIVERED"
  | "CANCELLED" | "FAILED" | "RETURN_REQUESTED" | "RETURNED" | "REFUNDED";
export type StockStatus = "PENDING" | "RESERVED" | "OUT_OF_STOCK" | "RELEASED" | "CONSUMED";

export type OrderSummary = {
  id: string; order_number: string; channel: string; external_ref: string | null; status: OrderStatus;
  payment_status: string; stock_status: StockStatus; customer_name: string; ship_city: string; total: string;
  item_count: number; warehouse_code: string | null; placed_at: string;
};

export type OrderDetail = OrderSummary & {
  allocation_note: string | null; status_reason: string | null; customer_phone: string; customer_email: string;
  ship_address: string; ship_province: string; ship_postal_code: string; ship_country: string; currency: string;
  subtotal: string; shipping_fee: string; discount: string; notes: string;
  paid_at: string | null; allocated_at: string | null; shipped_at: string | null; delivered_at: string | null;
  cancelled_at: string | null;
  items: { id: string; sku_id: string; sku_code: string; product_name: string; variant_name: string; quantity: number;
           unit_price: string; line_total: string }[];
  reservations: { id: string; sku_code: string; warehouse_code: string; quantity: number; status: string;
                  expires_at: string | null }[];
  history: { from_status: string | null; to_status: string; reason: string | null; actor_user_id: string | null;
             created_at: string }[];
  actions: { name: string; label: string; needs_reason: boolean }[];
};

export type OrderStats = { by_status: Partial<Record<OrderStatus, number>>; out_of_stock: number };

export type Balance = {
  warehouse_id: string; warehouse_code: string; sku_id: string; sku_code: string; product_name: string;
  variant_name: string; on_hand: number; reserved: number; available: number; damaged: number; in_transit: number;
  returned: number; threshold: number; updated_at: string;
};

export type LedgerEntry = {
  id: number; warehouse_code: string; sku_code: string; entry_type: string; d_on_hand: number; d_reserved: number;
  d_damaged: number; on_hand_after: number; reserved_after: number; damaged_after: number; reason_code: string | null;
  note: string | null; reference_type: string | null; reference_id: string | null; actor_user_id: string | null;
  created_at: string;
};

export type Reconcile = { checked: number; ok: boolean; mismatches: unknown[] };

// ---------------- Fase 4: pengiriman & retur ----------------
export type ShipmentStatus = "CREATED" | "LABEL_READY" | "HANDED_OVER" | "IN_TRANSIT" | "OUT_FOR_DELIVERY" | "DELIVERED"
  | "FAILED_DELIVERY" | "RETURNED_TO_SENDER" | "CANCELLED";
export type Shipment = {
  id: string; order_id: string; order_number: string; customer_name: string; ship_city: string; status: ShipmentStatus;
  provider: "manual" | "simulator" | "biteship"; account_name: string; courier_code: string; courier_name: string;
  service_code: string; tracking_number: string | null; tracking_url: string | null; cost: string | null; weight_g: number | null;
  manifest_number: string | null; label_printed_at: string | null; handed_over_at: string | null;
  delivered_at: string | null; created_at: string; last_tracked_at: string | null;
  events?: { status: ShipmentStatus; description: string; location: string; source: string; occurred_at: string }[];
};
export type CourierAccount = { id: string; name: string; provider: "manual" | "simulator" | "biteship"; couriers: string[];
  is_active: boolean; is_default: boolean; has_credentials: boolean; created_at: string; webhook_path: string | null };
export type Courier = { code: string; name: string; services: { code: string; name: string }[]; custom: boolean;
  is_active: boolean; phone: string; tracking_url_template: string | null; auto_booking: boolean };
export type Rate = { courier_code: string; courier_name: string; service_code: string; service_name: string; price: string | null; etd: string | null };
export type ManifestDoc = { id: string; number: string; courier_code: string; courier_name: string; status: string;
  driver_name: string | null; vehicle_plate: string | null; handed_over_at: string | null; created_at: string; packages: Shipment[] };
export type ReturnDoc = {
  id: string; number: string; status: "REQUESTED" | "APPROVED" | "REJECTED" | "RECEIVED" | "INSPECTED" | "CLOSED";
  reason_code: string; reason_label: string; note: string; return_tracking: string | null; order_id: string;
  order_number: string; customer_name: string; order_total: string; resolution: string | null; refund_amount: string | null;
  refund_ref: string | null; replacement_order_number: string | null; created_at: string; received_at: string | null;
  closed_at: string | null;
  lines: { id: string; sku_code: string; product_name: string; quantity: number; received_qty: number; restock_qty: number; damaged_qty: number }[];
};

// ---------------- Fase 5: analitik & notifikasi ----------------
export type Overview = {
  range: { from: string; to: string; timezone: string; sla_ship_hours: number };
  orders: { total: number; cancelled: number; shipped: number; on_hold: number; revenue: string; aov: string | null };
  kpi: Record<"fulfillment_rate" | "sla_compliance" | "avg_hours_to_ship" | "p90_hours_to_ship" | "pick_accuracy" | "pack_accuracy"
    | "inventory_accuracy" | "exception_rate" | "return_rate", number | null> & { cost_per_order: string | null };
  samples: Record<string, number>;
  by_channel: { channel: string; orders: number; revenue: string }[];
  by_status: { status: string; orders: number }[];
};
export type SeriesPoint = { date: string; placed: number; revenue: string; shipped: number; on_time: number };
export type SlaBoard = { sla_ship_hours: number; risk_hours: number; counts: { overdue: number; at_risk: number; on_track: number };
  by_stage: Record<string, { overdue: number; at_risk: number; on_track: number }>;
  orders: { order_id: string; order_number: string; status: string; channel: string; customer_name: string; warehouse: string | null;
            due_at: string; hours_left: number; state: "overdue" | "at_risk" | "on_track" }[] };
export type ProductivityRow = { user_id: string; name: string; email: string; picks: number; units_picked: number; short_picks: number;
  putaways: number; units_putaway: number; packages: number; count_lines: number; pick_accuracy: number | null };
export type HealthItem = { warehouse: string; sku_code: string; product_name: string; on_hand: number; available: number; threshold: number;
  sold_30d: number; avg_daily: number; days_of_cover: number | null; state: "out" | "low" | "slow" | "ok"; suggested_reorder: number };
export type NocData = { overall: "ok" | "warning" | "critical"; checks: { key: string; label: string; status: "ok" | "warning" | "critical"; detail: string }[];
  couriers: { name: string; provider: string; is_active: boolean; active: number; stale: number; last_tracked: string | null }[];
  channels: { channel: string; last_order: string; orders_24h: number }[]; api_keys: { active: number; last_used: string | null } };
export type AppNotification = { id: string; event_type: string; severity: "INFO" | "WARNING" | "CRITICAL"; title: string; body: string;
  link: string | null; read: boolean; created_at: string };
export type NotifChannel = { id: string; kind: "EMAIL" | "WEBHOOK"; name: string; target: string; events: string[]; is_active: boolean;
  has_secret: boolean; created_at: string; deliveries: { sent: number; pending: number; failed: number }; secret?: string };
