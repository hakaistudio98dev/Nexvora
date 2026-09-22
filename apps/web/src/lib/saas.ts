import type { SubscriptionInfo } from "./types";

export const FEATURE_LABEL: Record<string, string> = {
  oms: "Order & inventory", wms: "Gudang (WMS) & scanner", api_keys: "API key integrasi", priority_support: "Dukungan prioritas",
};
export const LIMIT_LABEL: Record<string, string> = {
  warehouses: "Gudang", users: "Pengguna", skus: "SKU aktif", orders_per_month: "Order / bulan",
};
export const STATUS_LABEL: Record<string, string> = {
  TRIALING: "Masa coba", ACTIVE: "Aktif", PAST_DUE: "Menunggu pembayaran", SUSPENDED: "Ditangguhkan (baca-saja)",
  CANCELLED: "Berhenti (baca-saja)", NONE: "Tanpa paket",
};

export function hasFeature(sub: SubscriptionInfo, f: string): boolean {
  return sub.unrestricted || sub.features.includes(f);
}

export function daysLeft(iso: string | null): number | null {
  if (!iso) return null;
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000);
}
