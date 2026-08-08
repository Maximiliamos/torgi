#!/usr/bin/env bash
set -euo pipefail

echo "scripts/docker-smoke.sh is kept as an operator-smoke compatibility entrypoint."
exec "$(dirname "$0")/docker-smoke-operator.sh"
