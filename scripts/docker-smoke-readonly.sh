#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/docker-smoke-lib.sh
source "$SCRIPT_DIR/docker-smoke-lib.sh"

export API_READ_ONLY=true
export API_RATE_LIMIT_PER_MINUTE=10000
docker compose up -d --build postgres redis migrate api web
wait_for_stack

request GET http://127.0.0.1:8080/health >/dev/null
login_user >/dev/null
authenticated_request GET http://127.0.0.1:8080/api/lots >/dev/null

map_headers="$SMOKE_TMP_DIR/map-headers"
map_body="$SMOKE_TMP_DIR/map-body"
map_status=$(curl --silent --show-error --dump-header "$map_headers" --output "$map_body" \
  --write-out "%{http_code}" -b "$COOKIE_JAR" \
  -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" \
  'http://127.0.0.1:8080/api/map/lots?west=39&south=57&east=40&north=58&limit=10')
[[ "$map_status" == "200" ]]
etag=$(awk 'BEGIN{IGNORECASE=1} /^etag:/ {sub(/\r$/, "", $2); print $2}' "$map_headers")
[[ -n "$etag" ]]
etag_status=$(curl --silent --show-error --output /dev/null --write-out "%{http_code}" \
  -H "If-None-Match: $etag" -b "$COOKIE_JAR" \
  -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" \
  'http://127.0.0.1:8080/api/map/lots?west=39&south=57&east=40&north=58&limit=10')
[[ "$etag_status" == "304" ]]

expect_status 404 POST http://127.0.0.1:8080/api/online/torgi-gov/sync \
  -b "$COOKIE_JAR" -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{"search":"smoke-test-no-results","max_items":1}' >/dev/null

npm --prefix WEB run test:e2e
python scripts/load-test-readonly.py
