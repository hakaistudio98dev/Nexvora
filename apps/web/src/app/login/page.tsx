"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Alert, Button, Field, Input } from "@/components/ui";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [tenant, setTenant] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tenant: tenant.trim().toLowerCase(), email: email.trim(), password }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => null);
        setError(r.status === 429 ? "Terlalu banyak percobaan. Tunggu satu menit lalu coba lagi."
          : d?.error?.message ?? "Login gagal. Periksa data Anda.");
        return;
      }
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard");
      router.refresh();
    } catch {
      setError("Tidak bisa terhubung ke server.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
      {error && <Alert>{error}</Alert>}
      <Field label="Kode workspace" hint="Didapat saat mendaftar, mis. toko-anda">
        {(id) => <Input id={id} value={tenant} onChange={(e) => setTenant(e.target.value)} autoComplete="organization" required />}
      </Field>
      <Field label="Email">
        {(id) => <Input id={id} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />}
      </Field>
      <Field label="Password">
        {(id) => <Input id={id} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />}
      </Field>
      <Button type="submit" loading={loading} className="mt-2 min-h-12 text-base">Masuk</Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <div className="w-full max-w-md rounded-2xl border border-concrete-dark bg-white shadow-pop">
        <div className="flex items-center justify-between border-b border-concrete-dark px-6 py-4">
          <span className="font-brand text-xl font-black [font-stretch:125%]">NEXVORA</span>
          
        </div>
        <div className="p-6">
          <h1 className="mb-1 text-2xl font-bold">Masuk ke workspace</h1>
          <p className="mb-6 text-sm text-ink-soft">Kelola order, stok, gudang, dan pengiriman di satu tempat.</p>
          <Suspense fallback={null}><LoginForm /></Suspense>
          <p className="mt-6 border-t border-concrete-dark pt-4 text-center text-sm">
            Belum punya workspace? <a href="/signup" className="font-semibold">Coba gratis 14 hari</a>
          </p>
        </div>
      </div>
    </main>
  );
}
