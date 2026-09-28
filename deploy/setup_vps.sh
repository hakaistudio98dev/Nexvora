#!/usr/bin/env bash
# Pasang Nexvora di VPS Ubuntu 22.04/24.04 yang masih kosong.
#   sudo bash deploy/setup_vps.sh nexvora.domainanda.com admin@domainanda.com
# Skrip ini aman dijalankan ulang (idempoten).
set -euo pipefail

DOMAIN="${1:?Pakai: setup_vps.sh <domain> <email-admin>}"
EMAIL="${2:?Pakai: setup_vps.sh <domain> <email-admin>}"
APP_DIR="${APP_DIR:-/opt/nexvora}"

echo "==> 1/6 Paket dasar"
apt-get update -qq
apt-get install -y -qq ca-certificates curl git ufw

echo "==> 2/6 Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "==> 3/6 Firewall (hanya SSH + HTTP + HTTPS)"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "==> 4/6 Menyiapkan $APP_DIR"
mkdir -p "$APP_DIR"
if [ ! -f "$APP_DIR/docker-compose.yml" ]; then
  echo "    Salin isi proyek ke $APP_DIR dulu (scp/rsync/git clone), lalu jalankan skrip ini lagi." >&2
  exit 1
fi
cd "$APP_DIR"

echo "==> 5/6 Membuat .env produksi"
if [ ! -f .env ]; then
  gen() { openssl rand -base64 36 | tr -d '\n=+/' | cut -c1-40; }
  FERNET="$(docker run --rm python:3.12-slim python -c \
    'from cryptography.fernet import Fernet' 2>/dev/null \
    && docker run --rm python:3.12-slim sh -c 'pip install -q cryptography && python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"' \
    || python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
  cat > .env <<ENV
ENV=production
PUBLIC_APP_URL=https://$DOMAIN
COOKIE_SECURE=true
CORS_ORIGINS=["https://$DOMAIN"]
JWT_SECRET=$(gen)
POSTGRES_PASSWORD=$(gen)
APP_ENCRYPTION_KEY=$FERNET
REDIS_URL=redis://redis:6379/0
ADMIN_EMAIL=$EMAIL
ADMIN_PASSWORD=$(gen)
WORKER_JOBS=maintenance,ai
ASSISTANT_ENGINE=auto
ENV
  chmod 600 .env
  echo "    .env dibuat. Password admin pertama:"
  grep ADMIN_PASSWORD .env
  echo "    SIMPAN sekarang, lalu ganti passwordnya setelah login pertama."
else
  echo "    .env sudah ada, dibiarkan."
fi

echo "==> 6/6 Menjalankan (HTTPS otomatis lewat Caddy)"
DOMAIN="$DOMAIN" EMAIL="$EMAIL" docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build

# Backup harian jam 02:00
install -m 755 deploy/backup.sh /usr/local/bin/nexvora-backup
( crontab -l 2>/dev/null | grep -v nexvora-backup ; echo "0 2 * * * APP_DIR=$APP_DIR /usr/local/bin/nexvora-backup >> /var/log/nexvora-backup.log 2>&1" ) | crontab -

cat <<DONE

Selesai. Buka https://$DOMAIN (sertifikat terbit otomatis dalam 1-2 menit).

Berikutnya:
  1. Login, ganti password admin.
  2. Uji restore backup minimal sekali: bash deploy/restore.sh <file-backup>
  3. Isi SMTP_HOST dan kunci Midtrans/Biteship di .env bila dipakai, lalu: docker compose up -d
DONE
