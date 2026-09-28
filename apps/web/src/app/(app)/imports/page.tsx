"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { CircleCheckBig, Download, FileSpreadsheet, TriangleAlert, Upload } from "lucide-react";
import { Alert, Badge, Button, Empty, PageHeader, Select } from "@/components/ui";
import { api, dt, errorText } from "@/lib/api";

type Kind = { kind: string; label: string; required: string[]; optional: string[]; allowed: boolean };
type Preview = { id: string; kind: string; label: string; total_rows: number; valid_rows: number; error_rows: number;
  sample: Record<string, unknown>[]; errors: { row: number; message: string }[] };
type Job = { id: string; kind: string; label: string; filename: string; status: string; total_rows: number;
  valid_rows: number; result: Record<string, number>; created_at: string; committed_at: string | null };

export default function ImportsPage() {
  const [kinds, setKinds] = useState<Kind[]>([]);
  const [kind, setKind] = useState("products");
  const [prev, setPrev] = useState<Preview | null>(null);
  const [done, setDone] = useState<{ result: Record<string, number>; skipped: number } | null>(null);
  const [skipErrors, setSkipErrors] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadJobs = useCallback(() => api.get<Job[]>("/imports/history").then(setJobs).catch(() => undefined), []);
  useEffect(() => { api.get<Kind[]>("/imports").then(setKinds).catch((e) => setErr(errorText(e))); loadJobs(); }, [loadJobs]);

  async function upload(file: File) {
    setErr(null); setDone(null); setPrev(null); setBusy(true);
    const form = new FormData();
    form.append("file", file);
    try {
      const r = await fetch(`/api/v1/imports/${kind}/preview`, { method: "POST", body: form });
      const data = await r.json();
      if (!r.ok) throw new Error(data?.error?.message ?? "Gagal membaca file");
      setPrev(data); setSkipErrors(false);
    } catch (e) { setErr(e instanceof Error ? e.message : errorText(e)); } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function commit() {
    if (!prev) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.post<{ result: Record<string, number>; skipped: number }>(`/imports/${prev.id}/commit`, { skip_errors: skipErrors });
      setDone(r); setPrev(null); loadJobs();
    } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  }

  const spec = kinds.find((k) => k.kind === kind);

  return (
    <>
      <PageHeader title="Impor data" desc="Pindahkan produk dan stok awal dari Excel atau sistem lama. File diperiksa dulu, baru dijalankan setelah Anda setuju." />
      {err && <Alert>{err}</Alert>}

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-concrete-dark bg-white p-5 shadow-card">
            <div className="grid gap-4 sm:grid-cols-[220px_1fr] sm:items-end">
              <div>
                <label htmlFor="kind" className="mb-1.5 block text-sm font-medium">Jenis data</label>
                <Select id="kind" value={kind} onChange={(e) => { setKind(e.target.value); setPrev(null); setDone(null); }}>
                  {kinds.map((k) => <option key={k.kind} value={k.kind} disabled={!k.allowed}>{k.label}{k.allowed ? "" : " — tidak ada izin"}</option>)}
                </Select>
              </div>
              <div className="flex flex-wrap gap-2">
                <a href={`/api/v1/imports/${kind}/template.csv`} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-concrete-line px-4 text-sm font-semibold hover:bg-concrete">
                  <Download size={16} aria-hidden="true" />Unduh contoh file
                </a>
                <Button onClick={() => fileRef.current?.click()} loading={busy} disabled={!spec?.allowed}>
                  <Upload size={16} aria-hidden="true" />Pilih file CSV
                </Button>
                <input ref={fileRef} type="file" accept=".csv,text/csv" className="sr-only" aria-label="Pilih file CSV"
                       onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
              </div>
            </div>
            {spec && (
              <p className="mt-3 text-xs text-ink-muted">
                Kolom wajib: <strong>{spec.required.join(", ")}</strong>
                {spec.optional.length > 0 && <> · opsional: {spec.optional.join(", ")}</>}. Pemisah koma maupun titik koma
                sama-sama diterima, maksimal 5.000 baris per file.
              </p>
            )}
          </div>

          {done && (
            <div className="rounded-xl border border-ok/30 bg-green-50 p-5">
              <div className="flex items-center gap-2 font-semibold text-ok"><CircleCheckBig size={20} aria-hidden="true" />Impor selesai</div>
              <ul className="mt-2 text-sm">
                {Object.entries(done.result).map(([k, v]) => (
                  <li key={k}>{k.replace(/_/g, " ")}: <strong>{v}</strong></li>))}
                {done.skipped > 0 && <li className="text-ink-soft">{done.skipped} baris bermasalah dilewati</li>}
              </ul>
            </div>
          )}

          {prev && (
            <div className="rounded-xl border border-concrete-dark bg-white shadow-card">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-concrete-dark p-4">
                <div><p className="font-bold">Hasil pemeriksaan — {prev.label}</p>
                  <p className="text-sm text-ink-muted">Belum ada data yang berubah.</p></div>
                <div className="flex gap-2"><Badge tone="ok">{prev.valid_rows} baris siap</Badge>
                  {prev.error_rows > 0 && <Badge tone="off">{prev.error_rows} bermasalah</Badge>}</div>
              </div>

              {prev.errors.length > 0 && (
                <div className="border-b border-concrete-dark p-4">
                  <div className="flex items-center gap-2 font-semibold text-danger"><TriangleAlert size={18} aria-hidden="true" />Baris yang perlu diperbaiki</div>
                  <ul className="mt-2 max-h-52 overflow-y-auto text-sm">
                    {prev.errors.map((e, i) => (
                      <li key={i} className="border-b border-concrete-dark py-1 last:border-0">
                        <span className="font-mono text-ink-muted">Baris {e.row}</span> — {e.message}</li>))}
                  </ul>
                  <label className="mt-3 flex min-h-10 items-center gap-2 text-sm">
                    <input type="checkbox" className="h-4 w-4 accent-ink" checked={skipErrors} onChange={(e) => setSkipErrors(e.target.checked)} />
                    Lewati baris bermasalah dan jalankan sisanya
                  </label>
                </div>
              )}

              {prev.sample.length > 0 && (
                <div className="overflow-x-auto border-b border-concrete-dark">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-concrete text-ink-muted"><tr>
                      {Object.keys(prev.sample[0]).filter((k) => !k.startsWith("_")).map((k) => (
                        <th key={k} className="px-3 py-2 font-medium">{k.replace(/_/g, " ")}</th>))}</tr></thead>
                    <tbody>{prev.sample.map((row, i) => (
                      <tr key={i} className="border-t border-concrete-dark">
                        {Object.entries(row).filter(([k]) => !k.startsWith("_")).map(([k, v]) => (
                          <td key={k} className="px-3 py-2">{v === null || v === "" ? "—" : String(v)}</td>))}</tr>))}</tbody>
                  </table>
                  <p className="px-3 py-2 text-xs text-ink-muted">Menampilkan {prev.sample.length} baris pertama dari {prev.valid_rows} baris yang siap.</p>
                </div>
              )}

              <div className="flex flex-wrap gap-2 p-4">
                <Button onClick={commit} loading={busy} disabled={prev.valid_rows === 0 || (prev.error_rows > 0 && !skipErrors)}>
                  Jalankan impor {prev.valid_rows} baris
                </Button>
                <Button variant="ghost" onClick={() => setPrev(null)}>Batal</Button>
              </div>
            </div>
          )}

          {!prev && !done && (
            <Empty title="Belum ada file yang diperiksa">
              Unduh contoh filenya, isi dengan data Anda di Excel, simpan sebagai CSV, lalu unggah di sini.
              Impor produk dulu, baru stok awalnya.
            </Empty>
          )}
        </div>

        <aside>
          <h2 className="mb-2 text-lg font-bold">Riwayat impor</h2>
          {jobs.length === 0 ? <p className="text-sm text-ink-muted">Belum ada.</p> : (
            <ul className="flex flex-col gap-2">{jobs.map((j) => (
              <li key={j.id} className="rounded-xl border border-concrete-dark bg-white p-3 shadow-card">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2 font-semibold"><FileSpreadsheet size={16} className="text-ink-muted" aria-hidden="true" />{j.label}</span>
                  <Badge tone={j.status === "COMMITTED" ? "ok" : "neutral"}>{j.status === "COMMITTED" ? "Selesai" : "Belum dijalankan"}</Badge>
                </div>
                <p className="mt-1 truncate text-xs text-ink-muted">{j.filename || "tanpa nama"} · {j.valid_rows}/{j.total_rows} baris · {dt(j.committed_at ?? j.created_at)}</p>
                {j.status === "COMMITTED" && <p className="text-xs text-ink-soft">{Object.entries(j.result).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ")}</p>}
              </li>))}</ul>)}
        </aside>
      </div>
    </>
  );
}
