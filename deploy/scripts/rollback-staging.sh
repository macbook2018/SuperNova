#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

TAG="${1:-}"
if [[ -z "$TAG" && -f .previous_successful_tag ]]; then
  TAG="$(cat .previous_successful_tag)"
fi
if [[ ! "$TAG" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: ./rollback-staging.sh <40-character-git-commit-sha>"
  echo "Or ensure .previous_successful_tag exists."
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "Missing .env."
  exit 1
fi

chmod 600 .env
export SUPERNOVA_IMAGE_TAG="$TAG"
COMPOSE=(docker compose --project-name supernova-staging --env-file .env -f compose.staging.yaml)

API_IMAGE="macbook201808/supernova:api-${TAG}"
CONSOLE_IMAGE="macbook201808/supernova:console-${TAG}"
for image in "$API_IMAGE" "$CONSOLE_IMAGE"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "Rollback image is not available locally: $image"
    echo "Rollback images are retained locally after successful deployments."
    exit 1
  fi
done

"${COMPOSE[@]}" up -d api knowledge-worker chat-import-worker console

for attempt in $(seq 1 45); do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18082/health/ready', timeout=3)" >/dev/null 2>&1 \
    && python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18083/healthz', timeout=3)" >/dev/null 2>&1; then
    echo "Application containers rolled back to ${TAG}."
    echo "Database schema/data were NOT rolled back. Verify backward compatibility."
    exit 0
  fi
  sleep 2
done

echo "Rollback image health check failed. Inspect Docker logs."
exit 1
