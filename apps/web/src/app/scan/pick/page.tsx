"use client";
import { useCallback, useEffect, useState } from "react";
import { Flash, ScanInput, Stepper } from "@/components/scanner";
import { ApiError, api, errorText } from "@/lib/api";
import { type Task, feedback, sendOrQueue } from "@/lib/wms";
import { useWarehouse } from "@/components/wh-context";

export default function PickPage() {
  const wh = useWarehouse()!;
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [idx, setIdx] = useState(0);
  const [locOk, setLocOk] = useState(false);
  const [qty, setQty] = useState(1);
  const [msg, setMsg] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);
  const [shortOpen, setShortOpen] = useState(false);

  const load = useCallback(async () => {
    try { setTasks(await api.get<Task[]>(`/wms/tasks?warehouse_id=${wh.id}&task_type=PICK`)); setIdx(0); }
    catch (e) { setMsg({ tone: "err", text: errorText(e) }); }
  }, [wh.id]);
  useEffect(() => { load(); }, [load]);

  const t = tasks?.[idx];
  useEffect(() => { setLocOk(!t?.from_location); setQty(t?.remaining ?? 1); setShortOpen(false); }, [t?.id, t?.from_location, t?.remaining]);

  function next(doneId: string) {
    setTasks((cur) => cur?.filter((x) => x.id !== doneId) ?? null);
    setIdx(0);
  }

  function onLocation(code: string) {
    if (!t) return;
    if (code.toUpperCase() === t.from_location) { feedback(true); setLocOk(true); setMsg(null); }
    else { feedback(false); setMsg({ tone: "err", text: `Salah lokasi. Pergi ke ${t.from_location}` }); }
  }

  async function onItem(code: string) {
    if (!t) return;
    const matches = code === t.barcode || code.toUpperCase() === t.sku_code.toUpperCase();
    if (!matches) { feedback(false); setMsg({ tone: "err", text: `Salah barang. Ambil ${t.sku_code}` }); return; }
    try {
      const r = await sendOrQueue<Task>(`/wms/tasks/${t.id}/pick`, { location_code: t.from_location, barcode: code, quantity: qty },
                                        `Pick ${t.sku_code} ×${qty}`);
      feedback(true);
      if (!r.ok) { setMsg({ tone: "info", text: `Offline: pick ${t.sku_code} ×${qty} disimpan dan akan dikirim otomatis.` }); next(t.id); return; }
      if (r.data.status === "DONE") { setMsg({ tone: "ok", text: `${t.sku_code} ×${qty} diambil untuk ${t.order_number}.` }); next(t.id); }
      else { setTasks((cur) => cur?.map((x) => (x.id === t.id ? r.data : x)) ?? null); setMsg({ tone: "ok", text: `Sisa ${r.data.remaining}.` }); }
    } catch (e) {
      feedback(false);
      setMsg({ tone: "err", text: errorText(e) });
      if (e instanceof ApiError && e.code === "TASK_CLOSED") next(t.id);
    }
  }

  async function reportShort(note: string) {
    if (!t) return;
    try {
      await sendOrQueue(`/wms/tasks/${t.id}/short`, { note }, `Kurang ${t.sku_code}`);
      setMsg({ tone: "info", text: `Barang kurang dilaporkan ke supervisor.` });
      next(t.id);
    } catch (e) { setMsg({ tone: "err", text: errorText(e) }); }
  }

  if (!tasks) return <p aria-busy="true">Memuat task…</p>;
  if (!t) return (
    <>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      <div className="rounded-xl border-[3px] border-ink bg-white p-6 text-center">
        <p className="text-2xl font-bold">Tidak ada task picking</p>
        <p className="mt-1 text-ink-soft">Supervisor membuat wave dari console, atau order dimulai dari menu Order.</p>
        <button type="button" onClick={load} className="mt-4 min-h-12 rounded-xl bg-ink px-5 font-bold text-white">Muat ulang</button>
      </div>
    </>
  );

  return (
    <>
      <div className="flex items-center justify-between text-sm"><span>Task {idx + 1} dari {tasks.length}</span>
        <span className="flex gap-2">
          <button type="button" disabled={idx === 0} onClick={() => setIdx(idx - 1)} className="min-h-10 rounded-xl border-2 border-ink px-3 disabled:opacity-30">Sebelumnya</button>
          <button type="button" disabled={idx >= tasks.length - 1} onClick={() => setIdx(idx + 1)} className="min-h-10 rounded-xl border-2 border-ink px-3 disabled:opacity-30">Lewati</button>
        </span>
      </div>
      <section className="rounded-xl border-[3px] border-ink bg-white" aria-live="polite">
        <div className="border-b-[3px] border-ink bg-signal px-4 py-3">
          <div className="text-sm font-semibold">{t.from_location ? "Pergi ke lokasi" : "Ambil dari area penerimaan"}</div>
          <div className="font-mono text-4xl font-bold">{t.from_location ?? "STAGING"}</div>
        </div>
        <div className="flex flex-col gap-1 px-4 py-3">
          <div className="font-mono text-2xl font-bold">{t.sku_code}</div>
          <div className="text-ink-soft">{t.product_name}{t.variant_name ? ` · ${t.variant_name}` : ""}</div>
          <div className="mt-1 text-lg">Ambil <strong className="text-3xl tabular-nums">{t.remaining}</strong> untuk <span className="font-mono">{t.order_number}</span></div>
        </div>
      </section>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      {!locOk ? (
        <ScanInput label="1. Scan label lokasi" onScan={onLocation} />
      ) : (
        <>
          {t.remaining > 1 && <Stepper value={qty} onChange={setQty} max={t.remaining} />}
          <ScanInput label={t.from_location ? "2. Scan barang" : "Scan barang"} onScan={onItem} />
        </>
      )}
      {!shortOpen ? (
        <button type="button" onClick={() => setShortOpen(true)} className="min-h-12 rounded-xl border-[3px] border-danger bg-white font-bold text-danger">Barang kurang / tidak ada</button>
      ) : (
        <form className="flex flex-col gap-2 rounded-xl border-[3px] border-danger bg-white p-3"
              onSubmit={(e) => { e.preventDefault(); reportShort(String(new FormData(e.currentTarget).get("note") ?? "")); }}>
          <label htmlFor="short-note" className="font-semibold">Keterangan untuk supervisor</label>
          <input id="short-note" name="note" defaultValue={`Hanya ada ${t.remaining - 1} atau kurang di ${t.from_location ?? "staging"}`} className="min-h-12 rounded-xl border-2 border-ink px-3" />
          <div className="flex gap-2">
            <button type="submit" className="min-h-12 flex-1 rounded-xl bg-danger font-bold text-white">Laporkan ({t.remaining} kurang)</button>
            <button type="button" onClick={() => setShortOpen(false)} className="min-h-12 rounded-xl border-2 border-ink px-4">Batal</button>
          </div>
        </form>
      )}
    </>
  );
}
