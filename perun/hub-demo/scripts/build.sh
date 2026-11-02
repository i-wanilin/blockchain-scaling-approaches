#!/usr/bin/env bash
# Build native Linux and Windows binaries from one LF-normalized script.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v go >/dev/null 2>&1 && [[ -x /home/linuxbrew/.linuxbrew/bin/go ]]; then
  export PATH="/home/linuxbrew/.linuxbrew/bin:$PATH"
fi
command -v go >/dev/null 2>&1 || {
  echo "Go is not on PATH." >&2
  exit 1
}
mkdir -p bin

go build -o bin/perun-node .
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -o bin/perun-node.exe .
echo "built bin/perun-node and bin/perun-node.exe"
