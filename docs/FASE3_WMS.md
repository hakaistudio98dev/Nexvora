# Fase 3 — WMS (Warehouse Management System)

## Alur kerja gudang
```
Supplier ─► INBOUND (scan terima) ─► selesai ─► stok masuk + task PUTAWAY ─► scan bin ─► stok di bin
                                                  (kurang/lebih/rusak → exception)

Order ALLOCATED ─► WAVE (kumpulan order) ─► task PICK per bin, urut jalur jalan
   └─► picker: scan lokasi → scan barang ─► selesai  (barang tidak ada → "Barang kurang" → exception → pick ulang)
        └─► PACKING STATION: scan no. order → scan tiap barang → timbang ─► READY_TO_SHIP
             (salah barang / kelebihan scan ditolak; berat di luar toleransi wajib alasan → exception)

CYCLE COUNT: supervisor pilih area → operator hitung tanpa melihat angka sistem → submit → supervisor setujui
   → selisih otomatis jadi penyesuaian di ledger + stok bin dikoreksi, akurasi (%) tercatat
```

## Stok per bin
- `bin_stock` menyimpan isi setiap bin (`quantity`) dan jumlah yang sedang dialokasikan untuk picking (`allocated`).
- Setiap perpindahan tercatat di `bin_movements` (append-only): PUTAWAY, PICK, COUNT_ADJUST, ADJUST.
- **Stok belum di bin** = stok fisik gudang − total isi bin: barang baru diterima, atau stok dari Fase 2.
  Tempatkan lewat Scanner → Putaway. Selama belum di-putaway, picking tetap bisa diambil dari area penerimaan (STAGING).
- Invarian: total isi bin tidak pernah melebihi stok fisik. Dijaga oleh penguncian baris saldo gudang saat putaway,
  dan dicek oleh `GET /inventory/reconcile` (field `bin_overflow`).
- Stok fisik gudang (`on_hand`) baru berkurang saat order **dikirim** (aksi `ship`). Di antara picking dan pengiriman,
  barang berada di area packing.
- Penyesuaian stok yang mengurangi fisik, bila stok di luar bin tidak cukup, wajib menyebut bin (`location_code`).

## Aplikasi scanner (`/scan`)
Web app mobile-first untuk operator. Dibuka di browser ponsel atau handheld Android, tanpa perlu install dari store.
- **Scanner genggam** (mode keyboard / HID): langsung bekerja, karena setiap scan diakhiri Enter.
- **Kamera ponsel**: tombol kamera (library ZXing). Wajib dibuka lewat **HTTPS** (atau `localhost`) agar izin kamera muncul.
- **Offline**: bila sinyal hilang, scan terima/putaway/pick/hitung/lapor disimpan di perangkat dan dikirim otomatis
  saat online. Setiap scan membawa `client_event_id` unik, jadi pengiriman ulang tidak pernah mencatat stok dua kali.
  Packing tetap butuh koneksi karena verifikasi harus langsung.
- Umpan balik bunyi dan getar untuk scan benar/salah.
- Gudang aktif dipilih sekali per perangkat.

## Cetak label
Console → Gudang (WMS) → Cetak label: barcode Code128 untuk **bin** (isi `full_code`, mis. `A-01-1-B1`) dan **SKU**
(isi barcode SKU, atau kode SKU bila barcode kosong). Cetak di printer biasa atau printer label.

## Perubahan perilaku dari Fase 2
- Aksi order **Mulai picking** sekarang membuat task picking (wave berisi 1 order).
- **Mulai packing** dan **Siap kirim** tidak lagi berupa tombol manual. Keduanya hanya lewat packing station,
  sehingga setiap paket pasti terverifikasi item dan beratnya.

## Endpoint
| Method | Path | Izin |
|---|---|---|
| GET | `/wms/stats`, `/wms/scan?code=`, `/wms/bins`, `/wms/unplaced`, `/wms/tasks`, `/wms/waves`, `/wms/inbound`, `/wms/counts`, `/wms/exceptions`, `/wms/pack-queue`, `/wms/pack/{order}`, `/wms/labels/bins` | `wms:read` |
| POST | `/wms/inbound/{id}/receive`, `/wms/tasks/{id}/putaway`, `/wms/putaway`, `/wms/tasks/{id}/pick`, `/wms/tasks/{id}/short`, `/wms/pack/{order}/scan`, `/wms/pack/{order}/complete`, `/wms/counts/{id}/lines`, `/wms/counts/{id}/submit`, `/wms/exceptions` | `wms:operate` |
| POST | `/wms/inbound`, `/wms/inbound/{id}/complete`, `/wms/inbound/{id}/cancel`, `/wms/waves`, `/wms/counts`, `/wms/counts/{id}/cancel`, `/wms/exceptions/{id}/resolve` | `wms:manage` |
| POST | `/wms/counts/{id}/approve` | `wms:manage` + `inventory:adjust` |

## Izin per peran
| Peran | Lihat | Kerjakan task (scanner) | Kelola (inbound, wave, count, exception) |
|---|---|---|---|
| Tenant Admin, Warehouse Manager | ✓ | ✓ | ✓ |
| Warehouse Operator | ✓ | ✓ | — |
| Finance, Customer Service, Viewer | ✓ | — | — |

## Pengaturan
`PACK_WEIGHT_TOLERANCE_PCT` (default 10): toleransi selisih berat paket terhadap total berat SKU. SKU tanpa berat
membuat pengecekan berat dilewati untuk order tersebut, jadi isi berat SKU di menu Produk agar verifikasi aktif.
