#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required to serialize Staging deployment and rollback operations."
  exit 1
fi
exec 9>"$APP_DIR/.staging-deploy.lock"
flock 9

if [[ -f .staging-deployment-awaiting-acceptance && ! -f .staging-deployment-blocked ]]; then
  echo "The current Staging deployment is awaiting acceptance; do not change it during review."
  exit 71
fi

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
CURRENT_TAG="$(cat .last_successful_tag 2>/dev/null || true)"

ROLLBACK_COMPLETED=0
trap '
  status=$?
  if [[ "$ROLLBACK_COMPLETED" != "1" ]]; then
    printf "rollback_failed_at_utc=%s\nrequested_commit_sha=%s\n" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TAG" \
      > .staging-deployment-blocked
    echo "Staging deployment queue remains BLOCKED because rollback was not verified."
  fi
  exit "$status"
' EXIT

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
STAGING_API_BASE_URL="${STAGING_API_BASE_URL:-https://api.wet001.com}"
STAGING_CONSOLE_BASE_URL="${STAGING_CONSOLE_BASE_URL:-https://bot.wet001.com}"

all_services_healthy() {
  local service container_id status image expected
  for service in postgres redis api console knowledge-worker chat-import-worker; do
    container_id="$("${COMPOSE[@]}" ps -q "$service")"
    [[ -n "$container_id" ]] || return 1
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    [[ "$status" == "healthy" ]] || return 1
    case "$service" in
      api|knowledge-worker|chat-import-worker)
        expected="macbook201808/supernova:api-${TAG}"
        image="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
        [[ "$image" == "$expected" ]] || return 1
        ;;
      console)
        expected="macbook201808/supernova:console-${TAG}"
        image="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
        [[ "$image" == "$expected" ]] || return 1
        ;;
    esac
  done
}

for attempt in $(seq 1 45); do
  if all_services_healthy \
    && python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18082/health/ready', timeout=3)" >/dev/null 2>&1 \
    && python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18083/healthz', timeout=3)" >/dev/null 2>&1 \
    && curl --connect-timeout 5 --max-time 20 -fsS "${STAGING_API_BASE_URL%/}/health/ready" >/dev/null \
    && curl --connect-timeout 5 --max-time 20 -fsS "${STAGING_CONSOLE_BASE_URL%/}/" | grep -q "SuperNova"; then
    DEPLOYED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [[ -n "$CURRENT_TAG" ]]; then
      printf '%s\n' "$CURRENT_TAG" > .previous_successful_tag.tmp
      mv .previous_successful_tag.tmp .previous_successful_tag
    fi
    printf '%s\n' "$TAG" > .last_successful_tag.tmp
    mv .last_successful_tag.tmp .last_successful_tag
    cat > .last_successful_deployment.json.tmp <<EOF
{
  "commit_sha": "$TAG",
  "image_tag": "$TAG",
  "deployed_at_utc": "$DEPLOYED_AT_UTC",
  "status": "healthy",
  "event_type": "rollback",
  "rollback_from_sha": "$CURRENT_TAG"
}
EOF
    mv .last_successful_deployment.json.tmp .last_successful_deployment.json
    rm -f .staging-deployment-blocked .staging-deployment-awaiting-acceptance
    ROLLBACK_COMPLETED=1
    echo "Application containers rolled back to ${TAG}."
    echo "Database schema/data were NOT rolled back. Verify backward compatibility."
    echo "Deployment record updated to ${TAG}."
    exit 0
  fi
  sleep 2
done

echo "Rollback image health check failed. Inspect Docker logs."
exit 1
