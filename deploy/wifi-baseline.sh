#!/usr/bin/env bash
# wifi-baseline.sh — the bare network under the measurements (cluster spec §7.1 `baseline`): no chain, no
# payment client, no game running. For each active board, one at a time so they do not share the air:
#   1. Wi-Fi link (iw dev wlan0 link: BSSID, frequency, signal, rx/tx bitrate) and the board facts;
#   2. ping round trip to bath0 (the stack) and to bath1 (UC1 sensor / UC2 hub): COUNT pings, INTERVAL apart;
#   3. TCP throughput to bath0 and to bath1, upload then download, SECONDS each (a Python sender/receiver
#      piped over ssh; nothing is installed on the boards).
# Output: results/pi/baseline/<date>-<time>-wifi-baseline/ with the raw outputs and summary.json
# (per board and target: RTT p50/p90/p99/max/loss, Mbit/s up/down). Run with the stacks down.
#
# Usage: deploy/wifi-baseline.sh            (COUNT=300 INTERVAL=0.1 TP_SECONDS=10 PORT=5299 to change; not SECONDS, a bash builtin)
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
read -ra BOARDS <<< "${BASELINE_BOARDS:-bath0 bath1 bath2 bath4 bath5 bath6 bath7}"   # bath3 is dead (2026-10-05)
TARGETS=(bath0 bath1)
COUNT=${COUNT:-300}; INTERVAL=${INTERVAL:-0.1}; SECONDS_TP=${TP_SECONDS:-10}; PORT=${PORT:-5299}
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8)
out="$REPO/results/pi/baseline/$(date +%Y%m%d-%H%M%S)-wifi-baseline"
mkdir -p "$out"/{ping,throughput,link,facts}
say() { echo "[$(date +%T)] $*"; }
ip_of() { getent hosts "$1" | awk '{print $1}' || true; }
declare -A IP
for t in "${TARGETS[@]}"; do IP[$t]=$("${SSH[@]}" "$t" "hostname -I | awk '{print \$1}'"); done

# the stacks must be down: a running chain would load bath0's radio and CPU
# (pgrep -x matches the process name only; -f would match this very command line)
if "${SSH[@]}" bath0 'pgrep -x anvil >/dev/null || pgrep -x zksync_server >/dev/null || [ -n "$(docker ps -q 2>/dev/null)" ]'; then
  echo "a stack is running on bath0 (anvil, zksync_server or docker); bring it down first (deploy/cluster down)" >&2
  exit 1
fi

# TCP sender/receiver: `srv up|down SECONDS PORT` on the target, `cli up|down SECONDS HOST PORT` on the board.
# up = board -> target, down = target -> board; the receiving side times the bytes (first to last byte).
TP=$(cat <<'PY'
import socket, sys, time, json
role, mode, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
buf = b"\0" * 65536
def send(s):
    end = time.time() + secs
    while time.time() < end:
        s.sendall(buf)
    s.shutdown(socket.SHUT_WR)
def recv(s):
    n, t0, t1 = 0, None, None
    while True:
        d = s.recv(1 << 20)
        if not d:
            break
        t1 = time.time(); t0 = t0 or t1; n += len(d)
    return n, (t1 - t0) if t0 and t1 and t1 > t0 else None
if role == "srv":
    l = socket.socket(); l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    l.bind(("0.0.0.0", int(sys.argv[4]))); l.listen(1); l.settimeout(60)
    s, _ = l.accept()
    if mode == "up":
        n, dt = recv(s); print(json.dumps({"bytes": n, "s": dt, "mbit_s": n * 8 / dt / 1e6 if dt else None}))
    else:
        send(s)
else:
    for attempt in range(20):                  # the receiver may still be starting (ssh + python on a Pi)
        try:
            s = socket.create_connection((sys.argv[4], int(sys.argv[5])), timeout=30)
            break
        except OSError:
            time.sleep(0.5)
    else:
        raise SystemExit("could not connect")
    if mode == "down":
        n, dt = recv(s); print(json.dumps({"bytes": n, "s": dt, "mbit_s": n * 8 / dt / 1e6 if dt else None}))
    else:
        send(s)
PY
)
tp() {   # tp BOARD TARGET MODE -> json on stdout
  local b=$1 t=$2 mode=$3 res
  "${SSH[@]}" "$t" "timeout 90 python3 - srv $mode $SECONDS_TP $PORT" <<<"$TP" > "$out/throughput/srv.$$" &
  local sp=$!; sleep 2
  if [ "$mode" = down ]; then
    res=$("${SSH[@]}" "$b" "timeout 90 python3 - cli down $SECONDS_TP ${IP[$t]} $PORT" <<<"$TP")
    wait $sp || true
  else
    "${SSH[@]}" "$b" "timeout 90 python3 - cli up $SECONDS_TP ${IP[$t]} $PORT" <<<"$TP" || true
    wait $sp || true; res=$(cat "$out/throughput/srv.$$")
  fi
  rm -f "$out/throughput/srv.$$"; echo "$res"
}

