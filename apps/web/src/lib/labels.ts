import type { OrderStatus, StockStatus } from "./types";

export const STATUS_LABEL: Record<OrderStatus, string> = {
  CREATED: "Baru", PAID: "Dibayar", ALLOCATED: "Teralokasi", PICKING: "Picking", PACKING: "Packing",
  READY_TO_SHIP: "Siap kirim", SHIPPED: "Dikirim", DELIVERED: "Diterima", CANCELLED: "Dibatalkan",
  FAILED: "Gagal bayar", RETURN_REQUESTED: "Minta retur", RETURNED: "Diretur", REFUNDED: "Refund",
};

export const STOCK_LABEL: Record<StockStatus, string> = {
  PENDING: "Menunggu", RESERVED: "Direservasi", OUT_OF_STOCK: "Stok kurang", RELEASED: "Dilepas", CONSUMED: "Terkirim",
};

export const FLOW: OrderStatus[] = ["CREATED", "PAID", "ALLOCATED", "PICKING", "PACKING", "READY_TO_SHIP", "SHIPPED", "DELIVERED"];

export const CHANNELS = ["MANUAL", "WEBSITE", "SHOPEE", "TOKOPEDIA", "TIKTOK", "LAZADA", "API", "OTHER"];

export const ENTRY_LABEL: Record<string, string> = {
  RECEIPT: "Terima", ADJUSTMENT: "Penyesuaian", RESERVE: "Reservasi", RELEASE: "Lepas reservasi",
  SHIP: "Kirim", DAMAGE: "Rusak", RETURN: "Retur",
};

export function statusTone(s: OrderStatus): "neutral" | "ok" | "off" | "signal" {
  if (s === "DELIVERED") return "ok";
  if (s === "CANCELLED" || s === "FAILED") return "off";
  if (s === "CREATED" || s === "PAID") return "signal";
  return "neutral";
}
