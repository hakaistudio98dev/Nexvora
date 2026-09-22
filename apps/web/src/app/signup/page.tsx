"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, Button, Field, Input } from "@/components/ui";
import { ApiError, api, errorText, rupiah } from "@/lib/api";
import { FEATURE_LABEL } from "@/lib/saas";
import type { Plan } from "@/lib/types";

export default function SignupPage() {
  const router = useRouter();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [plan, setPlan] = useState("growth");
  const [f, setF] = useState({ company_name: "", slug: "", full_name: "", email: "", password: "" });
  const [slugTouched, setSlugTouched] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<Plan[]>("/public/plans").then((p) => setPlans(p.filter((x) => x.self_serve))).catch(() => undefined); }, []);

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    if (k === "company_name" && !slugTouched) {
      setF({ ...f, company_name: v, slug: v.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) });
    } else setF({ ...f, [k]: k === "slug" ? v.toLowerCase() : v });
    if (k === "slug") setSlugTouched(true);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setErr(null); setBusy(true);
    try {
      await api.post("/public/signup", { ...f, plan_code: plan });
      const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tenant: f.slug, email: f.email, password: f.password }) });
      router.replace(r.ok ? "/dashboard" : "/login");
    } catch (x) {
      setErr(x instanceof ApiError && x.status === 429 ? "Terlalu banyak pendaftaran dari jaringan ini. Coba lagi nanti." : errorText(x));
    } finally { setBusy(false); }
  }

  return (
    <main className="mx-auto grid min-h-screen max-w-5xl items-center gap-8 p-6 lg:grid-cols-[1fr_440px]">
      <section>
        <span className="font-brand text-xl font-black [font-stretch:125%]">NEXVORA</span>
        <h1 className="mt-4 text-4xl font-bold leading-tight tracking-tight">Coba gratis 14 hari, tanpa kartu kredit.</h1>
        <p className="mt-3 max-w-md text-ink-soft">Pilih paket yang ingin dicoba. Setelah masa coba, lanjutkan dengan membayar dari menu Langganan; data Anda tetap utuh.</p>
        <div role="radiogroup" aria-label="Paket" className="mt-6 grid gap-3 sm:grid-cols-2">
          {plans.map((p) => (
            <button key={p.code} type="button" role="radio" aria-checked={plan === p.code} onClick={() => setPlan(p.code)}
                    className={`rounded-lg border-[3px] p-4 text-left ${plan === p.code ? "border-ink/30 bg-signal-soft" : "border-concrete-line bg-white hover:border-ink"}`}>
              <div className="text-lg font-bold">{p.name}</div>
              <div className="text-sm">{Number(p.price_monthly) > 0 ? `${rupiah(p.price_monthly)} / bulan setelah trial` : "Harga kontrak"}</div>
              <ul className="mt-2 text-sm text-ink-soft">{p.features.map((x) => <li key={x}>✓ {FEATURE_LABEL[x] ?? x}</li>)}</ul>
            </button>
          ))}
        </div>
      </section>
      <form onSubmit={submit} className="flex flex-col gap-4 rounded-2xl border border-concrete-dark bg-white p-6 shadow-pop">
        <h2 className="text-2xl font-bold">Buat workspace</h2>
        {err && <Alert>{err}</Alert>}
        <Field label="Nama brand / perusahaan">{(id) => <Input id={id} value={f.company_name} onChange={set("company_name")} required autoComplete="organization" />}</Field>
        <Field label="Kode workspace" hint="Dipakai saat login. Huruf kecil, angka, strip.">{(id) => <Input id={id} value={f.slug} onChange={set("slug")} required pattern="[a-z0-9][a-z0-9-]{2,40}" />}</Field>
        <Field label="Nama Anda">{(id) => <Input id={id} value={f.full_name} onChange={set("full_name")} required autoComplete="name" />}</Field>
        <Field label="Email kerja">{(id) => <Input id={id} type="email" value={f.email} onChange={set("email")} required autoComplete="email" />}</Field>
        <Field label="Password" hint="Minimal 12 karakter, gabungan huruf besar, kecil, angka/simbol">{(id) => <Input id={id} type="password" value={f.password} onChange={set("password")} required autoComplete="new-password" />}</Field>
        <Button type="submit" loading={busy} className="min-h-12 text-base">Mulai masa coba</Button>
        <p className="text-center text-sm">Sudah punya workspace? <a href="/login" className="font-semibold">Masuk</a></p>
      </form>
    </main>
  );
}
