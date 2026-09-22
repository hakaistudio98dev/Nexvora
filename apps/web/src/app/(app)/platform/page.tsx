"use client";
import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Field, Input, PageHeader, Select } from "@/components/ui";
import { api, dt, errorText, rupiah } from "@/lib/api";
import { FEATURE_LABEL, LIMIT_LABEL, STATUS_LABEL } from "@/lib/saas";
import type { Invoice, Plan } from "@/lib/types";

type SubRow = { tenant_id: string; slug: string; name: string; plan_code: string; status: string; billing_cycle: string;
  trial_ends_at: string | null; current_period_end: string | null; grace_until: string | null; usage: Record<string, number> };

export default function PlatformPage() {
  const [tab, setTab] = useState<"tenants" | "invoices" | "plans" | "health">("tenants");
  return (
    <>
      <PageHeader title="Admin platform" desc="Kelola langganan semua tenant, konfirmasi pembayaran, dan atur paket." />
      <div role="tablist" className="mb-5 flex gap-1 border-b border-concrete-dark">
        {([["tenants", "Tenant & langganan"], ["invoices", "Konfirmasi pembayaran"], ["plans", "Paket & harga"], ["health", "Kesehatan sistem"]] as const).map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={`-mb-0.5 min-h-11 border-b-2 px-4 text-sm font-semibold ${tab === k ? "border-signal" : "border-transparent text-ink-soft"}`}>{l}</button>))}
      </div>
      {tab === "tenants" && <Tenants />}
      {tab === "invoices" && <Invoices />}
      {tab === "plans" && <Plans />}
      {tab === "health" && <Health />}
    </>
  );
}

function Tenants() {
  const [rows, setRows] = useState<SubRow[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [edit, setEdit] = useState<SubRow | null>(null);
  const [f, setF] = useState({ plan_code: "", status: "", current_period_end: "", note: "" });
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => Promise.all([api.get<SubRow[]>("/platform/subscriptions"), api.get<Plan[]>("/platform/plans")])
    .then(([r, p]) => { setRows(r); setPlans(p); }).catch((e) => setErr(errorText(e))), []);
  useEffect(() => { load(); }, [load]);
  async function save(e: React.FormEvent) {
    e.preventDefault(); if (!edit) return;
    try {
      await api.patch(`/platform/tenants/${edit.tenant_id}/subscription`, { plan_code: f.plan_code || null, status: f.status || null,
        current_period_end: f.current_period_end ? new Date(f.current_period_end).toISOString() : null, note: f.note });
      setEdit(null); load();
    } catch (x) { setErr(errorText(x)); }
  }
  return (
    <div className="flex flex-col gap-4">
      {err && <Alert>{err}</Alert>}
      <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
        <table className="w-full min-w-[820px] text-left text-sm tabular-nums">
          <thead className="border-b border-concrete-dark bg-concrete"><tr><th className="px-3 py-2">Tenant</th><th className="px-3 py-2">Paket</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Berakhir</th><th className="px-3 py-2">Pemakaian</th><th className="px-3 py-2"><span className="sr-only">Aksi</span></th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.tenant_id} className="border-b border-concrete-dark">
              <td className="px-3 py-2"><div className="font-semibold">{r.name}</div><div className="font-mono text-xs text-ink-muted">{r.slug}</div></td>
              <td className="px-3 py-2">{r.plan_code} · {r.billing_cycle === "YEARLY" ? "thn" : "bln"}</td>
              <td className="px-3 py-2"><Badge tone={r.status === "ACTIVE" ? "ok" : r.status === "TRIALING" ? "signal" : "off"}>{STATUS_LABEL[r.status] ?? r.status}</Badge></td>
              <td className="px-3 py-2 text-xs">{dt(r.status === "TRIALING" ? r.trial_ends_at : r.status === "PAST_DUE" ? r.grace_until : r.current_period_end)}</td>
              <td className="px-3 py-2 text-xs">{r.usage.orders_per_month} order/bln · {r.usage.users} user · {r.usage.warehouses} gudang</td>
              <td className="px-3 py-2 text-right"><Button variant="ghost" onClick={() => { setEdit(r); setF({ plan_code: r.plan_code, status: r.status, current_period_end: "", note: "" }); }}>Ubah</Button></td>
            </tr>))}</tbody>
        </table>
      </div>
      {edit && (
        <form onSubmit={save} className="grid gap-3 rounded-xl border border-concrete-dark bg-white shadow-card p-4 sm:grid-cols-2">
          <h2 className="text-lg font-bold sm:col-span-2">Ubah langganan {edit.name}</h2>
          <Field label="Paket">{(id) => <Select id={id} value={f.plan_code} onChange={(e) => setF({ ...f, plan_code: e.target.value })}>{plans.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}</Select>}</Field>
          <Field label="Status">{(id) => <Select id={id} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{Object.keys(STATUS_LABEL).filter((x) => x !== "NONE").map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}</Select>}</Field>
          <Field label="Aktif sampai (opsional)" hint="Untuk kontrak Enterprise / perpanjangan manual">{(id) => <Input id={id} type="date" value={f.current_period_end} onChange={(e) => setF({ ...f, current_period_end: e.target.value })} />}</Field>
          <Field label="Catatan (wajib, masuk audit log)">{(id) => <Input id={id} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} required minLength={3} />}</Field>
          <div className="flex gap-2 sm:col-span-2"><Button type="submit">Simpan</Button><Button type="button" variant="ghost" onClick={() => setEdit(null)}>Batal</Button></div>
        </form>
      )}
    </div>
  );
}

