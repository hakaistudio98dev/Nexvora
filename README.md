# Nexvora — SaaS fulfillment: Foundation · OMS & Inventory · WMS · Langganan · Pengiriman & Retur · Analitik

Implementasi PRD v1.0:
- **Fase 1:** Auth, Tenant, RBAC, Product & SKU, Warehouse & Location, Audit log.
- **Fase 2:** Order Hub + state machine, inventory ledger immutable, reservasi stok ber-expiry, allocation engine,
  Idempotency-Key, penerimaan & penyesuaian stok. Detail: [docs/FASE2_OMS_INVENTORY.md](docs/FASE2_OMS_INVENTORY.md).
- **Fase 3:** stok per bin, inbound & receiving, putaway, wave picking, packing station (verifikasi item + berat),
  cycle count, exception queue, cetak label barcode, dan aplikasi scanner mobile `/scan` yang tahan offline.
  Detail: [docs/FASE3_WMS.md](docs/FASE3_WMS.md).
- **SaaS:** pendaftaran mandiri + trial, paket Starter/Growth/Enterprise dengan fitur & kuota, tagihan
  (transfer manual atau Midtrans), masa tenggang & mode baca-saja, API key integrasi, console admin platform.
  Detail: [docs/SAAS_LANGGANAN.md](docs/SAAS_LANGGANAN.md).
- **Fase 4:** akun kurir (manual, simulasi, Biteship), kurir tambahan/lokal per workspace, resi & label 100×150, manifest serah terima kurir, tracking
  otomatis/manual, retur (RMA) dengan inspeksi, refund & order pengganti. Detail: [docs/FASE4_PENGIRIMAN_RETUR.md](docs/FASE4_PENGIRIMAN_RETUR.md).
- **Fase 5:** dashboard KPI (PRD §25), papan SLA, produktivitas tim, SKU terlaris, kesehatan stok & saran pesan ulang,
  NOC tenant & platform, ekspor CSV, notifikasi in-app/email/webhook (HMAC), pengaturan SLA & batas stok.
  Detail: [docs/FASE5_ANALITIK_NOTIFIKASI.md](docs/FASE5_ANALITIK_NOTIFIKASI.md).

```
nexvora/
├── apps/
│   ├── api/                 FastAPI + SQLAlchemy 2 (async) + Alembic
│   │   ├── app/core/        config, db (konteks RLS), security, deps (RBAC), audit, rate limit
│   │   ├── app/models/      ORM model
│   │   ├── app/modules/     auth · tenants · users · catalog · warehouses · inventory · orders · wms · billing · apikeys · platform · public · shipping · returns · analytics · reports · notifications · settings · audit
│   │   ├── app/workers/     worker latar (reservasi kedaluwarsa)
│   │   ├── alembic/         migrasi SQL eksplisit + RLS + grants + seed RBAC
│   │   ├── scripts/         bootstrap super admin / tenant demo
│   │   └── tests/           tes integrasi terhadap PostgreSQL sungguhan
│   └── web/                 Next.js 14 (App Router) + TypeScript + Tailwind, pola BFF
├── infrastructure/          init role Postgres, Nginx
├── docs/SECURITY.md         model keamanan
└── docker-compose.yml
```

## Menjalankan di Windows (satu perintah)
```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

## Menjalankan dengan Docker
```bash
cp .env.example .env          # ganti semua password & JWT_SECRET
docker compose up --build -d  # postgres → migrate → api → web → nginx
make bootstrap                # super admin + tenant contoh "demo"
```
Buka http://localhost, login dengan tenant `platform` (super admin) atau `demo` / `admin@demo.nexvora.id`
(password tenant demo = `DEMO_ADMIN_PASSWORD`, default sama dengan password bootstrap).

## Development lokal tanpa Docker
```bash
# API
cd apps/api && pip install -r requirements-dev.txt
export DATABASE_MIGRATION_URL=postgresql+psycopg://nexvora_owner:...@localhost:5432/nexvora
export DATABASE_URL=postgresql+asyncpg://nexvora_app:...@localhost:5432/nexvora
alembic upgrade head && uvicorn app.main:app --reload     # docs: http://localhost:8000/docs
python -m pytest -q                                        # 57 tes: keamanan, stok, order, gudang, langganan, pengiriman, retur, analitik, notifikasi

# Web
cd apps/web && cp .env.example .env.local && npm install && npm run dev
npm run gen:types     # generate tipe TypeScript dari OpenAPI (kontrak FE ↔ BE)
```

## API v1
| Method | Path | Izin |
|---|---|---|
| POST | `/api/v1/auth/login` · `/refresh` · `/logout` | publik |
| GET | `/api/v1/auth/me` · POST `/logout-all` · `/change-password` | login |
| GET/POST/PATCH | `/api/v1/tenants` | `tenant:manage` (super admin) |
| GET/POST/PATCH | `/api/v1/users`, GET `/api/v1/roles` | `user:*`, `role:read` |
| GET/POST/PATCH | `/api/v1/products`, `/products/{id}/skus`, `/skus` | `product:*` |
| GET/POST/PATCH | `/api/v1/warehouses`, `/warehouses/{id}/locations` | `warehouse:*` |
| GET | `/api/v1/audit-logs` | `audit:read` |
| GET/POST | `/api/v1/orders`, `/orders/{id}/actions/{aksi}` | `order:*` |
| GET/POST | `/api/v1/inventory`, `/receipts`, `/adjustments`, `/ledger`, `/reconcile` | `inventory:*` |
| GET/POST | `/api/v1/wms/*` (inbound, tasks, waves, pack, counts, exceptions, scan) | `wms:*` |
| GET/POST | `/api/v1/billing/*`, `/api-keys`, `/platform/*`, `/public/*` | lihat docs/SAAS_LANGGANAN.md |
| GET/POST | `/api/v1/shipping/*`, `/api/v1/returns/*` | `shipping:*`, `returns:*` |
| GET | `/api/v1/analytics/*`, `/api/v1/reports/{nama}.csv` | `analytics:read` (+ izin data terkait) |
| GET/POST/PATCH | `/api/v1/notifications/*`, `/api/v1/settings` | `notification:*`, `settings:manage` |

Format error seragam: `{"error": {"code", "message", "correlation_id", "details?"}}`.

## Upgrade dari fase sebelumnya
Cukup jalankan lagi `start.ps1` (atau `docker compose up --build -d`). Container `migrate` menjalankan migrasi
baru otomatis (`0002_oms_inventory`, `0003_wms`); data lama tetap utuh. Stok dari Fase 2 muncul sebagai
"belum di bin" dan bisa ditempatkan lewat Scanner → Putaway.

## Scanner di ponsel
Buka `http://<IP-komputer>/scan` dari ponsel di jaringan yang sama untuk scanner genggam. **Kamera** hanya aktif
lewat HTTPS; untuk uji lokal gunakan `http://localhost/scan` di komputer, atau pasang TLS di Nginx.

## Integrasi dari luar
Sistem eksternal (marketplace, website, ERP) memanggil `http://<host>/ext/api/v1/...` dengan header `X-API-Key`.
Pendaftaran pelanggan baru: `http://<host>/signup`.

## Berikutnya
- **Fase 6 — Skala:** metrik Prometheus/OpenTelemetry, pemisahan worker per antrean, read replica untuk analitik.
- **Fase 7 — AI:** forecasting permintaan, prediksi stok habis, rekomendasi kurir, deteksi anomali order.
