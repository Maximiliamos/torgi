#!/usr/bin/env bash
set -euo pipefail

state_file=/run/bankrotai-api-watchdog.failures
live_url=http://127.0.0.1:8000/health/live
ready_url=http://127.0.0.1:8000/health/ready

if curl --fail --silent --show-error --connect-timeout 2 --max-time 8 "$live_url" >/dev/null; then
  rm -f "$state_file"
  if ! curl --fail --silent --show-error --connect-timeout 2 --max-time 15 "$ready_url" >/dev/null; then
    logger -t bankrotai-watchdog "readiness failed while liveness remained healthy"
  fi
  exit 0
fi

failures=0
if [[ -r "$state_file" ]]; then
  read -r failures < "$state_file" || failures=0
fi
failures=$((failures + 1))
printf '%s\n' "$failures" > "$state_file"
logger -t bankrotai-watchdog "liveness failure $failures of 3"

if (( failures < 3 )); then
  exit 0
fi

logger -t bankrotai-watchdog "restarting unresponsive API and tunnel after three liveness failures"
docker restart bankrotai-api >/dev/null
if docker container inspect bankrotai-cloudflared >/dev/null 2>&1; then
  docker restart bankrotai-cloudflared >/dev/null
fi
rm -f "$state_file"
