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
HTTPS_MODE="${HTTPS_MODE:-single}"
DOMAIN="${DOMAIN:-}"
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

HTTPS_MODE="${HTTPS_MODE:-single}"
DOMAIN="${DOMAIN:-}"

# LETSENCRYPT_EMAIL is optional (can be empty)

case "$HTTPS_MODE" in
  single)
    if [[ -z "$DOMAIN" ]]; then
      echo "[ERROR] HTTPS_MODE=single requires DOMAIN in .env.prod"
      exit 1
    fi
    ;;
  dual)
    if [[ -z "$APP_DOMAIN" || -z "$API_DOMAIN" ]]; then
      echo "[ERROR] HTTPS_MODE=dual requires APP_DOMAIN and API_DOMAIN in .env.prod"
      exit 1
    fi
    ;;
  *)
    echo "[ERROR] HTTPS_MODE must be single or dual (got: $HTTPS_MODE)"
    exit 1
    ;;
esac

render_single_http() {
  DOMAIN="$1"
  sed "s/\${DOMAIN}/${DOMAIN}/g" deploy/nginx/gateway.single.http.template.conf > deploy/nginx/gateway.conf
}

render_single_ssl() {
  DOMAIN="$1"
  sed "s/\${DOMAIN}/${DOMAIN}/g" deploy/nginx/gateway.single.ssl.template.conf > deploy/nginx/gateway.conf
}

render_dual_http() {
  APP_DOMAIN="$1"; API_DOMAIN="$2"
  sed -e "s/\${APP_DOMAIN}/${APP_DOMAIN}/g" -e "s/\${API_DOMAIN}/${API_DOMAIN}/g" \
    deploy/nginx/gateway.dual.http.template.conf > deploy/nginx/gateway.conf
}

render_dual_ssl() {
  APP_DOMAIN="$1"; API_DOMAIN="$2"
  sed -e "s/\${APP_DOMAIN}/${APP_DOMAIN}/g" -e "s/\${API_DOMAIN}/${API_DOMAIN}/g" \
    deploy/nginx/gateway.dual.ssl.template.conf > deploy/nginx/gateway.conf
}

case "$HTTPS_MODE" in
  single)
    render_single_http "$DOMAIN"
    ;;
  dual)
    render_dual_http "$APP_DOMAIN" "$API_DOMAIN"
    ;;
esac

dc() {
  docker compose -f docker-compose.prod.yml -f docker-compose.https.yml --env-file .env.prod "$@"
}

echo "[2/5] Start gateway for ACME challenge"
dc up -d gateway

echo "[3/5] Request certificates"
CERTBOT_ARGS=(certonly --webroot --webroot-path /var/www/certbot --agree-tos --non-interactive)

# Email is optional
if [[ -n "${LETSENCRYPT_EMAIL:-}" ]]; then
  CERTBOT_ARGS+=(--email "$LETSENCRYPT_EMAIL" --no-eff-email)
else
  CERTBOT_ARGS+=(--register-unsafely-without-email)
fi

case "$HTTPS_MODE" in
  single)
    CERTBOT_ARGS+=(-d "$DOMAIN")
    ;;
  dual)
    CERTBOT_ARGS+=(-d "$APP_DOMAIN" -d "$API_DOMAIN")
    ;;
esac

dc run --rm certbot certbot "${CERTBOT_ARGS[@]}"

echo "[4/5] Render SSL gateway config"
case "$HTTPS_MODE" in
  single)
    render_single_ssl "$DOMAIN"
    ;;
  dual)
    render_dual_ssl "$APP_DOMAIN" "$API_DOMAIN"
    ;;
esac

echo "[5/5] Reload gateway with TLS"
dc up -d --force-recreate gateway

echo "[DONE] HTTPS enabled"
case "$HTTPS_MODE" in
  single)
    echo "- https://$DOMAIN"
    echo "- API via https://$DOMAIN/api/v1/..."
    ;;
  dual)
    echo "- https://$APP_DOMAIN"
    echo "- https://$API_DOMAIN"
    ;;
esac
