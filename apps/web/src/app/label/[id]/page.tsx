"use client";
import { useEffect, useRef, useState } from "react";
import { api, errorText } from "@/lib/api";
import type { Shipment } from "@/lib/types";

type Label = Shipment & { sender: Record<string, string>; recipient: Record<string, string>; items: number; notes: string };

export default function LabelPage({ params }: { params: { id: string } }) {
  const [l, setL] = useState<Label | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const bar = useRef<SVGSVGElement>(null);
  const bar2 = useRef<SVGSVGElement>(null);
  useEffect(() => { api.get<Label>(`/shipping/shipments/${params.id}/label`).then(setL).catch((e) => setErr(errorText(e))); }, [params.id]);
  useEffect(() => {
    if (!l?.tracking_number) return;
    import("jsbarcode").then(({ default: J }) => {
      if (bar.current) J(bar.current, l.tracking_number!, { format: "CODE128", height: 70, width: 2, displayValue: false, margin: 0 });
      if (bar2.current) J(bar2.current, l.order_number, { format: "CODE128", height: 32, width: 1.4, displayValue: false, margin: 0 });
    }).then(() => { api.post(`/shipping/shipments/${l.id}/label-printed`).catch(() => undefined); }).catch(() => undefined);
  }, [l]);
  if (err) return <p className="p-6 text-danger">{err}</p>;
  if (!l) return <p className="p-6">Memuat label…</p>;
  return (
    <main className="flex min-h-screen flex-col items-center gap-4 bg-white p-4 print:p-0">
      <style>{"@page { size: 100mm 150mm; margin: 0 } @media print { .no-print { display:none } }"}</style>
      <button type="button" onClick={() => window.print()} className="no-print min-h-11 rounded bg-ink px-5 font-bold text-white">Cetak label (100×150 mm)</button>
      <article className="flex h-[150mm] w-[100mm] flex-col border-2 border-black text-[11px] leading-tight text-black">
        <div className="flex items-center justify-between border-b-2 border-black px-2 py-1.5"><span className="text-lg font-black">{l.courier_name}</span><span className="text-sm font-bold uppercase">{l.service_code}</span></div>
        <div className="border-b-2 border-black px-2 py-2 text-center"><svg ref={bar} className="mx-auto w-full" /><div className="font-mono text-lg font-black tracking-wider">{l.tracking_number}</div></div>
        <div className="border-b-2 border-black px-2 py-1.5"><div className="font-bold">PENERIMA</div><div className="text-sm font-black">{l.recipient.name} {l.recipient.phone}</div>
          <div>{l.recipient.address}</div><div className="font-bold">{l.recipient.city}{l.recipient.province ? `, ${l.recipient.province}` : ""} {l.recipient.postal_code}</div></div>
        <div className="border-b-2 border-black px-2 py-1.5"><div className="font-bold">PENGIRIM</div><div>{l.sender.name} {l.sender.phone}</div><div>{l.sender.address}, {l.sender.city} {l.sender.postal_code}</div></div>
        <div className="grid grid-cols-3 border-b-2 border-black text-center"><div className="border-r-2 border-black p-1"><div>Berat</div><div className="font-bold">{l.weight_g ? `${(l.weight_g / 1000).toFixed(2)} kg` : "-"}</div></div>
          <div className="border-r-2 border-black p-1"><div>Isi</div><div className="font-bold">{l.items} pcs</div></div><div className="p-1"><div>Ongkir</div><div className="font-bold">{l.cost ? `Rp${Number(l.cost).toLocaleString("id-ID")}` : "-"}</div></div></div>
        <div className="mt-auto px-2 py-1.5"><svg ref={bar2} className="w-2/3" /><div className="font-mono font-bold">{l.order_number}</div></div>
      </article>
      {l.provider === "simulator" && <p className="no-print text-sm font-semibold text-danger">Label simulasi — bukan resi kurir sungguhan.</p>}
    </main>
  );
}
