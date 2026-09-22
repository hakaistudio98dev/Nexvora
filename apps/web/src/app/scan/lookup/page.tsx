"use client";
import { useState } from "react";
import { Flash, ScanInput } from "@/components/scanner";
import { useWarehouse } from "@/components/wh-context";
import { api, errorText } from "@/lib/api";
import { EXC_LABEL, feedback, sendOrQueue } from "@/lib/wms";

type Result =
  | { type: "LOCATION"; location: { full_code: string; type: string }; stock: { sku_code: string; product_name: string; quantity: number; allocated: number }[] }
  | { type: "SKU"; sku: { sku_code: string; product_name: string; variant_name: string }; on_hand: number; available: number; unplaced: number; bins: { location: string; quantity: number }[] }
  | { type: "ORDER"; order: { order_number: string; status: string } };

export default function LookupPage() {
  const wh = useWarehouse()!;
  const [r, setR] = useState<Result | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);
  const [report, setReport] = useState(false);

  async function scan(code: string) {
    try { setR(await api.get<Result>(`/wms/scan?warehouse_id=${wh.id}&code=${encodeURIComponent(code)}`)); setMsg(null); feedback(true); }
    catch (e) { setR(null); feedback(false); setMsg({ tone: "err", text: errorText(e) }); }
  }

  async function send(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = { warehouse_id: wh.id, exc_type: String(f.get("type")), note: String(f.get("note")),
      location_code: r?.type === "LOCATION" ? r.location.full_code : (String(f.get("loc") || "") || null),
      barcode: r?.type === "SKU" ? r.sku.sku_code : null, quantity: f.get("qty") ? Number(f.get("qty")) : null };
    try {
      const res = await sendOrQueue("/wms/exceptions", body, "Laporan masalah");
      setMsg({ tone: res.ok ? "ok" : "info", text: res.ok ? "Laporan terkirim ke supervisor." : "Offline: laporan disimpan, dikirim nanti." });
      setReport(false);
    } catch (x) { setMsg({ tone: "err", text: errorText(x) }); }
  }

  return (
    <>
      <h1 className="text-2xl font-bold">Cek & lapor</h1>
      <ScanInput label="Scan bin, barang, atau nomor order" onScan={scan} />
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      {r?.type === "LOCATION" && (
        <section className="rounded-xl border-[3px] border-ink bg-white p-4">
          <div className="font-mono text-3xl font-bold">{r.location.full_code}</div>
          {r.stock.length === 0 ? <p className="text-ink-soft">Bin kosong.</p> : (
            <ul className="mt-2 divide-y divide-concrete-dark">{r.stock.map((s) => (
              <li key={s.sku_code} className="flex justify-between py-2"><span><span className="font-mono font-bold">{s.sku_code}</span><span className="block text-xs text-ink-soft">{s.product_name}</span></span>
                <span className="text-right text-xl font-bold tabular-nums">{s.quantity}{s.allocated ? <span className="block text-xs font-normal">{s.allocated} dialokasi</span> : null}</span></li>))}</ul>)}
        </section>
      )}
      {r?.type === "SKU" && (
        <section className="rounded-xl border-[3px] border-ink bg-white p-4">
          <div className="font-mono text-2xl font-bold">{r.sku.sku_code}</div>
          <div className="text-ink-soft">{r.sku.product_name}{r.sku.variant_name ? ` · ${r.sku.variant_name}` : ""}</div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center">
            {[["Fisik", r.on_hand], ["Tersedia", r.available], ["Belum di bin", r.unplaced]].map(([l, v]) => (
              <div key={l as string} className="rounded-xl bg-concrete p-2"><div className="text-xs">{l}</div><div className="text-2xl font-bold tabular-nums">{v}</div></div>))}
          </div>
          <ul className="mt-3">{r.bins.map((b) => <li key={b.location} className="flex justify-between py-1 font-mono"><span>{b.location}</span><span className="font-bold">{b.quantity}</span></li>)}</ul>
        </section>
      )}
      {r?.type === "ORDER" && <Flash tone="info">Order {r.order.order_number}: {r.order.status}</Flash>}
      {!report ? (
        <button type="button" onClick={() => setReport(true)} className="min-h-12 rounded-xl border-[3px] border-danger bg-white font-bold text-danger">Lapor masalah</button>
      ) : (
        <form onSubmit={send} className="flex flex-col gap-3 rounded-xl border-[3px] border-danger bg-white p-4">
          <label className="flex flex-col gap-1 font-semibold">Jenis masalah
            <select name="type" className="min-h-12 rounded-xl border-2 border-ink px-2 font-normal">
              {["DAMAGED", "MISSING", "WRONG_SKU", "WRONG_LOCATION", "OTHER"].map((t) => <option key={t} value={t}>{EXC_LABEL[t]}</option>)}
            </select></label>
          {r?.type !== "LOCATION" && <label className="flex flex-col gap-1 font-semibold">Bin (opsional)<input name="loc" className="min-h-12 rounded-xl border-2 border-ink px-3 font-mono font-normal" /></label>}
          <label className="flex flex-col gap-1 font-semibold">Jumlah (opsional)<input name="qty" type="number" min={0} inputMode="numeric" className="min-h-12 rounded-xl border-2 border-ink px-3 font-normal" /></label>
          <label className="flex flex-col gap-1 font-semibold">Keterangan<input name="note" required minLength={3} className="min-h-12 rounded-xl border-2 border-ink px-3 font-normal" /></label>
          <div className="flex gap-2"><button type="submit" className="min-h-12 flex-1 rounded-xl bg-danger font-bold text-white">Kirim laporan</button>
            <button type="button" onClick={() => setReport(false)} className="min-h-12 rounded-xl border-2 border-ink px-4">Batal</button></div>
        </form>
      )}
    </>
  );
}
