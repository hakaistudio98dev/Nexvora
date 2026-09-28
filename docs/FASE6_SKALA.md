# Fase 6 — Kesiapan skala

Fase ini tidak menambah fitur yang terlihat pengguna. Tujuannya satu: sistem tetap benar dan terpantau ketika
order per hari naik dari puluhan menjadi puluhan ribu.

## 1. Outbox: event tidak pernah hilang
Dulu perubahan status order dideteksi dengan memindai tabel setiap 30 detik. Sekarang setiap kejadian penting
dicatat ke tabel `outbox_events` **di dalam transaksi yang sama** dengan perubahan datanya:

```
[ordernya berubah status] ──satu transaksi──> [outbox_events: ORDER_STATUS]
                                                      │  worker "dispatcher"
                                                      ▼
                                       webhook integrasi · notifikasi · Redis Stream
```

Artinya: kalau transaksi gagal, event ikut batal; kalau server mati setelah commit, event tetap terkirim begitu
worker hidup lagi. Event yang gagal dicoba ulang dengan jeda bertahap **30 detik → 2 menit → 10 menit → 1 jam**,
lalu ditandai `FAILED` dan muncul di NOC.

Event yang dicatat: perubahan status order, perubahan status pengiriman, retur dibuat, dan retur diselesaikan.

`EVENT_BUS=redis` membuat dispatcher juga menyalin event ke **Redis Stream** `nexvora.events`, sehingga sistem lain
(mis. data warehouse atau layanan AI di Fase 7) bisa ikut membaca tanpa membebani database utama.

## 2. Worker dipisah per pekerjaan
`WORKER_JOBS` menentukan pekerjaan yang dijalankan satu proses:

| Job | Isi pekerjaan |
|---|---|
| `maintenance` | lepas reservasi kedaluwarsa, tarik status kurir, deteksi event notifikasi, siklus langganan |
| `dispatcher` | kirim event dari outbox |

Di `docker-compose.yml` keduanya menjadi service terpisah (`worker` dan `dispatcher`), jadi antrean event tidak
tertahan pekerjaan perawatan yang berat, dan masing-masing bisa ditambah replikanya sendiri:

```bash
docker compose up -d --scale dispatcher=3
```

Setiap job dikunci dengan **advisory lock PostgreSQL** (`pg_try_advisory_xact_lock`), jadi meski ada 3 replika,
satu siklus job hanya dijalankan satu proses. Tidak ada pekerjaan ganda.

## 3. Metrik Prometheus
`GET /metrics` menyajikan format Prometheus. Nginx menutup jalur ini dari internet; Prometheus mengambilnya
langsung dari jaringan Docker (`http://api:8000/metrics`). Isi `METRICS_TOKEN` bila ingin ditambah token Bearer.

| Metrik | Gunanya |
|---|---|
| `nexvora_http_requests_total{method,route,status}` | lalu lintas & rasio error per endpoint |
| `nexvora_http_request_seconds` | histogram latensi (p95/p99) |
| `nexvora_worker_job_total{job,result}`, `nexvora_worker_job_seconds` | kesehatan worker |
| `nexvora_outbox_pending`, `nexvora_outbox_lag_seconds` | antrean event menumpuk atau tidak |
| `nexvora_notification_pending` | notifikasi belum terkirim |
| `nexvora_db_pool_in_use` | koneksi database menipis |

Jalankan Prometheus bawaan (opsional):
```bash
docker compose --profile observability up -d
# buka http://localhost:9090
```

Alert yang disarankan: `nexvora_outbox_lag_seconds > 300`, error rate 5xx > 1% selama 5 menit,
p95 latensi > 1 detik, dan worker tidak melapor (`nexvora_worker_job_total` tidak naik) lebih dari 5 menit.

## 4. Read replica untuk laporan
Isi `DATABASE_READ_URL` dengan alamat replika PostgreSQL, maka **semua query analitik dan ekspor CSV** memakai
koneksi itu — laporan berat tidak lagi memperlambat proses order dan picking. Koneksi replika dibuka sebagai
`READ ONLY` dengan `STATEMENT_TIMEOUT_MS` (bawaan 15 detik) supaya satu query nakal tidak menggantung.
Kalau variabel dikosongkan, semuanya tetap memakai database utama seperti biasa.

Indeks baru ditambahkan untuk query laporan yang paling sering: order per tanggal, item order, pengiriman,
paket, task gudang, hasil hitung stok, retur, exception, dan ledger.

## 5. Yang dipantau di NOC
- **Workspace** (`/noc`): baris baru "Antrean event ke sistem luar" — menampilkan event yang antre dan yang gagal.
- **Platform** (Admin platform → Kesehatan sistem): kartu "Antrean event (outbox)" dengan jumlah antre, gagal,
  dan keterlambatan event tertua dalam detik.

## Pengaturan baru (.env)
| Variabel | Bawaan | Fungsi |
|---|---|---|
| `WORKER_JOBS` | `maintenance,dispatcher` | pekerjaan yang dijalankan proses worker |
| `EVENT_BUS` | `db` | `db` (outbox saja) atau `redis` (outbox + Redis Stream) |
| `METRICS_TOKEN` | kosong | token Bearer untuk `/metrics` |
| `DATABASE_READ_URL` | kosong | read replica untuk analitik & ekspor |
| `STATEMENT_TIMEOUT_MS` | `15000` | batas waktu query di replika |

## Yang sengaja belum dibuat
- **OpenTelemetry tracing.** Prometheus + correlation ID di log sudah cukup untuk skala saat ini; tracing baru
  berguna setelah layanan dipecah menjadi beberapa service.
- **Kafka / RabbitMQ.** Outbox + Redis Stream menutup kebutuhan sekarang tanpa menambah satu sistem lagi yang
  harus dirawat. Kalau nanti pindah, isi outbox tinggal diarahkan ke broker baru — kode pemanggilnya tidak berubah.
- **Sharding database.** PostgreSQL satu instance dengan read replica masih jauh dari batasnya untuk beban ini.
