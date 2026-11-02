#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

node="${1:?usage: ./scripts/preflight.sh NODE [CONFIG]}"
config="${2:-config/cluster.local.json}"

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
