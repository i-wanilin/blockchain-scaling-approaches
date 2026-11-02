#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

config="${1:-config/cluster.local.json}"
binary="bin/perun-node"
if [[ ! -x "$binary" ]]; then
  echo "Linux hub binary not found; run ./scripts/build.sh first." >&2
  exit 1
fi

"$binary" -mode preflight -node ingrid -config "$config"
mkdir -p results/logs
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log="results/logs/${timestamp}-ingrid.log"

set +e
"$binary" -mode hub -config "$config" 2>&1 | tee "$log"
status="${PIPESTATUS[0]}"
set -e
echo "node=ingrid exit_code=$status log=$log"
exit "$status"
