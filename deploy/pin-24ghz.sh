#!/usr/bin/env bash
# Pin this board's Wi-Fi to 2.4 GHz. Run with sudo:
#   sudo bash pin-24ghz.sh [BSSID]
# Sets netplan "band: 2.4GHz" and, if a BSSID is given, "bssid:" — the router's
# 2.4 GHz radio. The BSSID is needed where the router practises band steering:
# a band restriction alone was undone within 0.4 s by the router moving the
# board to its 5 GHz radio (bath0, 2026-09-29).
# On the Broadcom Wi-Fi of the Pi 3B+ and Pi 4 the chip also roams inside its own
# firmware and ignores both settings (bath1, 2026-09-29: pinned, still moved to
# 5 GHz). The driver option "roamoff=1" hands roaming back to wpa_supplicant, which
# honours the pin; the script sets it and reloads the driver.
# Keeps a backup and restores it by itself if the board cannot reach the router
# afterwards, so a mistake cannot lock the board out. The outcome, including the
# band actually in use, is written to the system log (journalctl -t pin-24ghz),
# readable without sudo by group adm.
set -euo pipefail
bssid="${1:-}"
f=/etc/netplan/50-cloud-init.yaml
bak=/etc/netplan/50-cloud-init.yaml.bak-before-2.4ghz   # not *.yaml, so netplan ignores it
[ -f "$bak" ] || cp -a "$f" "$bak"
python3 - "$f" "$bssid" <<'PY'
import sys, yaml
p, bssid = sys.argv[1], sys.argv[2]
d = yaml.safe_load(open(p))
n = 0
for ifc in ((d.get("network") or {}).get("wifis") or {}).values():
    if not isinstance(ifc, dict):      # e.g. "renderer: networkd" inside wifis
        continue
    for ap in (ifc.get("access-points") or {}).values():
        if isinstance(ap, dict):
            ap["band"] = "2.4GHz"
            if bssid:
                ap["bssid"] = bssid
            n += 1
if n == 0:
    sys.exit("no Wi-Fi access point found in " + p)
yaml.safe_dump(d, open(p, "w"), default_flow_style=False, sort_keys=False)
print("band" + (" and bssid" if bssid else "") + " set on", n, "access point(s)")
PY
chmod 600 "$f"
# keep cloud-init from rewriting the network config on later boots
echo 'network: {config: disabled}' > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
netplan generate
if ! grep -q freq_list /run/netplan/wpa-wlan0.conf; then
  cp -a "$bak" "$f"; netplan generate; echo "band was not written - original restored"; exit 1
fi
if [ -n "$bssid" ] && ! grep -qi "bssid=$bssid" /run/netplan/wpa-wlan0.conf; then
  cp -a "$bak" "$f"; netplan generate; echo "bssid was not written - original restored"; exit 1
fi
roam=/etc/modprobe.d/brcmfmac-roamoff.conf
reload=0
if lsmod | grep -q '^brcmfmac'; then
  if ! grep -qs 'roamoff=1' "$roam"; then echo 'options brcmfmac roamoff=1' > "$roam"; reload=1; fi
fi
gw=$(ip route | awk '/^default/{print $3; exit}')
# apply in the background: the SSH session drops while Wi-Fi reconnects
nohup bash -c "
  if [ $reload = 1 ]; then modprobe -r brcmfmac && modprobe brcmfmac; sleep 5; fi
  netplan apply; sleep 45
  if ping -c2 -W3 $gw >/dev/null 2>&1; then
    st=\$(wpa_cli -i wlan0 status 2>/dev/null | grep -E '^(bssid|freq)=' | paste -sd' ' -)
    fq=\$(echo \"\$st\" | sed -n 's/.*freq=\\([0-9]*\\).*/\\1/p')
    if [ -n \"\$fq\" ] && [ \"\$fq\" -lt 3000 ]; then logger -t pin-24ghz \"ok: \$st\"
    else logger -t pin-24ghz \"WARNING: reachable but not on 2.4 GHz: \$st\"; fi
  else
    cp -a $bak $f; rm -f $roam; modprobe -r brcmfmac && modprobe brcmfmac; sleep 5; netplan apply
    logger -t pin-24ghz 'reverted: router unreachable after the change'
  fi" >/dev/null 2>&1 &
echo "applied - Wi-Fi reconnects within a minute (reverts by itself if the router is unreachable)"
echo "check afterwards without sudo: journalctl -t pin-24ghz"
