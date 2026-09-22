"use client";
import { useCallback, useEffect, useState } from "react";
import { Flash, ScanInput, Stepper } from "@/components/scanner";
import { useWarehouse } from "@/components/wh-context";
import { api, errorText } from "@/lib/api";
import { type Task, feedback, sendOrQueue } from "@/lib/wms";

type Unplaced = { sku_id: string; sku_code: string; barcode: string | null; product_name: string; unplaced: number };

export default function PutawayPage() {
  const wh = useWarehouse()!;
  const [tasks, setTasks] = useState<Task[]>([]);
  const [free, setFree] = useState<Unplaced[]>([]);
  const [sel, setSel] = useState<{ kind: "task"; t: Task } | { kind: "free"; u: Unplaced } | null>(null);
  const [qty, setQty] = useState(1);
  const [msg, setMsg] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [t, u] = await Promise.all([api.get<Task[]>(`/wms/tasks?warehouse_id=${wh.id}&task_type=PUTAWAY`),
                                        api.get<Unplaced[]>(`/wms/unplaced?warehouse_id=${wh.id}`)]);
      setTasks(t);
      const inTasks = new Map<string, number>();
      t.forEach((x) => inTasks.set(x.sku_id, (inTasks.get(x.sku_id) ?? 0) + x.remaining));
      setFree(u.map((x) => ({ ...x, unplaced: x.unplaced - (inTasks.get(x.sku_id) ?? 0) })).filter((x) => x.unplaced > 0));
    } catch (e) { setMsg({ tone: "err", text: errorText(e) }); }
  }, [wh.id]);
  useEffect(() => { load(); }, [load]);

  const max = sel ? (sel.kind === "task" ? sel.t.remaining : sel.u.unplaced) : 1;
  useEffect(() => setQty(max), [max]);

  async function onBin(code: string) {
    if (!sel) return;
    try {
      const r = sel.kind === "task"
        ? await sendOrQueue(`/wms/tasks/${sel.t.id}/putaway`, { location_code: code, quantity: qty }, `Putaway ${sel.t.sku_code} ×${qty}`)
        : await sendOrQueue("/wms/putaway", { warehouse_id: wh.id, barcode: sel.u.barcode ?? sel.u.sku_code, location_code: code, quantity: qty },
                            `Putaway ${sel.u.sku_code} ×${qty}`);
      feedback(true);
      setMsg({ tone: r.ok ? "ok" : "info", text: r.ok ? `${qty} unit ditempatkan di ${code.toUpperCase()}.` : "Offline: disimpan dan dikirim otomatis nanti." });
      setSel(null); load();
    } catch (e) { feedback(false); setMsg({ tone: "err", text: errorText(e) }); }
  }

  if (sel) {
    const code = sel.kind === "task" ? sel.t.sku_code : sel.u.sku_code;
    const hint = sel.kind === "task" ? sel.t.to_location : null;
    return (
      <>
        <section className="rounded-xl border-[3px] border-ink bg-white p-4">
          <div className="font-mono text-2xl font-bold">{code}</div>
          <div className="text-ink-soft">{sel.kind === "task" ? sel.t.product_name : sel.u.product_name}</div>
          {hint && <div className="mt-2 text-lg">Saran bin: <span className="font-mono font-bold">{hint}</span></div>}
        </section>
        {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
        <Stepper value={qty} onChange={setQty} max={max} />
        <ScanInput label="Scan label bin tujuan" onScan={onBin} />
        <button type="button" onClick={() => setSel(null)} className="min-h-12 rounded-xl border-2 border-ink">Batal</button>
      </>
    );
  }
  return (
    <>
      <h1 className="text-2xl font-bold">Putaway</h1>
      {msg && <Flash tone={msg.tone}>{msg.text}</Flash>}
      {tasks.length === 0 && free.length === 0 && <p className="text-ink-soft">Semua stok sudah berada di bin.</p>}
      <ul className="flex flex-col gap-2">
        {tasks.map((t) => (
          <li key={t.id}><button type="button" onClick={() => { setSel({ kind: "task", t }); setMsg(null); }} className="flex w-full items-center justify-between rounded-xl border-[3px] border-ink bg-white p-4 text-left">
            <span><span className="block font-mono text-lg font-bold">{t.sku_code}</span><span className="text-sm text-ink-soft">{t.to_location ? `Saran ${t.to_location}` : "Pilih bin"}</span></span>
            <span className="text-2xl font-bold tabular-nums">{t.remaining}</span>
          </button></li>
        ))}
        {free.map((u) => (
          <li key={u.sku_id}><button type="button" onClick={() => { setSel({ kind: "free", u }); setMsg(null); }} className="flex w-full items-center justify-between rounded-xl border-[3px] border-dashed border-ink bg-white p-4 text-left">
            <span><span className="block font-mono text-lg font-bold">{u.sku_code}</span><span className="text-sm text-ink-soft">Stok lama belum punya bin</span></span>
            <span className="text-2xl font-bold tabular-nums">{u.unplaced}</span>
          </button></li>
        ))}
      </ul>
    </>
  );
}
