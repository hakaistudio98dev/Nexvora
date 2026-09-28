#!/usr/bin/env bash
# Backup database Nexvora. Dijalankan cron tiap hari 02:00, atau manual:
#   APP_DIR=/opt/nexvora bash deploy/backup.sh
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/nexvora}"
DEST="${BACKUP_DIR:-$APP_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
cd "$APP_DIR"
mkdir -p "$DEST"
STAMP="$(date +%F-%H%M)"
FILE="$DEST/nexvora-$STAMP.sql.gz"

docker compose exec -T postgres pg_dump -U nexvora_owner -d nexvora --clean --if-exists | gzip -9 > "$FILE"

# Backup yang tidak bisa dibuka bukan backup: pastikan isinya utuh
gzip -t "$FILE"
SIZE=$(stat -c%s "$FILE")
[ "$SIZE" -gt 10000 ] || { echo "GAGAL: hasil backup terlalu kecil ($SIZE byte)" >&2; exit 1; }

find "$DEST" -name 'nexvora-*.sql.gz' -mtime "+$KEEP_DAYS" -delete
echo "$(date -Is) OK $FILE ($((SIZE/1024)) KB)"

# Salin ke penyimpanan luar server bila rclone sudah diatur (mis. Google Drive / S3)
if [ -n "${RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null; then
  rclone copy "$FILE" "$RCLONE_REMOTE" && echo "$(date -Is) Disalin ke $RCLONE_REMOTE"
fi
