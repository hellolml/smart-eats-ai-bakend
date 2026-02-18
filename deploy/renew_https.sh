#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env.prod" ]]; then
  echo "[ERROR] .env.prod not found."
  exit 1
fi

source .env.prod
HTTPS_ENABLED="${HTTPS_ENABLED:-false}"

case "$HTTPS_ENABLED" in
  true|TRUE|True|1|yes|YES|on|ON) ;;
  *)
    echo "[ERROR] HTTPS_ENABLED is not true in .env.prod"
    exit 1
    ;;
esac

if [[ ! -f "deploy/nginx/gateway.conf" ]]; then
  echo "[ERROR] deploy/nginx/gateway.conf not found. Run deploy/enable_https.sh first."
  exit 1
fi

dc() {
  docker compose -f docker-compose.prod.yml -f deploy/docker-compose.https.yml --env-file .env.prod "$@"
}

dc run --rm certbot renew --webroot -w /var/www/certbot

dc exec gateway nginx -s reload

echo "[DONE] Certificate renewal completed and gateway reloaded"
