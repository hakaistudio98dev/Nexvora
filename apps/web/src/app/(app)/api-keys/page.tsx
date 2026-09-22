"use client";
import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Empty, Field, Input, Modal, PageHeader } from "@/components/ui";
import { api, dt, errorText } from "@/lib/api";

type Key = { id: string; name: string; hint: string; scopes: string[]; created_at: string; last_used_at: string | null;
  expires_at: string | null; revoked_at: string | null; active: boolean; key?: string };

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<Key[]>([]);
  const [scopes, setScopes] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<Key | null>(null);
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string[]>(["order:read", "order:write", "inventory:read", "product:read"]);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try { const [k, sc] = await Promise.all([api.get<Key[]>("/api-keys"), api.get<string[]>("/api-keys/scopes")]); setKeys(k); setScopes(sc); }
    catch (e) { setErr(errorText(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try { const k = await api.post<Key>("/api-keys", { name, scopes: picked.filter((x) => scopes.includes(x)) }); setOpen(false); setCreated(k); setName(""); load(); }
    catch (x) { setErr(errorText(x)); }
  }
  async function revoke(k: Key) {
    if (!confirm(`Cabut "${k.name}"? Integrasi yang memakainya langsung berhenti.`)) return;
    try { await api.post(`/api-keys/${k.id}/revoke`); load(); } catch (x) { setErr(errorText(x)); }
  }

  const base = typeof window !== "undefined" ? `${window.location.origin}/ext/api/v1` : "/ext/api/v1";
  return (
    <>
      <PageHeader title="Integrasi (API)" desc="Sambungkan toko online, marketplace, atau sistem lain agar order masuk otomatis."
        action={<Button onClick={() => setOpen(true)}>Buat API key</Button>} />
      {err && <Alert>{err}</Alert>}
      <div className="mb-4 rounded-xl border border-concrete-dark bg-white shadow-card p-4 text-sm">
        <div>Base URL: <code className="font-mono font-semibold">{base}</code></div>
        <div className="mt-1">Kirim header <code className="font-mono">X-API-Key: nxk_…</code>. Contoh: <code className="font-mono">POST {base}/orders</code> dengan <code className="font-mono">Idempotency-Key</code>.</div>
      </div>
      {created?.key && (
        <div role="status" className="mb-4 rounded-lg border-[3px] border-ok bg-green-50 p-4">
          <p className="font-semibold text-ok">API key “{created.name}” dibuat. Salin sekarang — kunci ini tidak akan ditampilkan lagi.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <code className="min-w-0 flex-1 break-all rounded-lg bg-white p-2 font-mono text-sm">{created.key}</code>
            <Button variant="secondary" onClick={() => { navigator.clipboard.writeText(created.key!); setCopied(true); }}>{copied ? "Tersalin" : "Salin"}</Button>
            <Button variant="ghost" onClick={() => { setCreated(null); setCopied(false); }}>Sudah disimpan</Button>
          </div>
        </div>
      )}
      {keys.length === 0 ? <Empty title="Belum ada API key" /> : (
        <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="border-b border-concrete-dark bg-concrete"><tr><th className="px-3 py-2">Nama</th><th className="px-3 py-2">Izin</th><th className="px-3 py-2">Terakhir dipakai</th><th className="px-3 py-2">Kedaluwarsa</th><th className="px-3 py-2"><span className="sr-only">Aksi</span></th></tr></thead>
            <tbody>{keys.map((k) => (
              <tr key={k.id} className="border-b border-concrete-dark">
                <td className="px-3 py-2"><div className="font-semibold">{k.name}</div><div className="font-mono text-xs text-ink-muted">{k.hint}</div></td>
                <td className="px-3 py-2"><div className="flex flex-wrap gap-1">{k.scopes.map((s) => <Badge key={s}>{s}</Badge>)}</div></td>
                <td className="px-3 py-2 text-xs">{dt(k.last_used_at)}</td>
                <td className="px-3 py-2 text-xs">{k.active ? dt(k.expires_at) : <Badge tone="off">{k.revoked_at ? "Dicabut" : "Kedaluwarsa"}</Badge>}</td>
                <td className="px-3 py-2 text-right">{k.active && <Button variant="ghost" onClick={() => revoke(k)}>Cabut</Button>}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
      <Modal open={open} title="Buat API key" onClose={() => setOpen(false)}>
        <form onSubmit={create} className="flex flex-col gap-4">
          <Field label="Nama" hint="Untuk mengenali integrasinya, mis. Shopee atau Website toko">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />}</Field>
          <fieldset><legend className="mb-2 text-sm font-semibold">Izin (berikan seperlunya)</legend>
            <div className="grid gap-2 sm:grid-cols-2">{scopes.map((s) => (
              <label key={s} className="flex min-h-11 items-center gap-2 rounded-lg border border-concrete-line px-3 font-mono text-sm">
                <input type="checkbox" className="h-4 w-4 accent-ink" checked={picked.includes(s)} onChange={(e) => setPicked(e.target.checked ? [...picked, s] : picked.filter((x) => x !== s))} />{s}
              </label>))}</div></fieldset>
          <Button type="submit">Buat</Button>
        </form>
      </Modal>
    </>
  );
}
