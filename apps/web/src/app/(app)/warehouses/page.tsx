"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useCan } from "@/components/me-context";
import { Alert, Badge, Button, Empty, Field, Input, Modal, PageHeader } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import type { Location, LocationType, Warehouse } from "@/lib/types";

const CHILD: Record<LocationType | "ROOT", LocationType | null> = { ROOT: "ZONE", ZONE: "RACK", RACK: "SHELF", SHELF: "BIN", BIN: null };
const LABEL: Record<LocationType, string> = { ZONE: "Zona", RACK: "Rak", SHELF: "Shelf", BIN: "Bin" };

export default function WarehousesPage() {
  const canWrite = useCan("warehouse:write");
  const [list, setList] = useState<Warehouse[]>([]);
  const [sel, setSel] = useState<Warehouse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const w = await api.get<Warehouse[]>("/warehouses");
      setList(w);
      setSel((cur) => (cur ? w.find((x) => x.id === cur.id) ?? null : w[0] ?? null));
    } catch (e) { setErr(errorText(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <>
      <PageHeader title="Lokasi & rak" desc="Susun gudang Anda: area → rak → tingkat → bin, supaya setiap barang punya alamat."
        action={canWrite && <Button onClick={() => setCreateOpen(true)}>Tambah gudang</Button>} />
      {err && <Alert>{err}</Alert>}
      {list.length === 0 ? (
        <Empty title="Belum ada gudang">{canWrite && "Tambahkan gudang pertama untuk mulai menyusun lokasi penyimpanan."}</Empty>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
          <ul className="flex flex-col gap-2" aria-label="Daftar gudang">
            {list.map((w) => (
              <li key={w.id}>
                <button type="button" onClick={() => setSel(w)} aria-pressed={sel?.id === w.id}
                  className={`w-full rounded-lg border p-3 text-left ${sel?.id === w.id ? "border-ink/30 bg-signal-soft" : "border-concrete-line bg-white hover:border-ink"}`}>
                  <div className="font-mono text-sm">{w.code}</div>
                  <div className="font-semibold">{w.name}</div>
                  {w.city && <div className="text-sm text-ink-soft">{w.city}</div>}
                </button>
              </li>
            ))}
          </ul>
          {sel && <LocationTree warehouse={sel} canWrite={canWrite} />}
        </div>
      )}
      <Modal open={createOpen} title="Tambah gudang" onClose={() => setCreateOpen(false)}>
        <WarehouseForm onDone={() => { setCreateOpen(false); load(); }} />
      </Modal>
    </>
  );
}

function WarehouseForm({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ code: "", name: "", city: "", address: "", postal_code: "", phone: "", contact_name: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/warehouses", { ...f, code: f.code.trim().toUpperCase() });
      onDone();
    } catch (x) { setErr(errorText(x)); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {err && <Alert>{err}</Alert>}
      <Field label="Kode gudang" hint="Huruf besar, angka, strip. Contoh: JKT-01">
        {(id) => <Input id={id} value={f.code} onChange={set("code")} required />}
      </Field>
      <Field label="Nama gudang">{(id) => <Input id={id} value={f.name} onChange={set("name")} required />}</Field>
      <Field label="Kota">{(id) => <Input id={id} value={f.city} onChange={set("city")} />}</Field>
      <Field label="Alamat">{(id) => <Input id={id} value={f.address} onChange={set("address")} />}</Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Kode pos">{(id) => <Input id={id} value={f.postal_code} onChange={set("postal_code")} inputMode="numeric" />}</Field>
        <Field label="No. telepon">{(id) => <Input id={id} value={f.phone} onChange={set("phone")} inputMode="tel" />}</Field>
      </div>
      <Field label="Nama pengirim di label" hint="Mis. nama brand atau PIC gudang">{(id) => <Input id={id} value={f.contact_name} onChange={set("contact_name")} />}</Field>
      <Button type="submit" loading={busy}>Simpan gudang</Button>
    </form>
  );
}

type Node = Location & { children: Node[] };

function LocationTree({ warehouse, canWrite }: { warehouse: Warehouse; canWrite: boolean }) {
  const [locs, setLocs] = useState<Location[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState<{ parent: Location | null; type: LocationType } | null>(null);

  const load = useCallback(() => api.get<Location[]>(`/warehouses/${warehouse.id}/locations`).then(setLocs)
    .catch((e) => setErr(errorText(e))), [warehouse.id]);
  useEffect(() => { load(); }, [load]);

  const tree = useMemo(() => {
    const map = new Map<string, Node>(locs.map((l) => [l.id, { ...l, children: [] }]));
    const roots: Node[] = [];
    map.forEach((n) => (n.parent_id ? map.get(n.parent_id)?.children.push(n) : roots.push(n)));
    return roots;
  }, [locs]);

  const bins = locs.filter((l) => l.type === "BIN").length;

  return (
    <section className="rounded-xl border border-concrete-dark bg-white shadow-card" aria-label={`Lokasi ${warehouse.name}`}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-concrete-dark p-4">
        <div>
          <h2 className="text-lg font-bold">{warehouse.name}</h2>
          <p className="text-sm text-ink-soft">{locs.length} lokasi · {bins} bin</p>
        </div>
        {canWrite && <Button variant="secondary" onClick={() => setAdding({ parent: null, type: "ZONE" })}>Tambah zona</Button>}
      </div>
      <div className="p-4">
        {err && <Alert>{err}</Alert>}
        {tree.length === 0 ? <p className="text-sm text-ink-muted">Mulai dengan membuat zona, misalnya A untuk area fast-moving.</p> : (
          <ul className="flex flex-col gap-1">{tree.map((n) => <TreeNode key={n.id} n={n} depth={0} canWrite={canWrite} onAdd={setAdding} />)}</ul>
        )}
      </div>
      <Modal open={!!adding} title={adding ? `Tambah ${LABEL[adding.type].toLowerCase()}${adding.parent ? ` di ${adding.parent.full_code}` : ""}` : ""}
             onClose={() => setAdding(null)}>
        {adding && <LocationForm warehouseId={warehouse.id} parent={adding.parent} type={adding.type}
                                 onDone={() => { setAdding(null); load(); }} />}
      </Modal>
    </section>
  );
}

function TreeNode({ n, depth, canWrite, onAdd }: { n: Node; depth: number; canWrite: boolean;
  onAdd: (a: { parent: Location; type: LocationType }) => void }) {
  const [open, setOpen] = useState(depth < 1);
  const child = CHILD[n.type];
  return (
    <li>
      <div className="flex min-h-11 items-center gap-2 rounded px-2 hover:bg-concrete" style={{ paddingLeft: depth * 20 + 8 }}>
        {n.children.length > 0 ? (
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`${open ? "Tutup" : "Buka"} ${n.full_code}`}
                  className="grid h-8 w-8 place-items-center rounded hover:bg-concrete-dark">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"
                 className={open ? "rotate-90" : ""}><path d="M4 2l4 4-4 4" /></svg>
          </button>
        ) : <span className="w-8" />}
        <Badge tone={n.type === "BIN" ? "signal" : "neutral"}>{LABEL[n.type]}</Badge>
        <span className="font-mono text-sm font-semibold">{n.full_code}</span>
        {!n.is_active && <Badge tone="off">Nonaktif</Badge>}
        {canWrite && child && (
          <button type="button" onClick={() => onAdd({ parent: n, type: child })}
                  className="ml-auto min-h-9 rounded px-2 text-sm font-semibold text-[#1F5BD8] hover:underline">
            Tambah {LABEL[child].toLowerCase()}
          </button>
        )}
      </div>
      {open && n.children.length > 0 && (
        <ul>{n.children.map((c) => <TreeNode key={c.id} n={c} depth={depth + 1} canWrite={canWrite} onAdd={onAdd} />)}</ul>
      )}
    </li>
  );
}

function LocationForm({ warehouseId, parent, type, onDone }: { warehouseId: string; parent: Location | null;
  type: LocationType; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post(`/warehouses/${warehouseId}/locations`, { type, code: code.trim().toUpperCase(), parent_id: parent?.id ?? null });
      onDone();
    } catch (x) { setErr(errorText(x)); } finally { setBusy(false); }
  }
  const preview = code ? (parent ? `${parent.full_code}-${code.toUpperCase()}` : code.toUpperCase()) : "—";
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {err && <Alert>{err}</Alert>}
      <Field label={`Kode ${LABEL[type].toLowerCase()}`} hint="Huruf besar dan angka saja">
        {(id) => <Input id={id} value={code} onChange={(e) => setCode(e.target.value.replace(/[^A-Za-z0-9]/g, ""))} required maxLength={32} />}
      </Field>
      <p className="text-sm">Kode lengkap: <span className="font-mono font-semibold">{preview}</span></p>
      <Button type="submit" loading={busy}>Simpan</Button>
    </form>
  );
}
