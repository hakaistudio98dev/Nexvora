"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useCan } from "@/components/me-context";
import { Alert, Badge, Button, Field, Input, Modal, Select } from "@/components/ui";
import { api, dt, errorText, rupiah } from "@/lib/api";
import { SHIP_STATUS, shipTone } from "@/lib/shipping";
import type { Courier, CourierAccount, OrderDetail, Rate, Shipment } from "@/lib/types";

/** Bagian pengiriman & retur di detail order. */
export function ShipmentPanel({ order, onChanged }: { order: OrderDetail; onChanged: () => void }) {
  const canWrite = useCan("shipping:write");
  const canReturn = useCan("returns:write");
  const [sh, setSh] = useState<Shipment | null | undefined>(undefined);
  const [err, setErr] = useState<string | null>(null);
  const [returnOpen, setReturnOpen] = useState(false);
  const load = useCallback(() => api.get<Shipment | null>(`/shipping/shipments/by-order/${order.id}`).then(setSh)
    .catch((e) => setErr(errorText(e))), [order.id]);
  useEffect(() => { load(); }, [load, order.status]);

  async function act(path: string, body?: unknown) {
    setErr(null);
    try { setSh(await api.post<Shipment>(path, body)); onChanged(); } catch (e) { setErr(errorText(e)); }
  }

  if (sh === undefined) return null;
  return (
    <section className="flex flex-col gap-2">
      <h3 className="font-semibold">Pengiriman</h3>
      {err && <Alert>{err}</Alert>}
      {!sh && order.status === "READY_TO_SHIP" && canWrite && <CreateShipment order={order} onDone={(s) => { setSh(s); onChanged(); }} />}
      {!sh && order.status !== "READY_TO_SHIP" && <p className="text-sm text-ink-muted">Resi dibuat setelah paket selesai di packing station.</p>}
      {sh && (
        <div className="rounded-lg border border-concrete-line p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><div className="font-semibold">{sh.courier_name} <span className="text-ink-muted">{sh.service_code}</span></div>
              <div className="font-mono text-lg font-bold">{sh.tracking_number ?? "Menunggu resi"}</div></div>
            <Badge tone={shipTone(sh.status)}>{SHIP_STATUS[sh.status]}</Badge>
          </div>
          <div className="mt-1 text-xs text-ink-muted">{sh.account_name}{sh.cost ? ` · ongkir ${rupiah(sh.cost)}` : ""}{sh.manifest_number ? ` · ${sh.manifest_number}` : ""}</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {sh.tracking_url && <a href={sh.tracking_url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-10 items-center rounded-md px-3 text-sm font-semibold underline">Lacak di situs kurir</a>}
            {sh.tracking_number && <a href={`/label/${sh.id}`} target="_blank" rel="noopener" className="inline-flex min-h-10 items-center rounded-md border border-ink px-3 text-sm font-semibold">Cetak label</a>}
            {canWrite && sh.status === "LABEL_READY" && !sh.manifest_number && <Button variant="ghost" onClick={() => confirm("Batalkan resi ini?") && act(`/shipping/shipments/${sh.id}/cancel`)}>Batalkan resi</Button>}
            {sh.provider !== "manual" && !["DELIVERED", "CANCELLED", "RETURNED_TO_SENDER"].includes(sh.status) && <Button variant="ghost" onClick={() => act(`/shipping/shipments/${sh.id}/refresh`)}>Perbarui status</Button>}
          </div>
          {canWrite && sh.provider === "manual" && ["HANDED_OVER", "IN_TRANSIT", "OUT_FOR_DELIVERY", "FAILED_DELIVERY"].includes(sh.status) && (
            <ManualTracking onSave={(b) => act(`/shipping/shipments/${sh.id}/tracking`, b)} />
          )}
          {sh.events && sh.events.length > 0 && (
            <ol className="mt-3 flex flex-col gap-2 border-l-2 border-concrete-line pl-3 text-sm">
              {[...sh.events].reverse().map((e, i) => (
                <li key={i}><div className="font-semibold">{SHIP_STATUS[e.status] ?? e.status}</div>
                  <div className="text-xs text-ink-muted">{dt(e.occurred_at)}{e.location ? ` · ${e.location}` : ""} · {e.description}</div></li>))}
            </ol>
          )}
        </div>
      )}
      {canReturn && ["SHIPPED", "DELIVERED"].includes(order.status) && (
        <Button variant="secondary" className="self-start" onClick={() => setReturnOpen(true)}>Ajukan retur</Button>
      )}
      <Modal open={returnOpen} title={`Retur ${order.order_number}`} onClose={() => setReturnOpen(false)}>
        <ReturnForm order={order} onDone={() => { setReturnOpen(false); onChanged(); }} />
      </Modal>
    </section>
  );
}

function CreateShipment({ order, onDone }: { order: OrderDetail; onDone: (s: Shipment) => void }) {
  const [accounts, setAccounts] = useState<CourierAccount[]>([]);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [acc, setAcc] = useState("");
  const [courier, setCourier] = useState("");
  const [service, setService] = useState("");
  const [resi, setResi] = useState("");
  const [cost, setCost] = useState("");
  const [rates, setRates] = useState<Rate[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    Promise.all([api.get<CourierAccount[]>("/shipping/accounts"), api.get<Courier[]>("/shipping/couriers")]).then(([a, c]) => {
      const active = a.filter((x) => x.is_active);
      setAccounts(active); setCouriers(c);
      setAcc((active.find((x) => x.is_default) ?? active[0])?.id ?? "");
    }).catch((e) => setErr(errorText(e)));
  }, []);
  const account = accounts.find((a) => a.id === acc);
  // Akun Manual menerima semua kurir (termasuk kurir kustom); akun otomatis hanya kurir yang didukung & diaktifkan
  const allowed = useMemo(() => couriers.filter((c) => !account || account.provider === "manual"
    || (c.auto_booking && (account.couriers.length === 0 || account.couriers.includes(c.code)))), [couriers, account]);
  const services = couriers.find((c) => c.code === courier)?.services ?? [];

  async function checkRates() {
    setErr(null);
    try { setRates((await api.get<{ rates: Rate[] }>(`/shipping/rates?order_id=${order.id}&account_id=${acc}`)).rates); } catch (e) { setErr(errorText(e)); }
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      onDone(await api.post<Shipment>("/shipping/shipments", { order_id: order.id, account_id: acc, courier_code: courier,
        service_code: service, tracking_number: account?.provider === "manual" ? resi.trim() : null, cost: cost || null }));
    } catch (x) { setErr(errorText(x)); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-lg border border-concrete-line p-3">
      {err && <Alert>{err}</Alert>}
      <Field label="Akun kurir">{(id) => <Select id={id} value={acc} onChange={(e) => { setAcc(e.target.value); setRates(null); }}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select>}</Field>
      {account && account.provider !== "manual" && (
        <div>
          <Button type="button" variant="ghost" onClick={checkRates}>Cek tarif</Button>
          {rates && <ul className="mt-1 flex flex-col gap-1">{rates.map((r) => (
            <li key={r.courier_code + r.service_code}><button type="button" onClick={() => { setCourier(r.courier_code); setService(r.service_code); }}
              className={`flex w-full min-h-10 justify-between rounded-lg border px-3 text-left text-sm ${courier === r.courier_code && service === r.service_code ? "border-ink/30 bg-signal-soft/40" : "border-concrete-line"}`}>
              <span>{r.courier_name} {r.service_name}{r.etd ? ` · ${r.etd}` : ""}</span><span className="font-semibold">{r.price ? rupiah(r.price) : "—"}</span></button></li>))}</ul>}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Field label="Kurir">{(id) => <Select id={id} value={courier} onChange={(e) => { setCourier(e.target.value); setService(""); }} required><option value="">Pilih</option>{allowed.map((c) => <option key={c.code} value={c.code}>{c.name}{c.custom ? " (kustom)" : ""}</option>)}</Select>}</Field>
        <Field label="Layanan">{(id) => <Select id={id} value={service} onChange={(e) => setService(e.target.value)}><option value="">—</option>{services.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}</Select>}</Field>
      </div>
      {account?.provider === "manual" && (
        <Field label="Nomor resi" hint="Dari marketplace (AWB) atau struk drop-off kurir">{(id) => <Input id={id} value={resi} onChange={(e) => setResi(e.target.value.toUpperCase())} required pattern="[A-Za-z0-9-]{6,60}" />}</Field>
      )}
      <Field label="Ongkir aktual (opsional)">{(id) => <Input id={id} type="number" min={0} value={cost} onChange={(e) => setCost(e.target.value)} />}</Field>
      <Button type="submit" loading={busy}>{account?.provider === "manual" ? "Simpan resi" : "Buat resi"}</Button>
    </form>
  );
}

function ManualTracking({ onSave }: { onSave: (b: { status: string; description: string; location: string }) => void }) {
  const [status, setStatus] = useState("IN_TRANSIT");
  const [desc, setDesc] = useState("");
  const [loc, setLoc] = useState("");
  return (
    <form className="mt-3 grid gap-2 border-t border-concrete-dark pt-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); onSave({ status, description: desc, location: loc }); setDesc(""); }}>
      <Field label="Update status">{(id) => <Select id={id} value={status} onChange={(e) => setStatus(e.target.value)}>
        {["IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED", "FAILED_DELIVERY", "RETURNED_TO_SENDER"].map((s) => <option key={s} value={s}>{SHIP_STATUS[s as keyof typeof SHIP_STATUS]}</option>)}</Select>}</Field>
      <Field label="Lokasi">{(id) => <Input id={id} value={loc} onChange={(e) => setLoc(e.target.value)} />}</Field>
      <div className="sm:col-span-2"><Field label="Keterangan (dari web kurir)">{(id) => <Input id={id} value={desc} onChange={(e) => setDesc(e.target.value)} required minLength={3} />}</Field></div>
      <Button type="submit" variant="secondary" className="sm:col-span-2">Simpan update</Button>
    </form>
  );
}

function ReturnForm({ order, onDone }: { order: OrderDetail; onDone: () => void }) {
  const [reasons, setReasons] = useState<{ code: string; label: string }[]>([]);
  const [reason, setReason] = useState("DAMAGED");
  const [note, setNote] = useState("");
  const [qty, setQty] = useState<Record<string, number>>({});
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<{ code: string; label: string }[]>("/returns/reasons").then(setReasons).catch(() => undefined); }, []);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const lines = Object.entries(qty).filter(([, q]) => q > 0).map(([order_item_id, quantity]) => ({ order_item_id, quantity }));
    if (!lines.length) { setErr("Pilih jumlah barang yang diretur"); return; }
    try { await api.post("/returns", { order_id: order.id, reason_code: reason, note, lines }); onDone(); } catch (x) { setErr(errorText(x)); }
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {err && <Alert>{err}</Alert>}
      <table className="w-full text-sm"><thead className="text-left text-ink-muted"><tr><th>Barang</th><th className="w-24">Diretur</th></tr></thead>
        <tbody>{order.items.map((i) => (
          <tr key={i.id} className="border-t border-concrete-dark"><td className="py-2"><span className="font-mono font-semibold">{i.sku_code}</span> <span className="text-ink-muted">dari {i.quantity}</span></td>
            <td><Input aria-label={`Jumlah retur ${i.sku_code}`} type="number" min={0} max={i.quantity} value={qty[i.id] ?? 0} onChange={(e) => setQty({ ...qty, [i.id]: Number(e.target.value) })} /></td></tr>))}</tbody></table>
      <Field label="Alasan">{(id) => <Select id={id} value={reason} onChange={(e) => setReason(e.target.value)}>{reasons.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}</Select>}</Field>
      <Field label="Catatan dari pelanggan">{(id) => <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      <Button type="submit">Ajukan retur</Button>
    </form>
  );
}
