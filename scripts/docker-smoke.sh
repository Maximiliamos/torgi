#!/usr/bin/env bash
set -euo pipefail

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-bankrotai-postgres-smoke-password}"
export REDIS_PASSWORD="${REDIS_PASSWORD:-bankrotai-redis-smoke-password}"
export BANKROTAI_API_KEY="${BANKROTAI_API_KEY:-bankrotai-api-smoke-key-123456}"
export AUTH_SESSION_SECRET="${AUTH_SESSION_SECRET:-bankrotai-ci-session-secret-1234567890}"
export AUTH_BOOTSTRAP_PASSWORD="${AUTH_BOOTSTRAP_PASSWORD:-bankrotai-ci-reader-password}"
export APP_ENV=ci
export WEB_BASIC_AUTH_USER="${WEB_BASIC_AUTH_USER:-bankrotai}"
export WEB_BASIC_AUTH_PASSWORD="${WEB_BASIC_AUTH_PASSWORD:-bankrotai-smoke-password}"
export E2E_USERNAME=reader
export E2E_PASSWORD="$AUTH_BOOTSTRAP_PASSWORD"

cookie_jar=$(mktemp)

cleanup() {
  rm -f "$cookie_jar"
  docker compose down -v --remove-orphans
}
trap cleanup EXIT

docker compose up -d --build

for _ in {1..60}; do
  migrate_id=$(docker compose ps -a -q migrate)
  migrate_status=$(docker inspect --format '{{.State.Status}}' "$migrate_id")
  if [[ "$migrate_status" == "exited" ]]; then
    migrate_code=$(docker inspect --format '{{.State.ExitCode}}' "$migrate_id")
    if [[ "$migrate_code" != "0" ]]; then
      docker compose logs migrate
      exit "$migrate_code"
    fi
    break
  fi
  sleep 1
done

if [[ "${migrate_status:-}" != "exited" ]]; then
  echo "Migration container did not finish in time" >&2
  exit 1
fi

for _ in {1..60}; do
  if curl --fail --silent http://127.0.0.1:8080/health >/dev/null \
    && curl --fail --silent -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" http://127.0.0.1:8080/ >/dev/null; then
    break
  fi
  sleep 2
done

curl --fail --silent http://127.0.0.1:8080/health
curl --fail --silent -c "$cookie_jar" -X POST \
  -H 'Content-Type: application/json' \
  -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" \
  -d "{\"username\":\"reader\",\"password\":\"$AUTH_BOOTSTRAP_PASSWORD\"}" \
  http://127.0.0.1:8080/api/auth/login
curl --fail --silent -b "$cookie_jar" \
  -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" http://127.0.0.1:8080/api/lots
curl --fail --silent -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" http://127.0.0.1:8080/
docker compose exec -T worker celery -A bankrotai.tasks:celery_app inspect ping --timeout 10

response=$(curl --fail --silent -X POST -H 'Content-Type: application/json' \
  -b "$cookie_jar" \
  -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" \
  -d '{"search":"smoke-test-no-results","max_items":1}' \
  http://127.0.0.1:8080/api/online/torgi-gov/sync)
task_id=$(python -c 'import json,sys; print(json.load(sys.stdin)["task_id"])' <<<"$response")
curl --fail --silent -b "$cookie_jar" -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" \
  "http://127.0.0.1:8080/api/tasks/$task_id"
npm --prefix WEB run test:e2e
