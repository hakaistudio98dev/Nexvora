"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useCan } from "@/components/me-context";
import { Alert, Badge, Button, Empty, Field, Input, PageHeader, Select } from "@/components/ui";
import { api, dt, errorText } from "@/lib/api";
import type { Page, Sku, Warehouse } from "@/lib/types";
import { type CountDoc, type CountRow, EXC_LABEL, type InboundDoc, type WaveRow, type WmsExc, type WmsStats,
  saveWarehouse, savedWarehouse } from "@/lib/wms";

const TABS = [
  { key: "overview", label: "Ringkasan" }, { key: "inbound", label: "Barang masuk" }, { key: "waves", label: "Ambil barang" },
  { key: "exceptions", label: "Masalah gudang" }, { key: "counts", label: "Hitung stok" }, { key: "labels", label: "Cetak label" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

export default function WmsPage() {
  const [whs, setWhs] = useState<Warehouse[]>([]);
  const [wh, setWh] = useState("");
  const [tab, setTab] = useState<TabKey>("overview");
  useEffect(() => {
    api.get<Warehouse[]>("/warehouses").then((l) => {
      const a = l.filter((w) => w.is_active);
      setWhs(a);
      setWh(a.find((w) => w.id === savedWarehouse())?.id ?? a[0]?.id ?? "");
    }).catch(() => undefined);
  }, []);

  return (
    <>
      <PageHeader title="Kerja gudang" desc="Terima barang, simpan ke rak, ambil & kemas pesanan, dan hitung stok."
        action={<div className="flex flex-wrap items-end gap-2 no-print">
          <div className="w-60"><label htmlFor="wwh" className="mb-1 block text-sm font-semibold">Gudang</label>
            <Select id="wwh" value={wh} onChange={(e) => { setWh(e.target.value); saveWarehouse(e.target.value); }}>
              {whs.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}
            </Select></div>
          <Link href="/scan" className="inline-flex min-h-11 items-center rounded-md bg-signal px-4 text-sm font-bold text-ink">Buka scanner</Link>
        </div>} />
      {!wh ? <Empty title="Belum ada gudang aktif">Buat gudang di menu Gudang & lokasi.</Empty> : (
        <>
          <div role="tablist" aria-label="Menu WMS" className="no-print mb-5 flex gap-1 overflow-x-auto border-b border-concrete-dark">
            {TABS.map((t) => (
              <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
                      className={`-mb-0.5 min-h-11 shrink-0 border-b-2 px-4 text-sm font-semibold ${tab === t.key ? "border-signal" : "border-transparent text-ink-soft"}`}>{t.label}</button>
            ))}
          </div>
          <div role="tabpanel">
            {tab === "overview" && <Overview wh={wh} />}
            {tab === "inbound" && <InboundTab wh={wh} />}
            {tab === "waves" && <WavesTab wh={wh} />}
            {tab === "exceptions" && <ExceptionsTab wh={wh} />}
            {tab === "counts" && <CountsTab wh={wh} />}
            {tab === "labels" && <LabelsTab wh={wh} />}
          </div>
        </>
      )}
    </>
  );
}

function Overview({ wh }: { wh: string }) {
  const [s, setS] = useState<WmsStats | null>(null);
  useEffect(() => { api.get<WmsStats>(`/wms/stats?warehouse_id=${wh}`).then(setS).catch(() => undefined); }, [wh]);
  if (!s) return null;
  const cards: [string, number, boolean?][] = [
    ["Order siap diambil dari rak", s.orders_allocated, s.orders_allocated > 0], ["Tugas ambil barang", s.open_picks],
    ["Order sedang picking", s.orders_picking], ["Order di packing", s.orders_packing], ["Siap dikirim", s.ready_to_ship],
    ["Paket 24 jam terakhir", s.packed_24h], ["Penerimaan barang terbuka", s.inbound_open], ["Barang belum disimpan ke rak", s.open_putaway],
    ["Masalah gudang terbuka", s.exceptions_open, s.exceptions_open > 0],
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {cards.map(([l, v, hot]) => (
        <div key={l} className={`rounded-xl border border-concrete-dark p-4 ${hot ? "bg-signal" : "bg-white"}`}>
          <div className="text-sm text-ink-soft">{l}</div><div className="text-3xl font-bold tabular-nums">{v}</div>
        </div>
      ))}
    </div>
  );
}

function InboundTab({ wh }: { wh: string }) {
  const canManage = useCan("wms:manage");
  const [docs, setDocs] = useState<InboundDoc[]>([]);
  const [skus, setSkus] = useState<Sku[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ supplier: "", reference: "" });
  const [lines, setLines] = useState([{ sku_id: "", expected_qty: "" }]);
  const load = useCallback(() => api.get<InboundDoc[]>(`/wms/inbound?warehouse_id=${wh}`).then(setDocs).catch((e) => setErr(errorText(e))), [wh]);
  useEffect(() => { load(); api.get<Page<Sku>>("/skus?limit=200").then((p) => setSkus(p.items.filter((s) => s.is_active))).catch(() => undefined); }, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try {
      await api.post("/wms/inbound", { warehouse_id: wh, ...form,
        lines: lines.filter((l) => l.sku_id).map((l) => ({ sku_id: l.sku_id, expected_qty: Number(l.expected_qty) || 0 })) });
      setForm({ supplier: "", reference: "" }); setLines([{ sku_id: "", expected_qty: "" }]); load();
    } catch (x) { setErr(errorText(x)); }
  }
  async function act(id: string, action: "complete" | "cancel") {
    try { await api.post(`/wms/inbound/${id}/${action}`); load(); } catch (x) { setErr(errorText(x)); }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
      <div className="flex flex-col gap-3">
        {err && <Alert>{err}</Alert>}
        {docs.length === 0 ? <Empty title="Belum ada penerimaan barang">Buat dokumen saat ada kiriman dari supplier, lalu staf gudang men-scan barangnya.</Empty> : docs.map((d) => (
          <div key={d.id} className="rounded-xl border border-concrete-dark bg-white shadow-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div><span className="font-mono font-bold">{d.number}</span> <Badge tone={d.status === "COMPLETED" ? "ok" : d.status === "CANCELLED" ? "off" : "signal"}>{d.status}</Badge>
                <div className="text-sm text-ink-soft">{d.supplier || "—"}{d.reference ? ` · ${d.reference}` : ""} · {dt(d.created_at)}</div></div>
              {canManage && d.status === "RECEIVING" && <div className="flex gap-2">
                <Button variant="secondary" onClick={() => act(d.id, "complete")}>Selesaikan</Button>
                <Button variant="ghost" onClick={() => act(d.id, "cancel")}>Batalkan</Button></div>}
            </div>
            <table className="mt-3 w-full text-sm tabular-nums"><thead className="text-left text-ink-muted"><tr><th className="py-1">SKU</th><th className="text-right">Dokumen</th><th className="text-right">Diterima</th><th className="text-right">Rusak</th></tr></thead>
              <tbody>{d.lines.map((l) => <tr key={l.sku_id} className="border-t border-concrete-dark"><td className="py-1 font-mono">{l.sku_code}</td><td className="text-right">{l.expected_qty}</td>
                <td className={`text-right font-semibold ${l.received_qty + l.damaged_qty !== l.expected_qty && d.status !== "RECEIVING" ? "text-danger" : ""}`}>{l.received_qty}</td><td className="text-right">{l.damaged_qty}</td></tr>)}</tbody></table>
          </div>
        ))}
      </div>
      {canManage && (
        <form onSubmit={create} className="flex h-fit flex-col gap-3 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
          <h2 className="text-lg font-bold">Catat barang masuk</h2>
          <Field label="Supplier">{(id) => <Input id={id} value={form.supplier} onChange={(e) => setForm({ ...form, supplier: e.target.value })} />}</Field>
          <Field label="No. PO / surat jalan">{(id) => <Input id={id} value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} />}</Field>
          <fieldset className="flex flex-col gap-2"><legend className="mb-1 text-sm font-semibold">Barang yang diharapkan (opsional)</legend>
            {lines.map((l, i) => (
              <div key={i} className="grid grid-cols-[1fr_90px] gap-2">
                <Select aria-label={`SKU baris ${i + 1}`} value={l.sku_id} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, sku_id: e.target.value } : x))}>
                  <option value="">Pilih SKU</option>{skus.map((s) => <option key={s.id} value={s.id}>{s.sku_code}</option>)}</Select>
                <Input aria-label={`Jumlah baris ${i + 1}`} type="number" min={0} placeholder="Qty" value={l.expected_qty} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, expected_qty: e.target.value } : x))} />
              </div>))}
            <Button type="button" variant="ghost" className="self-start" onClick={() => setLines([...lines, { sku_id: "", expected_qty: "" }])}>Tambah baris</Button>
          </fieldset>
          <Button type="submit">Buat dokumen penerimaan</Button>
          <p className="text-xs text-ink-muted">Operator lalu scan barang di menu Scanner → Terima barang.</p>
        </form>
      )}
    </div>
  );
}

