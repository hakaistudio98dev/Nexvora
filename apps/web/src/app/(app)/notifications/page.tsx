"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Empty, PageHeader } from "@/components/ui";
import { api, dt } from "@/lib/api";
import type { AppNotification } from "@/lib/types";

export default function NotificationsPage() {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const load = useCallback(() => api.get<{ unread: number; items: AppNotification[] }>(`/notifications${unreadOnly ? "?unread=true" : ""}`)
    .then((r) => setItems(r.items)).catch(() => undefined), [unreadOnly]);
  useEffect(() => { load(); }, [load]);
  async function read(n: AppNotification) { if (!n.read) { await api.post(`/notifications/${n.id}/read`); load(); } }
  return (
    <>
      <PageHeader title="Notifikasi" action={<Button variant="secondary" onClick={() => api.post("/notifications/read-all").then(load)}>Tandai semua dibaca</Button>} />
      <label className="mb-4 flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-ink" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />Hanya yang belum dibaca</label>
      {items.length === 0 ? <Empty title="Tidak ada notifikasi" /> : (
        <ul className="flex flex-col gap-2">{items.map((n) => (
          <li key={n.id} className={`rounded-lg border p-4 ${n.read ? "border-concrete-line bg-white" : "border-ink bg-white shadow-card ring-1 ring-ink/10"}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-semibold">{!n.read && <span className="mr-2 inline-block h-2.5 w-2.5 rounded-full bg-danger" aria-label="belum dibaca" />}{n.title}</span>
              <Badge tone={n.severity === "CRITICAL" ? "off" : n.severity === "WARNING" ? "signal" : "neutral"}>{n.severity === "CRITICAL" ? "Kritis" : n.severity === "WARNING" ? "Peringatan" : "Info"}</Badge>
            </div>
            {n.body && <p className="mt-1 text-sm text-ink-soft">{n.body}</p>}
            <div className="mt-2 flex items-center gap-3 text-xs text-ink-muted">{dt(n.created_at)}
              {n.link && <Link href={n.link} onClick={() => read(n)} className="font-semibold">Buka</Link>}
              {!n.read && <button type="button" onClick={() => read(n)} className="min-h-8 font-semibold underline">Tandai dibaca</button>}</div>
          </li>))}</ul>)}
    </>
  );
}
