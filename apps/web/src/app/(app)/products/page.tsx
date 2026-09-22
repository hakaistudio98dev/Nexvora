"use client";
import { useCallback, useEffect, useState } from "react";
import { useCan } from "@/components/me-context";
import { Alert, Badge, Button, Empty, Field, Input, Modal, PageHeader } from "@/components/ui";
import { api, errorText, qs } from "@/lib/api";
import type { Page, Product, ProductDetail, Sku } from "@/lib/types";

const LIMIT = 25;

export default function ProductsPage() {
  const canWrite = useCan("product:write");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<Page<Product> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.get<Page<Product>>(`/products${qs({ q, limit: LIMIT, offset })}`));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [q, offset]);

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  return (
    <>
      <PageHeader title="Produk" desc="Daftar produk dan variannya (SKU). Satu data untuk semua toko dan gudang."
        action={canWrite && <Button onClick={() => setCreateOpen(true)}>Tambah produk</Button>} />
      <div className="mb-4 max-w-sm">
        <label htmlFor="q" className="sr-only">Cari produk</label>
        <Input id="q" placeholder="Cari kode atau nama produk" value={q}
               onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
      </div>
      {error && <Alert>{error}</Alert>}

      <div className={`grid gap-6 ${selected ? "lg:grid-cols-[1fr_420px]" : ""}`}>
        <div>
          {data && data.items.length === 0 ? (
            <Empty title={q ? "Tidak ada produk yang cocok" : "Belum ada produk"}>
              {canWrite && !q && "Tambahkan produk pertama, lalu buat SKU untuk setiap variannya."}
            </Empty>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-concrete-dark bg-concrete">
                  <tr><th className="px-4 py-3">Kode</th><th className="px-4 py-3">Nama</th><th className="px-4 py-3">Status</th></tr>
                </thead>
                <tbody>
                  {data?.items.map((p) => (
                    <tr key={p.id} className={`border-b border-concrete-dark ${selected === p.id ? "bg-signal-soft" : ""}`}>
                      <td className="px-4 py-3 font-mono">
                        <button type="button" className="min-h-9 text-left font-semibold underline-offset-2 hover:underline"
                                onClick={() => setSelected(p.id)}>{p.code}</button>
                      </td>
                      <td className="px-4 py-3">{p.name}</td>
                      <td className="px-4 py-3">{p.is_active ? <Badge tone="ok">Aktif</Badge> : <Badge tone="off">Nonaktif</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data && data.total > LIMIT && (
            <div className="mt-3 flex items-center gap-3 text-sm">
              <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>Sebelumnya</Button>
              <span>{offset + 1}–{Math.min(offset + LIMIT, data.total)} dari {data.total}</span>
              <Button variant="secondary" disabled={offset + LIMIT >= data.total} onClick={() => setOffset(offset + LIMIT)}>Berikutnya</Button>
            </div>
          )}
        </div>
        {selected && <ProductPanel id={selected} canWrite={canWrite} onChanged={load} onClose={() => setSelected(null)} />}
      </div>

      <Modal open={createOpen} title="Tambah produk" onClose={() => setCreateOpen(false)}>
        <ProductForm onDone={(p) => { setCreateOpen(false); setSelected(p.id); load(); }} />
      </Modal>
    </>
  );
}

function ProductForm({ onDone }: { onDone: (p: Product) => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      onDone(await api.post<Product>("/products", { code: code.trim(), name: name.trim(), description }));
    } catch (x) {
      setErr(errorText(x));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {err && <Alert>{err}</Alert>}
      <Field label="Kode produk" hint="Huruf, angka, titik, strip. Unik di workspace Anda.">
        {(id) => <Input id={id} value={code} onChange={(e) => setCode(e.target.value)} required maxLength={64} />}
      </Field>
      <Field label="Nama produk">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required />}</Field>
      <Field label="Deskripsi (opsional)">{(id) => <Input id={id} value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>
      <Button type="submit" loading={busy}>Simpan produk</Button>
    </form>
  );
}

function ProductPanel({ id, canWrite, onChanged, onClose }: { id: string; canWrite: boolean; onChanged: () => void; onClose: () => void }) {
  const [p, setP] = useState<ProductDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [skuOpen, setSkuOpen] = useState(false);

  const load = useCallback(() => api.get<ProductDetail>(`/products/${id}`).then(setP).catch((e) => setErr(errorText(e))), [id]);
  useEffect(() => { setP(null); load(); }, [load]);

  async function toggleActive() {
    if (!p) return;
    try {
      await api.patch(`/products/${p.id}`, { is_active: !p.is_active });
      await load();
      onChanged();
    } catch (e) { setErr(errorText(e)); }
  }

  return (
    <aside className="h-fit rounded-xl border border-concrete-dark bg-white shadow-card" aria-label="Detail produk">
      <div className="flex items-start justify-between gap-3 border-b border-concrete-dark p-4">
        <div>
          <div className="font-mono text-sm text-ink-muted">{p?.code}</div>
          <h2 className="text-lg font-bold">{p?.name ?? "Memuat…"}</h2>
        </div>
        <Button variant="ghost" onClick={onClose} aria-label="Tutup detail">Tutup</Button>
      </div>
      <div className="flex flex-col gap-4 p-4">
        {err && <Alert>{err}</Alert>}
        {p?.description && <p className="text-sm text-ink-soft">{p.description}</p>}
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">SKU ({p?.skus.length ?? 0})</h3>
          {canWrite && <Button variant="secondary" onClick={() => setSkuOpen(true)}>Tambah SKU</Button>}
        </div>
        {p?.skus.length === 0 && <p className="text-sm text-ink-muted">Belum ada SKU. Setiap varian (ukuran, warna) menjadi satu SKU.</p>}
        <ul className="flex flex-col gap-2">
          {p?.skus.map((s) => <SkuRow key={s.id} s={s} />)}
        </ul>
        {canWrite && p && (
          <Button variant={p.is_active ? "danger" : "secondary"} onClick={toggleActive}>
            {p.is_active ? "Nonaktifkan produk" : "Aktifkan produk"}
          </Button>
        )}
      </div>
      <Modal open={skuOpen} title="Tambah SKU" onClose={() => setSkuOpen(false)}>
        <SkuForm productId={id} onDone={() => { setSkuOpen(false); load(); }} />
      </Modal>
    </aside>
  );
}

function SkuRow({ s }: { s: Sku }) {
  const dims = s.length_mm && s.width_mm && s.height_mm ? `${s.length_mm}×${s.width_mm}×${s.height_mm} mm` : null;
  return (
    <li className="rounded-lg border border-concrete-line p-3 text-sm">
      <div className="flex justify-between gap-2">
        <span className="font-mono font-semibold">{s.sku_code}</span>
        {!s.is_active && <Badge tone="off">Nonaktif</Badge>}
      </div>
      <div className="text-ink-soft">
        {[s.variant_name, s.unit, s.barcode && `barcode ${s.barcode}`, dims, s.weight_g && `${s.weight_g} g`].filter(Boolean).join(" · ")}
      </div>
    </li>
  );
}

function SkuForm({ productId, onDone }: { productId: string; onDone: () => void }) {
  const [f, setF] = useState({ sku_code: "", barcode: "", variant_name: "", unit: "PCS", length_mm: "", width_mm: "", height_mm: "", weight_g: "", reorder_point: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const num = (v: string) => (v.trim() === "" ? null : Number(v));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post<Sku>(`/products/${productId}/skus`, {
        sku_code: f.sku_code.trim(), barcode: f.barcode.trim() || null, variant_name: f.variant_name.trim(),
        unit: f.unit.trim().toUpperCase(), length_mm: num(f.length_mm), width_mm: num(f.width_mm),
        height_mm: num(f.height_mm), weight_g: num(f.weight_g), reorder_point: num(f.reorder_point),
      });
      onDone();
    } catch (x) { setErr(errorText(x)); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
      {err && <div className="sm:col-span-2"><Alert>{err}</Alert></div>}
      <Field label="Kode SKU">{(id) => <Input id={id} value={f.sku_code} onChange={set("sku_code")} required />}</Field>
      <Field label="Barcode (opsional)">{(id) => <Input id={id} value={f.barcode} onChange={set("barcode")} inputMode="numeric" />}</Field>
      <Field label="Varian">{(id) => <Input id={id} value={f.variant_name} onChange={set("variant_name")} placeholder="Hitam / M" />}</Field>
      <Field label="Unit">{(id) => <Input id={id} value={f.unit} onChange={set("unit")} />}</Field>
      <Field label="Panjang (mm)">{(id) => <Input id={id} type="number" min={1} value={f.length_mm} onChange={set("length_mm")} />}</Field>
      <Field label="Lebar (mm)">{(id) => <Input id={id} type="number" min={1} value={f.width_mm} onChange={set("width_mm")} />}</Field>
      <Field label="Tinggi (mm)">{(id) => <Input id={id} type="number" min={1} value={f.height_mm} onChange={set("height_mm")} />}</Field>
      <Field label="Berat (gram)">{(id) => <Input id={id} type="number" min={1} value={f.weight_g} onChange={set("weight_g")} />}</Field>
      <Field label="Batas stok menipis (opsional)" hint="Kosong = pakai default di Pengaturan">{(id) => <Input id={id} type="number" min={0} value={f.reorder_point} onChange={set("reorder_point")} />}</Field>
      <Button type="submit" loading={busy} className="sm:col-span-2">Simpan SKU</Button>
    </form>
  );
}
