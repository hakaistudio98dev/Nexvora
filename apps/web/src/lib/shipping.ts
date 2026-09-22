import type { ShipmentStatus } from "./types";

export const SHIP_STATUS: Record<ShipmentStatus, string> = {
  CREATED: "Menunggu resi", LABEL_READY: "Resi siap", HANDED_OVER: "Diserahkan ke kurir", IN_TRANSIT: "Dalam perjalanan",
  OUT_FOR_DELIVERY: "Diantar kurir", DELIVERED: "Diterima", FAILED_DELIVERY: "Gagal antar",
  RETURNED_TO_SENDER: "Dikembalikan", CANCELLED: "Dibatalkan",
};
export const PROVIDER_LABEL: Record<string, string> = {
  manual: "Manual (input resi)", simulator: "Simulasi (demo)", biteship: "Biteship (otomatis)",
};
export const RETURN_STATUS: Record<string, string> = {
  REQUESTED: "Diajukan", APPROVED: "Disetujui, menunggu barang", REJECTED: "Ditolak", RECEIVED: "Diterima gudang",
  INSPECTED: "Sudah diinspeksi", CLOSED: "Selesai",
};
export function shipTone(s: ShipmentStatus): "ok" | "off" | "signal" | "neutral" {
  if (s === "DELIVERED") return "ok";
  if (s === "CANCELLED" || s === "RETURNED_TO_SENDER" || s === "FAILED_DELIVERY") return "off";
  if (s === "LABEL_READY") return "signal";
  return "neutral";
}
