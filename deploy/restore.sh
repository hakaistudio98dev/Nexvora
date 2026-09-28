#!/usr/bin/env bash
# Pulihkan database dari file backup. WAJIB diuji minimal sekali sebelum produksi.
#   bash deploy/restore.sh backups/nexvora-2026-09-29-0200.sql.gz
set -euo pipefail
FILE="${1:?Pakai: restore.sh <file-backup.sql.gz>}"
APP_DIR="${APP_DIR:-$(pwd)}"
cd "$APP_DIR"
[ -f "$FILE" ] || { echo "File tidak ditemukan: $FILE" >&2; exit 1; }

echo "Ini akan MENIMPA isi database saat ini dengan $FILE."
read -r -p "Ketik YA untuk lanjut: " ok
[ "$ok" = "YA" ] || { echo "Dibatalkan."; exit 1; }

docker compose stop api worker dispatcher web || true
gunzip -c "$FILE" | docker compose exec -T postgres psql -U nexvora_owner -d nexvora -v ON_ERROR_STOP=1
docker compose start postgres redis
docker compose up -d
echo "Selesai. Cek https://<domain> dan pastikan data terakhir sesuai."
