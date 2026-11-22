#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

TAG="${1:-}"
if [[ ! "$TAG" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: ./deploy-staging.sh <40-character-git-commit-sha>"
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "Missing .env. Run ./bootstrap-staging.sh first."
  exit 1
fi

chmod 600 .env
export SUPERNOVA_IMAGE_TAG="$TAG"
mkdir -p backups
COMPOSE=(docker compose --project-name supernova-staging --env-file .env -f compose.staging.yaml)

"${COMPOSE[@]}" config --quiet

API_IMAGE="macbook201808/supernova:api-${TAG}"
CONSOLE_IMAGE="macbook201808/supernova:console-${TAG}"

echo "Pulling immutable API and Console images from Docker Hub..."
"${COMPOSE[@]}" pull api console

for image in "$API_IMAGE" "$CONSOLE_IMAGE"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "Required staging image is missing after Docker Hub pull: $image"
    exit 1
  fi
done

"${COMPOSE[@]}" up -d postgres redis

wait_for_service_health() {
  local service="$1"
  local container_id=""
  local status=""
  container_id="$("${COMPOSE[@]}" ps -q "$service")"
  if [[ -z "$container_id" ]]; then
    echo "Container for $service was not created."
    return 1
  fi
  for attempt in $(seq 1 60); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    if [[ "$status" == "healthy" ]]; then
      return 0
    fi
    if [[ "$status" == "unhealthy" || "$status" == "exited" ]]; then
      docker logs --tail 80 "$container_id" || true
      echo "$service entered state: $status"
      return 1
    fi
    sleep 2
  done
  docker logs --tail 80 "$container_id" || true
  echo "Timed out waiting for $service health."
  return 1
}

wait_for_url() {
  local url="$1"
  local label="$2"
  for attempt in $(seq 1 45); do
    if python3 -c "import urllib.request; urllib.request.urlopen('${url}', timeout=3)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Timed out waiting for ${label}: ${url}"
  return 1
}

wait_for_service_health postgres
wait_for_service_health redis

BACKUP_FILE="backups/pre-${TAG:0:12}-$(date +%Y%m%d-%H%M%S).dump"
"${COMPOSE[@]}" exec -T postgres \
  sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "$BACKUP_FILE"
test -s "$BACKUP_FILE"

find backups -maxdepth 1 -type f -name 'pre-*.dump' -printf '%T@ %p\n' \
  | sort -nr | tail -n +4 | cut -d' ' -f2- \
  | xargs -r rm -f --

PREVIOUS_TAG="$(cat .last_successful_tag 2>/dev/null || true)"
if [[ -n "$PREVIOUS_TAG" ]]; then
  printf '%s\n' "$PREVIOUS_TAG" > .previous_successful_tag
fi

echo "Applying Alembic migrations for ${TAG}..."
"${COMPOSE[@]}" run --rm --no-deps api alembic upgrade head

"${COMPOSE[@]}" up -d --remove-orphans api knowledge-worker chat-import-worker runtime-manager console

if ! wait_for_service_health api; then
  echo "Deployment failed. Previous application tag: ${PREVIOUS_TAG:-none}"
  echo "Database backup retained at: ${BACKUP_FILE}"
  exit 1
fi
if ! wait_for_service_health console; then
  echo "Deployment failed. Previous application tag: ${PREVIOUS_TAG:-none}"
  echo "Database backup retained at: ${BACKUP_FILE}"
  exit 1
fi
if ! wait_for_service_health knowledge-worker; then
  echo "Knowledge Worker failed its health check."
  echo "Database backup retained at: ${BACKUP_FILE}"
  exit 1
fi
if ! wait_for_service_health chat-import-worker; then
  echo "Chat Import Worker failed its health check."
  echo "Database backup retained at: ${BACKUP_FILE}"
  exit 1
fi
if ! wait_for_service_health runtime-manager; then
  echo "Runtime Manager failed its health check."
  echo "Database backup retained at: ${BACKUP_FILE}"
  exit 1
fi

wait_for_url "http://127.0.0.1:18082/health/ready" "API"
wait_for_url "http://127.0.0.1:18083/healthz" "Console"

printf '%s\n' "$TAG" > .last_successful_tag.tmp
mv .last_successful_tag.tmp .last_successful_tag

echo "Deployment successful: ${TAG}"
echo "Database backup: ${BACKUP_FILE}"
