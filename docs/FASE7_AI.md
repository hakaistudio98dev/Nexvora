# Fase 7 — Prediksi & saran (AI)

Semua perhitungan di fase ini memakai data Anda sendiri dan metode yang bisa dijelaskan. Tidak ada model kotak
hitam dan tidak ada data yang dikirim ke layanan luar. Kalau datanya belum cukup, sistem mengatakannya apa adanya,
bukan menebak-nebak.

Fitur ini termasuk paket **Enterprise**. Isi paket bisa diubah super admin dari console tanpa deploy ulang.

## 1. Ramalan permintaan
Untuk setiap SKU di setiap gudang:

```
ramalan(hari) = laju_harian × faktor_hari[senin..minggu]
```

- **laju_harian** — rata-rata terjual per hari dengan bobot eksponensial: penjualan 14 hari lalu berbobot setengah
  dari hari ini, jadi perubahan tren cepat terbaca tanpa terguncang satu hari ramai.
- **faktor hari** — pola mingguan (mis. Sabtu 1,4× hari biasa). Baru dipakai setelah ada 4 minggu data, dan ditarik
  ke 1 bila datanya sedikit supaya pola tidak dibentuk oleh satu-dua hari.
- **sigma** — simpangan sisa ramalan, dipakai untuk rentang perkiraan di grafik dan untuk stok pengaman.

Metode yang sedang dipakai selalu ditampilkan di layar: *pola mingguan*, *rata-rata sederhana* (data < 4 minggu),
atau *belum ada penjualan*.

## 2. Prediksi stok habis & saran pesan ulang
Perkiraan tanggal habis dihitung dengan mensimulasikan hari per hari memakai pola mingguan, bukan sekadar membagi
stok dengan rata-rata.

```
stok_pengaman     = z × sigma × √lead_time
titik_pesan_ulang = laju_harian × lead_time + stok_pengaman
saran_pesan       = laju_harian × (lead_time + cover) + stok_pengaman − tersedia − sedang_datang
```

`z` berasal dari **target tidak kehabisan** (95% → 1,64; 99% → 2,33). Barang yang sedang dalam perjalanan dari
supplier (dokumen penerimaan berstatus *menerima*) ikut dikurangi, supaya tidak memesan dua kali.

Tingkat risiko: **habis** (stok 0), **kritis** (habis sebelum barang baru datang), **waspada** (habis dalam
lead time + 7 hari), **aman**.

Tiga angka pengaturannya ada di menu **Pengaturan → Pesan ulang stok**: lama barang datang, stok ingin cukup
berapa hari, dan target tidak kehabisan.

## 3. Rekomendasi kurir
Dari 120 hari terakhir pengiriman Anda: tingkat paket sampai, rata-rata lama antar, tingkat gagal/dikembalikan,
dan ongkir rata-rata.

```
skor = 55% keandalan + 25% kecepatan + 20% biaya
```

Keandalan dihaluskan (Laplace) supaya kurir dengan satu paket sukses tidak langsung tampak sempurna. Statistik kota
tujuan dipakai lebih dulu; bila pengiriman ke kota itu kurang dari 5, dipakai angka nasional dan hal itu **ditulis
di layar**. Saran ini juga muncul saat membuat resi, lengkap dengan tombol "Pakai saran ini".

## 4. Deteksi anomali
Tujuh detektor, semuanya memakai **median + MAD** (bukan rata-rata) supaya satu pencilan tidak menggeser ambangnya
sendiri:

| Temuan | Kapan muncul |
|---|---|
| Nilai order janggal | nilai ≥ 6 simpangan robust di atas median dan ≥ 3× median |
| Jumlah SKU tidak wajar | kuantitas ≥ 8× median SKU itu (dan minimal 10 unit) |
| Lonjakan channel | order hari ini ≥ 2× dan ≥ 5 simpangan di atas median 2 minggu |
| Channel berhenti | channel yang biasanya ramai belum mengirim order sampai lewat tengah hari — biasanya integrasi putus |
| Order berulang | ≥ 4 order dari nomor yang sama dalam 24 jam |
| Retur menumpuk | satu SKU diretur ≥ 3 kali dalam seminggu |
| Ambil barang melambat | median waktu pick hari ini ≥ 2,5× dua minggu sebelumnya |

Setiap temuan hanya muncul sekali (dedup), selalu menyertakan angka pembandingnya, dan bisa ditandai
**sudah ditangani** atau **diabaikan**. Temuan juga dikirim sebagai notifikasi (event `AI_ANOMALY`), sedangkan
prediksi stok habis dikirim sebagai `AI_STOCKOUT_RISK` — keduanya bisa diteruskan ke email atau webhook.

## Kapan dihitung
Worker menjalankan job `ai`: ramalan dihitung ulang paling sering tiap 6 jam, deteksi anomali berjalan tiap siklus
(30 detik) dengan jendela waktu pendek supaya murah. Tombol **Hitung ulang sekarang** memaksa perhitungan untuk
workspace Anda tanpa menunggu jadwal.

```bash
# worker menjalankan perawatan + AI; pengiriman event dipisah
WORKER_JOBS=maintenance,ai      # service "worker"
WORKER_JOBS=dispatcher          # service "dispatcher"
```

## Batasannya (jujur)
- Ramalan tidak tahu rencana promo, harbolnas, atau libur panjang. Setelah kampanye besar, tekan "Hitung ulang"
  dan perlakukan sarannya sebagai batas bawah.
- SKU baru tanpa riwayat tidak bisa diramal; sistem menandainya *belum ada penjualan*, bukan menebak.
- Rekomendasi kurir hanya sebaik data Anda. Kurir yang belum pernah dipakai tidak akan pernah muncul sebagai saran.
- Deteksi anomali bersifat penanda, bukan vonis. Semua temuan tetap perlu diperiksa manusia sebelum diambil tindakan.
