#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env.prod" ]]; then
  echo "[ERROR] .env.prod not found. Copy .env.prod.example to .env.prod first."
  exit 1
fi

source .env.prod

HTTPS_ENABLED="${HTTPS_ENABLED:-false}"
APP_DOMAIN="${APP_DOMAIN:-}"
API_DOMAIN="${API_DOMAIN:-}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"

case "$HTTPS_ENABLED" in
  true|TRUE|True|1|yes|YES|on|ON) ;;
  *)
    echo "[ERROR] HTTPS_ENABLED is not true in .env.prod"
    exit 1
    ;;
esac

if [[ -z "$APP_DOMAIN" || -z "$API_DOMAIN" || -z "$LETSENCRYPT_EMAIL" ]]; then
  echo "[ERROR] APP_DOMAIN / API_DOMAIN / LETSENCRYPT_EMAIL must be set in .env.prod"
  exit 1
fi

if ! command -v envsubst >/dev/null 2>&1; then
  echo "[ERROR] envsubst not found. Install gettext package first."
  exit 1
fi

echo "[1/5] Render HTTP gateway config"
envsubst '${APP_DOMAIN} ${API_DOMAIN}' < deploy/nginx/gateway.http.template.conf > deploy/nginx/gateway.conf

dc() {
  docker compose -f docker-compose.prod.yml -f docker-compose.https.yml --env-file .env.prod "$@"
}

echo "[2/5] Start gateway for ACME challenge"
dc up -d gateway

echo "[3/5] Request certificates"
dc run --rm certbot \
  certonly --webroot \
  --webroot-path /var/www/certbot \
  --email "$LETSENCRYPT_EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$APP_DOMAIN" \
  -d "$API_DOMAIN"

echo "[4/5] Render SSL gateway config"
envsubst '${APP_DOMAIN} ${API_DOMAIN}' < deploy/nginx/gateway.ssl.template.conf > deploy/nginx/gateway.conf

echo "[5/5] Reload gateway with TLS"
dc up -d gateway

echo "[DONE] HTTPS enabled"
echo "- https://$APP_DOMAIN"
echo "- https://$API_DOMAIN"
