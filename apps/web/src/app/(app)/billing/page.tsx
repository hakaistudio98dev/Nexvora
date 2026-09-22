"use client";
import { useCallback, useEffect, useState } from "react";
import { useCan, useMe } from "@/components/me-context";
import { Alert, Badge, Button, PageHeader } from "@/components/ui";
import { api, dt, errorText, rupiah } from "@/lib/api";
import { FEATURE_LABEL, LIMIT_LABEL, STATUS_LABEL, daysLeft } from "@/lib/saas";
import type { Invoice, Plan, SubscriptionInfo } from "@/lib/types";

type Info = { subscription: SubscriptionInfo; usage: Record<string, number>; warnings: string[]; open_invoices: Invoice[];
  provider: string; bank_transfer_info: string };

export default function BillingPage() {
  const me = useMe();
  const canManage = useCan("billing:manage");
  const [info, setInfo] = useState<Info | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [cycle, setCycle] = useState<"MONTHLY" | "YEARLY">("MONTHLY");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [i, p, inv] = await Promise.all([api.get<Info>("/billing/subscription"), api.get<Plan[]>("/billing/plans"), api.get<Invoice[]>("/billing/invoices")]);
      setInfo(i); setPlans(p); setInvoices(inv); setErr(null);
    } catch (e) { setErr(errorText(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function checkout(code: string) {
    setBusy(code); setErr(null);
    try {
      const inv = await api.post<Invoice>("/billing/checkout", { plan_code: code, billing_cycle: cycle });
      if (inv.payment_url) window.location.href = inv.payment_url; else load();
    } catch (e) { setErr(errorText(e)); } finally { setBusy(null); }
  }
  async function pay(inv: Invoice) {
    try {
      const r = await api.post<Invoice>(`/billing/invoices/${inv.id}/pay`);
      if (r.payment_url) window.location.href = r.payment_url;
    } catch (e) { setErr(errorText(e)); }
  }
  async function toggleCancel(cancel: boolean) {
    if (cancel && !confirm("Hentikan langganan di akhir periode? Setelah itu akun menjadi baca-saja.")) return;
    try { await api.post(cancel ? "/billing/cancel" : "/billing/resume"); window.location.reload(); } catch (e) { setErr(errorText(e)); }
  }

  if (me.subscription.unrestricted) return <PageHeader title="Langganan" desc="Akun platform tidak berlangganan. Kelola paket tenant di Admin platform." />;
  if (!info) return err ? <Alert>{err}</Alert> : null;
  const s = info.subscription;
  const trial = daysLeft(s.trial_ends_at);

  return (
    <>
      <PageHeader title="Langganan" desc="Paket, pemakaian, dan tagihan workspace Anda." />
      {err && <div className="mb-4"><Alert>{err}</Alert></div>}

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <section className="rounded-xl border border-concrete-dark bg-white shadow-card p-5">
          <div className="text-sm text-ink-soft">Paket saat ini</div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="text-3xl font-bold">{s.plan_name}</span>
            <Badge tone={s.status === "ACTIVE" ? "ok" : s.read_only || s.status === "PAST_DUE" ? "off" : "signal"}>{STATUS_LABEL[s.status]}</Badge>
          </div>
          <p className="mt-2 text-sm text-ink-soft">
            {s.status === "TRIALING" && trial !== null && `Masa coba berakhir ${dt(s.trial_ends_at)} (${Math.max(0, trial)} hari lagi).`}
            {s.status === "ACTIVE" && `Periode berjalan sampai ${dt(s.current_period_end)}${s.cancel_at_period_end ? " — tidak diperpanjang" : ""}.`}
            {s.status === "PAST_DUE" && `Bayar sebelum ${dt(s.grace_until)} agar akun tidak menjadi baca-saja.`}
            {s.read_only && "Akun baca-saja. Bayar tagihan untuk mengaktifkan kembali."}
          </p>
          <ul className="mt-3 flex flex-wrap gap-1.5">{s.features.map((f) => <li key={f}><Badge>{FEATURE_LABEL[f] ?? f}</Badge></li>)}</ul>
          {canManage && s.status === "ACTIVE" && (
            <button type="button" onClick={() => toggleCancel(!s.cancel_at_period_end)} className="mt-4 min-h-10 text-sm font-semibold underline">
              {s.cancel_at_period_end ? "Lanjutkan perpanjangan otomatis" : "Hentikan di akhir periode"}
            </button>
          )}
        </section>

        <section className="rounded-xl border border-concrete-dark bg-white shadow-card p-5">
          <div className="mb-3 text-sm text-ink-soft">Pemakaian</div>
          <div className="flex flex-col gap-3">
            {Object.entries(LIMIT_LABEL).map(([k, label]) => {
              const used = info.usage[k] ?? 0; const lim = s.limits[k];
              const pct = lim ? Math.min(100, (used / lim) * 100) : 0;
              return (
                <div key={k}>
                  <div className="flex justify-between text-sm"><span>{label}</span><span className="tabular-nums font-semibold">{used}{lim != null ? ` / ${lim}` : " · tanpa batas"}</span></div>
                  {lim != null && <div className="mt-1 h-2 overflow-hidden rounded-full bg-concrete-dark" role="progressbar" aria-label={label} aria-valuenow={used} aria-valuemax={lim}>
                    <div className={`h-full ${pct >= 100 ? "bg-danger" : pct >= 80 ? "bg-signal-dark" : "bg-ink"}`} style={{ width: `${pct}%` }} /></div>}
                </div>
              );
            })}
          </div>
          {info.warnings.map((w) => <p key={w} className="mt-3 text-sm font-semibold text-danger">{w}</p>)}
          <p className="mt-3 text-xs text-ink-muted">Kuota order bersifat lunak: order tetap diterima walau melewati kuota, lalu Anda akan diminta upgrade.</p>
        </section>
      </div>

      {info.open_invoices.length > 0 && (
        <section className="mt-6 rounded-xl border border-concrete-dark bg-signal-soft p-5">
          <h2 className="text-lg font-bold">Tagihan menunggu pembayaran</h2>
          {info.open_invoices.map((i) => (
            <div key={i.id} className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-ink bg-white p-4">
              <div><div className="font-mono font-bold">{i.number}</div>
                <div className="text-sm">{plans.find((p) => p.code === i.plan_code)?.name ?? i.plan_code} · {i.billing_cycle === "YEARLY" ? "tahunan" : "bulanan"} · jatuh tempo {dt(i.due_at)}</div></div>
              <div className="flex items-center gap-3"><span className="text-lg font-bold tabular-nums">{rupiah(i.amount)}</span>
                {canManage && i.provider === "midtrans" && <Button onClick={() => pay(i)}>Bayar sekarang</Button>}</div>
            </div>
          ))}
          {info.provider === "manual" && <p className="mt-3 text-sm"><strong>Cara bayar:</strong> {info.bank_transfer_info} Sertakan nomor invoice sebagai berita transfer.</p>}
        </section>
      )}

      {canManage && (
        <section className="mt-8">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-bold">Pilih paket</h2>
            <div role="radiogroup" aria-label="Siklus tagihan" className="flex gap-1 rounded-full border border-concrete-dark bg-white p-1">
              {(["MONTHLY", "YEARLY"] as const).map((c) => (
                <button key={c} type="button" role="radio" aria-checked={cycle === c} onClick={() => setCycle(c)}
                        className={`min-h-9 rounded-full px-4 text-sm font-semibold ${cycle === c ? "bg-ink text-white" : ""}`}>{c === "MONTHLY" ? "Bulanan" : "Tahunan"}</button>))}
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {plans.map((p) => {
              const current = p.code === s.plan_code && s.status === "ACTIVE";
              const price = cycle === "YEARLY" ? p.price_yearly : p.price_monthly;
              return (
                <div key={p.code} className={`flex flex-col gap-3 rounded-lg border p-5 ${current ? "border-ink/30 bg-signal-soft/30" : "border-ink bg-white"}`}>
                  <div className="text-2xl font-bold">{p.name}</div>
                  <div className="text-sm text-ink-soft">{p.description}</div>
                  <div className="text-2xl font-bold">{Number(price) > 0 ? rupiah(price) : "Hubungi sales"}{Number(price) > 0 && <span className="text-sm font-normal"> / {cycle === "YEARLY" ? "tahun" : "bulan"}</span>}</div>
                  <ul className="flex flex-col gap-1 text-sm">
                    {p.features.map((f) => <li key={f}>✓ {FEATURE_LABEL[f] ?? f}</li>)}
                    {Object.entries(LIMIT_LABEL).map(([k, l]) => <li key={k} className="text-ink-soft">{l}: {p.limits[k] ?? "tanpa batas"}</li>)}
                  </ul>
                  <div className="mt-auto">
                    {p.self_serve && Number(price) > 0
                      ? <Button className="w-full" variant={current ? "secondary" : "primary"} loading={busy === p.code} onClick={() => checkout(p.code)}>{current ? "Perpanjang" : s.status === "ACTIVE" ? "Ganti ke paket ini" : "Pilih paket ini"}</Button>
                      : <a href="mailto:sales@nexvora.id" className="inline-flex min-h-11 w-full items-center justify-center rounded-md border border-ink font-semibold">Hubungi sales</a>}
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-2 text-xs text-ink-muted">Ganti paket berlaku saat tagihan dibayar; periode baru dimulai dari tanggal pembayaran.</p>
        </section>
      )}

      <section className="mt-8">
        <h2 className="mb-3 text-lg font-bold">Riwayat tagihan</h2>
        {invoices.length === 0 ? <p className="text-sm text-ink-muted">Belum ada tagihan.</p> : (
          <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
            <table className="w-full min-w-[560px] text-left text-sm tabular-nums">
              <thead className="border-b border-concrete-dark bg-concrete"><tr><th className="px-3 py-2">Invoice</th><th className="px-3 py-2">Paket</th><th className="px-3 py-2 text-right">Jumlah</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Dibayar</th></tr></thead>
              <tbody>{invoices.map((i) => (
                <tr key={i.id} className="border-b border-concrete-dark"><td className="px-3 py-2 font-mono">{i.number}</td>
                  <td className="px-3 py-2">{i.plan_code} · {i.billing_cycle === "YEARLY" ? "tahunan" : "bulanan"}</td>
                  <td className="px-3 py-2 text-right">{rupiah(i.amount)}</td>
                  <td className="px-3 py-2"><Badge tone={i.status === "PAID" ? "ok" : i.status === "OPEN" ? "signal" : "off"}>{i.status}</Badge></td>
                  <td className="px-3 py-2 text-xs">{dt(i.paid_at)}</td></tr>))}</tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
