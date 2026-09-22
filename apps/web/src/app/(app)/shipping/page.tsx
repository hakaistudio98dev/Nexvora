"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useCan } from "@/components/me-context";
import { Alert, Badge, Button, Empty, Field, Input, PageHeader, Select } from "@/components/ui";
import { api, dt, errorText } from "@/lib/api";
import { PROVIDER_LABEL, SHIP_STATUS, shipTone } from "@/lib/shipping";
import type { Courier, CourierAccount, ManifestDoc, OrderSummary, Page, Shipment, Warehouse } from "@/lib/types";
import { saveWarehouse, savedWarehouse } from "@/lib/wms";

const TABS = [["ready", "Siap kirim"], ["manifest", "Serah terima kurir"], ["transit", "Dalam pengiriman"], ["couriers", "Daftar kurir"], ["accounts", "Akun kurir"]] as const;
type Tab = (typeof TABS)[number][0];

export default function ShippingPage() {
  const [tab, setTab] = useState<Tab>("ready");
  const [whs, setWhs] = useState<Warehouse[]>([]);
  const [wh, setWh] = useState("");
  useEffect(() => { api.get<Warehouse[]>("/warehouses").then((l) => { const a = l.filter((w) => w.is_active); setWhs(a); setWh(a.find((w) => w.id === savedWarehouse())?.id ?? a[0]?.id ?? ""); }).catch(() => undefined); }, []);
  return (
    <>
      <PageHeader title="Pengiriman" desc="Resi, serah terima ke kurir, dan pelacakan paket."
        action={<div className="w-60"><label htmlFor="swh" className="mb-1 block text-sm font-semibold">Gudang</label>
          <Select id="swh" value={wh} onChange={(e) => { setWh(e.target.value); saveWarehouse(e.target.value); }}>{whs.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</Select></div>} />
      <div role="tablist" className="mb-5 flex gap-1 overflow-x-auto border-b border-concrete-dark">
        {TABS.map(([k, l]) => <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
          className={`-mb-0.5 min-h-11 shrink-0 border-b-2 px-4 text-sm font-semibold ${tab === k ? "border-signal" : "border-transparent text-ink-soft"}`}>{l}</button>)}
      </div>
      {tab === "ready" && <ReadyTab />}
      {tab === "manifest" && wh && <ManifestTab wh={wh} />}
      {tab === "transit" && <TransitTab wh={wh} />}
      {tab === "couriers" && <CouriersTab />}
      {tab === "accounts" && <AccountsTab />}
    </>
  );
}

function ReadyTab() {
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [ships, setShips] = useState<Shipment[]>([]);
  useEffect(() => {
    Promise.all([api.get<Page<OrderSummary>>("/orders?status=READY_TO_SHIP&limit=200"), api.get<Shipment[]>("/shipping/shipments?status=LABEL_READY")])
      .then(([o, s]) => { setOrders(o.items); setShips(s); }).catch(() => undefined);
  }, []);
  const byOrder = new Map(ships.map((s) => [s.order_id, s]));
  if (!orders.length) return <Empty title="Tidak ada paket menunggu dikirim" />;
  return (
    <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead className="border-b border-concrete-dark bg-concrete"><tr><th className="px-3 py-2">Order</th><th className="px-3 py-2">Tujuan</th><th className="px-3 py-2">Resi</th><th className="px-3 py-2"><span className="sr-only">Aksi</span></th></tr></thead>
        <tbody>{orders.map((o) => { const s = byOrder.get(o.id); return (
          <tr key={o.id} className="border-b border-concrete-dark">
            <td className="px-3 py-2 font-mono font-semibold">{o.order_number}</td>
            <td className="px-3 py-2">{o.customer_name}<div className="text-xs text-ink-muted">{o.ship_city}</div></td>
            <td className="px-3 py-2">{s ? <><span className="font-mono">{s.tracking_number}</span> <span className="text-xs text-ink-muted">{s.courier_name}{s.manifest_number ? ` · ${s.manifest_number}` : ""}</span></> : <Badge tone="signal">Belum ada resi</Badge>}</td>
            <td className="px-3 py-2 text-right">{s ? <a href={`/label/${s.id}`} target="_blank" rel="noopener" className="font-semibold">Cetak label</a> : <Link href="/orders" className="font-semibold">Buat resi</Link>}</td>
          </tr>); })}</tbody>
      </table>
    </div>
  );
}

function ManifestTab({ wh }: { wh: string }) {
  const [list, setList] = useState<ManifestDoc[]>([]);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [courier, setCourier] = useState("jne");
  const [sel, setSel] = useState<ManifestDoc | null>(null);
  const [code, setCode] = useState("");
  const [driver, setDriver] = useState({ name: "", plate: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const canWrite = useCan("shipping:write");
  const load = useCallback(async () => {
    const l = await api.get<ManifestDoc[]>(`/shipping/manifests?warehouse_id=${wh}`);
    setList(l); setSel((cur) => (cur ? l.find((x) => x.id === cur.id) ?? null : l.find((x) => x.status === "OPEN") ?? null));
  }, [wh]);
  useEffect(() => { load().catch(() => undefined); api.get<Courier[]>("/shipping/couriers").then(setCouriers).catch(() => undefined); }, [load]);
  async function create() {
    try { const m = await api.post<ManifestDoc>("/shipping/manifests", { warehouse_id: wh, courier_code: courier }); setSel(m); load(); } catch (e) { setMsg({ ok: false, text: errorText(e) }); }
  }
  async function scan(e: React.FormEvent) {
    e.preventDefault(); if (!sel || !code.trim()) return;
    try { const r = await api.post<{ added: Shipment; count: number }>(`/shipping/manifests/${sel.id}/scan`, { code: code.trim() }); setMsg({ ok: true, text: `${r.added.order_number} masuk (${r.count} paket)` }); setCode(""); load(); }
    catch (x) { setMsg({ ok: false, text: errorText(x) }); setCode(""); }
  }
  async function handover(e: React.FormEvent) {
    e.preventDefault(); if (!sel) return;
    try { const r = await api.post<ManifestDoc & { handed_over: number }>(`/shipping/manifests/${sel.id}/handover`, { driver_name: driver.name, vehicle_plate: driver.plate || null });
      setMsg({ ok: true, text: `${r.handed_over} paket diserahkan ke ${driver.name}. Stok & status order diperbarui.` }); load(); }
    catch (x) { setMsg({ ok: false, text: errorText(x) }); }
  }
  return (
    <div className="grid gap-6 lg:grid-cols-[300px_1fr]">
      <div className="flex flex-col gap-3">
        {canWrite && <div className="flex items-end gap-2 rounded-xl border border-concrete-dark bg-white shadow-card p-3">
          <div className="flex-1"><Field label="Kurir penjemput">{(id) => <Select id={id} value={courier} onChange={(e) => setCourier(e.target.value)}>{couriers.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</Select>}</Field></div>
          <Button onClick={create}>Buat</Button></div>}
        {list.map((m) => (
          <button key={m.id} type="button" onClick={() => setSel(m)} className={`rounded-lg border p-3 text-left ${sel?.id === m.id ? "border-ink/30 bg-signal-soft" : "border-concrete-line bg-white hover:border-ink"}`}>
            <div className="flex justify-between"><span className="font-mono font-semibold">{m.number}</span><span className="text-xs">{m.status === "OPEN" ? "Terbuka" : m.status === "HANDED_OVER" ? "Diserahkan" : "Batal"}</span></div>
            <div className="text-sm text-ink-soft">{m.courier_name} · {m.packages.length} paket</div></button>))}
      </div>
      <div>
        {msg && <div className="mb-3"><p role={msg.ok ? "status" : "alert"} className={`rounded-lg border px-3 py-2 text-sm font-semibold ${msg.ok ? "border-ok bg-green-50 text-ok" : "border-danger bg-red-50 text-danger"}`}>{msg.text}</p></div>}
        {!sel ? <Empty title="Pilih atau buat manifest">Manifest = daftar paket yang diserahkan ke satu kurir saat penjemputan. Setiap paket discan, lalu ditandatangani serah terimanya.</Empty> : (
          <div className="rounded-xl border border-concrete-dark bg-white shadow-card">
            <div className="border-b border-concrete-dark p-4"><span className="font-mono text-lg font-bold">{sel.number}</span> · {sel.courier_name}
              {sel.status === "HANDED_OVER" && <div className="text-sm text-ok">Diserahkan ke {sel.driver_name} {sel.vehicle_plate ? `(${sel.vehicle_plate})` : ""} · {dt(sel.handed_over_at)}</div>}</div>
            {sel.status === "OPEN" && canWrite && (
              <form onSubmit={scan} className="flex gap-2 border-b border-concrete-dark p-4">
                <label htmlFor="mscan" className="sr-only">Scan resi</label>
                <Input id="mscan" autoFocus value={code} onChange={(e) => setCode(e.target.value)} placeholder="Scan resi atau nomor order lalu Enter" className="font-mono" />
                <Button type="submit">Tambah</Button>
              </form>)}
            <ul className="divide-y divide-concrete-dark">{sel.packages.map((p) => (
              <li key={p.id} className="flex items-center justify-between px-4 py-2 text-sm"><span><span className="font-mono font-semibold">{p.tracking_number}</span> · {p.order_number} · {p.customer_name}</span>
                {sel.status === "OPEN" && canWrite && <button type="button" onClick={() => api.post(`/shipping/manifests/${sel.id}/remove/${p.id}`).then(load)} className="min-h-9 px-2 text-danger">Keluarkan</button>}</li>))}</ul>
            {sel.status === "OPEN" && canWrite && sel.packages.length > 0 && (
              <form onSubmit={handover} className="grid gap-3 border-t border-concrete-dark p-4 sm:grid-cols-[1fr_160px_auto] sm:items-end">
                <Field label="Nama kurir penjemput">{(id) => <Input id={id} value={driver.name} onChange={(e) => setDriver({ ...driver, name: e.target.value })} required minLength={2} />}</Field>
                <Field label="Plat kendaraan">{(id) => <Input id={id} value={driver.plate} onChange={(e) => setDriver({ ...driver, plate: e.target.value.toUpperCase() })} />}</Field>
                <Button type="submit">Serahkan {sel.packages.length} paket</Button>
              </form>)}
          </div>
        )}
      </div>
    </div>
  );
}

function TransitTab({ wh }: { wh: string }) {
  const [rows, setRows] = useState<Shipment[]>([]);
  const [q, setQ] = useState("");
  const load = useCallback(() => api.get<Shipment[]>(`/shipping/shipments?status=${q ? "" : "ACTIVE"}${q ? `&q=${encodeURIComponent(q)}` : ""}${wh ? `&warehouse_id=${wh}` : ""}`).then(setRows).catch(() => undefined), [wh, q]);
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [load]);
  return (
    <div className="flex flex-col gap-3">
      <div className="max-w-sm"><label htmlFor="tq" className="sr-only">Cari resi</label><Input id="tq" placeholder="Cari resi atau nomor order" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      {rows.length === 0 ? <Empty title="Tidak ada paket dalam perjalanan" /> : (
        <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
          <table className="w-full min-w-[700px] text-left text-sm">
            <thead className="border-b border-concrete-dark bg-concrete"><tr><th className="px-3 py-2">Resi</th><th className="px-3 py-2">Order</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Diserahkan</th><th className="px-3 py-2">Update terakhir</th></tr></thead>
            <tbody>{rows.map((s) => (
              <tr key={s.id} className="border-b border-concrete-dark">
                <td className="px-3 py-2"><div className="font-mono font-semibold">{s.tracking_url ? <a href={s.tracking_url} target="_blank" rel="noopener noreferrer">{s.tracking_number}</a> : s.tracking_number}</div><div className="text-xs text-ink-muted">{s.courier_name}</div></td>
                <td className="px-3 py-2">{s.order_number}<div className="text-xs text-ink-muted">{s.customer_name} · {s.ship_city}</div></td>
                <td className="px-3 py-2"><Badge tone={shipTone(s.status)}>{SHIP_STATUS[s.status]}</Badge></td>
                <td className="px-3 py-2 text-xs">{dt(s.handed_over_at)}</td>
                <td className="px-3 py-2 text-xs">{dt(s.last_tracked_at)}</td></tr>))}</tbody>
          </table>
        </div>)}
    </div>
  );
}

function AccountsTab() {
  const canManage = useCan("shipping:manage");
  const [rows, setRows] = useState<CourierAccount[]>([]);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [f, setF] = useState({ name: "", provider: "simulator", api_key: "" });
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api.get<CourierAccount[]>("/shipping/accounts").then(setRows).catch((e) => setErr(errorText(e))), []);
  useEffect(() => { load(); api.get<Courier[]>("/shipping/couriers").then(setCouriers).catch(() => undefined); }, [load]);
  async function create(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try { await api.post("/shipping/accounts", { name: f.name, provider: f.provider, api_key: f.api_key || null }); setF({ name: "", provider: "simulator", api_key: "" }); load(); } catch (x) { setErr(errorText(x)); }
  }
  async function patch(id: string, body: object) { try { await api.patch(`/shipping/accounts/${id}`, body); load(); } catch (x) { setErr(errorText(x)); } }
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <div className="flex flex-col gap-3">
        {err && <Alert>{err}</Alert>}
        {rows.map((a) => (
          <div key={a.id} className="rounded-xl border border-concrete-dark bg-white shadow-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div><span className="font-semibold">{a.name}</span> <Badge>{PROVIDER_LABEL[a.provider]}</Badge> {a.is_default && <Badge tone="signal">Default</Badge>} {!a.is_active && <Badge tone="off">Nonaktif</Badge>}</div>
              {canManage && <div className="flex gap-2">{!a.is_default && <Button variant="ghost" onClick={() => patch(a.id, { is_default: true })}>Jadikan default</Button>}
                <Button variant="ghost" onClick={() => patch(a.id, { is_active: !a.is_active })}>{a.is_active ? "Nonaktifkan" : "Aktifkan"}</Button></div>}
            </div>
            <p className="mt-1 text-xs text-ink-muted">{a.couriers.map((c) => couriers.find((x) => x.code === c)?.name ?? c).join(", ")}</p>
            {a.webhook_path && <p className="mt-2 text-xs">Webhook (isi di dashboard Biteship): <code className="break-all font-mono">{origin}{a.webhook_path}</code></p>}
          </div>))}
      </div>
      {canManage && (
        <form onSubmit={create} className="flex h-fit flex-col gap-3 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
          <h2 className="text-lg font-bold">Tambah akun kurir</h2>
          <Field label="Nama">{(id) => <Input id={id} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required minLength={2} />}</Field>
          <Field label="Jenis">{(id) => <Select id={id} value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })}>
            {Object.entries(PROVIDER_LABEL).filter(([k]) => k !== "manual").map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select>}</Field>
          {f.provider === "biteship" && <Field label="API key Biteship" hint="Disimpan terenkripsi">{(id) => <Input id={id} type="password" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} required />}</Field>}
          {f.provider === "simulator" && <p className="text-xs text-ink-muted">Resi & tracking palsu yang bergerak otomatis — hanya untuk mencoba alur. Jangan dipakai untuk paket sungguhan.</p>}
          <Button type="submit">Simpan</Button>
          <p className="text-xs text-ink-muted">Akun "Manual (input resi)" selalu tersedia untuk resi dari marketplace atau drop-off.</p>
        </form>
      )}
    </div>
  );
}

