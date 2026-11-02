#!/usr/bin/env bash
# Give this board a fixed IPv4 address on its Wi-Fi, outside the router's DHCP
# range (FRITZ!Box: .20-.200), so addresses no longer move when cards move.
# Run with sudo:   sudo bash set-static-ip.sh 192.168.178.21N [gateway]
# Keeps a backup and restores it by itself if the board cannot reach the router
# with the new address, so a mistake cannot lock the board out. The outcome is
# written to the system log (journalctl -t set-static-ip), readable without sudo.
set -euo pipefail
addr="${1:-}"; gw="${2:-192.168.178.1}"
[[ $addr =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: sudo bash set-static-ip.sh <address> [gateway]"; exit 2; }
if ! ip -4 addr show wlan0 | grep -q " $addr/" && ping -c2 -W2 "$addr" >/dev/null 2>&1; then
  echo "$addr is already used by another device - nothing changed"; exit 1
fi
f=/etc/netplan/50-cloud-init.yaml
bak=/etc/netplan/50-cloud-init.yaml.bak-before-static-ip   # not *.yaml, so netplan ignores it
[ -f "$bak" ] || cp -a "$f" "$bak"
python3 - "$f" "$addr" "$gw" <<'PY'
import sys, yaml
p, addr, gw = sys.argv[1:4]
d = yaml.safe_load(open(p))
wifis = (d.get("network") or {}).get("wifis") or {}
ifs = {k: v for k, v in wifis.items() if isinstance(v, dict)}   # skip e.g. "renderer: networkd"
if len(ifs) != 1:
    sys.exit("expected exactly one Wi-Fi interface in %s, found %d" % (p, len(ifs)))
name, w = next(iter(ifs.items()))
w["dhcp4"] = False
w["addresses"] = [addr + "/24"]
w["routes"] = [{"to": "default", "via": gw}]
w["nameservers"] = {"addresses": [gw], "search": ["fritz.box"]}
yaml.safe_dump(d, open(p, "w"), default_flow_style=False, sort_keys=False)
print("fixed address %s/24 set on %s (gateway and DNS %s)" % (addr, name, gw))
PY
chmod 600 "$f"
# keep cloud-init from rewriting the network config on later boots
echo 'network: {config: disabled}' > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
netplan generate
# apply in the background: this SSH session drops when the address changes
nohup bash -c "netplan apply; sleep 30
  if ping -c2 -W3 $gw >/dev/null 2>&1 && ip -4 addr show wlan0 | grep -q ' $addr/'; then
    logger -t set-static-ip \"ok: \$(ip -4 -br addr show wlan0)\"
  else
    cp -a $bak $f; netplan apply; logger -t set-static-ip 'reverted: router unreachable with the fixed address'
  fi" >/dev/null 2>&1 &
echo "applied - the board moves to $addr within about 30 s; this SSH session will drop"
