# Fase 4 — Pengiriman & Retur

## Alur pengiriman
```
READY_TO_SHIP ─► buat resi (akun kurir + kurir + layanan) ─► cetak label 100×150 mm
     └─► MANIFEST serah terima: kurir datang → scan resi tiap paket → isi nama kurir & plat → "Serahkan"
           └─► semua order di manifest → SHIPPED (stok fisik berkurang, tercatat di ledger)
                 └─► tracking: IN_TRANSIT → OUT_FOR_DELIVERY → DELIVERED (order otomatis DELIVERED)
                                                   └─ RETURNED_TO_SENDER → RMA otomatis (alasan: gagal kirim)
```
- **Resi wajib ada** sebelum order bisa dikirim. Satu order hanya punya satu pengiriman aktif. Resi unik per kurir.
- Resi bisa **dibatalkan** selama paket belum diserahkan dan belum masuk manifest.
- Manifest menolak paket untuk kurir lain, dari gudang lain, belum siap, atau yang sudah discan.
- Status tracking tidak bisa mundur, kecuali "gagal antar" yang kemudian dikirim ulang.
- Order juga bisa diserahkan satuan lewat tombol **Serahkan ke kurir** di halaman Order.

## Jenis akun kurir
| Jenis | Cocok untuk | Resi | Tracking |
|---|---|---|---|
| **Manual** (selalu tersedia) | Order marketplace (AWB dari Shopee/Tokopedia/TikTok), drop-off di counter, kurir toko sendiri | Diketik atau discan | Diperbarui manual dari web kurir |
| **Simulasi** | Mencoba alur end-to-end tanpa kontrak kurir | Resi palsu `SIM…` | Bergerak otomatis tiap `SIMULATOR_STEP_SECONDS` |
| **Biteship** (paket Growth+) | Satu integrasi untuk JNE, J&T, SiCepat, AnterAja, Pos, ID Express, Ninja, Lion, GoSend, Grab | Dibuat otomatis via API | Otomatis: webhook + polling worker |

### Kurir lain (kurir lokal / belum ada di daftar)
Daftar bawaan: JNE, J&T, SiCepat, AnterAja, Pos, ID Express, Ninja, Lion, TIKI, Wahana, SAP, RPX, Paxel, Lalamove,
Borzo, GoSend, Grab, dan Kurir toko sendiri.

Kurir lain bisa ditambahkan di **Pengiriman → Daftar kurir → Tambah kurir lain**:
- Isi nama, kode, layanan (dipisah koma), **link lacak** dengan `{resi}` (mis. `https://kurir.id/lacak?no={resi}`), dan kontak.
- Kurir tambahan hanya terlihat di workspace Anda. Kurir ini bisa dipakai lewat akun **Manual (input resi)**, untuk manifest
  serah terima, label, dan update tracking manual. Tombol **Lacak di situs kurir** otomatis muncul di detail order.
- Kurir yang dinonaktifkan tidak bisa dipakai untuk resi baru, tetapi riwayat pengiriman lamanya tetap menampilkan nama kurir.
- Pemesanan otomatis via Biteship hanya untuk kurir yang didukung Biteship.

### Mengaktifkan Biteship
1. Daftar di Biteship, ambil API key (mulai dari mode testing).
2. Console → Pengiriman → Akun kurir → Tambah → Biteship → tempel API key. Key disimpan **terenkripsi**
   dengan `APP_ENCRYPTION_KEY`.
3. Salin URL webhook yang tampil di kartu akun ke dashboard Biteship.
4. Lengkapi **kode pos & telepon gudang** (menu Gudang) serta kode pos pelanggan di order. Keduanya dipakai untuk tarif & pickup.

Keamanan webhook: URL berisi token rahasia per akun, dan isi payload **tidak dipercaya**. Sistem selalu menarik status
terbaru langsung dari API Biteship (pull-on-notify).

> Skema API Biteship di adapter mengikuti dokumentasi publik v1 dan sudah diuji dengan mock. Uji dulu di akun sandbox
> Biteship sebelum dipakai untuk paket sungguhan. Semua pemanggilan API ada di `app/modules/shipping/providers.py`.

## Alur retur (RMA)
```
Ajukan (CS/admin, dari order SHIPPED/DELIVERED) ─► REQUESTED ─► setujui / tolak (tolak = status order dikembalikan)
   └─► APPROVED ─► gudang menerima barang ─► RECEIVED   (stok masuk bucket "returned", belum bisa dijual)
         └─► inspeksi per SKU: layak jual / rusak ─► INSPECTED
               layak jual → stok tersedia + task putaway   ·   rusak → bucket damaged
         └─► penyelesaian: REFUND (nominal + referensi) · REPLACEMENT (order pengganti Rp0 otomatis) · NONE ─► CLOSED
```
Status order mengikuti PRD: `SHIPPED/DELIVERED → RETURN_REQUESTED → RETURNED → REFUNDED`.
Jumlah retur dibatasi jumlah yang dikirim dikurangi retur sebelumnya. Satu order hanya boleh punya satu retur aktif.
Semua mutasi stok retur tercatat di ledger (`d_returned`, `RETURN_RECEIVED/RESTOCK/DAMAGED`) dan tetap lolos
`/inventory/reconcile`.

## Izin per peran
| Peran | Resi, label, serah terima | Akun kurir | Ajukan/putuskan/selesaikan retur | Terima & inspeksi retur |
|---|---|---|---|---|
| Tenant Admin | ✓ | ✓ | ✓ | ✓ |
| Warehouse Manager / Operator | ✓ | — | — | ✓ |
| Customer Service | lihat | — | ✓ | — |
| Finance, Viewer | lihat | — | lihat | — |

## Scanner
Menu baru **Serah terima kurir** di `/scan`: pilih kurir yang datang, scan resi setiap paket, isi nama kurir, serahkan.

## Pengaturan baru (.env)
| Variabel | Fungsi |
|---|---|
| `APP_ENCRYPTION_KEY` | Kunci Fernet untuk mengenkripsi API key kurir. Wajib di production; jangan diganti setelah dipakai. Kalau terpaksa diganti, masukkan ulang API key di setiap akun kurir. |
| `SIMULATOR_STEP_SECONDS` | Kecepatan status kurir simulasi |
