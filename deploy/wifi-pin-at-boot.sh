#!/usr/bin/env bash
# wifi-pin-at-boot.sh — make the 2.4 GHz pin survive a reboot on the dual-band
# boards (bath0-bath2). Installs a boot-time check as a systemd service.
#
# Usage (on the board, after pin-24ghz.sh):  sudo bash wifi-pin-at-boot.sh [BSSID]
#
# Why: pin-24ghz.sh puts the band, the BSSID and brcmfmac roamoff=1 in place, and
# they hold while the board runs. At boot, though, the Broadcom chip makes its
# first join before the pin takes effect and ends up on the router's 5 GHz radio
# (seen on all three dual-band boards on 2026-09-30). Reloading the driver and
# reconnecting then joins the pinned radio. This service does that after every
# boot, up to three times, and logs the outcome (journalctl -t wifi-pin-at-boot).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
BSSID="${1:-02:00:00:00:00:01}"

cat > /usr/local/sbin/wifi-pin-at-boot <<EOF
#!/bin/sh
# Installed by deploy/wifi-pin-at-boot.sh. Rejoins the pinned 2.4 GHz radio.
want="$BSSID"
for try in 1 2 3; do
  for i in \$(seq 1 30); do iw dev wlan0 link | grep -q "^Connected" && break; sleep 2; done
  have=\$(iw dev wlan0 link | awk '/^Connected to/ {print \$3}')
  freq=\$(iw dev wlan0 link | awk '/freq:/ {print int(\$2)}')
  if [ "\$have" = "\$want" ]; then
    logger -t wifi-pin-at-boot "ok: bssid=\$have freq=\$freq (try \$try)"
    exit 0
  fi
  logger -t wifi-pin-at-boot "on bssid=\$have freq=\$freq, not \$want: reloading brcmfmac (try \$try)"
  modprobe -r brcmfmac_wcc brcmfmac; sleep 2; modprobe brcmfmac; sleep 5; netplan apply; sleep 20
done
logger -t wifi-pin-at-boot "WARNING: still not on \$want after 3 tries: bssid=\$have freq=\$freq"
exit 1
EOF
chmod 755 /usr/local/sbin/wifi-pin-at-boot

cat > /etc/systemd/system/wifi-pin-at-boot.service <<'EOF'
[Unit]
Description=Rejoin the pinned 2.4 GHz radio after boot (brcmfmac ignores the pin on first join)
After=network-online.target wifi-powersave-off.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/wifi-pin-at-boot
ExecStartPost=-/bin/sh -c 'command -v iw >/dev/null && iw dev wlan0 set power_save off'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable wifi-pin-at-boot.service >/dev/null 2>&1
echo "$(hostname): wifi-pin-at-boot installed (pins $BSSID); check after the next boot with: journalctl -t wifi-pin-at-boot"
