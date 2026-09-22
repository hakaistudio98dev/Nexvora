"use client";
import { useCallback, useEffect, useState } from "react";
import { useCan, useMe } from "@/components/me-context";
import { Alert, Badge, Button, Field, Input, PageHeader, Select } from "@/components/ui";
import { api, dt, errorText } from "@/lib/api";
import { hasFeature } from "@/lib/saas";
import type { NotifChannel } from "@/lib/types";

type Settings = { sla_ship_hours: number; sla_risk_hours: number; low_stock_threshold: number; timezone: string };
const TZ = ["Asia/Jakarta", "Asia/Pontianak", "Asia/Makassar", "Asia/Jayapura"];

export default function SettingsPage() {
  const canManage = useCan("settings:manage");
  const canNotif = useCan("notification:manage");
  const [st, setSt] = useState<Settings | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api.get<Settings>("/settings").then(setSt).catch(() => undefined); }, []);
  async function save(e: React.FormEvent) {
    e.preventDefault(); if (!st) return;
    try { setSt(await api.patch<Settings>("/settings", st)); setMsg({ ok: true, text: "Pengaturan disimpan." }); } catch (x) { setMsg({ ok: false, text: errorText(x) }); }
  }
  return (
    <>
      <PageHeader title="Pengaturan" desc="Target SLA, batas stok menipis, dan ke mana notifikasi dikirim." />
      {st && (
        <form onSubmit={save} className="grid max-w-3xl gap-4 rounded-xl border border-concrete-dark bg-white shadow-card p-5 sm:grid-cols-2">
          <h2 className="text-lg font-bold sm:col-span-2">Operasional</h2>
          {msg && <div className="sm:col-span-2"><p role={msg.ok ? "status" : "alert"} className={`text-sm font-semibold ${msg.ok ? "text-ok" : "text-danger"}`}>{msg.text}</p></div>}
          <Field label="SLA kirim (jam)" hint="Dari order dibayar sampai diserahkan ke kurir">{(id) => <Input id={id} type="number" min={1} max={720} disabled={!canManage} value={st.sla_ship_hours} onChange={(e) => setSt({ ...st, sla_ship_hours: Number(e.target.value) })} />}</Field>
          <Field label="Peringatan dini (jam sebelum batas)">{(id) => <Input id={id} type="number" min={0} max={168} disabled={!canManage} value={st.sla_risk_hours} onChange={(e) => setSt({ ...st, sla_risk_hours: Number(e.target.value) })} />}</Field>
          <Field label="Batas stok menipis (default)" hint="Bisa diatur khusus per SKU di menu Produk">{(id) => <Input id={id} type="number" min={0} disabled={!canManage} value={st.low_stock_threshold} onChange={(e) => setSt({ ...st, low_stock_threshold: Number(e.target.value) })} />}</Field>
          <Field label="Zona waktu laporan">{(id) => <Select id={id} disabled={!canManage} value={st.timezone} onChange={(e) => setSt({ ...st, timezone: e.target.value })}>{TZ.map((z) => <option key={z}>{z}</option>)}</Select>}</Field>
          {canManage && <Button type="submit" className="sm:col-span-2 sm:justify-self-start">Simpan</Button>}
        </form>
      )}
      {canNotif && <Channels />}
    </>
  );
}

