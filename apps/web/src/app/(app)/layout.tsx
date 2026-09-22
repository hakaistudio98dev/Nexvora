"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity, Bell, Boxes, ChartColumn, ClipboardList, CreditCard, History, House, KeyRound, Lock, LogOut, MapPin, Menu,
  Package, ScanLine, Settings, ShieldCheck, ShoppingCart, Truck, Undo2, Users, X, type LucideIcon,
} from "lucide-react";
import { MeContext } from "@/components/me-context";
import { api } from "@/lib/api";
import { STATUS_LABEL, daysLeft, hasFeature } from "@/lib/saas";
import type { Me } from "@/lib/types";

type Item = { href: string; label: string; icon: LucideIcon; perm: string | null; feature?: string };
// Dikelompokkan menurut pekerjaan sehari-hari, dengan nama yang dipahami pengguna (bukan istilah sistem)
const GROUPS: { title: string | null; items: Item[] }[] = [
  { title: null, items: [{ href: "/dashboard", label: "Beranda", icon: House, perm: null }] },
  { title: "Penjualan", items: [
    { href: "/orders", label: "Order", icon: ShoppingCart, perm: "order:read", feature: "oms" },
    { href: "/returns", label: "Retur", icon: Undo2, perm: "returns:read", feature: "oms" },
  ] },
  { title: "Stok & produk", items: [
    { href: "/inventory", label: "Stok barang", icon: Boxes, perm: "inventory:read", feature: "oms" },
    { href: "/products", label: "Produk", icon: Package, perm: "product:read" },
  ] },
  { title: "Gudang", items: [
    { href: "/wms", label: "Kerja gudang", icon: ClipboardList, perm: "wms:read", feature: "wms" },
    { href: "/warehouses", label: "Lokasi & rak", icon: MapPin, perm: "warehouse:read" },
    { href: "/shipping", label: "Pengiriman", icon: Truck, perm: "shipping:read", feature: "oms" },
  ] },
  { title: "Laporan", items: [
    { href: "/analytics", label: "Analitik", icon: ChartColumn, perm: "analytics:read", feature: "oms" },
    { href: "/noc", label: "Pantau sistem", icon: Activity, perm: "analytics:read", feature: "analytics" },
    { href: "/audit", label: "Riwayat aktivitas", icon: History, perm: "audit:read" },
  ] },
  { title: "Akun", items: [
    { href: "/users", label: "Tim & akses", icon: Users, perm: "user:read" },
    { href: "/billing", label: "Langganan", icon: CreditCard, perm: "billing:read" },
    { href: "/api-keys", label: "Integrasi (API)", icon: KeyRound, perm: "apikey:manage", feature: "api_keys" },
    { href: "/settings", label: "Pengaturan", icon: Settings, perm: "notification:read" },
    { href: "/platform", label: "Admin platform", icon: ShieldCheck, perm: "tenant:manage" },
  ] },
];

