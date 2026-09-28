"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Send, Sparkles, Trash2, User } from "lucide-react";
import { Alert, Badge, Button, Input, PageHeader } from "@/components/ui";
import { api, errorText } from "@/lib/api";

type Card = { type: "metrics" | "table"; title: string; items?: { label: string; value: string | number }[];
  columns?: string[]; rows?: (string | number)[][] };
type Pending = { name: string; args: Record<string, unknown>; confirm: string };
type ChatOut = { reply: string; cards: Card[]; tools_used: string[]; pending_action: Pending | null; engine: string; notice?: string };
type Msg = { role: "user" | "assistant"; text: string; cards?: Card[]; pending?: Pending | null; notice?: string; done?: boolean };

export default function AssistantPage() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [caps, setCaps] = useState<{ engine: string; suggestions: string[] } | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api.get<typeof caps>("/assistant/capabilities").then(setCaps).catch(() => undefined); }, []);
  useEffect(() => {
    api.get<{ role: "user" | "assistant"; content: string }[]>("/assistant/history?limit=20")
      .then((h) => setMsgs(h.map((m) => ({ role: m.role, text: m.content })))).catch(() => undefined);
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);

  const send = useCallback(async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    setInput(""); setErr(null); setBusy(true);
    setMsgs((m) => [...m, { role: "user", text: q }]);
    try {
      const r = await api.post<ChatOut>("/assistant/chat", { message: q });
      setMsgs((m) => [...m, { role: "assistant", text: r.reply, cards: r.cards, pending: r.pending_action, notice: r.notice }]);
    } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  }, [busy]);

  async function confirm(i: number, p: Pending) {
    setBusy(true); setErr(null);
    try {
      const r = await api.post<{ reply: string; cards: Card[] }>("/assistant/act", { name: p.name, args: p.args });
      setMsgs((m) => m.map((x, idx) => (idx === i ? { ...x, pending: null, done: true } : x))
        .concat({ role: "assistant", text: r.reply, cards: r.cards }));
    } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  }

  return (
    <>
      <PageHeader title="Asisten" desc="Tanya data atau beri perintah dengan bahasa biasa. Perintah yang mengubah data selalu minta konfirmasi dulu."
        action={msgs.length > 0 && <Button variant="ghost" onClick={() => api.delete("/assistant/history").then(() => setMsgs([]))}>
          <Trash2 size={16} aria-hidden="true" />Bersihkan</Button>} />
      {err && <Alert>{err}</Alert>}

      <div className="flex flex-col gap-4 pb-4">
        {msgs.length === 0 && (
          <div className="rounded-xl border border-concrete-dark bg-white p-5 shadow-card">
            <div className="flex items-center gap-2"><Sparkles size={18} className="text-signal-dark" aria-hidden="true" />
              <p className="font-semibold">Coba tanya seperti ini</p></div>
            <div className="mt-3 flex flex-wrap gap-2">
              {(caps?.suggestions ?? ["berapa order hari ini", "stok TSH-BLK-M", "order mana yang terlambat"]).map((s) => (
                <button key={s} type="button" onClick={() => send(s)}
                        className="min-h-9 rounded-full border border-concrete-line px-3 text-sm hover:border-ink hover:bg-concrete">{s}</button>))}
            </div>
            <p className="mt-3 text-xs text-ink-muted">Asisten hanya bisa melihat dan mengubah data yang memang boleh Anda akses,
              persis seperti tombol di layar.{caps?.engine === "rules" && " Saat ini berjalan dalam mode dasar (tanpa layanan AI luar)."}</p>
          </div>
        )}

        {msgs.map((m, i) => (
          <div key={i} className={`flex gap-3 ${m.role === "user" ? "justify-end" : ""}`}>
            {m.role === "assistant" && <span className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ink text-white"><Bot size={17} aria-hidden="true" /></span>}
            <div className={`min-w-0 max-w-[min(680px,92%)] ${m.role === "user" ? "order-first" : ""}`}>
              <div className={`rounded-xl px-4 py-3 ${m.role === "user" ? "bg-ink text-white" : "border border-concrete-dark bg-white shadow-card"}`}>
                <p className="whitespace-pre-wrap">{m.text}</p>
                {m.notice && <p className="mt-2 text-xs text-ink-muted">{m.notice}</p>}
              </div>
              {m.cards?.map((c, ci) => <CardView key={ci} card={c} />)}
              {m.pending && (
                <div className="mt-2 rounded-xl border border-signal-dark/40 bg-signal-soft p-4">
                  <p className="font-semibold">{m.pending.confirm}</p>
                  <p className="mt-1 text-sm text-ink-soft">Perintah ini mengubah data dan tercatat di Riwayat aktivitas atas nama Anda.</p>
                  <div className="mt-3 flex gap-2">
                    <Button onClick={() => confirm(i, m.pending!)} loading={busy}>Ya, jalankan</Button>
                    <Button variant="ghost" onClick={() => setMsgs((x) => x.map((y, idx) => (idx === i ? { ...y, pending: null } : y)))}>Batal</Button>
                  </div>
                </div>
              )}
              {m.done && <p className="mt-1 text-xs text-ok">Sudah dijalankan.</p>}
            </div>
            {m.role === "user" && <span className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-concrete-dark text-ink-soft"><User size={17} aria-hidden="true" /></span>}
          </div>
        ))}
        {busy && <p className="text-sm text-ink-muted" aria-live="polite">Sedang memeriksa data…</p>}
        <div ref={endRef} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="sticky bottom-0 mt-2 flex gap-2 border-t border-concrete-dark bg-concrete py-3">
        <label htmlFor="ask" className="sr-only">Tulis pertanyaan atau perintah</label>
        <Input id="ask" value={input} onChange={(e) => setInput(e.target.value)} disabled={busy}
               placeholder="Mis. stok TSH-BLK-M, atau mulai picking SO-2609-000001" className="h-12" />
        <Button type="submit" disabled={!input.trim()} loading={busy} className="h-12 px-5"><Send size={16} aria-hidden="true" />Kirim</Button>
      </form>
    </>
  );
}

function CardView({ card }: { card: Card }) {
  if (card.type === "metrics") {
    return (
      <div className="mt-2 rounded-xl border border-concrete-dark bg-white p-4 shadow-card">
        <p className="mb-2 font-semibold">{card.title}</p>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
          {card.items?.map((it) => (
            <div key={it.label}><dt className="text-xs text-ink-muted">{it.label}</dt>
              <dd className="text-lg font-bold tabular-nums">{it.value}</dd></div>))}
        </dl>
      </div>
    );
  }
  return (
    <div className="mt-2 overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
      <p className="border-b border-concrete-dark px-4 py-2 font-semibold">{card.title}</p>
      <table className="w-full text-left text-sm tabular-nums">
        <thead className="text-ink-muted"><tr>{card.columns?.map((c) => <th key={c} className="px-4 py-2 font-medium">{c}</th>)}</tr></thead>
        <tbody>{card.rows?.map((r, i) => (
          <tr key={i} className="border-t border-concrete-dark">{r.map((v, j) => (
            <td key={j} className={`px-4 py-2 ${j === 0 ? "font-mono font-semibold" : ""}`}>{v}</td>))}</tr>))}</tbody>
      </table>
      {card.rows?.length === 0 && <p className="px-4 py-3 text-sm text-ink-muted">Tidak ada data.</p>}
    </div>
  );
}
