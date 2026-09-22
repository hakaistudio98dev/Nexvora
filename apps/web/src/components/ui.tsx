"use client";
import { useEffect, useId, useRef } from "react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-ink text-white hover:bg-ink-soft disabled:bg-ink-muted shadow-card",
  secondary: "border border-concrete-line bg-white text-ink hover:bg-concrete hover:border-ink-muted",
  ghost: "text-ink-soft hover:bg-concrete hover:text-ink",
  danger: "border border-danger/40 bg-white text-danger hover:bg-red-50",
};

export function Button({ variant = "primary", className = "", loading, children, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${variants[variant]} ${className}`}
    >
      {loading && <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent" aria-hidden="true" />}
      {loading ? "Menyimpan…" : children}
    </button>
  );
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string;
  children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-ink">{label}</label>
      {children(id)}
      {hint && !error && <p className="text-xs text-ink-muted">{hint}</p>}
      {error && <p className="text-xs font-semibold text-danger">{error}</p>}
    </div>
  );
}

const inputCls = "min-h-10 w-full rounded-lg border border-concrete-line bg-white px-3 text-[15px] text-ink placeholder:text-ink-muted/70 transition-colors hover:border-ink-muted focus:border-ink disabled:bg-concrete disabled:text-ink-muted";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputCls} ${props.className ?? ""}`} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${inputCls} pr-8 ${props.className ?? ""}`} />;
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "off" | "signal"; children: ReactNode }) {
  const t = {
    neutral: "bg-concrete-dark/70 text-ink-soft",
    ok: "bg-green-50 text-ok ring-1 ring-inset ring-ok/20",
    off: "bg-red-50 text-danger ring-1 ring-inset ring-danger/20",
    signal: "bg-signal-soft text-[#6B5500] ring-1 ring-inset ring-signal-dark/25",
  }[tone];
  return <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold ${t}`}>{children}</span>;
}

export function Alert({ children, tone = "error" }: { children: ReactNode; tone?: "error" | "info" | "ok" }) {
  const t = { error: "border-danger/30 bg-red-50 text-danger", info: "border-concrete-line bg-white text-ink-soft", ok: "border-ok/30 bg-green-50 text-ok" }[tone];
  return <div role={tone === "error" ? "alert" : "status"} className={`rounded-lg border px-3.5 py-2.5 text-sm font-medium ${t}`}>{children}</div>;
}

export function PageHeader({ title, desc, action }: { title: string; desc?: string; action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-tight text-ink">{title}</h1>
        {desc && <p className="mt-1 max-w-2xl text-[15px] text-ink-muted">{desc}</p>}
      </div>
      {action}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-concrete-line bg-white px-6 py-10 text-center">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#9AA6B2" strokeWidth="1.5" aria-hidden="true"><path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z" /><path d="M3 7.5 12 12l9-4.5M12 12v9" /></svg>
      <p className="mt-3 font-semibold text-ink">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-ink-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-concrete-dark bg-white shadow-card ${className}`}>{children}</div>;
}

export function Modal({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void;
  children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="w-[min(580px,calc(100vw-24px))] rounded-2xl border border-concrete-dark p-0 shadow-pop backdrop:bg-ink/40 backdrop:backdrop-blur-[2px]"
    >
      <div className="flex items-center justify-between border-b border-concrete-dark px-5 py-3.5">
        <h2 className="text-lg font-bold text-ink">{title}</h2>
        <button type="button" onClick={onClose} aria-label="Tutup" className="grid min-h-10 min-w-10 place-items-center rounded-lg text-ink-muted hover:bg-concrete hover:text-ink">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 4l10 10M14 4 4 14" /></svg>
        </button>
      </div>
      <div className="p-5">{open && children}</div>
    </dialog>
  );
}