function Invoices() {
  const [rows, setRows] = useState<Invoice[]>([]);
  const [ref, setRef] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api.get<Invoice[]>("/platform/invoices?status=OPEN").then(setRows).catch((e) => setErr(errorText(e))), []);
  useEffect(() => { load(); }, [load]);
  async function markPaid(i: Invoice) {
    try { await api.post(`/platform/invoices/${i.id}/mark-paid`, { payment_ref: ref[i.id] ?? "" }); load(); } catch (e) { setErr(errorText(e)); }
  }
  return (
    <div className="flex flex-col gap-3">
      {err && <Alert>{err}</Alert>}
      <p className="text-sm text-ink-soft">Untuk pembayaran transfer manual: cocokkan mutasi rekening dengan nomor invoice, isi referensinya, lalu konfirmasi. Langganan tenant langsung aktif.</p>
      {rows.length === 0 ? <p className="text-sm text-ink-muted">Tidak ada tagihan terbuka.</p> : rows.map((i) => (
        <div key={i.id} className="flex flex-wrap items-end gap-3 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
          <div className="flex-1"><div className="font-mono font-bold">{i.number}</div><div className="text-sm">{i.plan_code} · {i.billing_cycle} · {rupiah(i.amount)} · jatuh tempo {dt(i.due_at)}</div></div>
          <div className="w-56"><Field label="Ref. transfer">{(id) => <Input id={id} value={ref[i.id] ?? ""} onChange={(e) => setRef({ ...ref, [i.id]: e.target.value })} />}</Field></div>
          <Button disabled={(ref[i.id] ?? "").trim().length < 3} onClick={() => markPaid(i)}>Tandai lunas</Button>
        </div>
      ))}
    </div>
  );
}

