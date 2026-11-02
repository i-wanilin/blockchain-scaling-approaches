#!/usr/bin/env bash
# wifi-powersave-off.sh — switch Wi-Fi power saving off on this board, now and
# whenever wlan0 appears (every boot, and after a brcmfmac reload).
#
# Usage (on the board):  sudo bash wifi-powersave-off.sh
#
# The Pi's Broadcom chip has power saving on by default: it sleeps between
# beacons, so a packet to an idle board can wait hundreds of milliseconds
# (a bath1 -> bath2 ping took up to 1.1 s on 2026-09-29). Every run's
# pre-flight asserts it is off (cluster spec §5).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

command -v iw >/dev/null || { apt-get update -qq && apt-get install -y -qq iw; }
IW="$(command -v iw)"

cat > /etc/systemd/system/wifi-powersave-off.service <<EOF
[Unit]
Description=Wi-Fi power save off on wlan0
BindsTo=sys-subsystem-net-devices-wlan0.device
After=sys-subsystem-net-devices-wlan0.device

[Service]
Type=oneshot
ExecStart=$IW dev wlan0 set power_save off
RemainAfterExit=yes

[Install]
WantedBy=sys-subsystem-net-devices-wlan0.device
EOF
systemctl daemon-reload
systemctl enable --now wifi-powersave-off.service >/dev/null 2>&1 || true
"$IW" dev wlan0 set power_save off

state="$("$IW" dev wlan0 get power_save)"
logger -t wifi-powersave-off "$state"
echo "$(hostname): $state"
[[ "$state" == *off* ]]
