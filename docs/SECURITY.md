# Model keamanan Nexvora — Fase 1

## Lapisan data (PostgreSQL)
| Kontrol | Implementasi |
|---|---|
| Isolasi tenant | Row-Level Security (`ENABLE` + `FORCE`) di semua tabel milik tenant. Policy membaca `app.tenant_id` yang dipasang per transaksi via `set_config(..., true)` sehingga tidak bocor antar koneksi pool. |
| Defense in depth | Setiap query di API juga memfilter `tenant_id` secara eksplisit; FK komposit `(tenant_id, id)` mencegah relasi lintas tenant (mis. SKU → produk tenant lain). |
| Least privilege | Dua role: `nexvora_owner` (hanya migrasi) dan `nexvora_app` (runtime, `NOBYPASSRLS`, bukan owner, tanpa hak DDL, tanpa `DELETE` pada data master). |
| Audit append-only | `audit_logs` hanya diberi `SELECT, INSERT` + trigger yang menolak `UPDATE/DELETE/TRUNCATE`. Field sensitif di-redact. |
| Login sebelum tenant diketahui | Fungsi `SECURITY DEFINER resolve_tenant_by_slug` — tabel `tenants` tetap di bawah RLS. |
| Integritas | CHECK constraint (format kode, dimensi > 0, hierarki lokasi), UNIQUE per tenant, trigger `updated_at`. |

## Lapisan aplikasi (FastAPI)
- Password **Argon2id**, kebijakan minimal 12 karakter + 3 kelas karakter, rehash otomatis bila parameter berubah.
- Login: pesan error generik (anti user-enumeration), waktu respons disamakan, **lockout** 15 menit setelah 5 kali gagal, rate limit per IP dan per akun (Redis).
- Access token JWT 15 menit (`iss`, `aud`, `exp`, `jti`, `tv`). Peran & izin dibaca ulang dari DB di setiap request, jadi perubahan role/nonaktif langsung berlaku.
- Refresh token acak 384-bit, yang disimpan hanya hash SHA-256, **rotasi** setiap pakai dan **reuse detection** (token lama dipakai lagi → seluruh keluarga sesi dicabut).
- `token_version` untuk logout semua perangkat, ganti password, dan penonaktifan user.
- Header keamanan, correlation ID di setiap respons, error tanpa detail SQL, `/docs` mati di production.

- **API key** integrasi: format `nxk_<tenant>_<rahasia>`, disimpan sebagai hash, izin dibatasi daftar putih, bisa dicabut
  dan kedaluwarsa, tidak bisa dipakai untuk endpoint akun (me, ganti password, buat API key).
- **Langganan** ditegakkan di API (bukan hanya di UI): fitur per prefix izin, mode baca-saja untuk langganan tidak aktif.
- **Webhook pembayaran** diverifikasi signature SHA-512 dan nominal; pemrosesan idempoten.
- **Self-signup** dibatasi rate limit per IP (API + Nginx) dan kode workspace yang dicadangkan.

## Lapisan web (Next.js BFF)
- Token disimpan di cookie **httpOnly + SameSite=Strict + Secure** (production); JavaScript browser tidak pernah melihat token.
- Proxy `/api/v1/*` menolak request pengubah data dari origin lain (CSRF), memblokir endpoint token, memvalidasi segmen path, dan melakukan refresh otomatis.
- CSP ketat, `X-Frame-Options: DENY`, `poweredByHeader` mati.

## Operasional
- API tidak di-expose ke publik; hanya web yang memanggilnya. Nginx menimpa `X-Real-IP`.
- Rahasia hanya dari environment (`.env` tidak di-commit). API menolak start di production bila `JWT_SECRET` masih default atau Redis belum diset.

## Wajib sebelum production
1. TLS di Nginx/Load Balancer + HSTS.
2. Backup PostgreSQL terjadwal (mis. `pg_dump`/WAL-G) **dan uji restore** berkala.
3. Rotasi `JWT_SECRET` dan password DB; simpan di secret manager.
4. Pindahkan password DB ke `scram-sha-256` saja dan batasi `pg_hba.conf` ke jaringan internal.
5. Aktifkan monitoring (OpenTelemetry → Prometheus/Grafana) dan alert untuk `auth.refresh_reuse_detected`.
