#!/usr/bin/env bash
# recorder.sh — per-board sampler for a measurement run (cluster spec §6).
# Usage: recorder.sh OUT_CSV DURATION_S   (started detached by deploy/cluster)
out=$1; dur=$2
# Absolute schedule in microseconds (EPOCHREALTIME) so the 5 s cadence does not drift.
start=${EPOCHREALTIME/./}; k=0
echo "t,temp_c,throttled,arm_mhz,mem_used_mb,load1,sd_read_kb,sd_write_kb,sd_io_ms,bssid,freq_mhz,signal_dbm,chrony_offset_ms,cpu_busy_pct" > "$out"
read -r r0 w0 io0 < <(awk '$3=="mmcblk0"{print $6, $10, $13}' /proc/diskstats)
# CPU utilisation, all cores: % of jiffies not idle/iowait since the previous sample (first line of /proc/stat:
# cpu user nice system idle iowait irq softirq steal ...).
cpu_jiffies() { local u n s i w q sq st; read -r _ u n s i w q sq st _ < /proc/stat; echo "$((u + n + s + i + w + q + sq + st)) $((i + w))"; }
read -r ct0 ci0 < <(cpu_jiffies)
while [ $((k * 5000000)) -lt $((dur * 1000000)) ]; do
  t=$(vcgencmd measure_temp 2>/dev/null | tr -dc '0-9.')
  th=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
  c=$(vcgencmd measure_clock arm 2>/dev/null | cut -d= -f2)
  # A failed sample must give an empty field, never a bash arithmetic error (would end the loop).
  if [[ $c =~ ^[0-9]+$ ]]; then mhz=$((c / 1000000)); else mhz=; fi
  mem=$(free -m | awk '/^Mem:/{print $3}')
  l1=$(cut -d' ' -f1 /proc/loadavg)
  read -r r w io < <(awk '$3=="mmcblk0"{print $6, $10, $13}' /proc/diskstats)
  link=$(iw dev wlan0 link 2>/dev/null)
  bssid=$(awk '/^Connected to/ {print $3}' <<< "$link")
  freq=$(awk '/freq:/ {print int($2)}' <<< "$link")
  sig=$(awk '/signal:/ {print $2}' <<< "$link")
  # chronyc -c tracking field 5 = "system time" offset in seconds (current
  # system clock error vs. the reference, sign: positive = system slow).
  off=$(chronyc -c tracking 2>/dev/null | awk -F, '{printf "%.3f", $5*1000}')
  read -r ct ci < <(cpu_jiffies)
  if [ $((ct - ct0)) -gt 0 ]; then
    cpu=$(awk -v t=$((ct - ct0)) -v i=$((ci - ci0)) 'BEGIN{printf "%.1f", 100 * (t - i) / t}')
  else cpu=; fi
  echo "$(date +%s.%N),$t,$th,$mhz,$mem,$l1,$(( (${r:-0} - ${r0:-0}) / 2 )),$(( (${w:-0} - ${w0:-0}) / 2 )),$(( ${io:-0} - ${io0:-0} )),$bssid,$freq,$sig,$off,$cpu" >> "$out"
  r0=$r; w0=$w; io0=$io; ct0=$ct; ci0=$ci
  k=$((k + 1))
  wait_us=$((start + k * 5000000 - ${EPOCHREALTIME/./}))
  if [ $wait_us -gt 0 ] && [ $((k * 5000000)) -lt $((dur * 1000000)) ]; then
    sleep "$(printf '%d.%06d' $((wait_us / 1000000)) $((wait_us % 1000000)))"
  fi
done
