#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required to acknowledge Staging health."
  exit 1
fi
exec 9>"$APP_DIR/.staging-deploy.lock"
flock 9

if [[ ! -f .staging-deployment-blocked ]]; then
  echo "Staging deployment queue is not blocked."
  exit 0
fi
if [[ ! -f .env || ! -s .last_successful_tag || ! -s .last_successful_deployment.json ]]; then
  echo "Cannot acknowledge health: deployment configuration or last successful record is missing."
  exit 1
fi

TAG="$(cat .last_successful_tag)"
if [[ ! "$TAG" =~ ^[0-9a-f]{40}$ ]]; then
  echo "The last successful deployment SHA is invalid."
  exit 1
fi

COMPOSE=(docker compose --project-name supernova-staging --env-file .env -f compose.staging.yaml)
for service in postgres redis api console knowledge-worker chat-import-worker; do
  container_id="$("${COMPOSE[@]}" ps -q "$service")"
  if [[ -z "$container_id" ]]; then
    echo "No running container found for $service."
    exit 1
  fi
  image="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
  case "$service" in
    api|knowledge-worker|chat-import-worker)
      expected="macbook201808/supernova:api-${TAG}"
      ;;
    console)
      expected="macbook201808/supernova:console-${TAG}"
      ;;
    postgres|redis)
      expected=""
      ;;
  esac
  if [[ -n "$expected" && "$image" != "$expected" ]]; then
    echo "$service is running $image, expected $expected."
    exit 1
  fi
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
  if [[ "$status" != "healthy" ]]; then
    echo "$service is not healthy: $status"
    exit 1
  fi
done

curl -fsS http://127.0.0.1:18082/health/ready >/dev/null
curl -fsS http://127.0.0.1:18083/healthz >/dev/null
STAGING_API_BASE_URL="${STAGING_API_BASE_URL:-https://api.wet001.com}"
STAGING_CONSOLE_BASE_URL="${STAGING_CONSOLE_BASE_URL:-https://bot.wet001.com}"
curl --connect-timeout 5 --max-time 20 -fsS "${STAGING_API_BASE_URL%/}/health/ready" >/dev/null
curl --connect-timeout 5 --max-time 20 -fsS "${STAGING_CONSOLE_BASE_URL%/}/" | grep -q "SuperNova"

python3 - "$TAG" .last_successful_deployment.json <<'PY'
import json
import sys

expected_sha, path = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    record = json.load(handle)
if record.get("commit_sha") != expected_sha:
    raise SystemExit("Deployment record SHA does not match .last_successful_tag.")
if record.get("status") != "healthy":
    raise SystemExit("Deployment record is not marked healthy.")
PY

rm -f .staging-deployment-blocked
echo "Staging health acknowledged. Deployment queue unblocked at SHA $TAG."
