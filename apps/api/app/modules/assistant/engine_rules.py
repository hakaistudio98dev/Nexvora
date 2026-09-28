"""Mesin tanpa AI eksternal: mencocokkan kalimat bahasa Indonesia ke perkakas.

Dipakai sebagai bawaan dan sebagai cadangan bila kunci API tidak diisi atau layanan AI sedang mati.
Tidak sepintar model bahasa, tetapi deterministik, cepat, gratis, dan tidak mengirim data ke luar.
"""
import re

ORDER_RE = re.compile(r"\b([A-Z]{2,3}-\d{4}-\d{4,8})\b", re.I)
RMA_RE = re.compile(r"\bRMA-[\w-]+\b", re.I)
RESI_RE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9-]{5,29}\b")   # resi: campur huruf & angka
DAYS_RE = re.compile(r"(\d{1,3})\s*hari")

STOPWORDS = ("stok", "sisa", "berapa", "cek", "lihat", "tolong", "coba", "dong", "ya", "untuk", "sku", "barang")


def _days(text: str) -> int | None:
    m = DAYS_RE.search(text)
    if m:
        return int(m.group(1))
    if "minggu ini" in text or "seminggu" in text or "minggu" in text:
        return 7
    if "bulan ini" in text or "sebulan" in text or "bulan" in text:
        return 30
    if "hari ini" in text:
        return 1
    return None


def parse(text: str) -> tuple[str, dict] | None:
    """Kembalikan (nama_perkakas, argumen) atau None bila tidak yakin."""
    t = " ".join(text.lower().split())
    order = ORDER_RE.search(text)
    order_no = order.group(1).upper() if order else None

    # --- perintah (mengubah data) lebih dulu, supaya "batalkan order X" tidak jadi pencarian
    if order_no:
        if re.search(r"\b(batal|batalkan|cancel)\b", t):
            alasan = re.sub(r".*\b(karena|sebab)\b", "", text, flags=re.I).strip() if re.search(r"\b(karena|sebab)\b", t) else None
            return "batalkan_order", {"order_number": order_no, "alasan": alasan}
        if re.search(r"\b(mulai|proses)\b.*\b(picking|ambil|pick)\b|\b(picking|ambil barang)\b.*\bmulai\b", t):
            return "mulai_picking", {"order_number": order_no}
        if re.search(r"\b(sudah )?(dibayar|bayar|lunas|paid)\b", t) and re.search(r"\b(tandai|set|ubah|konfirmasi)\b", t):
            return "tandai_dibayar", {"order_number": order_no}
        if re.search(r"\b(lacak|tracking|resi|paket|sampai mana|sudah sampai)\b", t):
            return "lacak_paket", {"kode": order_no}
        return "detail_order", {"order_number": order_no}

    if re.search(r"\b(lacak|tracking|resi|paket)\b", t):
        for cand in RESI_RE.findall(text):
            if cand.lower() not in ("lacak", "tracking", "resi", "paket"):
                return "lacak_paket", {"kode": cand.upper()}

    if re.search(r"\b(anomali|janggal|aneh|mencurigakan)\b", t):
        return "temuan_anomali", {}
    if re.search(r"\b(retur|rma|pengembalian)\b", t) or RMA_RE.search(text):
        return "retur_terbuka", {}
    if re.search(r"\b(terlambat|telat|sla|molor|lewat batas)\b", t):
        return "order_terlambat", {}
    if re.search(r"\b(habis|menipis|pesan ulang|reorder|restock|perlu dipesan)\b", t):
        return "risiko_stok", {}
    if re.search(r"\bkurir\b", t) or re.search(r"\b(ekspedisi|pengiriman)\b.*\b(terbaik|bagus|andal|pilih)\b", t):
        m = re.search(r"\bke\s+([a-z .'-]{3,30})", t)
        kota = m.group(1).strip().title() if m else None
        return "kurir_terbaik", {"kota": kota}
    if re.search(r"\bstok\b|\bsisa\b|\bpersediaan\b", t):
        q = " ".join(w for w in re.sub(r"[?.!,]", " ", text).split()
                     if w.lower() not in STOPWORDS and not re.fullmatch(r"(di|ada|masih|apa|gudang)", w.lower()))
        return ("stok_sku", {"q": q}) if q.strip() else ("risiko_stok", {})
    if re.search(r"\b(kinerja|performa|laporan|omzet|omset|penjualan|pendapatan|kpi|revenue)\b", t):
        return "kinerja", {"hari": _days(t) or 30}
    if re.search(r"\b(ringkasan|hari ini|dikerjakan|pekerjaan|to ?do|status gudang|kondisi)\b", t):
        return "ringkasan_hari_ini", {}
    if re.search(r"\border\b|\bpesanan\b|\bpelanggan\b", t):
        m = re.search(r"\b(?:order|pesanan)\s+(?:milik|dari|atas nama)\s+([\w .'-]{3,40})", t)
        status = None
        for kata, st in (("siap kirim", "READY_TO_SHIP"), ("dikirim", "SHIPPED"), ("dibayar", "PAID"),
                         ("baru", "CREATED"), ("diterima", "DELIVERED"), ("batal", "CANCELLED")):
            if kata in t:
                status = st
                break
        if m:
            return "cari_order", {"q": m.group(1).strip()}
        if status:
            return "cari_order", {"status": status}
        return "ringkasan_hari_ini", {}
    return None
