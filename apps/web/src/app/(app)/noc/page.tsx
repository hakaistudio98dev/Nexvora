"use client";
import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, PageHeader } from "@/components/ui";
import { UpgradeCard } from "@/components/upgrade";
import { ApiError, api, dt, errorText } from "@/lib/api";
import type { NocData } from "@/lib/types";

const TONE = { ok: "border-ok bg-green-50", warning: "border-signal-dark bg-signal-soft", critical: "border-danger bg-red-50" };
const WORD = { ok: "Normal", warning: "Perlu dicek", critical: "Bermasalah" };

export default function NocPage() {
  const [d, setD] = useState<NocData | null>(null);
  const [locked, setLocked] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api.get<NocData>("/analytics/noc").then((x) => { setD(x); setErr(null); })
    .catch((e) => { if (e instanceof ApiError && e.status === 402) setLocked(true); else setErr(errorText(e)); }), []);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);
  if (locked) return <><PageHeader title="Pantau sistem" /><UpgradeCard title="Pantauan kesehatan operasional & integrasi" /></>;
  return (
    <>
      <PageHeader title="Pantau sistem" desc="Kesehatan operasional dan integrasi, diperbarui otomatis setiap 30 detik."
        action={<Button variant="secondary" onClick={load}>Perbarui</Button>} />
      {err && <Alert>{err}</Alert>}
      {d && (
        <>
          <div className={`mb-5 rounded-lg border-[3px] p-4 text-lg font-bold ${TONE[d.overall]}`} role="status">Status keseluruhan: {WORD[d.overall]}</div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {d.checks.map((c) => (
              <div key={c.key} className={`rounded-lg border p-4 ${TONE[c.status]}`}>
                <div className="flex items-center justify-between gap-2"><span className="font-semibold">{c.label}</span><Badge tone={c.status === "ok" ? "ok" : c.status === "warning" ? "signal" : "off"}>{WORD[c.status]}</Badge></div>
                <p className="mt-1 text-sm text-ink-soft">{c.detail}</p>
              </div>))}
          </div>
          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border border-concrete-dark bg-white shadow-card p-4"><h2 className="mb-2 text-lg font-bold">Integrasi kurir</h2>
              <table className="w-full text-left text-sm tabular-nums"><thead className="text-ink-muted"><tr><th className="py-1">Akun</th><th className="text-right">Aktif</th><th className="text-right">Macet</th><th>Update terakhir</th></tr></thead>
                <tbody>{d.couriers.map((c) => <tr key={c.name} className="border-t border-concrete-dark"><td className="py-1.5">{c.name} <span className="text-xs text-ink-muted">{c.provider}</span></td><td className="text-right">{c.active}</td>
                  <td className={`text-right ${c.stale ? "font-bold text-danger" : ""}`}>{c.stale}</td><td className="text-xs">{dt(c.last_tracked)}</td></tr>)}</tbody></table></section>
            <section className="rounded-xl border border-concrete-dark bg-white shadow-card p-4"><h2 className="mb-2 text-lg font-bold">Channel order masuk</h2>
              <table className="w-full text-left text-sm tabular-nums"><thead className="text-ink-muted"><tr><th className="py-1">Channel</th><th className="text-right">24 jam</th><th>Order terakhir</th></tr></thead>
                <tbody>{d.channels.map((c) => <tr key={c.channel} className="border-t border-concrete-dark"><td className="py-1.5">{c.channel}</td><td className="text-right">{c.orders_24h}</td><td className="text-xs">{dt(c.last_order)}</td></tr>)}</tbody></table>
              <p className="mt-2 text-xs text-ink-muted">Channel yang biasanya ramai tapi berhenti mengirim order menandakan integrasi terputus. API key aktif: {d.api_keys.active} · terakhir dipakai {dt(d.api_keys.last_used)}</p></section>
          </div>
        </>
      )}
    </>
  );
}
