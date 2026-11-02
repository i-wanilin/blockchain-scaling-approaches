#!/bin/bash
# Test C: resident memory of the zkSync stack -- the Raspberry Pi question.
#
# Arbitrum passed the architecture test (its image IS multi-arch arm64) and
# still cannot run on a Pi 4, because ~24 compose services exceed 4 GB. So the
# deciding number for zkSync is not "does it compile" but "what does it hold
# resident". zksync_server hard-requires PostgreSQL, which Perun (a single
# 9.7 MB static binary) does not.
#
# Pi 4 reference: 4 GB total, ~3.7 GB usable after firmware reservation.
echo "=== zkSync stack footprint  $(date -Is) ==="
echo "context: $1"
echo
printf "%-28s %12s %12s\n" "component" "RSS_MB" "note"
TOT=0
# zksync_server runs on the host under zkstack
for pid in $(pgrep -f "zksync_server" 2>/dev/null); do
  RSS=$(awk '/VmRSS/{print int($2/1024)}' /proc/$pid/status 2>/dev/null)
  [ -n "$RSS" ] && printf "%-28s %12s %12s\n" "zksync_server (pid $pid)" "$RSS" "sequencer" && TOT=$((TOT+RSS))
done
# postgres + reth in containers
while read -r name mem; do
  MB=$(echo "$mem" | sed 's/MiB//;s/GiB/*1024/' | bc 2>/dev/null | cut -d. -f1)
  [ -n "$MB" ] && printf "%-28s %12s %12s\n" "$name" "$MB" "container" && TOT=$((TOT+MB))
done < <(docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null | awk '{print $1, $2}')
echo "---"
printf "%-28s %12s\n" "TOTAL RSS (MB)" "$TOT"
echo
echo "Pi 4 usable RAM ~3700 MB."
if [ "$TOT" -gt 0 ]; then
  echo "headroom vs Pi 4: $((3700 - TOT)) MB"
  [ "$TOT" -lt 3700 ] && echo "VERDICT: fits in a Pi 4's RAM (L1 hosted off-Pi)" \
                      || echo "VERDICT: does NOT fit a Pi 4"
fi
echo
echo "NOTE: reth (the L1) would live on the workstation, not the Pi -- as in the"
echo "Arbitrum plan. Subtract it for the Pi-only figure."
