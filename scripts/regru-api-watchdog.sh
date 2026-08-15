#!/usr/bin/env bash
set -euo pipefail

state_file=${WATCHDOG_STATE_FILE:-/run/bankrotai-api-watchdog.failures}
restart_file=${WATCHDOG_RESTART_FILE:-/run/bankrotai-api-watchdog.last-restart}
lock_file=${WATCHDOG_LOCK_FILE:-/run/bankrotai-api-watchdog.lock}
live_url=${WATCHDOG_LIVE_URL:-http://127.0.0.1:8000/health/live}
ready_url=${WATCHDOG_READY_URL:-http://127.0.0.1:8000/health/ready}
restart_cooldown_seconds=${WATCHDOG_RESTART_COOLDOWN_SECONDS:-300}

exec 9>"$lock_file"
if ! flock -n 9; then
  logger -t bankrotai-watchdog "another watchdog invocation is still running"
  exit 0
fi

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

now_epoch=${WATCHDOG_NOW_EPOCH:-$(date +%s)}
last_restart=0
if [[ -r "$restart_file" ]]; then
  read -r last_restart < "$restart_file" || last_restart=0
fi
if (( now_epoch - last_restart < restart_cooldown_seconds )); then
  logger -t bankrotai-watchdog "restart suppressed by ${restart_cooldown_seconds}s cooldown"
  exit 0
fi

logger -t bankrotai-watchdog "restarting unresponsive API and tunnel after three liveness failures"
printf '%s\n' "$now_epoch" > "$restart_file"
if ! timeout 30 docker restart bankrotai-api >/dev/null; then
  logger -t bankrotai-watchdog "API restart failed or timed out"
  exit 1
fi
if docker container inspect bankrotai-cloudflared >/dev/null 2>&1; then
  if ! timeout 30 docker restart bankrotai-cloudflared >/dev/null; then
    logger -t bankrotai-watchdog "tunnel restart failed or timed out"
    exit 1
  fi
fi
rm -f "$state_file"
