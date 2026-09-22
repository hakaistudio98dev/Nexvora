"use client";
import { ApiError, api } from "./api";

export type Task = {
  id: string; task_type: "PUTAWAY" | "PICK"; status: string; quantity: number; done_qty: number; remaining: number;
  sku_id: string; sku_code: string; barcode: string | null; variant_name: string; product_name: string;
  from_location: string | null; to_location: string | null; order_id: string | null; order_number: string | null;
  wave_id: string | null; inbound_id: string | null; created_at: string;
};
export type InboundDoc = {
  id: string; number: string; warehouse_id: string; supplier: string; reference: string; status: string; notes: string;
  created_at: string; completed_at: string | null;
  lines: { sku_id: string; sku_code: string; barcode: string | null; variant_name: string; product_name: string;
           expected_qty: number; received_qty: number; damaged_qty: number }[];
};
export type WaveRow = { id: string; number: string; status: string; created_at: string; orders: number; tasks: number;
  tasks_done: number; units: number; units_picked: number; short: number };
export type PackView = {
  order_id: string; order_number: string; status: string; customer_name: string; ship_city: string;
  items: { order_item_id: string; sku_code: string; barcode: string | null; variant_name: string; quantity: number;
           picked_qty: number; scanned_qty: number }[];
  picking_complete: boolean; open_pick_tasks: number; all_scanned: boolean; expected_weight_g: number | null;
  tolerance_pct: number; package: { weight_g: number; created_at: string } | null;
};
export type CountDoc = {
  id: string; number: string; scope: string; status: string; created_at: string; blind: boolean;
  submitted_at: string | null; approved_at: string | null;
  lines: { id: string; location: string; sku_code: string; variant_name: string; system_qty: number | null;
           counted_qty: number | null; variance: number | null }[];
  result?: { lines: number; matched: number; adjusted: number; accuracy_pct: number };
};
export type CountRow = { id: string; number: string; scope: string; status: string; created_at: string; lines: number; counted: number };
export type WmsExc = { id: string; exc_type: string; status: string; quantity: number | null; note: string;
  sku_code: string | null; location: string | null; order_number: string | null; can_repick: boolean;
  resolution: string | null; created_at: string; resolved_at: string | null };
export type WmsStats = { open_putaway: number; open_picks: number; orders_allocated: number; orders_picking: number;
  orders_packing: number; ready_to_ship: number; packed_24h: number; inbound_open: number; exceptions_open: number };

export const EXC_LABEL: Record<string, string> = {
  MISSING: "Barang kurang", DAMAGED: "Rusak", WRONG_SKU: "Salah barang", WRONG_LOCATION: "Salah lokasi",
  WEIGHT_MISMATCH: "Berat tidak cocok", OVER_RECEIPT: "Terima lebih", SHORT_RECEIPT: "Terima kurang", OTHER: "Lainnya",
};

// ---------------------------------------------------------------- gudang aktif (per perangkat)
const WH_KEY = "nx_wh";
export function savedWarehouse(): string {
  try { return localStorage.getItem(WH_KEY) ?? ""; } catch { return ""; }
}
export function saveWarehouse(id: string) {
  try { localStorage.setItem(WH_KEY, id); } catch { /* storage penuh/diblok */ }
}

// ---------------------------------------------------------------- antrean offline
// Scan dikirim dengan client_event_id unik. Bila jaringan putus, scan disimpan di perangkat
// dan dikirim ulang nanti; server mengenali ID yang sama sehingga stok tidak tercatat dua kali.
export type QueuedOp = { id: string; path: string; body: Record<string, unknown>; label: string; created: number };
const Q_KEY = "nx_scan_queue";
const listeners = new Set<() => void>();

export function queued(): QueuedOp[] {
  try { return JSON.parse(localStorage.getItem(Q_KEY) ?? "[]") as QueuedOp[]; } catch { return []; }
}
function writeQueue(q: QueuedOp[]) {
  try { localStorage.setItem(Q_KEY, JSON.stringify(q)); } catch { /* abaikan */ }
  listeners.forEach((f) => f());
}
export function onQueueChange(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

export async function sendOrQueue<T>(path: string, body: Record<string, unknown>, label: string):
  Promise<{ ok: true; data: T } | { ok: false; queued: true }> {
  const payload = { ...body, client_event_id: `ev-${crypto.randomUUID()}` };
  try {
    return { ok: true, data: await api.post<T>(path, payload) };
  } catch (e) {
    if (e instanceof ApiError) throw e; // ditolak server (salah barang, dll.) — harus diperbaiki sekarang
    writeQueue([...queued(), { id: payload.client_event_id, path, body: payload, label, created: Date.now() }]);
    return { ok: false, queued: true };
  }
}

let flushing = false;
export async function flushQueue(): Promise<{ sent: number; rejected: { label: string; message: string }[] }> {
  if (flushing) return { sent: 0, rejected: [] };
  flushing = true;
  let sent = 0;
  const rejected: { label: string; message: string }[] = [];
  try {
    for (const op of queued()) {
      try {
        await api.post(op.path, op.body);
        sent++;
      } catch (e) {
        if (!(e instanceof ApiError)) break; // masih offline, coba lagi nanti
        rejected.push({ label: op.label, message: e.message });
      }
      writeQueue(queued().filter((x) => x.id !== op.id));
    }
  } finally {
    flushing = false;
  }
  return { sent, rejected };
}

// ---------------------------------------------------------------- umpan balik scan
export function feedback(ok: boolean) {
  try {
    navigator.vibrate?.(ok ? 40 : [80, 60, 80]);
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = ok ? 1320 : 220;
    osc.type = ok ? "sine" : "square";
    gain.gain.value = 0.08;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + (ok ? 0.09 : 0.3));
    osc.onended = () => ctx.close();
  } catch { /* perangkat tanpa audio */ }
}