function SubscriptionBanner({ me }: { me: Me }) {
  const s = me.subscription;
  if (s.unrestricted) return null;
  let tone = "bg-signal-soft text-[#5C4A00] border-signal-dark/30";
  let text: string | null = null;
  const trial = daysLeft(s.trial_ends_at);
  if (s.read_only) { tone = "bg-red-50 text-danger border-danger/30"; text = `${STATUS_LABEL[s.status]}. Data tetap aman dan bisa dilihat, tetapi belum bisa diubah sampai pembayaran selesai.`; }
  else if (s.status === "PAST_DUE") { tone = "bg-red-50 text-danger border-danger/30"; text = `Pembayaran belum diterima. Akun menjadi baca-saja ${s.grace_until ? `dalam ${Math.max(0, daysLeft(s.grace_until) ?? 0)} hari` : "segera"}.`; }
  else if (s.status === "TRIALING" && trial !== null && trial <= 7) { text = `Masa coba paket ${s.plan_name} tersisa ${Math.max(0, trial)} hari.`; }
  if (!text) return null;
  return (
    <div role="status" className={`flex flex-wrap items-center justify-between gap-2 border-b px-5 py-2.5 text-sm font-medium lg:px-10 ${tone}`}>
      <span>{text}</span>
      {me.permissions.includes("billing:read") && <Link href="/billing" className="font-semibold underline underline-offset-2">Atur langganan</Link>}
    </div>
  );
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => { api.get<Me>("/auth/me").then(setMe).catch(() => undefined); }, []);
  useEffect(() => setMenuOpen(false), [pathname]);
  useEffect(() => {
    if (!me?.permissions.includes("notification:read")) return;
    const poll = () => api.get<{ unread: number }>("/notifications?limit=1").then((r) => setUnread(r.unread)).catch(() => undefined);
    poll();
    const t = setInterval(poll, 60000);
    return () => clearInterval(t);
  }, [me, pathname]);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  if (!me) {
    return <div className="grid min-h-screen place-items-center text-ink-muted" aria-busy="true">Menyiapkan workspace…</div>;
  }

  const canScan = me.permissions.includes("wms:operate") && hasFeature(me.subscription, "wms");
  const bell = me.permissions.includes("notification:read") && (
    <Link href="/notifications" aria-label={`Notifikasi, ${unread} belum dibaca`}
          className="relative grid min-h-10 min-w-10 place-items-center rounded-lg text-ink-soft hover:bg-concrete hover:text-ink">
      <Bell size={20} aria-hidden="true" />
      {unread > 0 && <span className="absolute -right-0.5 -top-0.5 min-w-5 rounded-full bg-danger px-1 text-center text-[11px] font-bold leading-5 text-white">{unread > 99 ? "99+" : unread}</span>}
    </Link>
  );

  const nav = (
    <nav aria-label="Menu utama" className="flex flex-1 flex-col gap-5 overflow-y-auto px-3 pb-4">
      {GROUPS.map((g) => {
        const items = g.items.filter((n) => !n.perm || me.permissions.includes(n.perm));
        if (!items.length) return null;
        return (
          <div key={g.title ?? "root"}>
            {g.title && <div className="mb-1 px-3 text-xs font-semibold text-ink-muted">{g.title}</div>}
            <ul className="flex flex-col gap-0.5">
              {items.map((n) => {
                const active = pathname === n.href || pathname.startsWith(n.href + "/");
                const lock = !!n.feature && !hasFeature(me.subscription, n.feature);
                const Icon = n.icon;
                return (
                  <li key={n.href}>
                    <Link href={lock ? "/billing" : n.href} aria-current={active ? "page" : undefined}
                          title={lock ? "Tidak termasuk paket Anda" : undefined}
                          className={`group relative flex min-h-10 items-center gap-3 rounded-lg px-3 text-[14.5px] font-medium transition-colors ${
                            active ? "bg-concrete text-ink" : lock ? "text-ink-muted/70 hover:bg-concrete" : "text-ink-soft hover:bg-concrete hover:text-ink"}`}>
                      {active && <span className="absolute left-0 top-2 bottom-2 w-1 rounded-r bg-signal" aria-hidden="true" />}
                      <Icon size={18} strokeWidth={active ? 2.25 : 1.75} aria-hidden="true" className={active ? "text-ink" : "text-ink-muted group-hover:text-ink"} />
                      <span className="flex-1">{n.label}</span>
                      {lock && <Lock size={14} aria-label="perlu upgrade" className="text-ink-muted" />}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );

  const account = (
    <div className="border-t border-concrete-dark p-3">
      <div className="flex items-center gap-3 rounded-lg px-2 py-2">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink text-sm font-bold text-white" aria-hidden="true">{initials(me.full_name)}</span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-ink">{me.full_name}</div>
          <div className="truncate text-xs text-ink-muted">{me.email}</div>
        </div>
        <button type="button" onClick={logout} aria-label="Keluar" title="Keluar"
                className="grid min-h-9 min-w-9 place-items-center rounded-lg text-ink-muted hover:bg-concrete hover:text-danger">
          <LogOut size={18} aria-hidden="true" />
        </button>
      </div>
    </div>
  );

  const brand = (
    <div className="flex items-center justify-between gap-2 px-5 pb-4 pt-5">
      <div className="min-w-0">
        <div className="font-brand text-lg font-black tracking-wide text-ink [font-stretch:125%]">NEXVORA</div>
        <div className="truncate text-xs text-ink-muted">{me.tenant.name}</div>
      </div>
      <div className="hidden lg:block">{bell}</div>
    </div>
  );

  return (
    <MeContext.Provider value={me}>
      <div className="min-h-screen lg:grid lg:grid-cols-[252px_1fr]">
        {/* Desktop: menu samping tetap */}
        <aside className="hidden border-r border-concrete-dark bg-white lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col">
          {brand}
          {canScan && (
            <div className="px-3 pb-4">
              <Link href="/scan" className="flex min-h-10 items-center justify-center gap-2 rounded-lg bg-signal text-sm font-semibold text-ink hover:bg-[#E8B900]">
                <ScanLine size={18} aria-hidden="true" />Buka scanner gudang
              </Link>
            </div>
          )}
          {nav}
          {account}
        </aside>

        {/* Mobile: bilah atas + laci menu */}
        <header className="app-topbar sticky top-0 z-30 flex items-center gap-2 border-b border-concrete-dark bg-white px-3 py-2 lg:hidden">
          <button type="button" onClick={() => setMenuOpen(true)} aria-label="Buka menu" aria-expanded={menuOpen}
                  className="grid min-h-10 min-w-10 place-items-center rounded-lg text-ink hover:bg-concrete"><Menu size={22} aria-hidden="true" /></button>
          <span className="font-brand text-base font-black text-ink [font-stretch:125%]">NEXVORA</span>
          <div className="ml-auto flex items-center gap-1">
            {canScan && <Link href="/scan" aria-label="Scanner gudang" className="grid min-h-10 min-w-10 place-items-center rounded-lg bg-signal text-ink"><ScanLine size={20} aria-hidden="true" /></Link>}
            {bell}
          </div>
        </header>
        {menuOpen && (
          <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
            <button type="button" className="absolute inset-0 bg-ink/40" aria-label="Tutup menu" onClick={() => setMenuOpen(false)} />
            <div className="absolute inset-y-0 left-0 flex w-[82%] max-w-[300px] flex-col bg-white shadow-pop">
              <div className="flex items-start justify-between">
                {brand}
                <button type="button" onClick={() => setMenuOpen(false)} aria-label="Tutup menu" className="m-3 grid min-h-10 min-w-10 place-items-center rounded-lg hover:bg-concrete"><X size={20} aria-hidden="true" /></button>
              </div>
              {nav}
              {account}
            </div>
          </div>
        )}

        <div className="min-w-0">
          <SubscriptionBanner me={me} />
          <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 lg:px-10 lg:py-8">{children}</main>
        </div>
      </div>
    </MeContext.Provider>
  );
}

