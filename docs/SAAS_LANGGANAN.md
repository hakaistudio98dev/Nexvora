# Sistem SaaS — paket, langganan, tagihan, API key

## Alur pelanggan
```
Landing page ─► /signup (pilih paket) ─► workspace + admin dibuat ─► TRIAL 14 hari (fitur paket penuh)
     │
     ├─ bayar di menu Langganan ────────────────────────────► ACTIVE (periode bulanan/tahunan)
     │                                                           │ H-7: invoice perpanjangan otomatis
     └─ trial habis tanpa bayar ─► PAST_DUE (masa tenggang 7 hari, fitur tetap jalan, banner merah)
                                     └─ tenggang habis ─► SUSPENDED = baca-saja
ACTIVE + "hentikan di akhir periode" ─► CANCELLED (baca-saja) saat periode berakhir
```
- **Baca-saja** berarti semua data tetap bisa dilihat dan diekspor, tetapi aksi tulis ditolak (`402 SUBSCRIPTION_INACTIVE`).
  Menu Langganan tetap berfungsi agar tenant bisa membayar. **Data tidak pernah dihapus otomatis.**
- Semua perubahan status dijalankan oleh service `worker` (setiap 30 detik) dan tercatat di audit log.

## Paket bawaan
Harga di bawah ini adalah **contoh**. Ubah dari **Admin platform → Paket & harga**, tanpa deploy ulang.

| | Starter | Growth | Enterprise |
|---|---|---|---|
| Fitur | Order & inventory | + WMS & scanner, API key | + dukungan prioritas |
| Gudang / pengguna / SKU aktif | 1 / 3 / 500 | 3 / 15 / 5.000 | tanpa batas |
| Order per bulan (kuota lunak) | 1.000 | 10.000 | tanpa batas |
| Harga contoh | Rp299.000/bln · Rp2.990.000/thn | Rp899.000/bln · Rp8.990.000/thn | kontrak (via sales) |

## Cara pembatasan bekerja
- **Fitur** dikunci di API berdasarkan prefix izin: semua `wms:*` butuh fitur `wms`, `order:*`/`inventory:*` butuh `oms`,
  `apikey:*` butuh `api_keys`. Endpoint baru otomatis ikut terkunci. Menu yang tidak termasuk paket tampil dengan label
  "Upgrade".
- **Batas keras** (gudang, pengguna, SKU aktif) dicek saat data dibuat atau diaktifkan kembali (`402 PLAN_LIMIT`).
- **Kuota order lunak**: order tidak pernah ditolak karena kuota, supaya order marketplace tidak hilang di tengah operasional.
  Peringatan muncul di 80% dan 100%.
- **Turun paket** ditolak bila pemakaian saat ini melebihi batas paket tujuan (`409 USAGE_EXCEEDS_PLAN`).
- Akun platform (super admin) tidak berlangganan dan tidak dibatasi.

## Pembayaran
| `BILLING_PROVIDER` | Cara kerja |
|---|---|
| `manual` (default) | Tenant melihat invoice + `BANK_TRANSFER_INFO`. Super admin mencocokkan mutasi rekening lalu klik **Tandai lunas** di Admin platform. |
| `midtrans` | Tombol **Bayar sekarang** membuka Midtrans Snap (VA, QRIS, e-wallet, kartu). Status lunas masuk otomatis via webhook. |

Pengaturan Midtrans:
1. Isi `MIDTRANS_SERVER_KEY` (mulai dengan key Sandbox) dan `BILLING_PROVIDER=midtrans` di `.env`.
2. Di dashboard Midtrans, set **Payment Notification URL** ke
   `https://<domain-anda>/ext/api/v1/billing/webhooks/midtrans`.
3. Webhook diverifikasi dengan signature SHA-512 **dan** nominal harus sama persis dengan invoice. Pembayaran ganda
   tidak memperpanjang langganan dua kali.

Perpanjangan disambung dari akhir periode berjalan. Ganti paket berlaku sejak tanggal bayar (belum ada prorata).

## API key (paket Growth ke atas)
- Dibuat di menu **API key**, dengan izin terbatas: `order:*`, `inventory:*`, `product:*`, `warehouse:read`, `wms:read`.
  Tidak bisa mengelola pengguna, peran, atau tagihan.
- Rahasia ditampilkan **sekali**; server hanya menyimpan hash SHA-256. Bisa dicabut kapan saja dan kedaluwarsa (default 1 tahun).
- Base URL integrasi: `https://<domain>/ext/api/v1` dengan header `X-API-Key: nxk_…`. Rate limit 1.200 request/menit per key.
- Aksi lewat API key tercatat di audit log tanpa `actor_user_id`.

## Endpoint
| Method | Path | Akses |
|---|---|---|
| GET | `/public/plans` · POST `/public/signup` | publik (signup dibatasi 5/jam per IP) |
| GET | `/billing/subscription`, `/billing/plans`, `/billing/invoices` | `billing:read` (Tenant Admin, Finance) |
| POST | `/billing/checkout`, `/billing/invoices/{id}/pay`, `/billing/cancel`, `/billing/resume` | `billing:manage` (Tenant Admin) |
| POST | `/billing/webhooks/midtrans` | Midtrans (signature) |
| GET/POST | `/api-keys`, `/api-keys/{id}/revoke`, `/api-keys/scopes` | `apikey:manage` + fitur `api_keys` |
| GET/PATCH/POST | `/platform/plans`, `/platform/subscriptions`, `/platform/tenants/{id}/subscription`, `/platform/invoices`, `/platform/invoices/{id}/mark-paid` | super admin |

## Upgrade dari versi sebelumnya
Migrasi `0004_saas` otomatis memberi setiap tenant yang sudah ada **trial Growth 14 hari**, jadi tidak ada yang langsung
terkunci. Setelah itu tentukan paketnya lewat Admin platform → Tenant & langganan (mis. set ACTIVE + tanggal berakhir
untuk klien kontrak).
