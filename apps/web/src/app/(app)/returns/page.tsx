"use client";
import { useCallback, useEffect, useState } from "react";
import { useCan } from "@/components/me-context";
import { Alert, Badge, Button, Empty, Field, Input, PageHeader, Select } from "@/components/ui";
import { api, dt, errorText, rupiah } from "@/lib/api";
import { RETURN_STATUS } from "@/lib/shipping";
import type { ReturnDoc } from "@/lib/types";

export default function ReturnsPage() {
  const [filter, setFilter] = useState<"OPEN" | "">("OPEN");
  const [rows, setRows] = useState<ReturnDoc[]>([]);
  const [sel, setSel] = useState<ReturnDoc | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api.get<ReturnDoc[]>(`/returns${filter ? `?status=${filter}` : ""}`).then((r) => {
    setRows(r); setSel((cur) => (cur ? r.find((x) => x.id === cur.id) ?? cur : null));
  }).catch((e) => setErr(errorText(e))), [filter]);
  useEffect(() => { load(); }, [load]);
  return (
    <>
      <PageHeader title="Retur" desc="Pengajuan retur, penerimaan & inspeksi barang, lalu refund atau penggantian." />
      <div className="mb-4 flex gap-2">{([["OPEN", "Sedang diproses"], ["", "Semua"]] as const).map(([k, l]) => (
        <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(k)} className={`min-h-10 rounded-full border px-4 text-sm font-semibold ${filter === k ? "border-ink bg-ink text-white" : "border-concrete-line bg-white"}`}>{l}</button>))}</div>
      {err && <Alert>{err}</Alert>}
      <div className={`grid gap-6 ${sel ? "xl:grid-cols-[1fr_480px]" : ""}`}>
        {rows.length === 0 ? <Empty title="Tidak ada retur">Retur diajukan dari detail order yang sudah dikirim.</Empty> : (
          <ul className="flex flex-col gap-2">{rows.map((r) => (
            <li key={r.id}><button type="button" onClick={() => setSel(r)} className={`w-full rounded-lg border p-3 text-left ${sel?.id === r.id ? "border-ink/30 bg-signal-soft/40" : "border-concrete-line bg-white hover:border-ink"}`}>
              <div className="flex flex-wrap justify-between gap-2"><span className="font-mono font-semibold">{r.number} · {r.order_number}</span><Badge tone={r.status === "CLOSED" ? "ok" : r.status === "REJECTED" ? "off" : "signal"}>{RETURN_STATUS[r.status]}</Badge></div>
              <div className="text-sm text-ink-soft">{r.customer_name} · {r.reason_label} · {dt(r.created_at)}</div></button></li>))}</ul>)}
        {sel && <ReturnPanel r={sel} onChanged={load} />}
      </div>
    </>
  );
}

