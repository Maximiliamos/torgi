#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/docker-smoke-lib.sh
source "$SCRIPT_DIR/docker-smoke-lib.sh"

export API_READ_ONLY=false
docker compose up -d --build
wait_for_stack

login_user >/dev/null
docker compose exec -T worker celery -A bankrotai.tasks:celery_app inspect ping --timeout 10

response=$(authenticated_request POST http://127.0.0.1:8080/api/online/torgi-gov/sync \
  -H 'Content-Type: application/json' \
  -d '{"search":"smoke-test-no-results","max_items":1}')
task_id=$(python -c 'import json,sys; print(json.load(sys.stdin)["task_id"])' <<<"$response")
for _ in {1..30}; do
  task_status=$(curl --silent --show-error --output "$SMOKE_TMP_DIR/task-status" --write-out "%{http_code}" \
    -b "$COOKIE_JAR" \
    -u "$WEB_BASIC_AUTH_USER:$WEB_BASIC_AUTH_PASSWORD" \
    "http://127.0.0.1:8080/api/tasks/$task_id")
  if [[ "$task_status" == "200" ]]; then
    python -c 'import json,sys; value=json.load(sys.stdin); assert value["status"] in {"running","completed","failed"}' \
      <"$SMOKE_TMP_DIR/task-status"
    exit 0
  fi
  if [[ "$task_status" != "404" ]]; then
    echo "Unexpected task status HTTP $task_status" >&2
    cat "$SMOKE_TMP_DIR/task-status" >&2
    false
  fi
  sleep 2
done
echo "Queued task did not become observable in time" >&2
false
