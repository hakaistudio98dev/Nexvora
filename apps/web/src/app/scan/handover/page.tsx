"use client";
import { useCallback, useEffect, useState } from "react";
import { Flash, ScanInput } from "@/components/scanner";
import { useWarehouse } from "@/components/wh-context";
import { api, errorText } from "@/lib/api";
import type { Courier, ManifestDoc, Shipment } from "@/lib/types";
import { feedback } from "@/lib/wms";

export default function HandoverPage() {
  const wh = useWarehouse()!;
  const [list, setList] = useState<ManifestDoc[]>([]);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [m, setM] = useState<ManifestDoc | null>(null);
  const [driver, setDriver] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);
  const load = useCallback(() => api.get<ManifestDoc[]>(`/shipping/manifests?warehouse_id=${wh.id}`).then((l) => { setList(l.filter((x) => x.status === "OPEN")); }).catch(() => undefined), [wh.id]);
  useEffect(() => { load(); api.get<Courier[]>("/shipping/couriers").then(setCouriers).catch(() => undefined); }, [load]);
  const refresh = async (id: string) => setM(await api.get<ManifestDoc>(`/shipping/manifests/${id}`));

  async function start(code: string) {
    try { const n = await api.post<ManifestDoc>("/shipping/manifests", { warehouse_id: wh.id, courier_code: code }); setM(n); } catch (e) { setMsg({ tone: "err", text: errorText(e) }); }
  }
  async function scan(code: string) {
    if (!m) return;
    try { const r = await api.post<{ added: Shipment; count: number }>(`/shipping/manifests/${m.id}/scan`, { code }); feedback(true);
      setMsg({ tone: "ok", text: `${r.added.tracking_number} · ${r.added.order_number} (${r.count} paket)` }); refresh(m.id); }
    catch (e) { feedback(false); setMsg({ tone: "err", text: errorText(e) }); }
  }
  async function handover(e: React.FormEvent) {
    e.preventDefault(); if (!m) return;
    try { const r = await api.post<{ handed_over: number }>(`/shipping/manifests/${m.id}/handover`, { driver_name: driver });
      setMsg({ tone: "ok", text: `${r.handed_over} paket diserahkan ke ${driver}.` }); setM(null); setDriver(""); load(); }
    catch (x) { setMsg({ tone: "err", text: errorText(x) }); }
  }

  if (!m) return (
    <>
      <h1 className="text-2xl font-bold">Serah terima kurir</h1>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      {list.map((x) => <button key={x.id} type="button" onClick={() => setM(x)} className="rounded-xl border-[3px] border-ink bg-white p-4 text-left"><div className="font-mono text-lg font-bold">{x.number}</div><div className="text-sm">{x.courier_name} · {x.packages.length} paket</div></button>)}
      <p className="text-sm font-semibold">Kurir yang datang menjemput:</p>
      <div className="grid grid-cols-2 gap-2">{couriers.map((c) => <button key={c.code} type="button" onClick={() => start(c.code)} className="min-h-14 rounded-xl border-[3px] border-ink bg-white font-bold">{c.name}</button>)}</div>
    </>
  );
  return (
    <>
      <div className="flex items-start justify-between"><div><div className="font-mono text-2xl font-bold">{m.number}</div><div className="text-sm">{m.courier_name} · {m.packages.length} paket</div></div>
        <button type="button" onClick={() => setM(null)} className="min-h-10 rounded-xl border-2 border-ink px-3 text-sm">Kembali</button></div>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      <ScanInput label="Scan resi di setiap paket" onScan={scan} />
      <ul className="divide-y divide-concrete-dark rounded-xl border-[3px] border-ink bg-white">{m.packages.map((p) => <li key={p.id} className="px-3 py-2"><span className="font-mono font-bold">{p.tracking_number}</span> <span className="text-sm text-ink-soft">{p.order_number}</span></li>)}</ul>
      {m.packages.length > 0 && (
        <form onSubmit={handover} className="flex flex-col gap-2 rounded-xl border-[3px] border-ink bg-white p-4">
          <label htmlFor="drv" className="font-semibold">Nama kurir penjemput</label>
          <input id="drv" value={driver} onChange={(e) => setDriver(e.target.value)} required minLength={2} className="min-h-14 rounded-xl border-[3px] border-ink px-3 text-lg" />
          <button type="submit" className="min-h-14 rounded-xl bg-ink text-lg font-bold text-white">Serahkan {m.packages.length} paket</button>
        </form>)}
    </>
  );
}
