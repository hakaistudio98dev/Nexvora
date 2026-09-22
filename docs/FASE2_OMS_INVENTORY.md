# Fase 2 — OMS & Inventory

## Alur order
```
POST /orders ──► CREATED ──mark_paid──► PAID ──(stok ada)──► ALLOCATED ──► PICKING ──► PACKING
                   │                     │                                                  │
                   │  reservasi stok     └─ stok kurang: tetap PAID + "Stok kurang",        ▼
                   │  ber-expiry            coba lagi via aksi reserve       READY_TO_SHIP ──► SHIPPED ──► DELIVERED
                   │                                                               (stok fisik berkurang)
                   └─ belum bayar & lewat batas waktu → CANCELLED otomatis (worker), stok kembali
CREATED/PAID/ALLOCATED ──cancel (wajib alasan)──► CANCELLED      CREATED/PAID ──mark_failed──► FAILED
```
- Reservasi dibuat **saat order masuk**, sehingga stok yang sama tidak bisa terjual dua kali di channel lain.
- Order belum dibayar: reservasi kedaluwarsa setelah `RESERVATION_TTL_MINUTES` (default 60). Order sudah bayar: tidak pernah kedaluwarsa.
- Order marketplace yang sudah lunas: kirim `"paid": true` agar langsung dialokasikan.
- Semua transisi tercatat di `order_status_history` (append-only) dan audit log.

## Allocation engine v1
Gudang aktif yang memiliki stok **lengkap untuk semua item**, diurutkan: kota gudang = kota tujuan → total stok tersedia
terbanyak → kode gudang. Kandidat dikunci (`SELECT … FOR UPDATE`) lalu dicek ulang sebelum reservasi, jadi aman dari
race condition. Split shipment antar gudang dijadwalkan di fase lanjutan.

## Inventory
| Kolom | Arti |
|---|---|
| On hand | Stok fisik di gudang |
| Reserved | Sudah dijanjikan ke order aktif |
| Available | `on_hand − reserved`, yang boleh dijual |
| Damaged | Stok rusak, dipisahkan dari stok jual |

Setiap perubahan lewat satu fungsi (`inventory.service.apply`) yang dalam satu transaksi mengunci baris saldo,
memvalidasi invarian, memperbarui saldo, dan menulis **ledger immutable**. Database menolak saldo negatif dan
reserved > on hand (CHECK constraint). `GET /inventory/reconcile` membuktikan saldo = Σ ledger.

Jenis mutasi: `RECEIPT`, `RESERVE`, `RELEASE`, `SHIP`, `ADJUSTMENT`, `DAMAGE` (dan `RETURN` untuk fase 4).
Penyesuaian wajib alasan (`COUNT_CORRECTION`, `FOUND`, `LOST`, `DAMAGED`, `WRITE_OFF_DAMAGED`, `OTHER`) dan catatan,
hanya untuk peran dengan izin `inventory:adjust`, dan tidak bisa menurunkan stok fisik di bawah jumlah yang sudah
direservasi.

## Integrasi channel (contoh)
```bash
curl -X POST https://nexvora.anda.com/api/v1/orders \
  -H "Authorization: Bearer <token>" \
  -H "Idempotency-Key: shopee-240921-000123" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "SHOPEE", "external_ref": "240921ABCDEF", "paid": true,
    "customer": {"name": "Sari", "phone": "0812..."},
    "shipping": {"address": "Jl. Kemang Raya 5", "city": "Jakarta", "postal_code": "12730"},
    "items": [{"sku_code": "TSH-BLK-M", "quantity": 2, "unit_price": "89000"}],
    "shipping_fee": "12000"
  }'
```
- **Idempotency-Key** sama + isi sama → respons yang sama (header `Idempotent-Replayed: true`). Tidak ada order ganda
  walau request dikirim ulang atau paralel. Form di web console juga mengirim kunci ini otomatis.
- Kunci sama + isi berbeda → `422 IDEMPOTENCY_CONFLICT`.
- `channel + external_ref` yang sudah ada → `409 DUPLICATE_ORDER`.

## Endpoint baru
| Method | Path | Izin |
|---|---|---|
| GET | `/api/v1/orders` (filter `status`, `stock_status`, `channel`, `q`), `/orders/stats`, `/orders/{id}` | `order:read` |
| POST | `/api/v1/orders` (header `Idempotency-Key` opsional) | `order:write` |
| POST | `/api/v1/orders/{id}/actions/{mark_paid,reserve,allocate,cancel,mark_failed}` | `order:write` |
| POST | `/api/v1/orders/{id}/actions/{start_picking,start_packing,ready_to_ship,ship,deliver}` | `order:fulfill` |
| GET | `/api/v1/inventory`, `/inventory/ledger`, `/inventory/reconcile`, `/inventory/reasons` | `inventory:read` |
| POST | `/api/v1/inventory/receipts` | `inventory:write` |
| POST | `/api/v1/inventory/adjustments` | `inventory:adjust` |
| POST | `/api/v1/inventory/reserve` | `order:write` |

## Izin per peran
| Peran | Order | Tahap fulfillment | Terima stok | Penyesuaian |
|---|---|---|---|---|
| Tenant Admin | ✓ | ✓ | ✓ | ✓ |
| Warehouse Manager | lihat | ✓ | ✓ | ✓ |
| Warehouse Operator | lihat | ✓ | — | — |
| Customer Service | ✓ | — | — | — |
| Finance, Viewer | lihat | — | — | — |
