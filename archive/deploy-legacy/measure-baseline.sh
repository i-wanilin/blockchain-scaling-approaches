#!/usr/bin/env bash
# measure-baseline.sh — characterize the assembled Pi cluster's environment
# BEFORE running experiments (the thesis's environment-realism axis needs a
# documented baseline: what does the raw LAN look like without netem?).
#
# Collects into deploy/baseline-results/<timestamp>/ :
#   - ping-matrix.txt      pairwise RTT (laptop + every Pi, all directions)
#   - iperf3.txt           per-Pi TCP throughput to the laptop (if iperf3
#                          server is running on the laptop: `iperf3 -s`)
#   - chrony.txt           chronyc tracking offset per Pi (time-sync quality)
#   - host-info.txt        per-Pi model, kernel, governor, temperature
#
# Usage (from the laptop, with SSH key access to the Pis):
#   ./measure-baseline.sh                       # hosts from ansible/inventory.ini
#   ./measure-baseline.sh ivan@192.168.50.101 ivan@192.168.50.102 ...
#
# Status: prepared, NOT yet exercised on hardware (no Pis assembled yet).
set -euo pipefail
cd "$(dirname "$0")"

PING_COUNT="${PING_COUNT:-20}"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="baseline-results/${TS}"
mkdir -p "$OUT"

# --- resolve host list -------------------------------------------------------
declare -a HOSTS=()
if (( $# > 0 )); then
  HOSTS=("$@")
elif [[ -f ansible/inventory.ini ]]; then
  # lines like: pi1 ansible_host=192.168.50.101 ...
  USER_=$(awk -F= '/^ansible_user=/{print $2}' ansible/inventory.ini | head -1)
  while read -r ip; do
    HOSTS+=("${USER_:-pi}@${ip}")
  done < <(awk '/^pi[0-9]+ /{for(i=1;i<=NF;i++) if($i ~ /^ansible_host=/){sub("ansible_host=","",$i); print $i}}' ansible/inventory.ini)
else
  echo "usage: $0 user@host [user@host ...]   (or create ansible/inventory.ini)" >&2
  exit 1
fi
echo "hosts: ${HOSTS[*]}"

SSH="ssh -o BatchMode=yes -o ConnectTimeout=5"

# --- pairwise ping matrix ----------------------------------------------------
{
  echo "# avg RTT (ms), ${PING_COUNT} pings, $(date -Is)"
  # laptop -> each Pi
  for h in "${HOSTS[@]}"; do
    ip="${h#*@}"
    rtt=$(ping -c "$PING_COUNT" -i 0.2 -q "$ip" 2>/dev/null | awk -F/ '/rtt/ {print $5}') || rtt=FAIL
    echo "laptop -> ${ip}: ${rtt:-FAIL}"
  done
  # each Pi -> each other Pi (full matrix over ssh)
  for src in "${HOSTS[@]}"; do
    for dst in "${HOSTS[@]}"; do
      [[ "$src" == "$dst" ]] && continue
      dst_ip="${dst#*@}"
      rtt=$($SSH "$src" "ping -c $PING_COUNT -i 0.2 -q $dst_ip" 2>/dev/null \
            | awk -F/ '/rtt/ {print $5}') || rtt=FAIL
      echo "${src#*@} -> ${dst_ip}: ${rtt:-FAIL}"
    done
  done
} | tee "$OUT/ping-matrix.txt"

# --- iperf3 throughput to the laptop (best effort) ---------------------------
{
  echo "# iperf3 -c <laptop> from each Pi (needs 'iperf3 -s' on the laptop)"
  LAPTOP_IP=$(awk -F= '/^laptop_ip=/{print $2}' ansible/inventory.ini 2>/dev/null | head -1)
  if [[ -n "${LAPTOP_IP:-}" ]]; then
    for h in "${HOSTS[@]}"; do
      echo "== $h =="
      $SSH "$h" "command -v iperf3 >/dev/null && iperf3 -c $LAPTOP_IP -t 5 --json 2>/dev/null \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"%.1f Mbit/s\" % (d[\"end\"][\"sum_received\"][\"bits_per_second\"]/1e6))' \
        || echo 'iperf3 unavailable or laptop server not running'"
    done
  else
    echo "laptop_ip not found in inventory — skipped"
  fi
} | tee "$OUT/iperf3.txt"

# --- chrony offsets (time-sync quality) --------------------------------------
{
  echo "# chronyc tracking per Pi — 'System time' line = current offset"
  for h in "${HOSTS[@]}"; do
    echo "== $h =="
    $SSH "$h" "chronyc tracking 2>/dev/null | grep -E 'Reference|System time|RMS offset|Stratum'" || echo FAIL
  done
} | tee "$OUT/chrony.txt"

# --- per-host info ------------------------------------------------------------
{
  for h in "${HOSTS[@]}"; do
    echo "== $h =="
    $SSH "$h" 'cat /proc/device-tree/model 2>/dev/null; echo;
               uname -r;
               cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null;
               vcgencmd measure_temp 2>/dev/null || true;
               free -m | head -2' || echo FAIL
  done
} | tee "$OUT/host-info.txt"

echo "== baseline written to deploy/${OUT}/ =="
