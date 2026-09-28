"use client";
import { useCallback, useEffect, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useCan } from "@/components/me-context";
import { Alert, Badge, Button, Empty, Field, Input, PageHeader, Select } from "@/components/ui";
import { UpgradeCard } from "@/components/upgrade";
import { ApiError, api, dt, errorText, rupiah } from "@/lib/api";
import type { AnomalyItem, CourierRec, DemandDetail, StockoutItem, Warehouse } from "@/lib/types";

const RISK: Record<StockoutItem["risk"], { label: string; tone: "off" | "signal" | "ok" | "neutral" }> = {
  habis: { label: "Habis", tone: "off" }, kritis: { label: "Kritis", tone: "off" },
  waspada: { label: "Waspada", tone: "signal" }, aman: { label: "Aman", tone: "ok" },
};
const METHOD: Record<string, string> = {
  pola_mingguan: "pola mingguan", rata_rata: "rata-rata sederhana", belum_ada_penjualan: "belum ada penjualan",
};

export default function AiPage() {
  const canManage = useCan("ai:manage");
  const [locked, setLocked] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [whs, setWhs] = useState<Warehouse[]>([]);
  const [wh, setWh] = useState("");
  const [stock, setStock] = useState<{ counts: Record<string, number>; items: StockoutItem[]; settings: Record<string, number> } | null>(null);
  const [detail, setDetail] = useState<DemandDetail | null>(null);
  const [anoms, setAnoms] = useState<AnomalyItem[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([
        api.get<typeof stock>(`/ai/stockout-risk${wh ? `?warehouse_id=${wh}` : ""}`),
        api.get<AnomalyItem[]>("/ai/anomalies?status=OPEN"),
      ]);
      setStock(s); setAnoms(a); setErr(null);
    } catch (e) { if (e instanceof ApiError && e.status === 402) setLocked(true); else setErr(errorText(e)); }
  }, [wh]);
  useEffect(() => { api.get<Warehouse[]>("/warehouses").then((l) => setWhs(l.filter((w) => w.is_active))).catch(() => undefined); }, []);
  useEffect(() => { load(); }, [load]);

  async function openDetail(it: StockoutItem) {
    try { setDetail(await api.get<DemandDetail>(`/ai/demand?warehouse_id=${it.warehouse_id}&sku_id=${it.sku_id}&horizon=14`)); }
    catch (e) { setErr(errorText(e)); }
  }
  async function decide(a: AnomalyItem, status: "ACK" | "DISMISSED") {
    try { await api.post(`/ai/anomalies/${a.id}/decide`, { status }); load(); } catch (e) { setErr(errorText(e)); }
  }
  async function rebuild() {
    setBusy(true);
    try { await api.post("/ai/rebuild"); await load(); } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  }

  if (locked) return <><PageHeader title="Prediksi & saran" />
    <UpgradeCard title="Prediksi stok habis, saran pesan ulang, rekomendasi kurir & deteksi anomali">Tersedia di paket Enterprise.</UpgradeCard></>;

  const risky = stock?.items.filter((x) => x.risk !== "aman") ?? [];
  const safeSoon = (stock?.items ?? []).filter((x) => x.risk === "aman" && x.days_until_out !== null).slice(0, 10);
  const urgent = risky.length ? risky : safeSoon;   // tetap tampilkan perkiraan terdekat walau semua aman
  const chartData = detail ? [
    ...detail.history.map((h) => ({ label: h.date.slice(5), aktual: h.actual })),
    ...detail.forecast.map((f) => ({ label: f.date.slice(5), ramalan: f.expected, rentang: [f.low, f.high] as [number, number] })),
  ] : [];

  return (
    <>
      <PageHeader title="Prediksi & saran" desc="Dihitung dari penjualan dan pengiriman Anda sendiri — setiap angka bisa ditelusuri."
        action={canManage && <Button variant="secondary" onClick={rebuild} loading={busy}>Hitung ulang sekarang</Button>} />
      {err && <Alert>{err}</Alert>}

      <section className="mb-8" aria-labelledby="stock-title">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div><h2 id="stock-title" className="text-lg font-bold">Stok yang akan habis</h2>
            {stock && <p className="text-sm text-ink-muted">Memakai perkiraan barang datang {stock.settings.lead_time} hari dan target stok cukup {stock.settings.cover_days} hari (ubah di Pengaturan).</p>}</div>
          <div className="flex items-end gap-3">
            {stock && <div className="flex gap-2">{(["habis", "kritis", "waspada"] as const).map((k) => (
              <Badge key={k} tone={RISK[k].tone}>{stock.counts[k] ?? 0} {RISK[k].label.toLowerCase()}</Badge>))}</div>}
            <div className="w-48"><Field label="Gudang">{(id) => <Select id={id} value={wh} onChange={(e) => setWh(e.target.value)}>
              <option value="">Semua</option>{whs.map((w) => <option key={w.id} value={w.id}>{w.code}</option>)}</Select>}</Field></div>
          </div>
        </div>
        {risky.length === 0 && urgent.length > 0 && (
          <p className="mb-2 text-sm text-ok">Semua stok masih aman. Di bawah ini perkiraan untuk SKU yang paling cepat menipis.</p>
        )}
        {urgent.length === 0 ? (
          <Empty title="Tidak ada stok yang berisiko habis">Perkiraan muncul setelah ada riwayat penjualan. Tekan "Hitung ulang sekarang" bila baru saja memasukkan data.</Empty>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
            <table className="w-full min-w-[820px] text-left text-sm tabular-nums">
              <thead className="border-b border-concrete-dark bg-concrete"><tr>
                <th className="px-3 py-2">SKU</th><th className="px-3 py-2">Gudang</th><th className="px-3 py-2 text-right">Tersedia</th>
                <th className="px-3 py-2 text-right">Terjual/hari</th><th className="px-3 py-2 text-right">Habis dalam</th>
                <th className="px-3 py-2 text-right">Titik pesan ulang</th><th className="px-3 py-2 text-right">Saran pesan</th><th className="px-3 py-2" /></tr></thead>
              <tbody>{urgent.map((x) => (
                <tr key={x.warehouse_id + x.sku_id} className="border-b border-concrete-dark last:border-0">
                  <td className="px-3 py-2"><span className="font-mono font-semibold">{x.sku_code}</span> <Badge tone={RISK[x.risk].tone}>{RISK[x.risk].label}</Badge>
                    <div className="text-xs text-ink-muted">{x.product_name}</div></td>
                  <td className="px-3 py-2 font-mono">{x.warehouse}</td>
                  <td className="px-3 py-2 text-right">{x.available}{x.incoming > 0 && <div className="text-xs text-ink-muted">+{x.incoming} datang</div>}</td>
                  <td className="px-3 py-2 text-right">{x.daily_rate}</td>
                  <td className="px-3 py-2 text-right">{x.days_until_out === null ? "—" : x.days_until_out === 0 ? "sudah habis" : `${x.days_until_out} hari`}
                    {x.stockout_date && <div className="text-xs text-ink-muted">{new Date(x.stockout_date).toLocaleDateString("id-ID", { day: "numeric", month: "short" })}</div>}</td>
                  <td className="px-3 py-2 text-right">{x.reorder_point}<div className="text-xs text-ink-muted">aman +{x.safety_stock}</div></td>
                  <td className="px-3 py-2 text-right text-base font-bold">{x.suggested_order || "—"}</td>
                  <td className="px-3 py-2 text-right"><button type="button" onClick={() => openDetail(x)} className="min-h-9 font-semibold hover:underline">Grafik</button></td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
        {detail && (
          <div className="mt-4 rounded-xl border border-concrete-dark bg-white p-4 shadow-card">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div><h3 className="font-bold"><span className="font-mono">{detail.sku_code}</span> · {detail.product_name}</h3>
                <p className="text-sm text-ink-muted">Metode: {METHOD[detail.method] ?? detail.method} dari {detail.history_days} hari data · rata-rata {detail.daily_rate}/hari · terjual 30 hari {detail.sold_30d} · diperbarui {dt(detail.generated_at)}</p></div>
              <Button variant="ghost" onClick={() => setDetail(null)}>Tutup</Button>
            </div>
            <div className="mt-3 h-64" role="img" aria-label={`Grafik penjualan dan ramalan ${detail.sku_code}`}>
              <ResponsiveContainer><ComposedChart data={chartData} margin={{ left: -20, right: 8 }}>
                <CartesianGrid stroke="#E2E7E5" strokeDasharray="3 3" /><XAxis dataKey="label" fontSize={11} interval={4} /><YAxis allowDecimals={false} fontSize={11} /><Tooltip /><Legend />
                <Area type="monotone" dataKey="rentang" name="Rentang perkiraan" stroke="none" fill="#F5C400" fillOpacity={0.25} />
                <Line type="linear" dataKey="aktual" name="Terjual" stroke="#1B2A41" strokeWidth={2} dot={false} />
                <Line type="linear" dataKey="ramalan" name="Ramalan" stroke="#1E7A4C" strokeWidth={2} strokeDasharray="5 3" dot={false} />
              </ComposedChart></ResponsiveContainer>
            </div>
          </div>
        )}
      </section>

      <section className="mb-8" aria-labelledby="anom-title">
        <h2 id="anom-title" className="mb-3 text-lg font-bold">Temuan yang perlu dicek</h2>
        {anoms.length === 0 ? <Empty title="Tidak ada temuan">Sistem terus memeriksa order janggal, lonjakan, channel yang berhenti, dan proses gudang yang melambat.</Empty> : (
          <ul className="flex flex-col gap-2">{anoms.map((a) => (
            <li key={a.id} className="rounded-xl border border-concrete-dark bg-white p-4 shadow-card">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2"><span className="font-semibold">{a.title}</span>
                    <Badge tone={a.severity === "CRITICAL" ? "off" : a.severity === "WARNING" ? "signal" : "neutral"}>{a.kind_label}</Badge></div>
                  <p className="mt-1 text-sm text-ink-soft">{a.detail}</p>
                  <p className="mt-1 text-xs text-ink-muted">{dt(a.created_at)}{a.entity_label ? ` · ${a.entity_label}` : ""}</p>
                </div>
                {canManage && <div className="flex gap-2"><Button variant="secondary" onClick={() => decide(a, "ACK")}>Sudah ditangani</Button>
                  <Button variant="ghost" onClick={() => decide(a, "DISMISSED")}>Abaikan</Button></div>}
              </div>
            </li>))}</ul>)}
      </section>

      <CourierSection />
    </>
  );
}

function CourierSection() {
  const [city, setCity] = useState("");
  const [data, setData] = useState<CourierRec | null>(null);
  const load = useCallback(() => api.get<CourierRec>(`/ai/courier${city ? `?city=${encodeURIComponent(city)}` : ""}`)
    .then(setData).catch(() => undefined), [city]);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [load]);
  return (
    <section aria-labelledby="cour-title">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div><h2 id="cour-title" className="text-lg font-bold">Kurir mana yang paling bisa diandalkan</h2>
          <p className="text-sm text-ink-muted">{data?.note}</p></div>
        <div className="w-56"><Field label="Kota tujuan">{(id) => <Input id={id} value={city} placeholder="Semua kota" onChange={(e) => setCity(e.target.value)} />}</Field></div>
      </div>
      {!data || data.candidates.length === 0 ? <Empty title="Belum cukup data pengiriman" /> : (
        <ul className="grid gap-2 md:grid-cols-2">{data.candidates.map((c, i) => (
          <li key={c.courier_code + c.service_code} className={`rounded-xl border bg-white p-4 shadow-card ${i === 0 ? "border-ok/40" : "border-concrete-dark"}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold uppercase">{c.courier_code} <span className="text-ink-muted">{c.service_code}</span></span>
              {i === 0 ? <Badge tone="ok">Paling disarankan</Badge> : <Badge>skor {c.score}</Badge>}
            </div>
            <p className="mt-1 text-sm text-ink-soft">{c.reason}</p>
            <p className="mt-1 text-xs text-ink-muted">dari {c.n} pengiriman</p>
          </li>))}</ul>)}
    </section>
  );
}
