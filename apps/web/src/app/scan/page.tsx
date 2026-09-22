"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useMe } from "@/components/me-context";
import { api } from "@/lib/api";
import type { WmsStats } from "@/lib/wms";
import { useWarehouse } from "@/components/wh-context";

export default function ScanHome() {
  const me = useMe();
  const wh = useWarehouse()!;
  const [st, setSt] = useState<WmsStats | null>(null);
  useEffect(() => { api.get<WmsStats>(`/wms/stats?warehouse_id=${wh.id}`).then(setSt).catch(() => undefined); }, [wh.id]);
  const items = [
    { href: "/scan/pick", label: "Ambil barang", sub: st ? `${st.open_picks} tugas` : "", hot: (st?.open_picks ?? 0) > 0 },
    { href: "/scan/pack", label: "Packing", sub: st ? `${st.orders_picking + st.orders_packing} order` : "" },
    { href: "/scan/receive", label: "Terima barang", sub: st ? `${st.inbound_open} dokumen` : "" },
    { href: "/scan/putaway", label: "Simpan ke rak", sub: st ? `${st.open_putaway} tugas` : "", hot: (st?.open_putaway ?? 0) > 0 },
    { href: "/scan/count", label: "Hitung stok", sub: "Cocokkan isi rak" },
    { href: "/scan/handover", label: "Serah terima kurir", sub: st ? `${st.ready_to_ship} siap kirim` : "" },
    { href: "/scan/lookup", label: "Cek & lapor", sub: "Bin, SKU, order" },
  ];
  return (
    <>
      <p className="text-sm text-ink-soft">Halo {me.full_name.split(" ")[0]}, pilih pekerjaan:</p>
      <nav aria-label="Menu scanner" className="grid grid-cols-2 gap-3">
        {items.map((i) => (
          <Link key={i.href} href={i.href}
                className={`flex min-h-28 flex-col justify-between rounded-xl border-[3px] border-ink p-4 ${i.hot ? "bg-signal" : "bg-white"} active:translate-y-0.5`}>
            <span className="text-xl font-bold leading-tight">{i.label}</span>
            <span className="text-sm">{i.sub}</span>
          </Link>
        ))}
      </nav>
      {st && st.exceptions_open > 0 && <p className="text-sm font-semibold text-danger">{st.exceptions_open} exception terbuka menunggu supervisor.</p>}
    </>
  );
}
