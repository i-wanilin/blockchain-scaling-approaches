#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

node="${1:?usage: ./scripts/run-client.sh NODE [PAYMENTS] [CONFIG]}"
payments="${2:-0}"
config="${3:-config/cluster.local.json}"

kernel="$(uname -r | tr '[:upper:]' '[:lower:]')"
if [[ "$kernel" == *microsoft* && -f bin/perun-node.exe ]]; then
  binary="bin/perun-node.exe"
elif [[ -x bin/perun-node ]]; then
  binary="bin/perun-node"
elif [[ -f bin/perun-node.exe ]]; then
  binary="bin/perun-node.exe"
else
  echo "No perun-node binary found; run the build script first." >&2
  exit 1
fi

"$binary" -mode preflight -node "$node" -config "$config"
mkdir -p results/logs
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log="results/logs/${timestamp}-${node}.log"

set +e
"$binary" -mode client -node "$node" -config "$config" -payments "$payments" 2>&1 | tee "$log"
status="${PIPESTATUS[0]}"
set -e
echo "node=$node exit_code=$status log=$log"
exit "$status"
