#!/usr/bin/env bash
# board-facts.sh — one JSON object describing this board right now, for the
# run manifest and the pre-flight check (cluster spec §5, §6).
link=$(iw dev wlan0 link)
python3 - "$link" <<'PY'
import json, os, shutil, subprocess, sys
def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
link = sys.argv[1]
def field(prefix):
    for l in link.splitlines():
        l = l.strip()
        if l.startswith(prefix):
            return l.split(prefix, 1)[1].strip().split()[0]
    return None
# chronyc -c tracking: [1]=ref id, [2]=ref name/IP, [3]=stratum, [4]=ref time,
# [5]=system time offset in seconds (current system clock error vs. reference).
track = sh("chronyc -c tracking").split(",")
def zksync_server_paths():
    # /proc, not pgrep -f: a pgrep pattern containing "zksync_server" would also appear (as
    # literal text) in this very subprocess's own command line and could self-match. Records the
    # full argv[0] of every matching process, so a run's facts show exactly which binary ran
    # (the measured one, the .a72 build, or anything else) -- zksync_server_up above still counts
    # only the measured binary.
    paths = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv0 = fh.read().split(b"\x00", 1)[0]
        except OSError:
            continue
        if argv0 and b"zksync_server" in argv0:
            paths.append(argv0.decode(errors="replace"))
    return sorted(set(paths))
print(json.dumps({
    "host": sh("hostname"),
    "model": open("/proc/device-tree/model").read().rstrip("\x00"),
    "serial": open("/proc/device-tree/serial-number").read().rstrip("\x00").lstrip("0"),
    "wifi_mac": open("/sys/class/net/wlan0/address").read().strip(),
    "kernel": os.uname().release,
    "bssid": field("Connected to"),
    "freq_mhz": int(float(field("freq:") or 0)),
    # signal and link rate at this moment (spec §6); the recorder samples signal throughout the run
    "signal_dbm": int(field("signal:")) if field("signal:") else None,
    "rx_bitrate_mbit": float(field("rx bitrate:")) if field("rx bitrate:") else None,
    "tx_bitrate_mbit": float(field("tx bitrate:")) if field("tx bitrate:") else None,
    "power_save": sh("iw dev wlan0 get power_save").split(":")[-1].strip(),
    "throttled": sh("vcgencmd get_throttled").split("=")[-1],
    "chrony_source": track[1] if len(track) > 1 else None,
    "chrony_offset_ms": float(track[4]) * 1000 if len(track) > 4 else None,
    "netem": "netem" in sh("tc qdisc show dev wlan0"),
    "disk_free_gb": round(shutil.disk_usage("/").free / 2**30, 1),
    "temp_c": float(sh("vcgencmd measure_temp").split("=")[-1].rstrip("'C") or 0),
    "docker_up": sh("docker ps -q 2>/dev/null | wc -l"),
    "containers": sh("docker ps --format '{{.Names}}' 2>/dev/null").split(),
    "zksync_server_up": int(sh("pgrep -fc '/zksync_server( |$)' 2>/dev/null") or 0),
    "zksync_server_paths": zksync_server_paths(),
    # pgrep -x matches the process name only (comm/argv[0] basename), not a command-line
    # substring, so this script -- which always runs as "bash"/"python3" -- cannot self-match a
    # process named "anvil" (bath0's Anvil, perun-uc1-measurement-path.md Task 2: /home/i_wanilin/bin/anvil).
    "anvil_up": sh("pgrep -x anvil | wc -l"),
    "perun_node_sha256": sh("sha256sum ~/measure-code/bin/perun-node 2>/dev/null | cut -d' ' -f1"),
}))
PY
