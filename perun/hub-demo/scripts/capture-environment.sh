#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v go >/dev/null 2>&1 && [[ -x /home/linuxbrew/.linuxbrew/bin/go ]]; then
  export PATH="/home/linuxbrew/.linuxbrew/bin:$PATH"
fi

mkdir -p results/environment
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="results/environment/${timestamp}-$(hostname).txt"

{
  echo "timestamp_utc=$timestamp"
  echo "hostname=$(hostname)"
  echo "commit=$(git rev-parse HEAD)"
  if command -v go >/dev/null 2>&1; then
    go version
  else
    echo "go=not-on-PATH"
  fi
  uname -a
  sed -n '1,12p' /etc/os-release
  ip -brief address
  ip route
  if command -v nmcli >/dev/null 2>&1; then
    nmcli -f IN-USE,SSID,BSSID,FREQ,CHAN,RATE,SIGNAL,SECURITY dev wifi
  fi
  for peer in "$@"; do
    echo "ping_peer=$peer"
    ping -c 50 "$peer"
  done
} 2>&1 | tee "$output"

echo "environment_log=$output"
