#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <IMAGE_TAG>"
  echo "Example: $0 20260218153000"
  exit 1
fi

TARGET_TAG="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env.prod" ]]; then
  echo "[ERROR] .env.prod not found."
  exit 1
fi

export IMAGE_TAG="$TARGET_TAG"
echo "[INFO] Rolling back to IMAGE_TAG=${IMAGE_TAG}"

source .env.prod
HTTPS_ENABLED="${HTTPS_ENABLED:-false}"

COMPOSE_FILES=(-f docker-compose.prod.yml)
case "$HTTPS_ENABLED" in
  true|TRUE|True|1|yes|YES|on|ON)
    if [[ ! -f "deploy/nginx/gateway.conf" ]]; then
      echo "[ERROR] HTTPS_ENABLED=true but deploy/nginx/gateway.conf not found"
      exit 1
    fi
    COMPOSE_FILES+=(-f docker-compose.https.yml)
    echo "[INFO] HTTPS enabled, rolling back with docker-compose.https.yml"
    ;;
esac

dc() {
  docker compose "${COMPOSE_FILES[@]}" --env-file .env.prod "$@"
}

dc up -d

echo "[DONE] Rollback applied"
dc ps
