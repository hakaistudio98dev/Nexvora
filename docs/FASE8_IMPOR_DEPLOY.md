# Fase 8 — Impor data, reservasi ulang otomatis, dan deploy produksi

Tiga hal yang selama ini menjadi penghalang untuk dipakai sungguhan.

## 1. Impor CSV massal (menu **Impor data**)
Dua jenis: **Produk & SKU** dan **Stok awal**. Alurnya selalu dua langkah:

```
unggah CSV → sistem memeriksa SEMUA baris → Anda lihat ringkasannya → tekan Jalankan
```

- **Tidak ada yang berubah sebelum Anda menekan Jalankan.** Pemeriksaan hanya membaca.
- Kesalahan dilaporkan **per nomor baris file asli**, mis. "Baris 4 — Kode SKU BTL-500 muncul dua kali di file ini",
  jadi mudah diperbaiki di Excel.
- File dengan kesalahan bisa dijalankan sebagian, tetapi hanya kalau Anda mencentang **"lewati baris bermasalah"**,
  dan jumlah yang dilewati dilaporkan kembali.
- Judul kolom yang umum dikenali otomatis: `sku`, `product_name`, `qty`, `gudang`, `berat`, dan lainnya.
  Pemisah koma maupun titik koma sama-sama diterima (Excel Indonesia memakai titik koma).
- Impor SKU yang sudah ada = **memperbarui**, bukan menggandakan. Menjalankan pekerjaan impor yang sama dua kali
  tidak menggandakan data.
- Stok masuk lewat impor tercatat di ledger dengan alasan `IMPORT`, jadi tetap bisa ditelusuri dan lolos rekonsiliasi.
- Batas: 5.000 baris dan 5 MB per file. Butuh izin **Impor data** (Admin atau Warehouse Manager).

Urutannya: impor **produk** dulu, baru **stok awal** — karena stok merujuk ke kode SKU.

## 2. Reservasi ulang otomatis
Dulu, order yang masuk saat stok kosong akan tertahan sampai seseorang menekan "Coba alokasikan lagi".
Sekarang, begitu stok bertambah — lewat penerimaan barang, penerimaan gudang (inbound), atau impor stok —
order yang tertahan **langsung dicoba lagi secara otomatis**:

- Antreannya **adil**: order paling lama dilayani lebih dulu, bukan yang paling kecil.
- Order yang berhasil dapat stok dan sudah dibayar langsung naik ke tahap **Teralokasi**.
- Pengguna diberi tahu lewat notifikasi bahwa order tersebut sudah bisa diproses.
- Worker juga mengulang pemeriksaan ini secara berkala sebagai jaring pengaman.

Respons API penerimaan stok kini menyertakan `orders_reallocated`, dan hasil impor stok menyertakan
`order_dialokasikan`, supaya terlihat berapa order yang langsung tertolong.

## 3. Deploy produksi di VPS
```bash
# di VPS Ubuntu yang masih kosong, setelah isi proyek disalin ke /opt/nexvora
sudo bash deploy/setup_vps.sh nexvora.domainanda.com admin@domainanda.com
```
Skrip ini memasang Docker, menyalakan firewall (hanya SSH/HTTP/HTTPS), membuat `.env` produksi dengan kunci acak,
menjalankan seluruh layanan dengan **HTTPS otomatis (Let's Encrypt lewat Caddy)**, dan memasang **backup harian jam 02.00**.
Password admin pertama ditampilkan sekali di akhir — simpan, lalu ganti setelah login.

File terkait:
| File | Isi |
|---|---|
| `deploy/setup_vps.sh` | pemasangan awal, aman dijalankan ulang |
| `deploy/docker-compose.prod.yml` | lapisan produksi: Caddy, port database ditutup dari luar |
| `deploy/Caddyfile` | HTTPS, header keamanan, `/metrics` ditutup, jalur webhook kurir/pembayaran |
| `deploy/backup.sh` | `pg_dump` terkompresi + **verifikasi isi**, simpan 14 hari, opsional salin ke Google Drive/S3 via rclone |
| `deploy/restore.sh` | pemulihan dengan konfirmasi ketik "YA" |

**Backup yang belum pernah diuji restore bukan backup.** Lakukan sekali di awal:
```bash
bash deploy/backup.sh
bash deploy/restore.sh backups/nexvora-<tanggal>.sql.gz
```
Untuk menyimpan backup di luar server (sangat disarankan — kalau VPS-nya hilang, backupnya ikut hilang):
atur `rclone` lalu isi `RCLONE_REMOTE=gdrive:nexvora-backup` di environment cron.

## Setelah pindah ke VPS
1. Ganti password admin.
2. Isi `SMTP_*` untuk notifikasi email, serta kunci Midtrans dan Biteship bila dipakai.
3. Daftarkan URL webhook (`https://domain-anda/ext/api/v1/...`) di dashboard Midtrans dan Biteship.
4. Kamera scanner otomatis aktif karena situs sudah HTTPS.
