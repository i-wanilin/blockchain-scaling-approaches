#!/bin/bash
# Run on bath0 with no stack up:  scp deploy/reth-idle-cpu.sh bath0:/tmp/ && ssh bath0 bash /tmp/reth-idle-cpu.sh
# Output of the run of 2026-10-29: results/pi/reth-idle-cpu-2026-10-29.txt
# CPU of zkSync's L1 (reth --dev) alone on bath0, without the zkSync server: idle board, then reth at
# 300 ms and at 1 s block time. System-wide CPU from /proc/stat; reth's own CPU from its cgroup
# (cpu.stat usage_usec), both averaged over the whole measuring window.
set -e
cd /home/i_wanilin/zksync-ecosystem/thesis_local
WARM=60; MEAS=300
stat() { awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9, $5+$6}' /proc/stat; }
cg() { local id; id=$(docker inspect -f '{{.Id}}' thesis_local-reth-1); awk '/usage_usec/{print $2}' /sys/fs/cgroup/system.slice/docker-$id.scope/cpu.stat; }
measure() { # $1 label, $2 with container (1/0)
  read t1 i1 < <(stat); [ "$2" = 1 ] && u1=$(cg); s1=$(date +%s.%N)
  sleep $MEAS
  read t2 i2 < <(stat); [ "$2" = 1 ] && u2=$(cg); s2=$(date +%s.%N)
  awk -v t1=$t1 -v t2=$t2 -v i1=$i1 -v i2=$i2 -v l="$1" 'BEGIN{printf "%s: whole board %.2f %% CPU (all 4 cores)", l, 100*(1-(i2-i1)/(t2-t1))}'
  [ "$2" = 1 ] && awk -v u1=$u1 -v u2=$u2 -v s1=$s1 -v s2=$s2 'BEGIN{c=(u2-u1)/1e6/(s2-s1); printf "; reth container %.2f %% of one core = %.2f %% of the board", 100*c, 25*c}'
  echo
}
blocks() { curl -s -X POST -H 'content-type: application/json' --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' http://127.0.0.1:8945 | sed 's/.*"result":"\(0x[0-9a-f]*\)".*/\1/'; }
[ "$(docker ps -q | wc -l)" = 0 ] || { echo "containers running, abort"; exit 1; }
echo "bath0 $(date -u +%FT%TZ), reth image $(grep -o 'reth:v[0-9.]*' docker-compose.yml), window ${MEAS}s after ${WARM}s warm-up; $(vcgencmd measure_temp)"
measure "idle (no containers)" 0
for bt in 300ms 1s; do
  sed "s/--dev.block-time 300ms/--dev.block-time $bt/" docker-compose.yml > /tmp/reth-probe.yml
  docker compose -f /tmp/reth-probe.yml --project-directory . up -d reth >/dev/null 2>&1
  sleep $WARM
  b1=$(blocks); measure "reth alone, block time $bt" 1; b2=$(blocks)
  echo "  blocks made in the window: $((b2-b1)) ($(awk -v n=$((b2-b1)) -v m=$MEAS 'BEGIN{printf "%.2f", n/m}') per s); memory $(docker stats --no-stream --format '{{.MemUsage}}' thesis_local-reth-1)"
  docker compose -f /tmp/reth-probe.yml --project-directory . stop reth >/dev/null 2>&1
done
rm -f /tmp/reth-probe.yml
echo "after: $(docker ps -q | wc -l) containers running; $(vcgencmd measure_temp); throttled $(vcgencmd get_throttled)"
