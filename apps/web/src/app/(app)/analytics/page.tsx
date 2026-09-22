"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useMe } from "@/components/me-context";
import { Alert, Badge, Field, Input, PageHeader, Select } from "@/components/ui";
import { UpgradeCard } from "@/components/upgrade";
import { ApiError, api, dt, errorText, qs, rupiah } from "@/lib/api";
import { CHANNELS, STATUS_LABEL } from "@/lib/labels";
import { hasFeature } from "@/lib/saas";
import type { HealthItem, Overview, ProductivityRow, SeriesPoint, SlaBoard, Warehouse } from "@/lib/types";

const PRESETS = [7, 30, 90] as const;
const iso = (d: Date) => d.toISOString().slice(0, 10);

export default function AnalyticsPage() {
  const me = useMe();
  const advanced = hasFeature(me.subscription, "analytics");
  const [days, setDays] = useState<number | "custom">(30);
  const [from, setFrom] = useState(iso(new Date(Date.now() - 29 * 864e5)));
  const [to, setTo] = useState(iso(new Date()));
  const [wh, setWh] = useState("");
  const [channel, setChannel] = useState("");
  const [whs, setWhs] = useState<Warehouse[]>([]);
  const [ov, setOv] = useState<Overview | null>(null);
  const [series, setSeries] = useState<SeriesPoint[]>([]);
  const [sla, setSla] = useState<SlaBoard | null>(null);
  const [prod, setProd] = useState<ProductivityRow[]>([]);
  const [top, setTop] = useState<{ sku_code: string; product_name: string; units: number; revenue: string; orders: number }[]>([]);
  const [health, setHealth] = useState<{ counts: Record<string, number>; items: HealthItem[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.get<Warehouse[]>("/warehouses").then(setWhs).catch(() => undefined); }, []);
  useEffect(() => {
    if (days === "custom") return;
    setFrom(iso(new Date(Date.now() - (days - 1) * 864e5))); setTo(iso(new Date()));
  }, [days]);
  const filt = useMemo(() => qs({ date_from: from, date_to: to, warehouse_id: wh, channel }), [from, to, wh, channel]);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [o, s] = await Promise.all([api.get<Overview>(`/analytics/overview${filt}`), api.get<SeriesPoint[]>(`/analytics/timeseries${filt}`)]);
      setOv(o); setSeries(s);
      if (advanced) {
        const [b, pr, t, hl] = await Promise.all([api.get<SlaBoard>(`/analytics/sla${filt}`), api.get<ProductivityRow[]>(`/analytics/productivity${filt}`),
          api.get<typeof top>(`/analytics/top-skus${filt}`), api.get<typeof health>(`/analytics/inventory-health${qs({ warehouse_id: wh })}`)]);
        setSla(b); setProd(pr); setTop(t); setHealth(hl);
      }
    } catch (e) { if (!(e instanceof ApiError && e.status === 402)) setErr(errorText(e)); }
  }, [filt, advanced, wh]);
  useEffect(() => { load(); }, [load]);

  const chart = series.map((p) => ({ ...p, label: new Date(p.date).toLocaleDateString("id-ID", { day: "numeric", month: "short" }) }));
  const pct = (v: number | null) => (v == null ? "—" : `${v.toLocaleString("id-ID")}%`);
  const kpis: { label: string; value: string; hint: string; good?: boolean | null }[] = ov ? [
    { label: "Order terkirim", value: pct(ov.kpi.fulfillment_rate), hint: `${ov.orders.shipped} dari ${ov.orders.total - ov.orders.cancelled} order aktif sudah dikirim` },
    { label: "Dikirim tepat waktu", value: pct(ov.kpi.sla_compliance), hint: `dikirim ≤ ${ov.range.sla_ship_hours} jam setelah dibayar (${ov.samples.sla_orders} order)`, good: ov.kpi.sla_compliance == null ? null : ov.kpi.sla_compliance >= 95 },
    { label: "Rata-rata waktu kirim", value: ov.kpi.avg_hours_to_ship == null ? "—" : `${ov.kpi.avg_hours_to_ship} jam`, hint: `P90 ${ov.kpi.p90_hours_to_ship ?? "—"} jam` },
    { label: "Akurasi picking", value: pct(ov.kpi.pick_accuracy), hint: `dari ${ov.samples.pick_tasks} tugas ambil barang`, good: ov.kpi.pick_accuracy == null ? null : ov.kpi.pick_accuracy >= 99 },
    { label: "Akurasi packing", value: pct(ov.kpi.pack_accuracy), hint: `${ov.samples.packages} paket tanpa selisih berat` },
    { label: "Akurasi stok", value: pct(ov.kpi.inventory_accuracy), hint: `dari ${ov.samples.count_lines} baris hitung stok`, good: ov.kpi.inventory_accuracy == null ? null : ov.kpi.inventory_accuracy >= 99 },
    { label: "Order bermasalah", value: pct(ov.kpi.exception_rate), hint: "ada masalah saat diproses di gudang" },
    { label: "Tingkat retur", value: pct(ov.kpi.return_rate), hint: `dari ${ov.samples.shipped_in_range} order dikirim` },
    { label: "Ongkir rata-rata", value: ov.kpi.cost_per_order ? rupiah(ov.kpi.cost_per_order) : "—", hint: `${ov.samples.shipments_with_cost} paket berongkir` },
    { label: "Pendapatan", value: rupiah(ov.orders.revenue), hint: ov.orders.aov ? `rata-rata ${rupiah(ov.orders.aov)} / order` : "" },
  ] : [];

  return (
    <>
      <PageHeader title="Analitik" desc="Kinerja penjualan, pengiriman, dan gudang Anda — dihitung langsung dari data terbaru." />
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <div role="group" aria-label="Rentang" className="flex gap-1">{PRESETS.map((d) => (
          <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)} className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${days === d ? "border-ink bg-ink text-white" : "border-concrete-line bg-white"}`}>{d} hari</button>))}</div>
        <div className="w-40"><Field label="Dari">{(id) => <Input id={id} type="date" value={from} onChange={(e) => { setDays("custom"); setFrom(e.target.value); }} />}</Field></div>
        <div className="w-40"><Field label="Sampai">{(id) => <Input id={id} type="date" value={to} onChange={(e) => { setDays("custom"); setTo(e.target.value); }} />}</Field></div>
        <div className="w-48"><Field label="Gudang">{(id) => <Select id={id} value={wh} onChange={(e) => setWh(e.target.value)}><option value="">Semua</option>{whs.map((w) => <option key={w.id} value={w.id}>{w.code}</option>)}</Select>}</Field></div>
        <div className="w-40"><Field label="Channel">{(id) => <Select id={id} value={channel} onChange={(e) => setChannel(e.target.value)}><option value="">Semua</option>{CHANNELS.map((c) => <option key={c}>{c}</option>)}</Select>}</Field></div>
      </div>
      {err && <Alert>{err}</Alert>}

      {ov && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {kpis.map((k) => (
            <div key={k.label} className={`rounded-xl border border-concrete-dark p-4 ${k.good === false ? "bg-red-50" : "bg-white"}`}>
              <div className="text-xs font-semibold text-ink-soft">{k.label}</div>
              <div className="text-2xl font-bold tabular-nums">{k.value}</div>
              <div className="mt-1 text-xs text-ink-muted">{k.hint}</div>
            </div>))}
        </div>
      )}

      <section className="mt-6 grid gap-4 lg:grid-cols-[2fr_1fr]">
        <div className="rounded-xl border border-concrete-dark bg-white shadow-card p-4">
          <h2 className="mb-2 text-lg font-bold">Order masuk vs dikirim</h2>
          <div className="h-64" role="img" aria-label="Grafik order masuk, dikirim, dan tepat waktu per hari">
            <ResponsiveContainer><LineChart data={chart} margin={{ left: -20, right: 8 }}>
              <CartesianGrid stroke="#D5DAD8" strokeDasharray="3 3" /><XAxis dataKey="label" fontSize={11} /><YAxis allowDecimals={false} fontSize={11} /><Tooltip /><Legend />
              <Line type="linear" dataKey="placed" name="Masuk" stroke="#1B2A41" strokeWidth={2} dot={false} />
              <Line type="linear" dataKey="shipped" name="Dikirim" stroke="#1F5BD8" strokeWidth={2} dot={false} />
              <Line type="linear" dataKey="on_time" name="Tepat SLA" stroke="#1E6B42" strokeWidth={2} strokeDasharray="4 3" dot={false} />
            </LineChart></ResponsiveContainer>
          </div>
        </div>
        <div className="rounded-xl border border-concrete-dark bg-white shadow-card p-4">
          <h2 className="mb-2 text-lg font-bold">Per channel</h2>
          <div className="h-64" role="img" aria-label="Grafik jumlah order per channel">
            <ResponsiveContainer><BarChart data={ov?.by_channel ?? []} layout="vertical" margin={{ left: 10 }}>
              <XAxis type="number" allowDecimals={false} fontSize={11} /><YAxis type="category" dataKey="channel" width={80} fontSize={11} /><Tooltip />
              <Bar dataKey="orders" name="Order" fill="#1B2A41" radius={[0, 4, 4, 0]} barSize={22} />
            </BarChart></ResponsiveContainer>
          </div>
        </div>
      </section>

      {!advanced ? (
        <div className="mt-6"><UpgradeCard title="Papan SLA, produktivitas tim, SKU terlaris & saran pesan ulang">Analitik lanjutan tersedia mulai paket Growth. KPI utama dan ekspor CSV tetap bisa dipakai.</UpgradeCard></div>
      ) : (
        <>
          {sla && (
            <section className="mt-6 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-lg font-bold">Papan SLA saat ini <span className="text-sm font-normal text-ink-soft">(batas {sla.sla_ship_hours} jam dari dibayar)</span></h2>
                <div className="flex gap-2 text-sm"><Badge tone="off">{sla.counts.overdue} terlambat</Badge><Badge tone="signal">{sla.counts.at_risk} berisiko</Badge><Badge tone="ok">{sla.counts.on_track} aman</Badge></div>
              </div>
              {sla.orders.length === 0 ? <p className="mt-3 text-sm text-ok">Semua order dalam proses masih aman.</p> : (
                <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm">
                  <thead className="text-ink-muted"><tr><th className="py-1">Order</th><th>Tahap</th><th>Gudang</th><th>Batas</th><th className="text-right">Sisa waktu</th></tr></thead>
                  <tbody>{sla.orders.map((o) => (
                    <tr key={o.order_id} className="border-t border-concrete-dark"><td className="py-1.5 font-mono font-semibold">{o.order_number}</td>
                      <td>{STATUS_LABEL[o.status as keyof typeof STATUS_LABEL] ?? o.status}</td><td>{o.warehouse ?? "—"}</td><td className="text-xs">{dt(o.due_at)}</td>
                      <td className={`text-right font-bold tabular-nums ${o.state === "overdue" ? "text-danger" : ""}`}>{o.hours_left < 0 ? `terlambat ${Math.abs(o.hours_left)} jam` : `${o.hours_left} jam`}</td></tr>))}</tbody>
                </table></div>)}
            </section>
          )}
          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border border-concrete-dark bg-white shadow-card p-4">
              <h2 className="mb-2 text-lg font-bold">Produktivitas tim</h2>
              {prod.length === 0 ? <p className="text-sm text-ink-muted">Belum ada aktivitas gudang di rentang ini.</p> : (
                <div className="overflow-x-auto"><table className="w-full text-left text-sm tabular-nums">
                  <thead className="text-ink-muted"><tr><th className="py-1">Nama</th><th className="text-right">Unit dipick</th><th className="text-right">Akurasi</th><th className="text-right">Paket</th><th className="text-right">Putaway</th><th className="text-right">Hitung</th></tr></thead>
                  <tbody>{prod.map((r) => <tr key={r.user_id} className="border-t border-concrete-dark"><td className="py-1.5">{r.name}</td><td className="text-right">{r.units_picked}</td>
                    <td className={`text-right ${r.pick_accuracy != null && r.pick_accuracy < 99 ? "font-bold text-danger" : ""}`}>{pct(r.pick_accuracy)}</td><td className="text-right">{r.packages}</td><td className="text-right">{r.units_putaway}</td><td className="text-right">{r.count_lines}</td></tr>)}</tbody>
                </table></div>)}
            </section>
            <section className="rounded-xl border border-concrete-dark bg-white shadow-card p-4">
              <h2 className="mb-2 text-lg font-bold">SKU terlaris</h2>
              {top.length === 0 ? <p className="text-sm text-ink-muted">Belum ada penjualan.</p> : (
                <table className="w-full text-left text-sm tabular-nums"><thead className="text-ink-muted"><tr><th className="py-1">SKU</th><th className="text-right">Unit</th><th className="text-right">Order</th><th className="text-right">Pendapatan</th></tr></thead>
                  <tbody>{top.slice(0, 10).map((t) => <tr key={t.sku_code} className="border-t border-concrete-dark"><td className="py-1.5"><span className="font-mono font-semibold">{t.sku_code}</span> <span className="text-xs text-ink-muted">{t.product_name}</span></td>
                    <td className="text-right">{t.units}</td><td className="text-right">{t.orders}</td><td className="text-right">{rupiah(t.revenue)}</td></tr>)}</tbody></table>)}
            </section>
          </div>
          {health && (
            <section className="mt-6 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-bold">Kesehatan stok & saran pesan ulang</h2>
                <div className="flex gap-2 text-sm"><Badge tone="off">{health.counts.out} habis</Badge><Badge tone="signal">{health.counts.low} menipis</Badge><Badge>{health.counts.slow} tidak laku 30 hari</Badge></div></div>
              <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm tabular-nums">
                <thead className="text-ink-muted"><tr><th className="py-1">SKU</th><th>Gudang</th><th className="text-right">Tersedia</th><th className="text-right">Batas</th><th className="text-right">Terjual 30 hr</th><th className="text-right">Cukup untuk</th><th className="text-right">Saran pesan</th></tr></thead>
                <tbody>{health.items.filter((x) => x.state !== "ok").slice(0, 50).map((x) => (
                  <tr key={x.warehouse + x.sku_code} className="border-t border-concrete-dark"><td className="py-1.5"><span className="font-mono font-semibold">{x.sku_code}</span> {x.state === "out" ? <Badge tone="off">habis</Badge> : x.state === "low" ? <Badge tone="signal">menipis</Badge> : <Badge>lambat</Badge>}</td>
                    <td className="font-mono">{x.warehouse}</td><td className="text-right">{x.available}</td><td className="text-right">{x.threshold}</td><td className="text-right">{x.sold_30d}</td>
                    <td className="text-right">{x.days_of_cover == null ? "—" : `${x.days_of_cover} hari`}</td><td className="text-right font-bold">{x.suggested_reorder || "—"}</td></tr>))}</tbody>
              </table></div>
              <p className="mt-2 text-xs text-ink-muted">Saran pesan ulang = kebutuhan 30 hari (rata-rata penjualan harian × 30) dikurangi stok tersedia.</p>
            </section>
          )}
        </>
      )}
      <Exports filt={filt} />
    </>
  );
}

function Exports({ filt }: { filt: string }) {
  const [list, setList] = useState<{ name: string; label: string; allowed: boolean }[]>([]);
  const [semi, setSemi] = useState(false);
  useEffect(() => { api.get<typeof list>("/reports").then(setList).catch(() => undefined); }, []);
  const suffix = filt ? `${filt}&sep=${semi ? "semicolon" : "comma"}` : `?sep=${semi ? "semicolon" : "comma"}`;
  return (
    <section className="mt-6 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-bold">Ekspor CSV</h2>
        <label className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-ink" checked={semi} onChange={(e) => setSemi(e.target.checked)} />Pemisah titik koma (Excel Indonesia)</label></div>
      <div className="mt-3 flex flex-wrap gap-2">{list.filter((r) => r.allowed).map((r) => (
        <a key={r.name} href={`/api/v1/reports/${r.name}.csv${suffix}`} className="inline-flex min-h-10 items-center rounded-md border border-ink px-3 text-sm font-semibold hover:bg-concrete">{r.label}</a>))}</div>
      <p className="mt-2 text-xs text-ink-muted">Mengikuti filter tanggal, gudang, dan channel di atas. Maksimal 100.000 baris per file.</p>
    </section>
  );
}
