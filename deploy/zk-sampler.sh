#!/usr/bin/env bash
# zk-sampler.sh — bath0 only, zkSync runs: server CPU/RSS and chain progress every 5 s.
# Usage: zk-sampler.sh OUT_CSV DURATION_S [PID_FILE]   (started detached by deploy/cluster)
# The server is the PID in PID_FILE (default ~/zk-run/server.pid, written by `deploy/cluster up zksync`).
# server_cpu_pct is per interval, from two /proc/<pid>/stat readings (utime + stime delta over the wall
# time between them, 100 = one core), as in the CPU report; `ps %cpu` would be the lifetime average.
# l2_block = eth_blockNumber, l1_batch_sealed = zks_L1BatchNumber (sealed batches) from the local L2 API.
out=$1; dur=$2; pidf=${3:-$HOME/zk-run/server.pid}
pid=$(cat "$pidf" 2>/dev/null)
case "$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" in
  */zksync_server\ *) ;;
  *) echo "zk-sampler: PID '$pid' from $pidf is not a running zksync_server" >&2 ;;   # still samples the chain
esac
tck=$(getconf CLK_TCK)
echo "t,server_cpu_pct,server_rss_mb,l2_block,l1_batch_sealed" > "$out"
rpc() {
  curl -s -m 2 -X POST -H 'content-type: application/json' \
    --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$1\",\"params\":[]}" http://127.0.0.1:3050 \
    | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["result"],16))' 2>/dev/null
}
# utime + stime of the server in clock ticks (fields 14 and 15; the command name in field 2 has no spaces here).
ticks() { awk '{print $14 + $15}' "/proc/$pid/stat" 2>/dev/null; }
rss_mb() { awk '/^VmRSS:/ {printf "%d", $2 / 1024}' "/proc/$pid/status" 2>/dev/null; }
start=${EPOCHREALTIME/./}; k=0
c0=$(ticks); w0=${EPOCHREALTIME/./}
while [ $((k * 5000000)) -lt $((dur * 1000000)) ]; do
  k=$((k + 1)); wait_us=$((start + k * 5000000 - ${EPOCHREALTIME/./}))
  [ $wait_us -gt 0 ] && sleep "$(printf '%d.%06d' $((wait_us / 1000000)) $((wait_us % 1000000)))"
  c1=$(ticks); w1=${EPOCHREALTIME/./}
  # A failed reading gives an empty field, never a bash arithmetic error (would end the loop).
  if [ -n "$c0" ] && [ -n "$c1" ] && [ $((w1 - w0)) -gt 0 ]; then
    cpu=$(awk -v d=$((c1 - c0)) -v t="$tck" -v us=$((w1 - w0)) 'BEGIN{printf "%.1f", 100 * d / t / (us / 1e6)}')
  else cpu=; fi
  echo "$(date +%s.%N),$cpu,$(rss_mb),$(rpc eth_blockNumber),$(rpc zks_L1BatchNumber)" >> "$out"
  c0=$c1; w0=$w1
done
