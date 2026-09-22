# Fase 5 — Analitik, NOC, Laporan & Notifikasi

## KPI (menu Analitik)
Semua KPI dihitung langsung dari data operasional, tanpa tabel ringkasan yang bisa basi. Filter tersedia untuk rentang
tanggal, gudang, dan channel. Tanggal mengikuti zona waktu di Pengaturan.

| KPI (PRD §25) | Rumus |
|---|---|
| Fulfillment rate | order yang sudah dikirim ÷ order aktif (tidak batal/gagal) yang dibuat di rentang |
| Kepatuhan SLA | order yang diserahkan ke kurir ≤ *SLA jam* setelah dibayar ÷ order yang dikirim di rentang |
| Waktu kirim rata-rata & P90 | jam dari dibayar sampai diserahkan ke kurir |
| Akurasi picking | task pick selesai ÷ (selesai + dilaporkan kurang) |
| Akurasi packing | paket tanpa selisih berat ÷ semua paket |
| Akurasi inventory | baris cycle count yang cocok ÷ baris yang dihitung (hitungan yang disetujui) |
| Exception rate | order dengan exception gudang ÷ order |
| Return rate | retur (tidak ditolak) ÷ order yang dikirim |
| Ongkir per order | rata-rata ongkir aktual pengiriman |

**Paket Starter:** KPI di atas, grafik harian, per channel, dan ekspor CSV.
**Paket Growth ke atas:** papan SLA langsung, produktivitas per operator (unit pick, akurasi, paket, putaway, hitung),
SKU terlaris, kesehatan stok (habis/menipis/tidak laku) dengan saran jumlah pesan ulang, dan NOC.

## Papan SLA
Order yang sudah dibayar tetapi belum diserahkan ke kurir ditampilkan dengan sisa waktu:
**terlambat**, **berisiko** (sisa ≤ *jam peringatan dini*), atau **aman**. SLA dan jam peringatan diatur di **Pengaturan**.

## NOC
- **Workspace** (`/noc`): worker latar, SLA, order tertahan, exception terbuka, masalah pengiriman 7 hari, tracking kurir
  yang macet >48 jam, kegagalan notifikasi, serta aktivitas per channel. Channel yang tiba-tiba sepi menandakan integrasi
  putus.
- **Platform** (Admin platform → Kesehatan sistem): latensi database, Redis, detak worker, antrean notifikasi, backlog
  reservasi, dan jumlah tenant per status langganan.

## Ekspor CSV
Tersedia laporan order, item order, SLA, pengiriman, retur, saldo stok, dan ledger. File dibuat UTF-8 dengan BOM agar rapi
di Excel, dengan opsi pemisah titik koma untuk Excel berbahasa Indonesia. Maksimal 100.000 baris per file.
Sel teks yang diawali `= + - @` dinetralkan untuk mencegah formula injection. Setiap laporan butuh izin data terkait
(mis. laporan retur butuh `returns:read`).

## Notifikasi
| Event | Kapan |
|---|---|
| Stok menipis | tersedia ≤ batas (per SKU atau default Pengaturan), maksimal sekali per SKU per gudang per hari |
| Order tertahan | order tidak mendapat stok |
| SLA berisiko / terlambat | berdasarkan SLA di Pengaturan, sekali per order |
| Masalah pengiriman | paket gagal diantar atau dikembalikan kurir |
| Exception gudang | laporan barang kurang, rusak, salah, atau berat tidak cocok |
| Retur baru | retur diajukan atau dibuat otomatis |
| Langganan | trial hampir habis atau pembayaran terlambat |
| Status order (khusus webhook) | setiap perubahan status order, untuk sinkronisasi ke sistem luar |

- **In-app:** ikon lonceng dengan jumlah belum dibaca, dan halaman Notifikasi.
- **Email:** isi `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, dan `SMTP_FROM` di `.env`. Tanpa SMTP, pengiriman email
  ditandai gagal dengan alasan yang jelas; notifikasi in-app tetap berjalan.
- **Webhook (Growth+):** POST JSON ke URL **https** publik. URL ke alamat privat/localhost ditolak (perlindungan SSRF), dan
  redirect tidak diikuti. Header yang dikirim:
  - `X-Nexvora-Event`: kode event
  - `X-Nexvora-Delivery`: ID unik pengiriman (untuk idempotensi di sisi penerima)
  - `X-Nexvora-Signature: t=<unix>,v1=<hex>` dengan `v1 = HMAC_SHA256(secret, "<t>.<body>")`

  Bila gagal, dicoba ulang setelah 1 menit, 5 menit, 30 menit, 2 jam, lalu 6 jam. Riwayat per kanal bisa dilihat di Pengaturan.

**Menyambungkan ke WhatsApp/Telegram:** buat workflow **n8n** dengan node *Webhook* (URL https publik), verifikasi signature
di node *Code*, lalu teruskan ke node WhatsApp gateway atau Telegram. Dengan cara ini tidak ada kredensial WhatsApp yang
perlu disimpan di Nexvora.

Contoh verifikasi signature (Node.js):
```js
const [t, v1] = req.headers["x-nexvora-signature"].split(",").map(x => x.split("=")[1]);
const expected = crypto.createHmac("sha256", SECRET).update(`${t}.${rawBody}`).digest("hex");
if (!crypto.timingSafeEqual(Buffer.from(v1), Buffer.from(expected)) || Date.now()/1000 - t > 300) reject();
```

## Pengaturan (`/settings`)
SLA kirim (jam), peringatan dini (jam), batas stok menipis default, dan zona waktu laporan. Batas stok bisa ditimpa per SKU
lewat field **Batas stok menipis** di menu Produk.

## Catatan skala
Deteksi event berjalan di worker setiap 30 detik, dengan dedup per kejadian. Untuk volume besar (ratusan ribu order per hari),
query analitik sebaiknya diarahkan ke read replica. Ini termasuk rencana Fase 6.