function Channels() {
  const me = useMe();
  const webhooks = hasFeature(me.subscription, "webhooks");
  const [rows, setRows] = useState<NotifChannel[]>([]);
  const [events, setEvents] = useState<{ code: string; label: string }[]>([]);
  const [f, setF] = useState({ kind: "EMAIL", name: "", target: "", events: ["SLA_BREACH", "ORDER_ON_HOLD", "LOW_STOCK", "SHIPMENT_PROBLEM"] });
  const [secret, setSecret] = useState<string | null>(null);
  const [log, setLog] = useState<{ id: string; rows: { id: string; title: string; status: string; attempts: number; last_error: string | null; created_at: string }[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api.get<NotifChannel[]>("/notifications/channels").then(setRows).catch((e) => setErr(errorText(e))), []);
  useEffect(() => { load(); api.get<{ code: string; label: string }[]>("/notifications/events").then(setEvents).catch(() => undefined); }, [load]);
  async function add(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try { const c = await api.post<NotifChannel>("/notifications/channels", f); setSecret(c.secret ?? null); setF({ ...f, name: "", target: "" }); load(); } catch (x) { setErr(errorText(x)); }
  }
  async function test(c: NotifChannel) { try { await api.post(`/notifications/channels/${c.id}/test`); setErr(null); alert("Pesan tes terkirim."); } catch (x) { setErr(errorText(x)); } }
  async function toggle(c: NotifChannel) { await api.patch(`/notifications/channels/${c.id}`, { is_active: !c.is_active }); load(); }
  async function showLog(c: NotifChannel) { setLog({ id: c.id, rows: await api.get(`/notifications/channels/${c.id}/deliveries`) }); }
  return (
    <section className="mt-8 grid gap-6 lg:grid-cols-[1fr_400px]">
      <div className="flex flex-col gap-3">
        <h2 className="text-lg font-bold">Kanal notifikasi</h2>
        {err && <Alert>{err}</Alert>}
        {secret && <div role="status" className="rounded-lg border-[3px] border-ok bg-green-50 p-3 text-sm"><p className="font-semibold text-ok">Secret webhook (tampil sekali) — pakai untuk memverifikasi header X-Nexvora-Signature:</p><code className="break-all font-mono">{secret}</code>
          <Button variant="ghost" onClick={() => setSecret(null)}>Sudah disimpan</Button></div>}
        {rows.length === 0 && <p className="text-sm text-ink-muted">Notifikasi tetap muncul di aplikasi (ikon lonceng). Tambahkan email atau webhook agar juga dikirim keluar.</p>}
        {rows.map((c) => (
          <div key={c.id} className="rounded-xl border border-concrete-dark bg-white shadow-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-2"><div><Badge>{c.kind === "EMAIL" ? "Email" : "Webhook"}</Badge> <span className="font-semibold">{c.name}</span> {!c.is_active && <Badge tone="off">Nonaktif</Badge>}
              <div className="break-all font-mono text-xs text-ink-muted">{c.target}</div></div>
              <div className="flex gap-1"><Button variant="ghost" onClick={() => test(c)}>Tes</Button><Button variant="ghost" onClick={() => showLog(c)}>Riwayat</Button><Button variant="ghost" onClick={() => toggle(c)}>{c.is_active ? "Nonaktifkan" : "Aktifkan"}</Button></div></div>
            <p className="mt-1 text-xs text-ink-soft">{c.events.map((e) => events.find((x) => x.code === e)?.label ?? e).join(" · ")}</p>
            <p className="text-xs text-ink-muted">Terkirim {c.deliveries.sent} · antre {c.deliveries.pending} · gagal {c.deliveries.failed}</p>
            {log?.id === c.id && <ul className="mt-2 divide-y divide-concrete-dark border-t border-concrete-dark text-xs">{log.rows.map((d) => (
              <li key={d.id} className="flex justify-between gap-2 py-1"><span>{d.title}</span><span className={d.status === "FAILED" ? "text-danger" : d.status === "SENT" ? "text-ok" : ""}>{d.status}{d.attempts > 1 ? ` (${d.attempts}x)` : ""}{d.last_error ? ` — ${d.last_error}` : ""} · {dt(d.created_at)}</span></li>))}</ul>}
          </div>))}
      </div>
      <form onSubmit={add} className="flex h-fit flex-col gap-3 rounded-xl border border-concrete-dark bg-white shadow-card p-4">
        <h3 className="text-lg font-bold">Tambah kanal</h3>
        <Field label="Jenis">{(id) => <Select id={id} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="EMAIL">Email</option><option value="WEBHOOK" disabled={!webhooks}>Webhook (n8n, WhatsApp gateway, Slack){webhooks ? "" : " — paket Growth"}</option></Select>}</Field>
        <Field label="Nama">{(id) => <Input id={id} value={f.name} required minLength={2} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
        <Field label={f.kind === "EMAIL" ? "Alamat email" : "URL webhook (https)"}>{(id) => <Input id={id} type={f.kind === "EMAIL" ? "email" : "url"} value={f.target} required onChange={(e) => setF({ ...f, target: e.target.value })} />}</Field>
        <fieldset><legend className="mb-1 text-sm font-semibold">Kirim untuk event</legend>
          {events.map((ev) => (
            <label key={ev.code} className="flex min-h-9 items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-ink" checked={f.events.includes(ev.code)}
              onChange={(e) => setF({ ...f, events: e.target.checked ? [...f.events, ev.code] : f.events.filter((x) => x !== ev.code) })} />{ev.label}</label>))}
        </fieldset>
        <Button type="submit">Simpan kanal</Button>
        <p className="text-xs text-ink-muted">Webhook dikirim sebagai JSON bertanda tangan HMAC-SHA256, dicoba ulang otomatis hingga 5 kali bila gagal. Cocok disambungkan ke n8n untuk meneruskan ke WhatsApp atau Telegram.</p>
      </form>
    </section>
  );
}
