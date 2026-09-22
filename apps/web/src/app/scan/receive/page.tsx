"use client";
import { useCallback, useEffect, useState } from "react";
import { useCan } from "@/components/me-context";
import { Flash, ScanInput, Stepper } from "@/components/scanner";
import { useWarehouse } from "@/components/wh-context";
import { api, errorText } from "@/lib/api";
import { type InboundDoc, feedback, sendOrQueue } from "@/lib/wms";

export default function ReceivePage() {
  const wh = useWarehouse()!;
  const canManage = useCan("wms:manage");
  const [docs, setDocs] = useState<InboundDoc[]>([]);
  const [doc, setDoc] = useState<InboundDoc | null>(null);
  const [qty, setQty] = useState(1);
  const [damaged, setDamaged] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);

  const load = useCallback(() => api.get<InboundDoc[]>(`/wms/inbound?warehouse_id=${wh.id}&status=RECEIVING`).then(setDocs)
    .catch((e) => setMsg({ tone: "err", text: errorText(e) })), [wh.id]);
  useEffect(() => { load(); }, [load]);
  const refresh = async (id: string) => setDoc(await api.get<InboundDoc>(`/wms/inbound/${id}`));

  async function onScan(code: string) {
    if (!doc) return;
    try {
      const r = await sendOrQueue<{ sku_code: string; received_qty: number; damaged_qty: number; expected_qty: number; over: boolean }>(
        `/wms/inbound/${doc.id}/receive`, { barcode: code, quantity: qty, damaged }, `Terima ${code} ×${qty}`);
      feedback(true);
      if (!r.ok) { setMsg({ tone: "info", text: `Offline: ${code} ×${qty} disimpan, dikirim otomatis nanti.` }); return; }
      const d = r.data;
      setMsg({ tone: d.over ? "info" : "ok", text: `${d.sku_code}: ${d.received_qty} baik${d.damaged_qty ? `, ${d.damaged_qty} rusak` : ""} / dokumen ${d.expected_qty}${d.over ? " — melebihi dokumen" : ""}` });
      setQty(1); setDamaged(false);
      refresh(doc.id);
    } catch (e) { feedback(false); setMsg({ tone: "err", text: errorText(e) }); }
  }

  async function complete() {
    if (!doc || !confirm(`Selesaikan ${doc.number}? Stok akan masuk dan task putaway dibuat.`)) return;
    try {
      const r = await api.post<InboundDoc & { result: { putaway_tasks: number } }>(`/wms/inbound/${doc.id}/complete`);
      setMsg({ tone: "ok", text: `${r.number} selesai. ${r.result.putaway_tasks} task putaway dibuat.` });
      setDoc(null); load();
    } catch (e) { setMsg({ tone: "err", text: errorText(e) }); }
  }

  if (!doc) return (
    <>
      <h1 className="text-2xl font-bold">Terima barang</h1>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      {docs.length === 0 ? <p className="text-ink-soft">Tidak ada dokumen inbound terbuka. Supervisor membuatnya di console → Gudang (WMS) → Inbound.</p> : (
        <ul className="flex flex-col gap-2">
          {docs.map((d) => (
            <li key={d.id}><button type="button" onClick={() => { setDoc(d); setMsg(null); }} className="w-full rounded-xl border-[3px] border-ink bg-white p-4 text-left">
              <div className="font-mono text-lg font-bold">{d.number}</div>
              <div className="text-sm text-ink-soft">{d.supplier || "Tanpa supplier"}{d.reference ? ` · ${d.reference}` : ""} · {d.lines.length} SKU</div>
            </button></li>
          ))}
        </ul>
      )}
    </>
  );

  return (
    <>
      <div className="flex items-start justify-between"><div><div className="font-mono text-2xl font-bold">{doc.number}</div><div className="text-sm text-ink-soft">{doc.supplier}</div></div>
        <button type="button" onClick={() => setDoc(null)} className="min-h-10 rounded-xl border-2 border-ink px-3 text-sm">Dokumen lain</button></div>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      <Stepper value={qty} onChange={setQty} />
      <label className="flex min-h-12 items-center gap-3 text-base font-semibold">
        <input type="checkbox" className="h-6 w-6 accent-danger" checked={damaged} onChange={(e) => setDamaged(e.target.checked)} /> Barang rusak
      </label>
      <ScanInput label="Scan barang yang diterima" onScan={onScan} />
      <ul className="divide-y divide-concrete-dark rounded-xl border-[3px] border-ink bg-white">
        {doc.lines.map((l) => {
          const got = l.received_qty + l.damaged_qty;
          return (
            <li key={l.sku_id} className="flex items-center justify-between px-3 py-2">
              <div><div className="font-mono font-bold">{l.sku_code}</div><div className="text-xs text-ink-soft">{l.product_name}{l.damaged_qty ? ` · ${l.damaged_qty} rusak` : ""}</div></div>
              <div className={`text-xl font-bold tabular-nums ${got === l.expected_qty ? "text-ok" : got > l.expected_qty ? "text-danger" : ""}`}>{got}/{l.expected_qty}</div>
            </li>
          );
        })}
      </ul>
      {canManage && <button type="button" onClick={complete} className="min-h-14 rounded-xl bg-ink text-lg font-bold text-white">Selesaikan penerimaan</button>}
    </>
  );
}
