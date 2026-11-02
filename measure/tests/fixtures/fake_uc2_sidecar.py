"""A stand-in for `perun-node -mode uc2-player` (perun/hub-demo/uc2_player.go):
the Task 8 protocol without go-perun, so measure/perun_uc2.py can be tested.

Requests are handled one at a time in order, one reply per request in request
order (the Go sidecar's pay FIFOs are per channel; the Python side does not
depend on that). Channels: "hub" and p<k> for every other player of six, each
with a stake (own balance); a successful pay lowers the own balance and raises
the version. A ledger `state` reports free funds; here the balances are the
channel's own [mine, peer].

Knobs: --timeout-id ID (that pay answers "unresolved: update timeout" and its
channel is broken from then on; later pays to it "channel broken: ..."),
--die-after N (exit 3 once N pays are answered), --fail-op OP (answer OP with
ok: false), --trace PATH (the requests received, one JSON line each).
Like Go: close refuses while a channel is broken; init echoes the name's index.
"""
import argparse
import json
import os
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--timeout-id", default="")
ap.add_argument("--die-after", type=int, default=0)
ap.add_argument("--fail-op", default="")
ap.add_argument("--trace", default="")
ap.add_argument("--stake", type=int, default=1000)
a = ap.parse_args()
trace = open(a.trace, "a", buffering=1) if a.trace else None

name, chans, answered, quiesced = "p0", {}, 0, False


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    op = req.get("op", "")
    if trace:
        trace.write(json.dumps(req) + "\n")
    if op == a.fail_op:
        out({"op": op, "ok": False, "error": "test failure"})
    elif op == "init":
        name = req["name"]
        out({"op": "init", "ok": True, "address": "0x" + "11" * 20, "index": int(name[1:])})
    elif op == "open":
        me = int(name[1:])
        chans = {t: {"version": 0, "mine": a.stake, "peer": a.stake, "broken": False}
                 for t in ["hub"] + [f"p{k}" for k in range(6) if k != me]}
        now = time.time()
        out({"op": "open", "ok": True, "t_start": now, "t_ledger_funded": now, "t_virtual_start": now,
             "t_virtual_open": now, "ledger_channel_id": "0x" + "22" * 32,
             "virtual": {t: "0x" + "33" * 32 for t in chans if t != "hub"}, "l1_gas_used": 45801,
             "l1_fee_wei": "0", "txs": 2})
    elif op == "pay":
        ch, amount = chans.get(req.get("to", "")), int(req["amount_wei"])
        rep = {"op": "pay", "id": req["id"], "ok": False, "t_submitted": 0, "t_confirmed": 0,
               "version": 0, "error": ""}
        if ch is None:
            rep["error"] = f"rejected: unknown target {req.get('to')!r}"
        elif quiesced:
            rep["error"] = "unresolved: after quiesce"
        elif ch["broken"]:
            rep["error"] = "channel broken: update timeout"
        elif req["id"] == a.timeout_id:
            ch["broken"] = True
            rep.update(t_submitted=time.time(), error="unresolved: update timeout")
        elif ch["mine"] < amount:
            rep["error"] = "channel empty"
        else:
            t = time.time()
            ch["version"] += 1
            ch["mine"] -= amount
            ch["peer"] += amount
            rep.update(ok=True, t_submitted=t, t_confirmed=time.time(), version=ch["version"])
        out(rep)
        answered += 1
        if a.die_after and answered >= a.die_after:
            os._exit(3)
    elif op == "state":
        out({"op": "state", "ok": True, "channels": {
            t: {"version": c["version"], "balances_wei": [str(c["mine"]), str(c["peer"])], "broken": c["broken"]}
            for t, c in chans.items()}})
    elif op == "quiesce":
        quiesced = True
        out({"op": "quiesce", "ok": True, "dropped": 0, "versions": {t: c["version"] for t, c in chans.items()}})
    elif op == "close":
        bad = [t for t, c in chans.items() if c["broken"]]
        if bad:
            out({"op": "close", "ok": False, "error": "channel broken: " + ",".join(bad)})
        else:
            now = time.time()
            out({"op": "close", "ok": True, "t_start": now, "t_virtual_settled": now, "t_withdrawn": now,
                 "virtual_settle_s": 0.0, "ledger_settle_s": 0.0, "final_ledger_version": 1,
                 "final_ledger_balances_wei": [str(chans["hub"]["mine"]), str(chans["hub"]["peer"])],
                 "virtual_final": [], "collisions": 0, "l1_gas_used": 213700, "l1_fee_wei": "0", "txs": 2,
                 "by_contract": {}})
    elif op == "exit":
        out({"op": "exit", "ok": True, "error": ""})
        break
    else:
        out({"op": op, "ok": False, "error": f"unknown op {op!r}"})
