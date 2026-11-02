#!/usr/bin/env bash
# Remove generated channel/identity state while preserving benchmark evidence.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf .state
if [[ "${1:-}" == "--clear-results" ]]; then
  rm -rf results
  echo "removed local distributed-benchmark state and results"
else
  echo "removed local distributed-benchmark state; preserved results"
fi