function ReturnPanel({ r, onChanged }: { r: ReturnDoc; onChanged: () => void }) {
  const canWrite = useCan("returns:write");
  const canReceive = useCan("returns:receive");
  const [err, setErr] = useState<string | null>(null);
  const [recv, setRecv] = useState<Record<string, number>>({});
  const [insp, setInsp] = useState<Record<string, { restock: number; damaged: number }>>({});
  const [res, setRes] = useState({ resolution: "REFUND", amount: "", ref: "" });
  const [note, setNote] = useState("");
  useEffect(() => {
    setErr(null);
    setRecv(Object.fromEntries(r.lines.map((l) => [l.id, l.quantity])));
    setInsp(Object.fromEntries(r.lines.map((l) => [l.id, { restock: l.received_qty, damaged: 0 }])));
  }, [r]);
  async function post(path: string, body: unknown) { setErr(null); try { await api.post(path, body); onChanged(); } catch (e) { setErr(errorText(e)); } }
  return (
    <aside className="h-fit rounded-xl border border-concrete-dark bg-white shadow-card" aria-label="Detail retur">
      <div className="border-b border-concrete-dark p-4"><div className="font-mono text-lg font-bold">{r.number}</div>
        <div className="text-sm">Order {r.order_number} · {r.customer_name} · total {rupiah(r.order_total)}</div>
        <div className="mt-1 text-sm"><Badge>{r.reason_label}</Badge> {r.note && <span className="text-ink-soft">“{r.note}”</span>}</div></div>
      <div className="flex flex-col gap-4 p-4 text-sm">
        {err && <Alert>{err}</Alert>}
        <table className="w-full tabular-nums"><thead className="text-left text-ink-muted"><tr><th>SKU</th><th className="text-right">Retur</th><th className="text-right">Terima</th><th className="text-right">Jual</th><th className="text-right">Rusak</th></tr></thead>
          <tbody>{r.lines.map((l) => <tr key={l.id} className="border-t border-concrete-dark"><td className="py-1.5 font-mono">{l.sku_code}</td><td className="text-right">{l.quantity}</td><td className="text-right">{l.received_qty}</td><td className="text-right">{l.restock_qty}</td><td className="text-right">{l.damaged_qty}</td></tr>)}</tbody></table>

        {r.status === "REQUESTED" && canWrite && (
          <div className="flex flex-col gap-2"><Field label="Catatan keputusan">{(id) => <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
            <div className="flex gap-2"><Button onClick={() => post(`/returns/${r.id}/decide`, { approve: true, note })}>Setujui retur</Button>
              <Button variant="danger" onClick={() => post(`/returns/${r.id}/decide`, { approve: false, note })}>Tolak</Button></div></div>)}
        {r.status === "APPROVED" && canReceive && (
          <form onSubmit={(e) => { e.preventDefault(); post(`/returns/${r.id}/receive`, r.lines.map((l) => ({ line_id: l.id, received_qty: recv[l.id] ?? 0 }))); }} className="flex flex-col gap-2">
            <p className="font-semibold">Terima barang di gudang</p>
            {r.lines.map((l) => <Field key={l.id} label={`${l.sku_code} diterima (maks ${l.quantity})`}>{(id) => <Input id={id} type="number" min={0} max={l.quantity} value={recv[l.id] ?? 0} onChange={(e) => setRecv({ ...recv, [l.id]: Number(e.target.value) })} />}</Field>)}
            <Button type="submit">Catat penerimaan</Button></form>)}
        {r.status === "RECEIVED" && canReceive && (
          <form onSubmit={(e) => { e.preventDefault(); post(`/returns/${r.id}/inspect`, r.lines.map((l) => ({ line_id: l.id, restock_qty: insp[l.id]?.restock ?? 0, damaged_qty: insp[l.id]?.damaged ?? 0 }))); }} className="flex flex-col gap-2">
            <p className="font-semibold">Inspeksi: layak jual kembali atau rusak?</p>
            {r.lines.filter((l) => l.received_qty > 0).map((l) => (
              <div key={l.id} className="grid grid-cols-[1fr_90px_90px] items-end gap-2"><span className="pb-3 font-mono">{l.sku_code} ({l.received_qty})</span>
                <Field label="Jual">{(id) => <Input id={id} type="number" min={0} value={insp[l.id]?.restock ?? 0} onChange={(e) => setInsp({ ...insp, [l.id]: { ...insp[l.id], restock: Number(e.target.value) } })} />}</Field>
                <Field label="Rusak">{(id) => <Input id={id} type="number" min={0} value={insp[l.id]?.damaged ?? 0} onChange={(e) => setInsp({ ...insp, [l.id]: { ...insp[l.id], damaged: Number(e.target.value) } })} />}</Field></div>))}
            <Button type="submit">Simpan inspeksi</Button>
            <p className="text-xs text-ink-muted">Barang layak jual kembali ke stok tersedia dan dibuatkan task putaway.</p></form>)}
        {r.status === "INSPECTED" && canWrite && (
          <form onSubmit={(e) => { e.preventDefault(); post(`/returns/${r.id}/resolve`, { resolution: res.resolution, refund_amount: res.resolution === "REFUND" ? res.amount : null, refund_ref: res.ref || null }); }} className="flex flex-col gap-2">
            <p className="font-semibold">Penyelesaian untuk pelanggan</p>
            <Field label="Solusi">{(id) => <Select id={id} value={res.resolution} onChange={(e) => setRes({ ...res, resolution: e.target.value })}>
              <option value="REFUND">Refund dana</option><option value="REPLACEMENT">Kirim barang pengganti</option><option value="NONE">Tanpa kompensasi</option></Select>}</Field>
            {res.resolution === "REFUND" && <><Field label="Nominal refund">{(id) => <Input id={id} type="number" min={1} value={res.amount} onChange={(e) => setRes({ ...res, amount: e.target.value })} required />}</Field>
              <Field label="Referensi transfer / marketplace">{(id) => <Input id={id} value={res.ref} onChange={(e) => setRes({ ...res, ref: e.target.value })} />}</Field></>}
            {res.resolution === "REPLACEMENT" && <p className="text-xs text-ink-muted">Order pengganti Rp0 dibuat otomatis dan langsung dialokasikan ke gudang.</p>}
            <Button type="submit">Selesaikan retur</Button></form>)}
        {r.status === "CLOSED" && <p className="text-ok">Selesai {dt(r.closed_at)}: {r.resolution === "REFUND" ? `refund ${rupiah(r.refund_amount ?? 0)}` : r.resolution === "REPLACEMENT" ? `pengganti ${r.replacement_order_number}` : "tanpa kompensasi"}</p>}
      </div>
    </aside>
  );
}
