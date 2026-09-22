"use client";
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Empty, PageHeader, Select } from "@/components/ui";
import { api, errorText, qs } from "@/lib/api";
import type { AuditLog } from "@/lib/types";

const TYPES = ["", "user", "product", "sku", "warehouse", "location", "tenant", "session"];

export default function AuditPage() {
  const [items, setItems] = useState<AuditLog[]>([]);
  const [type, setType] = useState("");
  const [more, setMore] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(async (beforeId?: number) => {
    try {
      const page = await api.get<AuditLog[]>(`/audit-logs${qs({ entity_type: type, before_id: beforeId, limit: 50 })}`);
      setItems((cur) => (beforeId ? [...cur, ...page] : page));
      setMore(page.length === 50);
    } catch (e) { setErr(errorText(e)); }
  }, [type]);
  useEffect(() => { load(); }, [load]);

  return (
    <>
      <PageHeader title="Riwayat aktivitas" desc="Siapa mengubah apa dan kapan. Catatan ini permanen dan tidak bisa dihapus." />
      <div className="mb-4 flex items-center gap-3">
        <label htmlFor="t" className="text-sm font-semibold">Jenis data</label>
        <Select id="t" value={type} onChange={(e) => setType(e.target.value)} className="max-w-[200px]">
          {TYPES.map((t) => <option key={t} value={t}>{t || "Semua"}</option>)}
        </Select>
      </div>
      {err && <Alert>{err}</Alert>}
      {items.length === 0 ? <Empty title="Belum ada aktivitas tercatat" /> : (
        <ul className="divide-y divide-concrete-dark rounded-xl border border-concrete-dark bg-white shadow-card">
          {items.map((a) => (
            <li key={a.id}>
              <button type="button" onClick={() => setOpen(open === a.id ? null : a.id)} aria-expanded={open === a.id}
                      className="flex min-h-12 w-full flex-wrap items-center justify-between gap-2 px-4 py-2 text-left text-sm hover:bg-concrete">
                <span className="font-mono font-semibold">{a.action}</span>
                <span className="text-ink-muted">{new Date(a.created_at).toLocaleString("id-ID")}</span>
              </button>
              {open === a.id && (
                <div className="grid gap-3 bg-concrete px-4 py-3 text-xs md:grid-cols-2">
                  <div><div className="mb-1 font-semibold">Sebelum</div><pre className="overflow-x-auto whitespace-pre-wrap font-mono">{JSON.stringify(a.before, null, 2) ?? "—"}</pre></div>
                  <div><div className="mb-1 font-semibold">Sesudah</div><pre className="overflow-x-auto whitespace-pre-wrap font-mono">{JSON.stringify(a.after, null, 2) ?? "—"}</pre></div>
                  <div className="text-ink-muted md:col-span-2">Entity {a.entity_type} {a.entity_id ?? ""} · IP {a.ip ?? "—"} · correlation {a.correlation_id ?? "—"}</div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {more && items.length > 0 && (
        <Button variant="secondary" className="mt-4" onClick={() => load(items[items.length - 1].id)}>Muat lebih banyak</Button>
      )}
    </>
  );
}
