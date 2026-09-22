"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Boxes, CircleCheck, CircleCheckBig, Clock, Inbox, PackageCheck, PackagePlus, ScanLine, ShoppingCart, TriangleAlert, Truck,
  Undo2, UserPlus, Warehouse as WarehouseIcon, type LucideIcon,
} from "lucide-react";
import { useMe } from "@/components/me-context";
import { api, dt } from "@/lib/api";
import { hasFeature } from "@/lib/saas";
import type { AuditLog, OrderStats, Page, Product, ReturnDoc, Shipment, User, Warehouse } from "@/lib/types";

type Task = { key: string; icon: LucideIcon; tone: "critical" | "warning" | "normal"; count: number; text: string; href: string; cta: string };
type Step = { key: string; icon: LucideIcon; title: string; desc: string; done: boolean; href: string; cta: string };

const TONE = {
  critical: { icon: "bg-red-50 text-danger", count: "text-danger" },
  warning: { icon: "bg-signal-soft text-[#8A6D00]", count: "text-ink" },
  normal: { icon: "bg-concrete text-ink-soft", count: "text-ink" },
};

const ACTION_LABEL: Record<string, string> = {
  "order.created": "Order baru masuk", "order.status_changed": "Status order berubah", "product.created": "Produk ditambahkan",
  "sku.created": "SKU ditambahkan", "inventory.received": "Stok diterima", "inventory.adjusted": "Stok disesuaikan",
  "warehouse.created": "Gudang ditambahkan", "location.created": "Lokasi rak ditambahkan", "user.created": "Anggota tim ditambahkan",
  "shipment.created": "Resi dibuat", "manifest.handed_over": "Paket diserahkan ke kurir", "return.created": "Retur diajukan",
  "return.closed": "Retur selesai", "wave.created": "Batch ambil barang dibuat", "inbound.completed": "Barang masuk selesai dicatat",
  "cycle_count.approved": "Hasil hitung stok disetujui", "auth.login": "Masuk ke aplikasi", "billing.checkout": "Tagihan dibuat",
  "subscription.paid": "Langganan dibayar",
};

function greeting() {
  const h = new Date().getHours();
  return h < 11 ? "Selamat pagi" : h < 15 ? "Selamat siang" : h < 19 ? "Selamat sore" : "Selamat malam";
}

