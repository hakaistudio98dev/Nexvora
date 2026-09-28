# Asisten dalam aplikasi

Menu **Asisten** memungkinkan Anda bertanya dan memberi perintah dengan bahasa biasa, tanpa menghafal letak menu.

```
Anda: berapa order hari ini?
Asisten: Hari ini: 41 siap diambil dari rak, 1 siap kirim, 55 tertahan karena stok.
         [kartu ringkasan]

Anda: mulai picking SO-2609-000002
Asisten: Perintah ini mengubah data, jadi saya perlu konfirmasi Anda dulu.
         [ Mulai ambil barang untuk SO-2609-000002?  → Ya, jalankan | Batal ]
```

## Yang bisa ditanyakan
| Contoh kalimat | Hasil |
|---|---|
| "berapa order hari ini", "apa yang perlu dikerjakan" | ringkasan pekerjaan |
| "stok TSH-BLK-M", "sisa stok kaos hitam" | saldo stok semua gudang |
| "cari order milik Rina", "order yang siap kirim" | daftar order |
| "detail SO-2609-000001" | isi order + resinya |
| "lacak SO-2609-000001" / "lacak JNE0099887766" | riwayat pelacakan paket |
| "order mana yang terlambat" | papan SLA (paket Growth) |
| "ada retur baru?" | retur yang sedang diproses |
| "laporan penjualan 7 hari" | KPI periode tersebut |
| "stok apa yang mau habis", "kurir terbaik ke Bandung", "ada yang aneh hari ini" | fitur AI (paket Enterprise) |

## Yang bisa diperintahkan
`tandai SO-… sudah dibayar` · `mulai picking SO-…` · `batalkan SO-… karena …`

Perintah **tidak pernah langsung dijalankan**. Asisten menyiapkannya, lalu Anda menekan **Ya, jalankan**.
Setiap perintah yang dijalankan tercatat di **Riwayat aktivitas** dengan aksi `assistant.action`, lengkap dengan
nama Anda, isi perintah, dan hasilnya.

## Batas kewenangan
Asisten **tidak punya hak istimewa**. Setiap permintaan dijalankan memakai izin peran dan paket langganan Anda,
lewat jalur yang sama dengan tombol di layar:

- Staf CS tidak akan melihat perintah picking, dan permintaannya ditolak bila tetap dicoba.
- Fitur di luar paket dijawab dengan penjelasan paketnya, bukan dikerjakan diam-diam.
- Data tenant lain tidak bisa disentuh — dijaga Row Level Security di database, bukan hanya oleh kode aplikasi.
- Saat langganan berstatus baca-saja, perintah pengubah data ditolak seperti biasa.

## Dua mesin
| Mesin | Kapan dipakai | Catatan |
|---|---|---|
| **Aturan** (bawaan) | `ANTHROPIC_API_KEY` kosong | Mencocokkan kalimat Indonesia ke perkakas. Deterministik, gratis, dan **tidak ada data yang keluar dari server Anda**. |
| **Claude** | `ANTHROPIC_API_KEY` diisi | Memahami kalimat bebas dan bisa menggabungkan beberapa langkah. Isi percakapan dikirim ke API Anthropic. |

Pengaturan di `.env`:
```
ASSISTANT_ENGINE=auto        # auto | rules | llm
ANTHROPIC_API_KEY=           # kosong = mode aturan
ASSISTANT_MODEL=claude-sonnet-4-5
```

Kalau layanan AI sedang tidak bisa dihubungi, asisten otomatis kembali ke mode aturan dan memberi tahu di layar,
bukan menampilkan error. Model juga **tidak pernah** diberi wewenang menjalankan perintah pengubah data — kalau
model memanggilnya, hasilnya tetap berupa usulan yang menunggu tombol konfirmasi Anda.

## Privasi
Mode aturan tidak mengirim apa pun ke luar. Mode Claude mengirim pertanyaan Anda dan ringkasan hasil perkakas
(bukan seluruh isi database) ke API Anthropic. Riwayat percakapan disimpan per pengguna dan bisa dihapus kapan saja
lewat tombol **Bersihkan**.