say "wifi baseline -> $out (boards ${BOARDS[*]}; targets ${TARGETS[*]}; $COUNT pings every ${INTERVAL}s; ${SECONDS_TP}s TCP)"
for b in "${BOARDS[@]}"; do
  "${SSH[@]}" "$b" 'iw dev wlan0 link' > "$out/link/$b.txt" 2>&1 || true
  "${SSH[@]}" "$b" 'bash -s' < "$REPO/deploy/board-facts.sh" > "$out/facts/$b.json" 2>/dev/null || true
  for t in "${TARGETS[@]}"; do
    [ "$b" = "$t" ] && continue
    say "$b -> $t: ping"
    "${SSH[@]}" "$b" "ping -n -q -c $COUNT -i $INTERVAL -W 1 ${IP[$t]} >/dev/null; ping -n -c $COUNT -i $INTERVAL -W 1 ${IP[$t]}" \
      > "$out/ping/$b-to-$t.txt" 2>&1 || true
    say "$b -> $t: throughput up/down"
    tp "$b" "$t" up > "$out/throughput/$b-to-$t-up.json"
    tp "$b" "$t" down > "$out/throughput/$b-to-$t-down.json"
  done
done
git -C "$REPO" rev-parse HEAD > "$out/commit.txt"

python3 - "$out" <<'PY'
import glob, json, os, re, sys
out = sys.argv[1]
def pct(xs, p):
    xs = sorted(xs)
    if not xs: return None
    h = p * (len(xs) - 1); lo = int(h); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (h - lo)
summ = {}
for f in sorted(glob.glob(os.path.join(out, "ping", "*.txt"))):
    b, t = os.path.basename(f)[:-4].split("-to-")
    txt = open(f).read()
    rtt = [float(x) for x in re.findall(r"time=([\d.]+) ms", txt)]
    m = re.search(r"(\d+) packets transmitted, (\d+) received", txt)
    sent, got = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    e = summ.setdefault(b, {}).setdefault(t, {})
    e["rtt_ms"] = {"n": len(rtt), "p50": pct(rtt, .5), "p90": pct(rtt, .9), "p99": pct(rtt, .99),
                   "max": max(rtt) if rtt else None}
    e["loss_share"] = (sent - got) / sent if sent else None
    for mode in ("up", "down"):
        p = os.path.join(out, "throughput", f"{b}-to-{t}-{mode}.json")
        try:
            e[f"mbit_s_{mode}"] = json.loads(open(p).read().strip().splitlines()[-1])["mbit_s"]
        except Exception:
            e[f"mbit_s_{mode}"] = None
for f in sorted(glob.glob(os.path.join(out, "link", "*.txt"))):
    b = os.path.basename(f)[:-4]; txt = open(f).read()
    g = lambda k: (re.search(k + r"\s*([^\s]+)", txt) or [None, None])[1]
    summ.setdefault(b, {})["link"] = {"bssid": g("Connected to"), "freq_mhz": g("freq:"),
                                      "signal_dbm": g("signal:"), "rx_mbit": g("rx bitrate:"),
                                      "tx_mbit": g("tx bitrate:")}
json.dump(summ, open(os.path.join(out, "summary.json"), "w"), indent=1)
print(f"{'board':6} {'target':6} {'rtt p50':>8} {'p99':>8} {'max':>8} {'loss':>5} {'up Mb/s':>8} {'down':>8}  link")
for b, v in summ.items():
    for t, e in v.items():
        if t == "link": continue
        r = e["rtt_ms"]; f = lambda x: f"{x:8.1f}" if x is not None else "       -"
        print(f"{b:6} {t:6} {f(r['p50'])} {f(r['p99'])} {f(r['max'])} {e['loss_share'] or 0:5.3f} "
              f"{f(e['mbit_s_up'])} {f(e['mbit_s_down'])}  {v.get('link', {}).get('freq_mhz')} MHz "
              f"{v.get('link', {}).get('signal_dbm')} dBm")
PY
say "done: $out"
