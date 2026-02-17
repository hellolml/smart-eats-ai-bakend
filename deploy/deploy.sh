#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env.prod" ]]; then
  echo "[ERROR] .env.prod not found. Copy .env.prod.example to .env.prod first."
  exit 1
fi

export IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
echo "[INFO] Deploying with IMAGE_TAG=${IMAGE_TAG}"

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
    echo "[INFO] HTTPS enabled, deploying with docker-compose.https.yml"
    ;;
esac

dc() {
  docker compose "${COMPOSE_FILES[@]}" --env-file .env.prod "$@"
}

# Build and start
dc build --pull
dc up -d

# Wait for backend
for i in {1..30}; do
  if dc exec -T backend curl -fsS http://localhost:8000/ >/dev/null 2>&1; then
    echo "[OK] Backend is healthy"
    break
  fi
  if [[ "$i" -eq 30 ]]; then
    echo "[ERROR] Backend health check failed"
    dc logs backend --tail=100
    exit 1
  fi
  sleep 2
done

# Wait for frontend
for i in {1..30}; do
  if dc exec -T frontend wget -qO- http://localhost/healthz >/dev/null 2>&1; then
    echo "[OK] Frontend is healthy"
    break
  fi
  if [[ "$i" -eq 30 ]]; then
    echo "[ERROR] Frontend health check failed"
    dc logs frontend --tail=100
    exit 1
  fi
  sleep 2
done

echo "[DONE] Deploy successful"
dc ps