function Plans() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => { api.get<Plan[]>("/platform/plans").then(setPlans).catch((e) => setErr(errorText(e))); }, []);
  const upd = (code: string, patch: Partial<Plan>) => setPlans(plans.map((p) => (p.code === code ? { ...p, ...patch } : p)));
  async function save(p: Plan) {
    try {
      await api.patch(`/platform/plans/${p.code}`, { name: p.name, description: p.description, price_monthly: p.price_monthly,
        price_yearly: p.price_yearly, features: p.features, limits: p.limits });
      setSaved(p.code); setErr(null);
    } catch (e) { setErr(errorText(e)); }
  }
  return (
    <div className="flex flex-col gap-4">
      {err && <Alert>{err}</Alert>}
      <div className="grid gap-4 lg:grid-cols-3">
        {plans.map((p) => (
          <div key={p.code} className="flex flex-col gap-3 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
            <Field label="Nama">{(id) => <Input id={id} value={p.name} onChange={(e) => upd(p.code, { name: e.target.value })} />}</Field>
            <Field label="Deskripsi">{(id) => <Input id={id} value={p.description} onChange={(e) => upd(p.code, { description: e.target.value })} />}</Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Harga/bulan">{(id) => <Input id={id} type="number" min={0} value={p.price_monthly} onChange={(e) => upd(p.code, { price_monthly: e.target.value })} />}</Field>
              <Field label="Harga/tahun">{(id) => <Input id={id} type="number" min={0} value={p.price_yearly} onChange={(e) => upd(p.code, { price_yearly: e.target.value })} />}</Field>
            </div>
            <fieldset><legend className="mb-1 text-sm font-semibold">Fitur</legend>
              {Object.entries(FEATURE_LABEL).map(([k, l]) => (
                <label key={k} className="flex min-h-9 items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-ink" checked={p.features.includes(k)}
                  onChange={(e) => upd(p.code, { features: e.target.checked ? [...p.features, k] : p.features.filter((x) => x !== k) })} />{l}</label>))}
            </fieldset>
            <fieldset className="grid grid-cols-2 gap-2"><legend className="mb-1 text-sm font-semibold">Batas (kosong = tanpa batas)</legend>
              {Object.entries(LIMIT_LABEL).map(([k, l]) => (
                <Field key={k} label={l}>{(id) => <Input id={id} type="number" min={0} value={p.limits[k] ?? ""}
                  onChange={(e) => upd(p.code, { limits: { ...p.limits, [k]: e.target.value === "" ? null : Number(e.target.value) } })} />}</Field>))}
            </fieldset>
            <Button onClick={() => save(p)}>{saved === p.code ? "Tersimpan" : `Simpan ${p.code}`}</Button>
          </div>
        ))}
      </div>
    </div>
  );
}

type PNoc = { database: { status: string; latency_ms: number }; redis: { status: string }; worker: { status: string; seconds_since_beat: number | null };
  queues: { notification_pending: number; notification_failed_24h: number; reservations_expiry_backlog: number };
  tenants: { by_subscription: Record<string, number>; total: number }; orders_24h: number };

function Health() {
  const [d, setD] = useState<PNoc | null>(null);
  const load = useCallback(() => api.get<PNoc>("/platform/noc").then(setD).catch(() => undefined), []);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);
  if (!d) return null;
  const tile = (label: string, ok: boolean, detail: string) => (
    <div key={label} className={`rounded-lg border p-4 ${ok ? "border-ok bg-green-50" : "border-danger bg-red-50"}`}><div className="font-semibold">{label}</div><div className="text-sm text-ink-soft">{detail}</div></div>);
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {tile("Database", d.database.status === "ok", `latensi ${d.database.latency_ms} ms`)}
      {tile("Redis", d.redis.status !== "down", d.redis.status === "not_configured" ? "belum dikonfigurasi (wajib di production)" : d.redis.status)}
      {tile("Worker", d.worker.status === "ok", d.worker.seconds_since_beat == null ? "belum berjalan" : `detak ${d.worker.seconds_since_beat} detik lalu`)}
      {tile("Antrean notifikasi", d.queues.notification_failed_24h === 0, `${d.queues.notification_pending} antre · ${d.queues.notification_failed_24h} gagal 24 jam`)}
      {tile("Reservasi kedaluwarsa tertunda", d.queues.reservations_expiry_backlog === 0, `${d.queues.reservations_expiry_backlog} belum diproses`)}
      {tile("Tenant", true, `${d.tenants.total} total · ${Object.entries(d.tenants.by_subscription).map(([k, v]) => `${k} ${v}`).join(" · ")} · ${d.orders_24h} order 24 jam`)}
    </div>
  );
}