export default function Dashboard() {
  const me = useMe();
  const can = (p: string) => me.permissions.includes(p);
  const feat = (f: string) => hasFeature(me.subscription, f);
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [today, setToday] = useState<{ placed: number; shipped: number } | null>(null);
  const [recent, setRecent] = useState<AuditLog[]>([]);
  const [hideGuide, setHideGuide] = useState(false);

  useEffect(() => {
    try { setHideGuide(localStorage.getItem(`nx_guide_${me.tenant.id}`) === "hide"); } catch { /* storage diblok */ }
    (async () => {
      const t: Task[] = [];
      const safe = async <T,>(p: Promise<T>): Promise<T | null> => { try { return await p; } catch { return null; } };
      const todayIso = new Date().toISOString().slice(0, 10);
      const [stats, labelReady, returns, lowStock, sla, whs, products, invAny, users, series] = await Promise.all([
        can("order:read") && feat("oms") ? safe(api.get<OrderStats>("/orders/stats")) : null,
        can("shipping:read") && feat("oms") ? safe(api.get<Shipment[]>("/shipping/shipments?status=LABEL_READY&limit=500")) : null,
        can("returns:read") && feat("oms") ? safe(api.get<ReturnDoc[]>("/returns?status=OPEN")) : null,
        can("inventory:read") && feat("oms") ? safe(api.get<Page<unknown>>("/inventory?low_only=true&limit=1")) : null,
        can("analytics:read") && feat("analytics") ? safe(api.get<{ counts: { overdue: number; at_risk: number } }>("/analytics/sla")) : null,
        can("warehouse:read") ? safe(api.get<Warehouse[]>("/warehouses")) : null,
        can("product:read") ? safe(api.get<Page<Product>>("/products?limit=1")) : null,
        can("inventory:read") && feat("oms") ? safe(api.get<Page<unknown>>("/inventory?limit=1")) : null,
        can("user:read") ? safe(api.get<Page<User>>("/users?limit=1")) : null,
        can("analytics:read") ? safe(api.get<{ placed: number; shipped: number }[]>(`/analytics/timeseries?date_from=${todayIso}&date_to=${todayIso}`)) : null,
      ]);
      const b = stats?.by_status ?? {};
      const wmsOn = feat("wms") && can("wms:read");
      let wmsOpen = { exceptions: 0, putaway: 0 };
      if (wmsOn && whs?.length) {
        const all = await Promise.all(whs.filter((w) => w.is_active).map((w) => safe(api.get<{ exceptions_open: number; open_putaway: number }>(`/wms/stats?warehouse_id=${w.id}`))));
        wmsOpen = all.reduce((a, x) => ({ exceptions: a.exceptions + (x?.exceptions_open ?? 0), putaway: a.putaway + (x?.open_putaway ?? 0) }), wmsOpen);
      }
      const push = (x: Task) => { if (x.count > 0) t.push(x); };
      push({ key: "sla", icon: Clock, tone: "critical", count: sla?.counts.overdue ?? 0, text: "order sudah melewati batas waktu kirim", href: "/analytics", cta: "Lihat order terlambat" });
      push({ key: "hold", icon: TriangleAlert, tone: "critical", count: stats?.out_of_stock ?? 0, text: "order tertahan karena stok belum cukup", href: "/orders", cta: "Cek order" });
      push({ key: "exc", icon: TriangleAlert, tone: "warning", count: wmsOpen.exceptions, text: "masalah di gudang menunggu diselesaikan", href: "/wms", cta: "Selesaikan" });
      push({ key: "pick", icon: ShoppingCart, tone: "warning", count: b.ALLOCATED ?? 0, text: "order siap diambil dari rak", href: wmsOn ? "/wms" : "/orders", cta: wmsOn ? "Buat batch ambil barang" : "Proses order" });
      push({ key: "pack", icon: PackageCheck, tone: "normal", count: (b.PICKING ?? 0) + (b.PACKING ?? 0), text: "order sedang diambil atau dikemas", href: wmsOn ? "/scan/pack" : "/orders", cta: "Lanjutkan packing" });
      const ready = b.READY_TO_SHIP ?? 0;
      const withResi = labelReady?.length ?? 0;
      push({ key: "resi", icon: Truck, tone: "warning", count: Math.max(0, ready - withResi), text: "paket siap kirim tetapi belum punya resi", href: "/shipping", cta: "Buat resi" });
      push({ key: "handover", icon: Truck, tone: "normal", count: withResi, text: "paket menunggu dijemput kurir", href: "/shipping", cta: "Serahkan ke kurir" });
      push({ key: "ret_decide", icon: Undo2, tone: "warning", count: returns?.filter((r) => r.status === "REQUESTED").length ?? 0, text: "pengajuan retur perlu diputuskan", href: "/returns", cta: "Tinjau retur" });
      push({ key: "ret_recv", icon: Undo2, tone: "normal", count: returns?.filter((r) => ["APPROVED", "RECEIVED", "INSPECTED"].includes(r.status)).length ?? 0, text: "retur sedang diproses di gudang", href: "/returns", cta: "Lanjutkan" });
      push({ key: "putaway", icon: ScanLine, tone: "normal", count: wmsOpen.putaway, text: "barang masuk belum disimpan ke rak", href: "/scan/putaway", cta: "Simpan ke rak" });
      push({ key: "low", icon: Boxes, tone: "warning", count: lowStock?.total ?? 0, text: "SKU stoknya menipis", href: "/inventory", cta: "Lihat stok" });
      push({ key: "unpaid", icon: Clock, tone: "normal", count: b.CREATED ?? 0, text: "order menunggu pembayaran", href: "/orders", cta: "Lihat order" });
      setTasks(t);

      const orderTotal = Object.values(b).reduce((a, x) => a + (x ?? 0), 0);
      setSteps([
        { key: "wh", icon: WarehouseIcon, title: "Tambahkan gudang", desc: "Alamat dan kode pos dipakai untuk label pengiriman.", done: (whs?.length ?? 0) > 0, href: "/warehouses", cta: "Tambah gudang" },
        { key: "prod", icon: PackagePlus, title: "Masukkan produk", desc: "Setiap ukuran atau warna menjadi satu SKU dengan barcode.", done: (products?.total ?? 0) > 0, href: "/products", cta: "Tambah produk" },
        { key: "stock", icon: Boxes, title: "Catat stok awal", desc: "Isi jumlah barang yang sekarang ada di gudang.", done: (invAny?.total ?? 0) > 0, href: "/inventory", cta: "Terima stok" },
        { key: "order", icon: ShoppingCart, title: "Buat order pertama", desc: "Atau sambungkan toko online lewat menu Integrasi.", done: orderTotal > 0, href: "/orders", cta: "Buat order" },
        { key: "team", icon: UserPlus, title: "Undang tim", desc: "Beri staf gudang dan CS akses sesuai perannya.", done: (users?.total ?? 0) > 1, href: "/users", cta: "Undang" },
      ].filter((s) => (s.key !== "team" || can("user:read")) && (s.key !== "stock" || can("inventory:read"))));
      if (series?.[0]) setToday(series[0]);
      if (can("audit:read")) setRecent(((await safe(api.get<AuditLog[]>("/audit-logs?limit=25"))) ?? []).filter((r) => !r.action.startsWith("auth.")).slice(0, 6));
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const doneSteps = steps.filter((s) => s.done).length;
  const showGuide = steps.length > 0 && doneSteps < steps.length && !hideGuide;
  const nextStep = steps.find((s) => !s.done);

  return (
    <>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{greeting()}, {me.full_name.split(" ")[0]}</h1>
          <p className="mt-1 text-ink-muted">{new Date().toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long" })}</p>
        </div>
        {today && (
          <p className="text-sm text-ink-soft">Hari ini <strong className="text-ink">{today.placed}</strong> order masuk, <strong className="text-ink">{today.shipped}</strong> dikirim</p>
        )}
      </div>

      {showGuide && (
        <section aria-labelledby="guide-title" className="mb-8 rounded-xl border border-concrete-dark bg-white p-5 shadow-card">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="guide-title" className="text-lg font-bold">Siapkan workspace Anda</h2>
              <p className="text-sm text-ink-muted">{doneSteps} dari {steps.length} langkah selesai.{nextStep ? ` Berikutnya: ${nextStep.title.toLowerCase()}.` : ""}</p>
            </div>
            <button type="button" className="min-h-9 rounded-lg px-2 text-sm text-ink-muted hover:bg-concrete"
                    onClick={() => { setHideGuide(true); try { localStorage.setItem(`nx_guide_${me.tenant.id}`, "hide"); } catch { /* */ } }}>Sembunyikan</button>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-concrete-dark" role="progressbar" aria-valuenow={doneSteps} aria-valuemax={steps.length} aria-label="Progres persiapan">
            <div className="h-full rounded-full bg-ok transition-all" style={{ width: `${(doneSteps / steps.length) * 100}%` }} />
          </div>
          <ol className="mt-4 grid items-start gap-2 md:grid-cols-5">
            {steps.map((s, i) => (
              <li key={s.key} className={`flex flex-col gap-2 rounded-lg border p-3 ${s.done ? "border-transparent bg-concrete" : s === nextStep ? "border-ink/30 bg-white" : "border-concrete-dark bg-white"}`}>
                <div className="flex items-center gap-2">
                  {s.done ? <CircleCheckBig size={20} className="text-ok" aria-label="selesai" />
                    : <span className="grid h-5 w-5 place-items-center rounded-full border border-ink-muted text-[11px] font-bold text-ink-soft">{i + 1}</span>}
                  <span className={`text-sm font-semibold ${s.done ? "text-ink-muted line-through decoration-ink-muted/40" : ""}`}>{s.title}</span>
                </div>
                {!s.done && <><p className="text-xs text-ink-muted">{s.desc}</p>
                  <Link href={s.href} className={`mt-auto inline-flex min-h-9 items-center justify-center rounded-lg px-3 text-sm font-semibold ${s === nextStep ? "bg-ink text-white hover:bg-ink-soft" : "border border-concrete-line hover:bg-concrete"}`}>{s.cta}</Link></>}
              </li>
            ))}
          </ol>
        </section>
      )}

      <section aria-labelledby="todo-title">
        <h2 id="todo-title" className="mb-3 text-lg font-bold">Yang perlu dikerjakan</h2>
        {tasks === null ? (
          <div className="rounded-xl border border-concrete-dark bg-white p-6 text-ink-muted shadow-card" aria-busy="true">Memeriksa pekerjaan…</div>
        ) : tasks.length === 0 ? (
          <div className="flex items-center gap-4 rounded-xl border border-concrete-dark bg-white p-6 shadow-card">
            <span className="grid h-11 w-11 place-items-center rounded-full bg-green-50 text-ok"><CircleCheck size={24} aria-hidden="true" /></span>
            <div><p className="font-semibold">Semua beres untuk saat ini</p><p className="text-sm text-ink-muted">Pekerjaan baru muncul di sini begitu ada order, retur, atau stok yang perlu ditangani.</p></div>
          </div>
        ) : (
          <ul className="divide-y divide-concrete-dark overflow-hidden rounded-xl border border-concrete-dark bg-white shadow-card">
            {tasks.map((t) => {
              const Icon = t.icon;
              return (
                <li key={t.key} className="flex flex-wrap items-center gap-4 px-4 py-3.5 sm:flex-nowrap">
                  <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg ${TONE[t.tone].icon}`}><Icon size={20} aria-hidden="true" /></span>
                  <p className="min-w-0 flex-1 text-[15px]"><strong className={`mr-1 text-lg tabular-nums ${TONE[t.tone].count}`}>{t.count.toLocaleString("id-ID")}</strong>{t.text}</p>
                  <Link href={t.href} className={`inline-flex min-h-10 shrink-0 items-center rounded-lg px-4 text-sm font-semibold ${t.tone === "critical" ? "bg-ink text-white hover:bg-ink-soft" : "border border-concrete-line hover:bg-concrete"}`}>{t.cta}</Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {recent.length > 0 && (
        <section className="mt-8" aria-labelledby="recent-title">
          <div className="mb-3 flex items-center justify-between"><h2 id="recent-title" className="text-lg font-bold">Aktivitas terakhir</h2>
            <Link href="/audit" className="text-sm font-semibold text-ink-soft hover:text-ink">Lihat semua</Link></div>
          <ul className="divide-y divide-concrete-dark rounded-xl border border-concrete-dark bg-white shadow-card">
            {recent.map((r) => (
              <li key={r.id} className="flex items-center gap-3 px-4 py-3 text-sm">
                <Inbox size={16} className="shrink-0 text-ink-muted" aria-hidden="true" />
                <span className="flex-1">{ACTION_LABEL[r.action] ?? r.action}{r.after && typeof r.after === "object" && "order_number" in r.after ? ` · ${String(r.after.order_number)}` : ""}</span>
                <span className="text-xs text-ink-muted">{dt(r.created_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
