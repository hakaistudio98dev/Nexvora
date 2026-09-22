"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useCan } from "@/components/me-context";
import { ShipmentPanel } from "@/components/shipment-panel";
import { Alert, Badge, Button, Empty, Field, Input, Modal, PageHeader, Select } from "@/components/ui";
import { api, dt, errorText, qs, rupiah } from "@/lib/api";
import { CHANNELS, FLOW, STATUS_LABEL, STOCK_LABEL, statusTone } from "@/lib/labels";
import type { OrderDetail, OrderStats, OrderStatus, OrderSummary, Page, Sku } from "@/lib/types";

const LIMIT = 25;
const FILTERS: { key: string; label: string; status?: OrderStatus; stock?: string }[] = [
  { key: "all", label: "Semua" },
  { key: "oos", label: "Stok kurang", stock: "OUT_OF_STOCK" },
  ...(["CREATED", "PAID", "ALLOCATED", "PICKING", "PACKING", "READY_TO_SHIP", "SHIPPED", "DELIVERED", "CANCELLED"] as OrderStatus[])
    .map((s) => ({ key: s, label: STATUS_LABEL[s], status: s })),
];

export default function OrdersPage() {
  const canWrite = useCan("order:write");
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<Page<OrderSummary> | null>(null);
  const [stats, setStats] = useState<OrderStats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const f = FILTERS.find((x) => x.key === filter)!;
  const load = useCallback(async () => {
    try {
      const [page, st] = await Promise.all([
        api.get<Page<OrderSummary>>(`/orders${qs({ status: f.status, stock_status: f.stock, q, limit: LIMIT, offset })}`),
        api.get<OrderStats>("/orders/stats"),
      ]);
      setData(page); setStats(st); setErr(null);
    } catch (e) { setErr(errorText(e)); }
  }, [f.status, f.stock, q, offset]);

  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [load]);

  const count = (key: string) => {
    if (!stats) return null;
    if (key === "all") return Object.values(stats.by_status).reduce((a, b) => a + (b ?? 0), 0);
    if (key === "oos") return stats.out_of_stock;
    return stats.by_status[key as OrderStatus] ?? 0;
  };

  return (
    <>
      <PageHeader title="Order" desc="Semua order dari setiap channel dalam satu antrean, lengkap dengan status stok dan gudangnya."
        action={canWrite && <Button onClick={() => setCreateOpen(true)}>Buat order</Button>} />

      <div role="group" aria-label="Filter status" className="mb-4 flex gap-2 overflow-x-auto pb-1">
        {FILTERS.filter((x) => x.key === "all" || x.key === filter || (count(x.key) ?? 0) > 0).map((x) => {
          const n = count(x.key);
          const on = filter === x.key;
          return (
            <button key={x.key} type="button" aria-pressed={on} onClick={() => { setFilter(x.key); setOffset(0); }}
              className={`inline-flex min-h-10 shrink-0 items-center gap-2 rounded-full border px-3 text-sm font-semibold ${on ? "border-ink bg-ink text-white" : x.key === "oos" && n ? "border-danger bg-white text-danger" : "border-concrete-line bg-white text-ink hover:border-ink"}`}>
              {x.label}{n !== null && <span className="tabular-nums opacity-70">{n}</span>}
            </button>
          );
        })}
      </div>

      <div className="mb-4 max-w-sm">
        <label htmlFor="q" className="sr-only">Cari order</label>
        <Input id="q" placeholder="Cari no. order, ref marketplace, atau pelanggan" value={q}
               onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
      </div>
      {err && <Alert>{err}</Alert>}

      <div className={`grid gap-6 ${selected ? "xl:grid-cols-[1fr_480px]" : ""}`}>
        <div>
          {data && data.items.length === 0 ? (
            <Empty title="Tidak ada order di sini">{canWrite && filter === "all" && !q && "Buat order manual, atau hubungkan channel lewat API POST /api/v1/orders."}</Empty>
          ) : (
            <>
            {/* Ponsel: daftar kartu, lebih mudah dibaca daripada tabel yang digeser */}
            <ul className="flex flex-col gap-2 md:hidden">
              {data?.items.map((o) => (
                <li key={o.id}><button type="button" onClick={() => setSelected(o.id)} className={`w-full rounded-xl border bg-white p-3 text-left shadow-card ${selected === o.id ? "border-ink/40" : "border-concrete-dark"}`}>
                  <div className="flex items-center justify-between gap-2"><span className="font-mono font-semibold">{o.order_number}</span><Badge tone={statusTone(o.status)}>{STATUS_LABEL[o.status]}</Badge></div>
                  <div className="mt-1 flex items-center justify-between gap-2 text-sm"><span>{o.customer_name} · {o.ship_city}</span><span className="font-semibold tabular-nums">{rupiah(o.total)}</span></div>
                  {o.stock_status === "OUT_OF_STOCK" && <div className="mt-1 text-xs font-semibold text-danger">Stok kurang</div>}
                </button></li>
              ))}
            </ul>
            <div className="hidden overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card md:block">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead className="border-b border-concrete-dark bg-concrete">
                  <tr><th className="px-3 py-3">Order</th><th className="px-3 py-3">Pelanggan</th><th className="px-3 py-3 text-right">Total</th><th className="px-3 py-3">Status</th><th className="px-3 py-3">Stok</th><th className="px-3 py-3">Masuk</th></tr>
                </thead>
                <tbody>
                  {data?.items.map((o) => (
                    <tr key={o.id} className={`border-b border-concrete-dark ${selected === o.id ? "bg-signal-soft" : ""}`}>
                      <td className="px-3 py-2.5">
                        <button type="button" onClick={() => setSelected(o.id)} className="min-h-9 whitespace-nowrap text-left font-mono font-semibold hover:underline">{o.order_number}</button>
                        <div className="text-xs text-ink-muted">{o.channel}{o.external_ref ? ` · ${o.external_ref}` : ""}</div>
                      </td>
                      <td className="px-3 py-2.5">{o.customer_name}<div className="text-xs text-ink-muted">{o.ship_city}</div></td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{rupiah(o.total)}</td>
                      <td className="px-3 py-2.5"><Badge tone={statusTone(o.status)}>{STATUS_LABEL[o.status]}</Badge></td>
                      <td className="px-3 py-2.5">
                        {o.stock_status === "OUT_OF_STOCK" ? <span className="text-xs font-bold text-danger">Stok kurang</span>
                          : <span className="text-xs text-ink-soft">{STOCK_LABEL[o.stock_status]}{o.warehouse_code ? ` · ${o.warehouse_code}` : ""}</span>}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-ink-soft">{dt(o.placed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            </>
          )}
          {data && data.total > LIMIT && (
            <div className="mt-3 flex items-center gap-3 text-sm">
              <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>Sebelumnya</Button>
              <span>{offset + 1}–{Math.min(offset + LIMIT, data.total)} dari {data.total}</span>
              <Button variant="secondary" disabled={offset + LIMIT >= data.total} onClick={() => setOffset(offset + LIMIT)}>Berikutnya</Button>
            </div>
          )}
        </div>
        {selected && <OrderPanel id={selected} onChanged={load} onClose={() => setSelected(null)} />}
      </div>

      <Modal open={createOpen} title="Buat order" onClose={() => setCreateOpen(false)}>
        <CreateOrder onDone={(o) => { setCreateOpen(false); setSelected(o.id); load(); }} />
      </Modal>
    </>
  );
}

function OrderPanel({ id, onChanged, onClose }: { id: string; onChanged: () => void; onClose: () => void }) {
  const [o, setO] = useState<OrderDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reasonFor, setReasonFor] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const load = useCallback(() => api.get<OrderDetail>(`/orders/${id}`).then(setO).catch((e) => setErr(errorText(e))), [id]);
  useEffect(() => { setO(null); setErr(null); load(); }, [load]);

  async function run(action: string, withReason?: string) {
    setBusy(action); setErr(null);
    try {
      setO(await api.post<OrderDetail>(`/orders/${id}/actions/${action}`, withReason ? { reason: withReason } : {}));
      setReasonFor(null); setReason(""); onChanged();
    } catch (e) { setErr(errorText(e)); } finally { setBusy(null); }
  }

  const step = o ? FLOW.indexOf(o.status) : -1;
  const primary = o?.actions.filter((a) => !a.needs_reason) ?? [];
  const destructive = o?.actions.filter((a) => a.needs_reason) ?? [];

  return (
    <aside className="h-fit rounded-xl border border-concrete-dark bg-white shadow-card" aria-label="Detail order">
      <div className="flex items-start justify-between gap-3 border-b border-concrete-dark p-4">
        <div>
          <div className="font-mono text-lg font-bold">{o?.order_number ?? "Memuat…"}</div>
          {o && <div className="mt-1 flex flex-wrap gap-1.5"><Badge tone={statusTone(o.status)}>{STATUS_LABEL[o.status]}</Badge><Badge>{o.channel}</Badge>{o.payment_status === "PAID" && <Badge tone="ok">Lunas</Badge>}</div>}
        </div>
        <Button variant="ghost" onClick={onClose}>Tutup</Button>
      </div>
      {o && (
        <div className="flex flex-col gap-5 p-4 text-sm">
          {err && <Alert>{err}</Alert>}

          {step >= 0 && (
            <ol className="grid grid-cols-8 gap-1" aria-label="Progres order">
              {FLOW.map((s, i) => (
                <li key={s} title={STATUS_LABEL[s]} className={`h-2 rounded-full ${i <= step ? "bg-ink" : "bg-concrete-dark"} ${i === step ? "!bg-signal ring-2 ring-ink" : ""}`}>
                  <span className="sr-only">{STATUS_LABEL[s]}{i === step ? " (saat ini)" : ""}</span>
                </li>
              ))}
            </ol>
          )}

          {o.stock_status === "OUT_OF_STOCK" && (
            <div role="status" className="rounded-lg border border-danger bg-red-50 p-3">
              <div className="font-semibold text-danger">Stok belum cukup</div>
              <div className="text-ink-soft">{o.allocation_note} Terima stok di menu Inventory, lalu coba reservasi lagi.</div>
            </div>
          )}
          {o.stock_status === "RESERVED" && o.allocation_note && <p className="text-ink-soft">{o.allocation_note}</p>}
          {o.status_reason && ["CANCELLED", "FAILED"].includes(o.status) && <p className="text-ink-soft">Alasan: {o.status_reason}</p>}
          {["PICKING", "PACKING"].includes(o.status) && (
            <p className="rounded-lg border border-concrete-line bg-concrete p-3 text-ink-soft">
              {o.status === "PICKING" ? "Task picking sudah dibuat di gudang. " : ""}Setelah picking selesai, order diverifikasi
              di <a href="/scan/pack" className="font-semibold">packing station</a> (scan item + timbang) agar berstatus Siap kirim.
            </p>
          )}

          {(primary.length > 0 || destructive.length > 0) && (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap gap-2">
                {primary.map((a, i) => (
                  <Button key={a.name} variant={i === 0 ? "primary" : "secondary"} loading={busy === a.name} onClick={() => run(a.name)}>{a.label}</Button>
                ))}
                {destructive.map((a) => (
                  <Button key={a.name} variant="danger" onClick={() => { setReasonFor(a.name); setReason(""); }}>{a.label}</Button>
                ))}
              </div>
              {reasonFor && (
                <form className="flex flex-col gap-2 rounded-lg border border-concrete-line p-3" onSubmit={(e) => { e.preventDefault(); run(reasonFor, reason.trim()); }}>
                  <Field label="Alasan">{(fid) => <Input id={fid} value={reason} onChange={(e) => setReason(e.target.value)} required maxLength={300} autoFocus />}</Field>
                  <div className="flex gap-2">
                    <Button type="submit" variant="danger" loading={busy === reasonFor} disabled={!reason.trim()}>Konfirmasi</Button>
                    <Button type="button" variant="ghost" onClick={() => setReasonFor(null)}>Batal</Button>
                  </div>
                </form>
              )}
            </div>
          )}

          <section>
            <h3 className="mb-2 font-semibold">Item</h3>
            <ul className="divide-y divide-concrete-dark rounded-lg border border-concrete-line">
              {o.items.map((it) => (
                <li key={it.id} className="flex justify-between gap-3 px-3 py-2">
                  <div><div className="font-mono font-semibold">{it.sku_code}</div><div className="text-ink-soft">{it.product_name}{it.variant_name ? ` · ${it.variant_name}` : ""}</div></div>
                  <div className="text-right tabular-nums"><div>{it.quantity} × {rupiah(it.unit_price)}</div><div className="font-semibold">{rupiah(it.line_total)}</div></div>
                </li>
              ))}
            </ul>
            <dl className="mt-2 grid grid-cols-2 gap-y-1 tabular-nums">
              <dt className="text-ink-soft">Subtotal</dt><dd className="text-right">{rupiah(o.subtotal)}</dd>
              <dt className="text-ink-soft">Ongkir</dt><dd className="text-right">{rupiah(o.shipping_fee)}</dd>
              {Number(o.discount) > 0 && <><dt className="text-ink-soft">Diskon</dt><dd className="text-right">−{rupiah(o.discount)}</dd></>}
              <dt className="font-bold">Total</dt><dd className="text-right font-bold">{rupiah(o.total)}</dd>
            </dl>
          </section>

          {["READY_TO_SHIP", "SHIPPED", "DELIVERED", "RETURN_REQUESTED", "RETURNED", "REFUNDED"].includes(o.status) &&
            <ShipmentPanel order={o} onChanged={() => { load(); onChanged(); }} />}

          <section>
            <h3 className="mb-1 font-semibold">Alamat kirim</h3>
            <p>{o.customer_name}{o.customer_phone ? ` · ${o.customer_phone}` : ""}</p>
            <p className="text-ink-soft">{o.ship_address}, {o.ship_city}{o.ship_province ? `, ${o.ship_province}` : ""} {o.ship_postal_code}</p>
          </section>

          {o.reservations.length > 0 && (
            <section>
              <h3 className="mb-2 font-semibold">Reservasi stok</h3>
              <ul className="flex flex-col gap-1">
                {o.reservations.map((r) => (
                  <li key={r.id} className="flex flex-wrap justify-between gap-2">
                    <span className="font-mono">{r.sku_code} × {r.quantity} @ {r.warehouse_code}</span>
                    <span className="text-ink-soft">{({ ACTIVE: "aktif", RELEASED: "dilepas", CONSUMED: "terkirim", EXPIRED: "kedaluwarsa" } as Record<string, string>)[r.status] ?? r.status}{r.status === "ACTIVE" && r.expires_at ? ` · berlaku sampai ${dt(r.expires_at)}` : ""}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h3 className="mb-2 font-semibold">Riwayat</h3>
            <ol className="relative flex flex-col gap-3 border-l-2 border-concrete-line pl-4">
              {o.history.map((h, i) => (
                <li key={i}>
                  <span className="absolute -left-[7px] mt-1.5 h-3 w-3 rounded-full border border-white bg-ink" aria-hidden="true" />
                  <div className="font-semibold">{STATUS_LABEL[h.to_status as keyof typeof STATUS_LABEL] ?? h.to_status}</div>
                  <div className="text-xs text-ink-muted">{dt(h.created_at)}{h.reason ? ` · ${h.reason}` : ""}</div>
                </li>
              ))}
            </ol>
          </section>
        </div>
      )}
    </aside>
  );
}

type Line = { sku_id: string; quantity: string; unit_price: string };

function CreateOrder({ onDone }: { onDone: (o: OrderDetail) => void }) {
  const [skus, setSkus] = useState<Sku[]>([]);
  const [f, setF] = useState({ channel: "MANUAL", external_ref: "", name: "", phone: "", email: "", address: "", city: "", province: "", postal_code: "", shipping_fee: "0", discount: "0", notes: "" });
  const [lines, setLines] = useState<Line[]>([{ sku_id: "", quantity: "1", unit_price: "" }]);
  const [paid, setPaid] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Satu kunci per form: klik ganda / retry jaringan tidak membuat order ganda
  const idemKey = useMemo(() => `ui-${crypto.randomUUID()}`, []);

  useEffect(() => { api.get<Page<Sku>>("/skus?limit=200").then((p) => setSkus(p.items.filter((s) => s.is_active))).catch(() => undefined); }, []);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const setLine = (i: number, k: keyof Line, v: string) => setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)));
  const subtotal = lines.reduce((a, l) => a + (Number(l.quantity) || 0) * (Number(l.unit_price) || 0), 0);
  const total = subtotal + (Number(f.shipping_fee) || 0) - (Number(f.discount) || 0);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    const items = lines.filter((l) => l.sku_id).map((l) => ({ sku_id: l.sku_id, quantity: Number(l.quantity), unit_price: l.unit_price || "0" }));
    if (items.length === 0) { setErr("Tambahkan minimal satu item"); return; }
    setBusy(true);
    try {
      const o = await api.post<OrderDetail>("/orders", {
        channel: f.channel, external_ref: f.external_ref.trim() || null,
        customer: { name: f.name.trim(), phone: f.phone.trim(), email: f.email.trim() || null },
        shipping: { address: f.address.trim(), city: f.city.trim(), province: f.province.trim(), postal_code: f.postal_code.trim() },
        items, shipping_fee: f.shipping_fee || "0", discount: f.discount || "0", notes: f.notes, paid,
      }, { "Idempotency-Key": idemKey });
      onDone(o);
    } catch (x) { setErr(errorText(x)); } finally { setBusy(false); }
  }

  return (
    <form onSubmit={submit} className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto pr-1">
      {err && <Alert>{err}</Alert>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Channel">{(id) => <Select id={id} value={f.channel} onChange={set("channel")}>{CHANNELS.map((c) => <option key={c}>{c}</option>)}</Select>}</Field>
        <Field label="No. order marketplace (opsional)" hint="Mencegah order yang sama masuk dua kali">{(id) => <Input id={id} value={f.external_ref} onChange={set("external_ref")} />}</Field>
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-semibold">Item</legend>
        {lines.map((l, i) => (
          <div key={i} className="grid grid-cols-[1fr_70px_110px_auto] items-end gap-2">
            <div>
              <label htmlFor={`sku-${i}`} className="sr-only">SKU</label>
              <Select id={`sku-${i}`} value={l.sku_id} onChange={(e) => setLine(i, "sku_id", e.target.value)} required={i === 0}>
                <option value="">Pilih SKU</option>
                {skus.map((s) => <option key={s.id} value={s.id}>{s.sku_code}{s.variant_name ? ` — ${s.variant_name}` : ""}</option>)}
              </Select>
            </div>
            <div><label htmlFor={`qty-${i}`} className="sr-only">Qty</label><Input id={`qty-${i}`} type="number" min={1} value={l.quantity} onChange={(e) => setLine(i, "quantity", e.target.value)} /></div>
            <div><label htmlFor={`price-${i}`} className="sr-only">Harga satuan</label><Input id={`price-${i}`} type="number" min={0} step="100" placeholder="Harga" value={l.unit_price} onChange={(e) => setLine(i, "unit_price", e.target.value)} /></div>
            <button type="button" aria-label={`Hapus item ${i + 1}`} disabled={lines.length === 1} onClick={() => setLines(lines.filter((_, j) => j !== i))}
                    className="min-h-11 min-w-11 rounded text-ink-muted hover:bg-concrete disabled:opacity-30">✕</button>
          </div>
        ))}
        <Button type="button" variant="ghost" className="self-start" onClick={() => setLines([...lines, { sku_id: "", quantity: "1", unit_price: "" }])}>Tambah item</Button>
      </fieldset>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Nama pelanggan">{(id) => <Input id={id} value={f.name} onChange={set("name")} required />}</Field>
        <Field label="No. HP">{(id) => <Input id={id} value={f.phone} onChange={set("phone")} inputMode="tel" />}</Field>
        <div className="sm:col-span-2"><Field label="Alamat">{(id) => <Input id={id} value={f.address} onChange={set("address")} required />}</Field></div>
        <Field label="Kota">{(id) => <Input id={id} value={f.city} onChange={set("city")} required />}</Field>
        <Field label="Kode pos">{(id) => <Input id={id} value={f.postal_code} onChange={set("postal_code")} inputMode="numeric" />}</Field>
        <Field label="Ongkir">{(id) => <Input id={id} type="number" min={0} value={f.shipping_fee} onChange={set("shipping_fee")} />}</Field>
        <Field label="Diskon">{(id) => <Input id={id} type="number" min={0} value={f.discount} onChange={set("discount")} />}</Field>
      </div>
      <label className="flex min-h-11 items-center gap-2 text-sm">
        <input type="checkbox" className="h-4 w-4 accent-ink" checked={paid} onChange={(e) => setPaid(e.target.checked)} />
        Pembayaran sudah terverifikasi (langsung dialokasikan ke gudang)
      </label>
      <div className="flex items-center justify-between border-t border-concrete-dark pt-3">
        <span className="text-sm">Total <strong className="tabular-nums">{rupiah(Math.max(total, 0))}</strong></span>
        <Button type="submit" loading={busy}>Simpan order</Button>
      </div>
    </form>
  );
}
