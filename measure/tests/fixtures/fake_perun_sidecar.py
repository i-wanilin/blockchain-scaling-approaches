"""A stand-in for `perun-node -mode uc1-customer`: speaks the sidecar's JSON-line
protocol (perun/hub-demo/uc1_customer.go) without go-perun, so the Python
adapter can be tested for ordering, drain, quiesce and death without a chain.

Like the Go sidecar: replies leave in request order (an ordered slot writer),
pays run one at a time through a FIFO, quiesce drops the queued pays and waits
for the one in flight, an update timeout breaks the channel, and the queued
pays still run when stdin hits EOF.

Knobs (argv): --pay-delay S, --reject ID,ID (rejected: test), --timeout-id ID
(unresolved: update timeout, then channel broken; with --timeout-signed the
sensor did sign that update, so the channel state includes it), --die-after N (exit 3 once
N pays are done), --noise-after N (a non-JSON line on stdout after the N-th
pay reply), --hang-op OP (never answer OP), --fail-op OP (answer OP with
ok: false, "test failure"), --close-broken (close a broken channel anyway), --trace PATH (one JSON line per
pay start and per reply, with the time).
"""
import argparse
import collections
import json
import os
import sys
import threading
import time

ap = argparse.ArgumentParser()
ap.add_argument("--pay-delay", type=float, default=0.005)
ap.add_argument("--reject", default="")
ap.add_argument("--timeout-id", default="")
ap.add_argument("--timeout-signed", action="store_true")
ap.add_argument("--die-after", type=int, default=0)
ap.add_argument("--noise-after", type=int, default=0)
ap.add_argument("--hang-op", default="")
ap.add_argument("--fail-op", default="")
ap.add_argument("--close-broken", action="store_true")
ap.add_argument("--trace", default="")
a = ap.parse_args()
reject = set(filter(None, a.reject.split(",")))

trace_lock = threading.Lock()
trace = open(a.trace, "a", buffering=1) if a.trace else None


def note(**kw):
    if trace:
        with trace_lock:
            trace.write(json.dumps(dict(kw, t=time.time())) + "\n")


class Slot:
    def __init__(self):
        self.ev, self.val = threading.Event(), None

    def set(self, v):
        self.val = v
        self.ev.set()


slots = collections.deque()
slots_cv = threading.Condition()
replies_out = 0


def writer():
    global replies_out
    while True:
        with slots_cv:
            while not slots:
                slots_cv.wait()
            s = slots.popleft()
        if s is None:
            return
        s.ev.wait()
        line = json.dumps(s.val) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()
        note(reply=s.val)
        if s.val.get("op") == "pay":
            replies_out += 1
            if a.noise_after and replies_out == a.noise_after:
                sys.stdout.write("pkg/test: using rootSeed 42\n")
                sys.stdout.flush()


def reserve():
    s = Slot()
    with slots_cv:
        slots.append(s)
        slots_cv.notify()
    return s


fifo = collections.deque()          # (slot, id, amount) or (slot, fn)
fifo_cv = threading.Condition()
state = {"version": 0, "balance": 10**18, "paid": 0, "broken": "", "quiesced": False,
         "in_flight": False, "done": 0}


def worker():
    while True:
        with fifo_cv:
            while not fifo:
                fifo_cv.wait()
            job = fifo.popleft()
            if job is None:
                return
            state["in_flight"] = len(job) == 3
        if len(job) == 2:
            job[0].set(job[1]())
            continue
        slot, id_, amount = job
        if a.die_after and state["done"] >= a.die_after:
            time.sleep(0.05)                        # let the replies already made get out
            os._exit(3)
        note(start=id_)
        rep = {"op": "pay", "id": id_, "ok": False, "t_submitted": 0, "t_confirmed": 0,
               "version": 0, "error": ""}
        if state["broken"]:
            rep["error"] = "channel broken: " + state["broken"]
        else:
            t_sub = time.time()
            time.sleep(a.pay_delay)
            if id_ in reject:
                rep.update(t_submitted=t_sub, error="rejected: test")
            elif id_ == a.timeout_id:
                state["broken"] = "update timeout"
                if a.timeout_signed:
                    state["version"] += 1
                    state["balance"] -= amount
                    state["paid"] += amount
                rep.update(t_submitted=t_sub, error="unresolved: update timeout")
            else:
                state["version"] += 1
                state["balance"] -= amount
                state["paid"] += amount
                rep.update(ok=True, t_submitted=t_sub, t_confirmed=time.time(), version=state["version"])
        with fifo_cv:
            state["in_flight"] = False
            state["done"] += 1
        slot.set(rep)


