"use client";
import { useEffect, useRef, useState } from "react";

/**
 * Input scan: bekerja dengan scanner genggam (mode keyboard, diakhiri Enter) dan kamera ponsel.
 */
export function ScanInput({ label, placeholder, onScan, autoFocus = true, disabled }: {
  label: string; placeholder?: string; onScan: (code: string) => void; autoFocus?: boolean; disabled?: boolean;
}) {
  const [value, setValue] = useState("");
  const [camera, setCamera] = useState(false);
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => { if (autoFocus && !disabled) ref.current?.focus(); }, [autoFocus, disabled, label]);

  function submit(code: string) {
    const c = code.trim();
    if (!c) return;
    setValue("");
    onScan(c);
    setTimeout(() => ref.current?.focus(), 0);
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="scan-input" className="text-sm font-semibold">{label}</label>
      <div className="flex gap-2">
        <input
          ref={ref} id="scan-input" value={value} disabled={disabled} placeholder={placeholder ?? "Scan atau ketik lalu Enter"}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submit(value); } }}
          autoComplete="off" autoCapitalize="characters" spellCheck={false} enterKeyHint="go"
          className="min-h-14 w-full rounded border-[3px] border-ink bg-white px-3 font-mono text-lg"
        />
        <button type="button" onClick={() => setCamera(true)} disabled={disabled} aria-label="Scan dengan kamera"
                className="grid min-h-14 min-w-14 place-items-center rounded border-[3px] border-ink bg-signal">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10" />
          </svg>
        </button>
      </div>
      {camera && <CameraScanner onClose={() => setCamera(false)} onResult={(c) => { setCamera(false); submit(c); }} />}
    </div>
  );
}

function CameraScanner({ onResult, onClose }: { onResult: (code: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let stop: (() => void) | null = null;
    let done = false;
    (async () => {
      try {
        const { BrowserMultiFormatReader } = await import("@zxing/browser");
        const reader = new BrowserMultiFormatReader();
        const controls = await reader.decodeFromVideoDevice(undefined, video.current!, (result) => {
          if (result && !done) { done = true; onResult(result.getText()); }
        });
        stop = () => controls.stop();
        if (done) stop();
      } catch {
        setErr("Kamera tidak bisa dibuka. Izinkan akses kamera, dan pastikan situs dibuka lewat HTTPS.");
      }
    })();
    return () => { done = true; stop?.(); };
  }, [onResult]);

  return (
    <div role="dialog" aria-modal="true" aria-label="Scan kamera" className="fixed inset-0 z-50 flex flex-col bg-black">
      <video ref={video} className="h-full w-full flex-1 object-cover" muted playsInline />
      <div className="pointer-events-none absolute inset-x-8 top-1/3 h-1/4 rounded border-4 border-signal" aria-hidden="true" />
      {err && <p className="absolute inset-x-4 top-4 rounded bg-white p-3 text-sm font-semibold text-danger">{err}</p>}
      <button type="button" onClick={onClose} className="m-4 min-h-14 rounded bg-white text-lg font-bold">Tutup kamera</button>
    </div>
  );
}

export function Flash({ tone, children }: { tone: "ok" | "err" | "info"; children: React.ReactNode }) {
  const cls = { ok: "border-ok bg-green-50 text-ok", err: "border-danger bg-red-50 text-danger", info: "border-ink bg-white text-ink" }[tone];
  return <div role={tone === "err" ? "alert" : "status"} className={`rounded border-[3px] px-4 py-3 text-base font-semibold ${cls}`}>{children}</div>;
}

export function Stepper({ value, onChange, max, label = "Jumlah" }: { value: number; onChange: (n: number) => void; max?: number; label?: string }) {
  return (
    <div className="flex items-center gap-2" role="group" aria-label={label}>
      <button type="button" aria-label="Kurangi" onClick={() => onChange(Math.max(1, value - 1))} className="min-h-14 min-w-14 rounded border-[3px] border-ink bg-white text-2xl font-bold">−</button>
      <input type="number" inputMode="numeric" min={1} max={max} value={value} aria-label={label}
             onChange={(e) => onChange(Math.max(1, Math.min(max ?? 1e9, Number(e.target.value) || 1)))}
             className="min-h-14 w-24 rounded border-[3px] border-ink text-center text-2xl font-bold tabular-nums" />
      <button type="button" aria-label="Tambah" onClick={() => onChange(Math.min(max ?? 1e9, value + 1))} className="min-h-14 min-w-14 rounded border-[3px] border-ink bg-white text-2xl font-bold">+</button>
      {max !== undefined && <span className="text-sm text-ink-muted">maks {max}</span>}
    </div>
  );
}
