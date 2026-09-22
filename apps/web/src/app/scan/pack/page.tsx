"use client";
import { useState } from "react";
import { Flash, ScanInput } from "@/components/scanner";
import { useWarehouse } from "@/components/wh-context";
import { ApiError, api, errorText } from "@/lib/api";
import { type PackView, feedback } from "@/lib/wms";

export default function PackPage() {
  useWarehouse();
  const [v, setV] = useState<PackView | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);
  const [weight, setWeight] = useState("");
  const [override, setOverride] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function openOrder(code: string) {
    try {
      const view = await api.get<PackView>(`/wms/pack/${encodeURIComponent(code)}`);
      setV(view); setWeight(""); setOverride(null);
      if (!view.picking_complete) { feedback(false); setMsg({ tone: "err", text: `Picking belum lengkap (${view.open_pick_tasks} task terbuka).` }); }
      else if (!["PICKING", "PACKING"].includes(view.status)) { feedback(false); setMsg({ tone: "err", text: `Order berstatus ${view.status}.` }); }
      else { feedback(true); setMsg({ tone: "info", text: "Scan setiap barang yang dimasukkan ke paket." }); }
    } catch (e) { feedback(false); setMsg({ tone: "err", text: errorText(e) }); }
  }

  async function scanItem(code: string) {
    if (!v) return;
    try {
      const r = await api.post<{ sku_code: string; scanned_qty: number; quantity: number; view: PackView }>(
        `/wms/pack/${v.order_number}/scan`, { barcode: code });
      setV(r.view); feedback(true);
      setMsg({ tone: "ok", text: `${r.sku_code}: ${r.scanned_qty}/${r.quantity}` });
    } catch (e) { feedback(false); setMsg({ tone: "err", text: errorText(e) }); }
  }

  async function complete() {
    if (!v) return;
    setBusy(true);
    try {
      const done = await api.post<PackView>(`/wms/pack/${v.order_number}/complete`,
        { weight_g: Number(weight), override_reason: override || null });
      feedback(true);
      setMsg({ tone: "ok", text: `${done.order_number} siap dikirim. Tempel label pengiriman di paket.` });
      setV(null);
    } catch (e) {
      feedback(false);
      if (e instanceof ApiError && e.code === "WEIGHT_MISMATCH") setOverride("");
      setMsg({ tone: "err", text: errorText(e) });
    } finally { setBusy(false); }
  }

  if (!v) return (
    <>
      <h1 className="text-2xl font-bold">Packing station</h1>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      <ScanInput label="Scan nomor order (SO-…)" onScan={openOrder} />
    </>
  );

  const doneUnits = v.items.reduce((a, i) => a + i.scanned_qty, 0);
  const totalUnits = v.items.reduce((a, i) => a + i.quantity, 0);
  return (
    <>
      <div className="flex items-start justify-between">
        <div><div className="font-mono text-2xl font-bold">{v.order_number}</div><div className="text-sm text-ink-soft">{v.customer_name} · {v.ship_city}</div></div>
        <button type="button" onClick={() => { setV(null); setMsg(null); }} className="min-h-10 rounded-xl border-2 border-ink px-3 text-sm">Ganti order</button>
      </div>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      <ul className="flex flex-col gap-2" aria-label="Isi paket">
        {v.items.map((i) => {
          const full = i.scanned_qty >= i.quantity;
          return (
            <li key={i.order_item_id} className={`flex items-center justify-between rounded-xl border-[3px] px-3 py-2 ${full ? "border-ok bg-green-50" : "border-ink bg-white"}`}>
              <div><div className="font-mono font-bold">{i.sku_code}</div><div className="text-sm text-ink-soft">{i.variant_name}</div></div>
              <div className="text-2xl font-bold tabular-nums">{i.scanned_qty}/{i.quantity}</div>
            </li>
          );
        })}
      </ul>
      {!v.all_scanned ? (
        <ScanInput label={`Scan barang (${doneUnits}/${totalUnits})`} onScan={scanItem} />
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); complete(); }} className="flex flex-col gap-3 rounded-xl border-[3px] border-ink bg-white p-4">
          <label htmlFor="w" className="font-semibold">Berat paket (gram)</label>
          <input id="w" type="number" inputMode="numeric" min={1} required value={weight} onChange={(e) => setWeight(e.target.value)} autoFocus
                 className="min-h-14 rounded-xl border-[3px] border-ink px-3 text-2xl font-bold tabular-nums" />
          {v.expected_weight_g && <p className="text-sm text-ink-soft">Perkiraan {v.expected_weight_g} g (±{v.tolerance_pct}%) sebelum kemasan.</p>}
          {override !== null && (
            <>
              <label htmlFor="ov" className="font-semibold text-danger">Alasan berat berbeda</label>
              <input id="ov" required value={override} onChange={(e) => setOverride(e.target.value)} placeholder="mis. kardus tebal / bubble wrap ekstra"
                     className="min-h-12 rounded-xl border-[3px] border-danger px-3" />
            </>
          )}
          <button type="submit" disabled={busy} className="min-h-14 rounded-xl bg-ink text-lg font-bold text-white disabled:opacity-50">{busy ? "Menyimpan…" : "Paket selesai"}</button>
        </form>
      )}
    </>
  );
}