def bal():
    return [str(state["balance"]), str(state["paid"])]


threading.Thread(target=writer, daemon=True).start()
wt = threading.Thread(target=worker, daemon=True)
wt.start()

exit_ = False
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    slot = reserve()
    req = json.loads(line)
    op = req.get("op", "")
    if op == a.hang_op:
        continue                                    # never filled: the writer stalls here, as a hung Go op would
    if op == a.fail_op:
        slot.set({"op": op, "ok": False, "error": "test failure"})
        continue
    if op == "pay":
        with fifo_cv:
            if state["quiesced"]:
                slot.set({"op": "pay", "id": req["id"], "ok": False, "t_submitted": 0, "t_confirmed": 0,
                          "version": 0, "error": "unresolved: after quiesce"})
            else:
                fifo.append((slot, req["id"], int(req["amount_wei"])))
                fifo_cv.notify()
    elif op == "init":
        slot.set({"op": "init", "ok": True, "address": "0x" + "11" * 20})
    elif op == "open":
        slot.set({"op": "open", "ok": True, "t_start": time.time(), "t_funded": time.time(),
                  "channel_id": "0x" + "22" * 32, "l1_gas_used": 45801, "l1_fee_wei": "0", "txs": 1})
    elif op == "state":
        slot.set({"op": "state", "ok": True, "version": state["version"], "balances_wei": bal(),
                  "broken": bool(state["broken"])})
    elif op == "quiesce":
        with fifo_cv:
            state["quiesced"] = True
            drop = [j for j in fifo if j is not None and len(j) == 3]
            keep = [j for j in fifo if j is None or len(j) != 3]
            fifo.clear()
            fifo.extend(keep)
        for s, id_, _ in drop:
            s.set({"op": "pay", "id": id_, "ok": False, "t_submitted": 0, "t_confirmed": 0,
                   "version": 0, "error": "unresolved: dropped at quiesce"})
        while state["in_flight"]:
            time.sleep(0.001)
        slot.set({"op": "quiesce", "ok": True, "dropped": len(drop), "version": state["version"]})
    elif op == "close":
        def close():
            if state["broken"] and not a.close_broken:
                return {"op": "close", "ok": False, "error": "channel broken: " + state["broken"]}
            return {"op": "close", "ok": True, "t_start": time.time(), "t_finalized": time.time(),
                    "t_withdrawn": time.time(), "final_version": state["version"] + 1,
                    "final_balances_wei": bal(), "l1_gas_used": 213700, "l1_fee_wei": "0", "txs": 2,
                    "by_contract": {}}
        with fifo_cv:
            fifo.append((slot, close))
            fifo_cv.notify()
    elif op == "exit":
        slot.set({"op": "exit", "ok": True, "error": ""})
        exit_ = True
        break
    else:
        slot.set({"op": op, "ok": False, "error": f"unknown op {op!r}"})

# Like the Go sidecar: every reserved reply is written before leaving, so the
# queued pays still run after EOF on stdin.
with fifo_cv:
    fifo.append(None)
    fifo_cv.notify()
wt.join()
with slots_cv:
    slots.append(None)
    slots_cv.notify()
while True:
    with slots_cv:
        if not slots:
            break
    time.sleep(0.005)
sys.stdout.flush()