function CouriersTab() {
  const canManage = useCan("shipping:manage");
  const [rows, setRows] = useState<Courier[]>([]);
  const [f, setF] = useState({ name: "", code: "", services: "", tracking_url_template: "", phone: "" });
  const [codeTouched, setCodeTouched] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api.get<Courier[]>("/shipping/couriers?include_inactive=true").then(setRows).catch((e) => setErr(errorText(e))), []);
  useEffect(() => { load(); }, [load]);
  const slug = (v: string) => v.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 30);
  async function add(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try {
      await api.post("/shipping/couriers", { name: f.name.trim(), code: f.code, phone: f.phone.trim(),
        services: f.services.split(",").map((x) => x.trim()).filter(Boolean), tracking_url_template: f.tracking_url_template.trim() || null });
      setF({ name: "", code: "", services: "", tracking_url_template: "", phone: "" }); setCodeTouched(false); load();
    } catch (x) { setErr(errorText(x)); }
  }
  async function toggle(c: Courier) { try { await api.patch(`/shipping/couriers/${c.code}`, { is_active: !c.is_active }); load(); } catch (x) { setErr(errorText(x)); } }
  const custom = rows.filter((c) => c.custom);
  const builtin = rows.filter((c) => !c.custom);
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
      <div className="flex flex-col gap-4">
        {err && <Alert>{err}</Alert>}
        <section>
          <h2 className="mb-2 text-lg font-bold">Kurir tambahan Anda</h2>
          {custom.length === 0 ? <p className="text-sm text-ink-muted">Belum ada. Tambahkan kurir lokal atau kurir yang belum ada di daftar bawaan.</p> : (
            <ul className="flex flex-col gap-2">{custom.map((c) => (
              <li key={c.code} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-concrete-dark bg-white shadow-card p-3">
                <div><span className="font-semibold">{c.name}</span> <span className="font-mono text-xs text-ink-muted">{c.code}</span> {!c.is_active && <Badge tone="off">Nonaktif</Badge>}
                  <div className="text-xs text-ink-soft">{c.services.map((x) => x.name).join(", ")}{c.phone ? ` · ${c.phone}` : ""}</div>
                  {c.tracking_url_template && <div className="break-all font-mono text-xs text-ink-muted">{c.tracking_url_template}</div>}</div>
                {canManage && <Button variant="ghost" onClick={() => toggle(c)}>{c.is_active ? "Nonaktifkan" : "Aktifkan"}</Button>}
              </li>))}</ul>)}
        </section>
        <section>
          <h2 className="mb-2 text-lg font-bold">Kurir bawaan</h2>
          <div className="flex flex-wrap gap-2">{builtin.map((c) => <Badge key={c.code} tone={c.auto_booking ? "neutral" : "off"}>{c.name}</Badge>)}</div>
          <p className="mt-2 text-xs text-ink-muted">Semua kurir bisa dipakai lewat akun Manual (input resi). Pemesanan otomatis lewat Biteship hanya untuk kurir yang didukung Biteship.</p>
        </section>
      </div>
      {canManage && (
        <form onSubmit={add} className="flex h-fit flex-col gap-3 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
          <h2 className="text-lg font-bold">Tambah kurir lain</h2>
          <Field label="Nama kurir">{(id) => <Input id={id} value={f.name} required minLength={2} onChange={(e) => setF({ ...f, name: e.target.value, code: codeTouched ? f.code : slug(e.target.value) })} />}</Field>
          <Field label="Kode" hint="Huruf kecil, angka, garis bawah">{(id) => <Input id={id} value={f.code} required pattern="[a-z0-9][a-z0-9_]{1,29}" onChange={(e) => { setCodeTouched(true); setF({ ...f, code: slug(e.target.value) }); }} />}</Field>
          <Field label="Layanan" hint="Pisahkan dengan koma, mis. Reguler, Same day">{(id) => <Input id={id} value={f.services} onChange={(e) => setF({ ...f, services: e.target.value })} />}</Field>
          <Field label="Link lacak (opsional)" hint="Tulis {resi} di posisi nomor resi">{(id) => <Input id={id} value={f.tracking_url_template} placeholder="https://kurir.id/lacak?no={resi}" onChange={(e) => setF({ ...f, tracking_url_template: e.target.value })} />}</Field>
          <Field label="Kontak kurir (opsional)">{(id) => <Input id={id} value={f.phone} inputMode="tel" onChange={(e) => setF({ ...f, phone: e.target.value })} />}</Field>
          <Button type="submit">Simpan kurir</Button>
        </form>
      )}
    </div>
  );
}
