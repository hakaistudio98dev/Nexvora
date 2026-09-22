"use client";
import { useCallback, useEffect, useState } from "react";
import { Flash, ScanInput } from "@/components/scanner";
import { useWarehouse } from "@/components/wh-context";
import { api, errorText } from "@/lib/api";
import { type CountDoc, type CountRow, feedback, sendOrQueue } from "@/lib/wms";

export default function CountPage() {
  const wh = useWarehouse()!;
  const [list, setList] = useState<CountRow[]>([]);
  const [doc, setDoc] = useState<CountDoc | null>(null);
  const [bin, setBin] = useState<string | null>(null);
  const [sku, setSku] = useState<string | null>(null);
  const [qty, setQty] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);

  const load = useCallback(() => api.get<CountRow[]>(`/wms/counts?warehouse_id=${wh.id}`).then((r) => setList(r.filter((x) => x.status === "OPEN")))
    .catch((e) => setMsg({ tone: "err", text: errorText(e) })), [wh.id]);
  useEffect(() => { load(); }, [load]);
  const open = async (id: string) => setDoc(await api.get<CountDoc>(`/wms/counts/${id}`));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!doc || !bin || !sku) return;
    try {
      const r = await sendOrQueue(`/wms/counts/${doc.id}/lines`, { location_code: bin, barcode: sku, counted_qty: Number(qty) }, `Hitung ${sku}@${bin}`);
      feedback(true);
      setMsg({ tone: r.ok ? "ok" : "info", text: r.ok ? `${sku} di ${bin}: ${qty} tersimpan.` : "Offline: hasil hitung disimpan, dikirim nanti." });
      setSku(null); setQty("");
      if (r.ok) open(doc.id);
    } catch (x) { feedback(false); setMsg({ tone: "err", text: errorText(x) }); }
  }

  async function submit() {
    if (!doc || !confirm("Kirim hasil hitung ke supervisor?")) return;
    try { await api.post(`/wms/counts/${doc.id}/submit`); setMsg({ tone: "ok", text: `${doc.number} dikirim untuk persetujuan.` }); setDoc(null); load(); }
    catch (x) { setMsg({ tone: "err", text: errorText(x) }); }
  }

  if (!doc) return (
    <>
      <h1 className="text-2xl font-bold">Hitung stok</h1>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      {list.length === 0 ? <p className="text-ink-soft">Tidak ada cycle count terbuka.</p> : list.map((c) => (
        <button key={c.id} type="button" onClick={() => open(c.id)} className="rounded-xl border-[3px] border-ink bg-white p-4 text-left">
          <div className="font-mono text-lg font-bold">{c.number}</div>
          <div className="text-sm text-ink-soft">Area {c.scope || "semua bin"} · {c.counted}/{c.lines} baris dihitung</div>
        </button>
      ))}
    </>
  );

  const pending = doc.lines.filter((l) => l.counted_qty === null);
  return (
    <>
      <div className="flex items-start justify-between"><div><div className="font-mono text-2xl font-bold">{doc.number}</div><div className="text-sm text-ink-soft">Area {doc.scope || "semua bin"}{doc.blind ? " · hitung tanpa melihat angka sistem" : ""}</div></div>
        <button type="button" onClick={() => setDoc(null)} className="min-h-10 rounded-xl border-2 border-ink px-3 text-sm">Kembali</button></div>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      {!bin ? <ScanInput label="Scan label bin" onScan={(c) => { setBin(c.toUpperCase()); feedback(true); }} />
        : !sku ? (
          <>
            <p className="text-lg">Bin <span className="font-mono font-bold">{bin}</span> <button type="button" onClick={() => setBin(null)} className="ml-2 text-sm underline">ganti</button></p>
            <ScanInput label="Scan barang di bin ini" onScan={(c) => { setSku(c); feedback(true); }} />
          </>
        ) : (
          <form onSubmit={save} className="flex flex-col gap-3 rounded-xl border-[3px] border-ink bg-white p-4">
            <p><span className="font-mono font-bold">{sku}</span> di <span className="font-mono font-bold">{bin}</span></p>
            <label htmlFor="cq" className="font-semibold">Jumlah dihitung</label>
            <input id="cq" type="number" inputMode="numeric" min={0} required autoFocus value={qty} onChange={(e) => setQty(e.target.value)}
                   className="min-h-14 rounded-xl border-[3px] border-ink px-3 text-2xl font-bold tabular-nums" />
            <div className="flex gap-2"><button type="submit" className="min-h-14 flex-1 rounded-xl bg-ink text-lg font-bold text-white">Simpan</button>
              <button type="button" onClick={() => setSku(null)} className="min-h-14 rounded-xl border-2 border-ink px-4">Batal</button></div>
          </form>
        )}
      {pending.length > 0 && (
        <details className="rounded-xl border-2 border-ink bg-white p-3"><summary className="min-h-10 cursor-pointer font-semibold">{pending.length} baris belum dihitung</summary>
          <ul className="mt-2 text-sm">{pending.map((l) => <li key={l.id} className="font-mono">{l.location} · {l.sku_code}</li>)}</ul>
          <p className="mt-2 text-xs text-ink-muted">Bin yang ternyata kosong: scan bin + barang, lalu isi 0.</p></details>
      )}
      <button type="button" onClick={submit} className="min-h-14 rounded-xl border-[3px] border-ink bg-signal text-lg font-bold">Kirim hasil hitung</button>
    </>
  );
}