function WavesTab({ wh }: { wh: string }) {
  const canManage = useCan("wms:manage");
  const [waves, setWaves] = useState<WaveRow[]>([]);
  const [st, setSt] = useState<WmsStats | null>(null);
  const [max, setMax] = useState("20");
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [w, s] = await Promise.all([api.get<WaveRow[]>(`/wms/waves?warehouse_id=${wh}`), api.get<WmsStats>(`/wms/stats?warehouse_id=${wh}`)]);
      setWaves(w); setSt(s);
    } catch (e) { setErr(errorText(e)); }
  }, [wh]);
  useEffect(() => { load(); }, [load]);
  async function create() {
    setErr(null);
    try { await api.post("/wms/waves", { warehouse_id: wh, max_orders: Number(max) || 20 }); load(); } catch (e) { setErr(errorText(e)); }
  }
  return (
    <div className="flex flex-col gap-4">
      {canManage && (
        <div className="flex flex-wrap items-end gap-3 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
          <p className="flex-1 text-sm"><strong className="text-2xl tabular-nums">{st?.orders_allocated ?? 0}</strong> order siap diambil. Satu batch menggabungkan beberapa order menjadi daftar ambil per rak, diurutkan sesuai jalur jalan.</p>
          <div className="w-28"><Field label="Maks. order">{(id) => <Input id={id} type="number" min={1} max={200} value={max} onChange={(e) => setMax(e.target.value)} />}</Field></div>
          <Button onClick={create} disabled={!st?.orders_allocated}>Buat batch ambil barang</Button>
        </div>
      )}
      {err && <Alert>{err}</Alert>}
      {waves.length === 0 ? <Empty title="Belum ada batch ambil barang" /> : (
        <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
          <table className="w-full min-w-[640px] text-left text-sm tabular-nums">
            <thead className="border-b border-concrete-dark bg-concrete"><tr><th className="px-3 py-2">Batch</th><th className="px-3 py-2">Status</th><th className="px-3 py-2 text-right">Order</th><th className="px-3 py-2">Progres unit</th><th className="px-3 py-2 text-right">Kurang</th><th className="px-3 py-2">Dibuat</th></tr></thead>
            <tbody>{waves.map((w) => (
              <tr key={w.id} className="border-b border-concrete-dark">
                <td className="px-3 py-2 font-mono font-semibold">{w.number}</td>
                <td className="px-3 py-2"><Badge tone={w.status === "DONE" ? "ok" : "signal"}>{w.status}</Badge></td>
                <td className="px-3 py-2 text-right">{w.orders}</td>
                <td className="px-3 py-2"><div className="flex items-center gap-2"><div className="h-2 w-32 overflow-hidden rounded-full bg-concrete-dark"><div className="h-full bg-ink" style={{ width: `${w.units ? (w.units_picked / w.units) * 100 : 0}%` }} /></div>{w.units_picked}/{w.units}</div></td>
                <td className={`px-3 py-2 text-right ${w.short ? "font-bold text-danger" : ""}`}>{w.short}</td>
                <td className="px-3 py-2 text-xs text-ink-soft">{dt(w.created_at)}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ExceptionsTab({ wh }: { wh: string }) {
  const canManage = useCan("wms:manage");
  const [status, setStatus] = useState<"OPEN" | "RESOLVED">("OPEN");
  const [rows, setRows] = useState<WmsExc[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [resolving, setResolving] = useState<WmsExc | null>(null);
  const [note, setNote] = useState("");
  const load = useCallback(() => api.get<WmsExc[]>(`/wms/exceptions?warehouse_id=${wh}&status=${status}`).then(setRows).catch((e) => setErr(errorText(e))), [wh, status]);
  useEffect(() => { load(); }, [load]);
  async function resolve(action: "NOTE" | "REPICK") {
    if (!resolving) return;
    try { await api.post(`/wms/exceptions/${resolving.id}/resolve`, { action, note }); setResolving(null); setNote(""); load(); }
    catch (e) { setErr(errorText(e)); }
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-2">{(["OPEN", "RESOLVED"] as const).map((s) => (
        <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)} className={`min-h-10 rounded-full border px-4 text-sm font-semibold ${status === s ? "border-ink bg-ink text-white" : "border-concrete-line bg-white"}`}>{s === "OPEN" ? "Terbuka" : "Selesai"}</button>))}</div>
      {err && <Alert>{err}</Alert>}
      {rows.length === 0 ? <Empty title={status === "OPEN" ? "Tidak ada masalah gudang" : "Belum ada yang diselesaikan"} /> : (
        <ul className="flex flex-col gap-2">{rows.map((e) => (
          <li key={e.id} className="rounded-xl border border-concrete-dark bg-white shadow-card p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="flex flex-wrap items-center gap-2"><Badge tone={e.status === "OPEN" ? "signal" : "ok"}>{EXC_LABEL[e.exc_type] ?? e.exc_type}</Badge>
                  <span className="font-mono text-sm">{[e.order_number, e.sku_code, e.location].filter(Boolean).join(" · ")}</span>{e.quantity != null && <span className="text-sm">qty {e.quantity}</span>}</div>
                <p className="mt-1 text-sm">{e.note}</p>
                {e.resolution && <p className="mt-1 text-sm text-ok">Penyelesaian: {e.resolution}</p>}
                <p className="text-xs text-ink-muted">{dt(e.created_at)}</p>
              </div>
              {canManage && e.status === "OPEN" && <Button variant="secondary" onClick={() => { setResolving(e); setNote(""); }}>Selesaikan</Button>}
            </div>
            {resolving?.id === e.id && (
              <div className="mt-3 flex flex-col gap-2 border-t border-concrete-dark pt-3">
                <Field label="Catatan penyelesaian">{(id) => <Input id={id} value={note} onChange={(x) => setNote(x.target.value)} />}</Field>
                <div className="flex flex-wrap gap-2">
                  {e.can_repick && <Button disabled={note.trim().length < 3} onClick={() => resolve("REPICK")}>Pick ulang dari bin lain</Button>}
                  <Button variant="secondary" disabled={note.trim().length < 3} onClick={() => resolve("NOTE")}>Tandai selesai</Button>
                  <Button variant="ghost" onClick={() => setResolving(null)}>Batal</Button>
                </div>
                {e.exc_type === "MISSING" && <p className="text-xs text-ink-muted">Stok di bin asal masih tercatat. Buat cycle count untuk bin itu agar angka sistem dikoreksi.</p>}
              </div>
            )}
          </li>))}</ul>
      )}
    </div>
  );
}

function CountsTab({ wh }: { wh: string }) {
  const canManage = useCan("wms:manage");
  const canAdjust = useCan("inventory:adjust");
  const [rows, setRows] = useState<CountRow[]>([]);
  const [doc, setDoc] = useState<CountDoc | null>(null);
  const [prefix, setPrefix] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api.get<CountRow[]>(`/wms/counts?warehouse_id=${wh}`).then(setRows).catch((e) => setErr(errorText(e))), [wh]);
  useEffect(() => { load(); }, [load]);
  const open = async (id: string) => { try { setDoc(await api.get<CountDoc>(`/wms/counts/${id}`)); } catch (e) { setErr(errorText(e)); } };
  async function create(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try { const d = await api.post<CountDoc>("/wms/counts", { warehouse_id: wh, prefix }); setDoc(d); setPrefix(""); load(); } catch (x) { setErr(errorText(x)); }
  }
  async function act(action: "approve" | "cancel") {
    if (!doc) return;
    try { setDoc(await api.post<CountDoc>(`/wms/counts/${doc.id}/${action}`)); load(); } catch (x) { setErr(errorText(x)); }
  }
  return (
    <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
      <div className="flex flex-col gap-3">
        {canManage && (
          <form onSubmit={create} className="flex flex-col gap-3 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
            <Field label="Area yang dihitung" hint="Awalan kode lokasi, mis. A atau A-01. Kosong = semua bin.">{(id) => <Input id={id} value={prefix} onChange={(e) => setPrefix(e.target.value.toUpperCase())} />}</Field>
            <Button type="submit">Mulai hitung stok</Button>
          </form>
        )}
        {rows.map((c) => (
          <button key={c.id} type="button" onClick={() => open(c.id)} className={`rounded-lg border p-3 text-left ${doc?.id === c.id ? "border-ink/30 bg-signal-soft" : "border-concrete-line bg-white hover:border-ink"}`}>
            <div className="flex justify-between"><span className="font-mono font-semibold">{c.number}</span><span className="text-xs">{c.status}</span></div>
            <div className="text-sm text-ink-soft">Area {c.scope || "semua"} · {c.counted}/{c.lines} dihitung</div>
          </button>))}
      </div>
      <div>
        {err && <Alert>{err}</Alert>}
        {doc && (
          <div className="rounded-xl border border-concrete-dark bg-white shadow-card">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-concrete-dark p-4">
              <div><span className="font-mono text-lg font-bold">{doc.number}</span> <Badge>{doc.status}</Badge></div>
              <div className="flex gap-2">
                {doc.status === "SUBMITTED" && canManage && canAdjust && <Button onClick={() => act("approve")}>Setujui & sesuaikan stok</Button>}
                {["OPEN", "SUBMITTED"].includes(doc.status) && canManage && <Button variant="ghost" onClick={() => act("cancel")}>Batalkan</Button>}
              </div>
            </div>
            {doc.result && <p className="border-b border-concrete-dark bg-green-50 px-4 py-2 text-sm font-semibold text-ok">Akurasi {doc.result.accuracy_pct}% · {doc.result.adjusted} baris disesuaikan dari {doc.result.lines}</p>}
            {doc.status === "OPEN" && <p className="px-4 pt-3 text-sm text-ink-soft">Operator menghitung lewat Scanner → Hitung stok (tanpa melihat angka sistem).</p>}
            <div className="overflow-x-auto p-4">
              <table className="w-full text-sm tabular-nums"><thead className="text-left text-ink-muted"><tr><th className="py-1">Bin</th><th>SKU</th><th className="text-right">Sistem</th><th className="text-right">Dihitung</th><th className="text-right">Selisih</th></tr></thead>
                <tbody>{doc.lines.map((l) => (
                  <tr key={l.id} className="border-t border-concrete-dark"><td className="py-1.5 font-mono">{l.location}</td><td className="font-mono">{l.sku_code}</td>
                    <td className="text-right">{l.system_qty ?? "—"}</td><td className="text-right">{l.counted_qty ?? "—"}</td>
                    <td className={`text-right font-bold ${l.variance ? (l.variance < 0 ? "text-danger" : "text-ok") : ""}`}>{l.variance == null ? "—" : l.variance > 0 ? `+${l.variance}` : l.variance}</td></tr>))}</tbody></table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function LabelsTab({ wh }: { wh: string }) {
  const [kind, setKind] = useState<"BIN" | "SKU">("BIN");
  const [prefix, setPrefix] = useState("");
  const [codes, setCodes] = useState<{ value: string; caption: string }[]>([]);
  const load = useCallback(async () => {
    if (kind === "BIN") {
      const r = await api.get<{ full_code: string }[]>(`/wms/labels/bins?warehouse_id=${wh}&prefix=${encodeURIComponent(prefix)}`);
      setCodes(r.map((x) => ({ value: x.full_code, caption: x.full_code })));
    } else {
      const r = await api.get<Page<Sku>>(`/skus?limit=200${prefix ? `&q=${encodeURIComponent(prefix)}` : ""}`);
      setCodes(r.items.map((s) => ({ value: s.barcode || s.sku_code, caption: `${s.sku_code}${s.variant_name ? ` · ${s.variant_name}` : ""}` })));
    }
  }, [kind, prefix, wh]);
  useEffect(() => { const t = setTimeout(() => load().catch(() => undefined), 250); return () => clearTimeout(t); }, [load]);
  return (
    <div>
      <div className="no-print mb-4 flex flex-wrap items-end gap-3">
        <div className="flex gap-2">{(["BIN", "SKU"] as const).map((k) => (
          <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)} className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${kind === k ? "border-ink bg-ink text-white" : "border-concrete-line bg-white"}`}>{k === "BIN" ? "Label bin" : "Label SKU"}</button>))}</div>
        <div className="w-56"><Field label={kind === "BIN" ? "Awalan lokasi" : "Cari SKU"}>{(id) => <Input id={id} value={prefix} onChange={(e) => setPrefix(e.target.value)} />}</Field></div>
        <Button onClick={() => window.print()} disabled={!codes.length}>Cetak {codes.length} label</Button>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 print:grid-cols-3 print:gap-2">
        {codes.map((c) => <Label key={c.value + c.caption} value={c.value} caption={c.caption} big={kind === "BIN"} />)}
      </div>
    </div>
  );
}

function Label({ value, caption, big }: { value: string; caption: string; big: boolean }) {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => {
    import("jsbarcode").then(({ default: JsBarcode }) => {
      if (ref.current) JsBarcode(ref.current, value, { format: "CODE128", height: big ? 60 : 44, width: 2, displayValue: false, margin: 0 });
    }).catch(() => undefined);
  }, [value, big]);
  return (
    <div className="break-inside-avoid rounded-xl border border-concrete-dark bg-white shadow-card p-3 text-center">
      <svg ref={ref} className="mx-auto max-w-full" role="img" aria-label={`Barcode ${value}`} />
      <div className={`mt-1 font-mono font-bold ${big ? "text-2xl" : "text-sm"}`}>{caption}</div>
    </div>
  );
}
