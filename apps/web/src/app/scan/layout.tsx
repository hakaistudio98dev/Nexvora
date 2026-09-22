"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { MeContext } from "@/components/me-context";
import { WhContext } from "@/components/wh-context";
import { api } from "@/lib/api";
import type { Me, Warehouse } from "@/lib/types";
import { flushQueue, onQueueChange, queued, saveWarehouse, savedWarehouse } from "@/lib/wms";


export default function ScanLayout({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [whs, setWhs] = useState<Warehouse[]>([]);
  const [wh, setWh] = useState<Warehouse | null>(null);
  const [pending, setPending] = useState(0);
  const [online, setOnline] = useState(true);
  const [note, setNote] = useState<string | null>(null);
  const pathname = usePathname();

  useEffect(() => {
    api.get<Me>("/auth/me").then(setMe).catch(() => undefined);
    api.get<Warehouse[]>("/warehouses").then((list) => {
      const active = list.filter((w) => w.is_active);
      setWhs(active);
      setWh(active.find((w) => w.id === savedWarehouse()) ?? active[0] ?? null);
    }).catch(() => undefined);
  }, []);

  const flush = useCallback(async () => {
    if (!queued().length) return;
    const r = await flushQueue();
    if (r.sent || r.rejected.length) {
      setNote(r.rejected.length
        ? `${r.sent} scan terkirim. ${r.rejected.length} ditolak server: ${r.rejected.map((x) => `${x.label} (${x.message})`).join("; ")}`
        : `${r.sent} scan tertunda berhasil dikirim.`);
    }
  }, []);

  useEffect(() => {
    const upd = () => setPending(queued().length);
    upd();
    const off = onQueueChange(upd);
    const on = () => { setOnline(true); flush(); };
    const offline = () => setOnline(false);
    setOnline(navigator.onLine);
    window.addEventListener("online", on);
    window.addEventListener("offline", offline);
    const t = setInterval(flush, 20000);
    return () => { off(); window.removeEventListener("online", on); window.removeEventListener("offline", offline); clearInterval(t); };
  }, [flush]);

  if (!me) return <div className="grid min-h-screen place-items-center text-ink-muted" aria-busy="true">Memuat…</div>;
  if (!me.subscription.unrestricted && !me.subscription.features.includes("wms")) {
    return (
      <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-3 p-6 text-center">
        <p className="text-2xl font-extrabold">Scanner gudang belum aktif</p>
        <p className="text-ink-soft">Fitur WMS & scanner tersedia mulai paket Growth. Minta admin workspace Anda untuk upgrade di menu Langganan.</p>
        <Link href="/dashboard" className="min-h-12 rounded-xl bg-ink px-4 py-3 font-bold text-white">Kembali ke console</Link>
      </div>
    );
  }

  return (
    <MeContext.Provider value={me}>
      <WhContext.Provider value={wh}>
        <div className="mx-auto flex min-h-screen max-w-xl flex-col">
          <header className="sticky top-0 z-20 flex items-center gap-2 border-b-[3px] border-ink bg-ink px-3 py-2 text-white">
            {pathname !== "/scan" ? (
              <Link href="/scan" className="grid min-h-12 min-w-12 place-items-center rounded font-bold hover:bg-ink-line" aria-label="Kembali ke menu">←</Link>
            ) : (
              <span className="px-2 text-lg font-black font-brand [font-stretch:125%]">SCAN</span>
            )}
            <label htmlFor="wh-pick" className="sr-only">Gudang</label>
            <select id="wh-pick" value={wh?.id ?? ""} onChange={(e) => { const w = whs.find((x) => x.id === e.target.value) ?? null; setWh(w); if (w) saveWarehouse(w.id); }}
                    className="min-h-12 flex-1 rounded-xl bg-ink-line px-2 font-mono text-sm text-white">
              {whs.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}
            </select>
            <button type="button" onClick={flush} aria-label={`${pending} scan tertunda. Kirim sekarang`}
                    className={`min-h-12 rounded px-3 text-sm font-bold ${pending ? "bg-signal text-ink" : online ? "bg-ok" : "bg-danger"}`}>
              {pending ? `${pending} tertunda` : online ? "Online" : "Offline"}
            </button>
          </header>
          {note && (
            <div role="status" className="flex items-start justify-between gap-2 border-b-2 border-ink bg-white px-3 py-2 text-sm">
              <span>{note}</span><button type="button" onClick={() => setNote(null)} className="min-h-9 px-2 font-bold" aria-label="Tutup">✕</button>
            </div>
          )}
          <main className="flex flex-1 flex-col gap-4 p-4">
            {wh ? children : <p>Belum ada gudang aktif. Buat gudang di console terlebih dulu.</p>}
          </main>
        </div>
      </WhContext.Provider>
    </MeContext.Provider>
  );
}
