#!/usr/bin/env bash
# Pin this board's Wi-Fi to the router's 5 GHz radio (dual-band boards only:
# Pi 4 bath0, Pi 3B+ bath1/bath2). Run with sudo:
#   sudo bash pin-5ghz.sh BSSID_5GHZ
# Mirror of pin-24ghz.sh: sets netplan "band: 5GHz" and "bssid:" (the router
# practises band steering, so the BSSID is needed), keeps brcmfmac roamoff=1.
# Why (2026-10-04): the shared 2.4 GHz channel collapses the UC1 sensor stream
# under heavy payment traffic; moving bath0/bath1 to 5 GHz takes their flows off
# 2.4 GHz once (router -> 3B customers stays on 2.4 GHz).
# Keeps a backup of the current (2.4 GHz) config and restores it by itself if,
# 90 s after the change, the board cannot reach the router or is not on 5 GHz.
# Outcome in the system log: journalctl -t pin-5ghz. Undo: sudo bash pin-24ghz.sh <2.4 GHz BSSID>
set -euo pipefail
bssid="${1:?usage: pin-5ghz.sh BSSID_5GHZ}"
f=/etc/netplan/50-cloud-init.yaml
bak=/etc/netplan/50-cloud-init.yaml.bak-before-5ghz   # not *.yaml, so netplan ignores it
cp -a "$f" "$bak"
python3 - "$f" "$bssid" <<'PY'
import sys, yaml
p, bssid = sys.argv[1], sys.argv[2]
d = yaml.safe_load(open(p))
n = 0
for ifc in ((d.get("network") or {}).get("wifis") or {}).values():
    if not isinstance(ifc, dict):
        continue
    for ap in (ifc.get("access-points") or {}).values():
        if isinstance(ap, dict):
            ap["band"] = "5GHz"
            ap["bssid"] = bssid
            n += 1
if n == 0:
    sys.exit("no Wi-Fi access point found in " + p)
yaml.safe_dump(d, open(p, "w"), default_flow_style=False, sort_keys=False)
print("band 5GHz and bssid set on", n, "access point(s)")
PY
chmod 600 "$f"
netplan generate
if ! grep -qi "bssid=$bssid" /run/netplan/wpa-wlan0.conf; then
  cp -a "$bak" "$f"; netplan generate; echo "bssid was not written - original restored"; exit 1
fi
gw=$(ip route | awk '/^default/{print $3; exit}')
# apply in the background: the SSH session drops while Wi-Fi reconnects
nohup bash -c "
  netplan apply; sleep 90
  st=\$(wpa_cli -i wlan0 status 2>/dev/null | grep -E '^(bssid|freq)=' | paste -sd' ' -)
  fq=\$(echo \"\$st\" | sed -n 's/.*freq=\\([0-9]*\\).*/\\1/p')
  if ping -c2 -W3 $gw >/dev/null 2>&1 && [ -n \"\$fq\" ] && [ \"\$fq\" -gt 4900 ]; then
    logger -t pin-5ghz \"ok: \$st\"
  else
    cp -a $bak $f; netplan generate; netplan apply
    logger -t pin-5ghz \"reverted to the previous config: \$st\"
  fi" >/dev/null 2>&1 &
echo "applied - Wi-Fi reconnects within ~1-2 min (reverts by itself after 90 s if not reachable on 5 GHz)"
echo "check afterwards without sudo: journalctl -t pin-5ghz"
