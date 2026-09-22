"use client";
import Link from "next/link";

export function UpgradeCard({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-concrete-line bg-signal-soft p-5">
      <p className="text-lg font-bold">{title}</p>
      <p className="mt-1 text-sm text-ink-soft">{children ?? "Tersedia mulai paket Growth."}</p>
      <Link href="/billing" className="mt-3 inline-flex min-h-10 items-center rounded-md bg-ink px-4 text-sm font-semibold text-white">Lihat paket</Link>
    </div>
  );
}
