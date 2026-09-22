"use client";
import { useCallback, useEffect, useState } from "react";
import { useCan } from "@/components/me-context";
import { Alert, Badge, Button, Empty, Field, Input, Modal, PageHeader, Select } from "@/components/ui";
import { api, dt, errorText, qs } from "@/lib/api";
import { ENTRY_LABEL } from "@/lib/labels";
import type { Balance, LedgerEntry, Page, Reconcile, Sku, Warehouse } from "@/lib/types";


export default function InventoryPage() {
  const canReceive = useCan("inventory:write");
  const canAdjust = useCan("inventory:adjust");
  const [whs, setWhs] = useState<Warehouse[]>([]);
  const [skus, setSkus] = useState<Sku[]>([]);
  const [wh, setWh] = useState("");
  const [q, setQ] = useState("");
  const [lowOnly, setLowOnly] = useState(false);
  const [rows, setRows] = useState<Balance[]>([]);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [focus, setFocus] = useState<Balance | null>(null);
  const [rec, setRec] = useState<Reconcile | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [modal, setModal] = useState<null | "receive" | "adjust">(null);

  useEffect(() => {
    api.get<Warehouse[]>("/warehouses").then(setWhs).catch(() => undefined);
    api.get<Page<Sku>>("/skus?limit=200").then((p) => setSkus(p.items)).catch(() => undefined);
  }, []);

  const load = useCallback(async () => {
    try {
      const [b, l, r] = await Promise.all([
        api.get<Page<Balance>>(`/inventory${qs({ warehouse_id: wh, q, low_only: lowOnly || undefined, limit: 500 })}`),
        api.get<LedgerEntry[]>(`/inventory/ledger${qs({ warehouse_id: focus?.warehouse_id ?? wh, sku_id: focus?.sku_id, limit: 30 })}`),
        api.get<Reconcile>(`/inventory/reconcile${qs({ warehouse_id: wh })}`),
      ]);
      setRows(b.items); setLedger(l); setRec(r); setErr(null);
    } catch (e) { setErr(errorText(e)); }
  }, [wh, q, lowOnly, focus]);
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [load]);

  const totals = rows.reduce((a, r) => ({ on_hand: a.on_hand + r.on_hand, reserved: a.reserved + r.reserved, available: a.available + r.available }), { on_hand: 0, reserved: 0, available: 0 });

  return (
    <>
      <PageHeader title="Stok barang" desc="Jumlah barang di setiap gudang. Setiap perubahan tercatat di riwayat stok dan tidak bisa diubah."
        action={<div className="flex flex-wrap gap-2">
          {canAdjust && <Button variant="secondary" onClick={() => setModal("adjust")}>Penyesuaian</Button>}
          {canReceive && <Button onClick={() => setModal("receive")}>Terima stok</Button>}
        </div>} />

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        {[["Stok fisik", totals.on_hand], ["Sudah dipesan", totals.reserved], ["Bisa dijual", totals.available]].map(([l, v]) => (
          <div key={l as string} className="rounded-xl border border-concrete-dark bg-white shadow-card p-4">
            <div className="text-sm text-ink-muted">{l}</div>
            <div className="text-3xl font-bold tabular-nums">{(v as number).toLocaleString("id-ID")}</div>
          </div>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="w-56">
          <label htmlFor="wh" className="mb-1 block text-sm font-semibold">Gudang</label>
          <Select id="wh" value={wh} onChange={(e) => { setWh(e.target.value); setFocus(null); }}>
            <option value="">Semua gudang</option>
            {whs.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}
          </Select>
        </div>
        <div className="w-64">
          <label htmlFor="iq" className="mb-1 block text-sm font-semibold">Cari</label>
          <Input id="iq" placeholder="Kode SKU atau nama produk" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" className="h-4 w-4 accent-ink" checked={lowOnly} onChange={(e) => setLowOnly(e.target.checked)} />
          Hanya stok menipis (di bawah batas per SKU / Pengaturan)
        </label>
        {rec && (
          <span className={`ml-auto text-sm font-semibold ${rec.ok ? "text-ok" : "text-danger"}`} role="status">
            {rec.ok ? `Stok cocok dengan riwayat (${rec.checked} SKU diperiksa)` : `${rec.mismatches.length} stok tidak cocok dengan riwayat — hubungi admin`}
          </span>
        )}
      </div>
      {err && <Alert>{err}</Alert>}

      {rows.length === 0 ? (
        <Empty title={lowOnly ? "Tidak ada stok yang menipis" : "Belum ada stok"}>{canReceive && !lowOnly && "Gunakan Terima stok untuk mencatat stok awal atau barang masuk dari supplier."}</Empty>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="border-b border-concrete-dark bg-concrete">
              <tr><th className="px-3 py-3">SKU</th><th className="px-3 py-3">Gudang</th><th className="px-3 py-3 text-right">Fisik</th><th className="px-3 py-3 text-right">Dipesan</th><th className="px-3 py-3 text-right">Tersedia</th><th className="px-3 py-3 text-right">Rusak</th><th className="px-3 py-3"><span className="sr-only">Aksi</span></th></tr>
            </thead>
            <tbody className="tabular-nums">
              {rows.map((r) => {
                const on = focus?.sku_id === r.sku_id && focus?.warehouse_id === r.warehouse_id;
                return (
                  <tr key={`${r.warehouse_id}-${r.sku_id}`} className={`border-b border-concrete-dark ${on ? "bg-signal-soft" : ""}`}>
                    <td className="px-3 py-2.5"><div className="font-mono font-semibold">{r.sku_code}</div><div className="text-xs text-ink-muted">{r.product_name}{r.variant_name ? ` · ${r.variant_name}` : ""}</div></td>
                    <td className="px-3 py-2.5 font-mono">{r.warehouse_code}</td>
                    <td className="px-3 py-2.5 text-right">{r.on_hand}</td>
                    <td className="px-3 py-2.5 text-right text-ink-soft">{r.reserved}</td>
                    <td className="px-3 py-2.5 text-right font-bold">{r.available <= r.threshold ? <Badge tone={r.available <= 0 ? "off" : "signal"}>{r.available}</Badge> : r.available}</td>
                    <td className="px-3 py-2.5 text-right text-ink-soft">{r.damaged}</td>
                    <td className="px-3 py-2.5 text-right"><Button variant="ghost" onClick={() => setFocus(on ? null : r)}>{on ? "Semua riwayat" : "Riwayat"}</Button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <section className="mt-8">
        <h2 className="mb-3 text-lg font-bold">
          Riwayat stok {focus ? <span className="font-mono text-base">{focus.sku_code} @ {focus.warehouse_code}</span> : "terbaru"}
        </h2>
        {ledger.length === 0 ? <p className="text-sm text-ink-muted">Belum ada mutasi.</p> : (
          <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
            <table className="w-full min-w-[720px] text-left text-sm tabular-nums">
              <thead className="border-b border-concrete-dark bg-concrete">
                <tr><th className="px-3 py-2">Waktu</th><th className="px-3 py-2">Mutasi</th><th className="px-3 py-2">SKU</th><th className="px-3 py-2 text-right">Perubahan fisik</th><th className="px-3 py-2 text-right">Perubahan dipesan</th><th className="px-3 py-2 text-right">Saldo fisik</th><th className="px-3 py-2">Referensi</th></tr>
              </thead>
              <tbody>
                {ledger.map((e) => (
                  <tr key={e.id} className="border-b border-concrete-dark">
                    <td className="px-3 py-2 text-xs text-ink-soft">{dt(e.created_at)}</td>
                    <td className="px-3 py-2">{ENTRY_LABEL[e.entry_type] ?? e.entry_type}{e.reason_code && e.entry_type !== "RELEASE" ? <span className="text-xs text-ink-muted"> · {e.reason_code}</span> : null}</td>
                    <td className="px-3 py-2 font-mono text-xs">{e.sku_code} @ {e.warehouse_code}</td>
                    <td className={`px-3 py-2 text-right ${e.d_on_hand > 0 ? "text-ok" : e.d_on_hand < 0 ? "text-danger" : "text-ink-muted"}`}>{e.d_on_hand > 0 ? `+${e.d_on_hand}` : e.d_on_hand || "·"}</td>
                    <td className="px-3 py-2 text-right text-ink-soft">{e.d_reserved > 0 ? `+${e.d_reserved}` : e.d_reserved || "·"}</td>
                    <td className="px-3 py-2 text-right font-semibold">{e.on_hand_after}</td>
                    <td className="px-3 py-2 text-xs">{e.reference_id ?? "—"}{e.note ? <div className="text-ink-muted">{e.note}</div> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <Modal open={modal === "receive"} title="Terima stok" onClose={() => setModal(null)}>
        <ReceiveForm whs={whs.filter((w) => w.is_active)} skus={skus.filter((s) => s.is_active)} defaultWh={wh} onDone={() => { setModal(null); load(); }} />
      </Modal>
      <Modal open={modal === "adjust"} title="Penyesuaian stok" onClose={() => setModal(null)}>
        <AdjustForm whs={whs.filter((w) => w.is_active)} skus={skus} defaults={focus} onDone={() => { setModal(null); load(); }} />
      </Modal>
    </>
  );
}

function ReceiveForm({ whs, skus, defaultWh, onDone }: { whs: Warehouse[]; skus: Sku[]; defaultWh: string; onDone: () => void }) {
  const [wh, setWh] = useState(defaultWh || whs[0]?.id || "");
  const [ref, setRef] = useState("");
  const [lines, setLines] = useState([{ sku_id: "", quantity: "" }]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const setLine = (i: number, k: "sku_id" | "quantity", v: string) => setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const valid = lines.filter((l) => l.sku_id && Number(l.quantity) > 0).map((l) => ({ sku_id: l.sku_id, quantity: Number(l.quantity) }));
    if (!valid.length) { setErr("Isi minimal satu SKU dengan jumlah lebih dari 0"); return; }
    setBusy(true);
    try { await api.post("/inventory/receipts", { warehouse_id: wh, reference: ref.trim(), lines: valid }); onDone(); }
    catch (x) { setErr(errorText(x)); } finally { setBusy(false); }
  }
  if (!whs.length) return <p className="text-sm">Buat gudang terlebih dulu di menu Gudang & lokasi.</p>;
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {err && <Alert>{err}</Alert>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Gudang">{(id) => <Select id={id} value={wh} onChange={(e) => setWh(e.target.value)}>{whs.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</Select>}</Field>
        <Field label="Referensi" hint="No. PO / surat jalan">{(id) => <Input id={id} value={ref} onChange={(e) => setRef(e.target.value)} />}</Field>
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-semibold">Barang masuk</legend>
        {lines.map((l, i) => (
          <div key={i} className="grid grid-cols-[1fr_100px_auto] gap-2">
            <div><label htmlFor={`rs-${i}`} className="sr-only">SKU</label>
              <Select id={`rs-${i}`} value={l.sku_id} onChange={(e) => setLine(i, "sku_id", e.target.value)}>
                <option value="">Pilih SKU</option>{skus.map((s) => <option key={s.id} value={s.id}>{s.sku_code}{s.variant_name ? ` — ${s.variant_name}` : ""}</option>)}
              </Select></div>
            <div><label htmlFor={`rq-${i}`} className="sr-only">Jumlah</label><Input id={`rq-${i}`} type="number" min={1} placeholder="Jumlah" value={l.quantity} onChange={(e) => setLine(i, "quantity", e.target.value)} /></div>
            <button type="button" aria-label={`Hapus baris ${i + 1}`} disabled={lines.length === 1} onClick={() => setLines(lines.filter((_, j) => j !== i))} className="min-h-11 min-w-11 rounded text-ink-muted hover:bg-concrete disabled:opacity-30">✕</button>
          </div>
        ))}
        <Button type="button" variant="ghost" className="self-start" onClick={() => setLines([...lines, { sku_id: "", quantity: "" }])}>Tambah baris</Button>
      </fieldset>
      <Button type="submit" loading={busy}>Catat penerimaan</Button>
    </form>
  );
}

function AdjustForm({ whs, skus, defaults, onDone }: { whs: Warehouse[]; skus: Sku[]; defaults: Balance | null; onDone: () => void }) {
  const [reasons, setReasons] = useState<{ code: string; label: string }[]>([]);
  const [f, setF] = useState({ warehouse_id: defaults?.warehouse_id ?? whs[0]?.id ?? "", sku_id: defaults?.sku_id ?? "", reason_code: "COUNT_CORRECTION", delta: "", note: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<{ code: string; label: string }[]>("/inventory/reasons").then(setReasons).catch(() => undefined); }, []);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const signed = f.reason_code === "COUNT_CORRECTION" || f.reason_code === "OTHER";
  const hint = signed ? "Positif menambah, negatif mengurangi stok fisik"
    : f.reason_code === "LOST" ? "Isi angka negatif, mis. -2"
    : f.reason_code === "DAMAGED" ? "Jumlah yang dipindah dari stok baik ke stok rusak"
    : f.reason_code === "WRITE_OFF_DAMAGED" ? "Jumlah stok rusak yang dimusnahkan" : "Jumlah barang yang ditemukan";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await api.post("/inventory/adjustments", { ...f, delta: Number(f.delta), note: f.note.trim() }); onDone(); }
    catch (x) { setErr(errorText(x)); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {err && <Alert>{err}</Alert>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Gudang">{(id) => <Select id={id} value={f.warehouse_id} onChange={set("warehouse_id")}>{whs.map((w) => <option key={w.id} value={w.id}>{w.code}</option>)}</Select>}</Field>
        <Field label="SKU">{(id) => <Select id={id} value={f.sku_id} onChange={set("sku_id")} required><option value="">Pilih SKU</option>{skus.map((s) => <option key={s.id} value={s.id}>{s.sku_code}</option>)}</Select>}</Field>
        <Field label="Alasan">{(id) => <Select id={id} value={f.reason_code} onChange={set("reason_code")}>{reasons.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}</Select>}</Field>
        <Field label="Jumlah" hint={hint}>{(id) => <Input id={id} type="number" value={f.delta} onChange={set("delta")} required />}</Field>
      </div>
      <Field label="Catatan" hint="Wajib — tersimpan permanen di ledger dan audit log">{(id) => <Input id={id} value={f.note} onChange={set("note")} required minLength={3} />}</Field>
      <Button type="submit" loading={busy}>Simpan penyesuaian</Button>
    </form>
  );
}
