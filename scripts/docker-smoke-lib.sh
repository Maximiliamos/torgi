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
export E2E_USERNAME="${E2E_USERNAME:-reader}"
export E2E_PASSWORD="$AUTH_BOOTSTRAP_PASSWORD"
export DEPLOYMENT_COMMIT="${GITHUB_SHA:-0000000000000000000000000000000000000000}"

SMOKE_TMP_DIR="$(mktemp -d)"
COOKIE_JAR="$SMOKE_TMP_DIR/cookies.txt"

request() {
  local method="$1"
  local url="$2"
  shift 2
  local body="$SMOKE_TMP_DIR/response-body"
  local status

  status=$(curl \
    --silent \
    --show-error \
    --output "$body" \
    --write-out "%{http_code}" \
    --request "$method" \
    "$@" \
    "$url") || {
      echo "Network error: $method $url" >&2
      cat "$body" >&2 || true
      return 1
    }

  if [[ "$status" -lt 200 || "$status" -ge 300 ]]; then
    echo "HTTP $status: $method $url" >&2
    cat "$body" >&2 || true
    return 1
  fi

  cat "$body"
}

expect_status() {
  local expected="$1"
  local method="$2"
  local url="$3"
  shift 3
  local body="$SMOKE_TMP_DIR/expected-response-body"
  local status
  status=$(curl --silent --show-error --output "$body" --write-out "%{http_code}" \
    --request "$method" "$@" "$url")
  if [[ "$status" != "$expected" ]]; then
    echo "Expected HTTP $expected, received HTTP $status: $method $url" >&2
    cat "$body" >&2 || true
    return 1
  fi
  cat "$body"
}

on_error() {
  local exit_code=$?
  echo "=== SMOKE FAILURE (exit $exit_code) ===" >&2
  echo "=== CONTAINERS ===" >&2
  docker compose ps -a >&2 || true
  echo "=== API LOGS ===" >&2
  docker compose logs --tail=300 api >&2 || true
  echo "=== WORKER LOGS ===" >&2
  docker compose logs --tail=300 worker >&2 || true
  echo "=== MIGRATION LOGS ===" >&2
  docker compose logs --tail=300 migrate >&2 || true
  echo "=== REDIS LOGS ===" >&2
  docker compose logs --tail=100 redis >&2 || true
  return "$exit_code"
}

cleanup() {
  rm -rf "$SMOKE_TMP_DIR"
  docker compose down -v --remove-orphans
}

trap on_error ERR
trap cleanup EXIT

wait_for_stack() {
  local migrate_id migrate_status migrate_code
  for _ in {1..90}; do
    migrate_id=$(docker compose ps -a -q migrate)
    if [[ -n "$migrate_id" ]]; then
      migrate_status=$(docker inspect --format '{{.State.Status}}' "$migrate_id")
      if [[ "$migrate_status" == "exited" ]]; then
        migrate_code=$(docker inspect --format '{{.State.ExitCode}}' "$migrate_id")
        if [[ "$migrate_code" != "0" ]]; then
          echo "Migration container exited with code $migrate_code" >&2
          return "$migrate_code"
        fi
        break
      fi
    fi
    sleep 1
  done
  if [[ "${migrate_status:-}" != "exited" ]]; then
    echo "Migration container did not finish in time" >&2
    return 1
  fi

  for _ in {1..90}; do
    if curl --fail --silent http://127.0.0.1:8080/health >/dev/null \
      && curl --fail --silent -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" \
        http://127.0.0.1:8080/ >/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "WEB/API stack did not become ready" >&2
  return 1
}

login_user() {
  request POST http://127.0.0.1:8080/api/auth/login \
    -c "$COOKIE_JAR" \
    -H 'Content-Type: application/json' \
    -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" \
    -d "{\"username\":\"$E2E_USERNAME\",\"password\":\"$AUTH_BOOTSTRAP_PASSWORD\"}"
}

authenticated_request() {
  local method="$1"
  local url="$2"
  shift 2
  request "$method" "$url" -b "$COOKIE_JAR" \
    -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" "$@"
}
