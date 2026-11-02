"""Writes measure/tests/fixtures/uc2-run: a small synthetic UC2 run folder for
measure/tests/test_analyze_uc2.py, laid out as deploy/cluster's one_run_uc2
(Task 13) writes it. Rerun it after changing it; its output is committed.

    PYTHONPATH=. .venv/bin/python measure/tests/fixtures/make_uc2_run.py

The match: two players (p0 = host bath1, p1 = guest bath2), 20 Hz, 40 ticks,
Arbitrum. The trace is hand-made in the event-log format of tank-arena
(arena/events.py): p0 fires at ticks 2, 6, ..., 38, p1 at ticks 3, 7, ..., 39
(1 point each); p1 hits p0 at ticks 5, 13, 21 and p0 hits p1 at ticks 8, 16
(5 points each). The ledger rows follow arena/paybridge.payments_for. Every
payment succeeded except p1's fire at tick 23 (reverted, so the token never
moved); p1's fire at tick 19 confirmed after its next shot's tick. p1's board
stalled 0.5 s at tick 20. points-balances.json is what the chain holds after
those outcomes, so the reconciliation is exact; p1's trace is gzipped."""
import csv
import gzip
import hashlib
import json
import os
import shutil

from measure.ledger import FIELDS

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uc2-run")
RUN = "20261006-120000-arbitrum-uc2-test"
T0 = 1791300000.0          # match start (wall clock) on every board
DT = 0.05
TICKS = 40
START = 10000
FIRES = {0: list(range(2, 40, 4)), 1: list(range(3, 40, 4))}
HITS = [(5, 1, 0), (13, 1, 0), (21, 1, 0), (8, 0, 1), (16, 0, 1)]   # (tick, shooter, victim)
BOARDS = ("bath1", "bath2")
LAG = {0: 0.004, 1: 0.006}           # tick lag per board, s
CONFIRM = {0: 0.100, 1: 0.150}       # submitted -> confirmed, s
FIRE_GAS, HIT_GAS = 51000, 34000
GAS_PRICE = 10 ** 8


def trace_lines():
    out = []
    for tick in range(1, TICKS + 1):
        t_ms = round((tick - 1) * DT * 1000, 4)
        for p in (0, 1):
            out.append({"tick": tick, "t_ms": t_ms, "type": "move", "player": p,
                        "payload": {"x": 100.0 + tick, "y": 200.0 + p, "heading": 0.0, "turret_heading": 0.0,
                                    "speed": 1.0}})
        for p in (0, 1):
            if tick in FIRES[p]:
                out.append({"tick": tick, "t_ms": t_ms, "type": "fire", "player": p,
                            "payload": {"angle": 0.5, "ammo_left": 29, "from": p, "to": "arena", "amount": 1}})
        for (t, s, v) in HITS:
            if t == tick:
                out.append({"tick": tick, "t_ms": t_ms, "type": "hit", "player": s,
                            "payload": {"victim": v, "shooter": s, "damage": 20, "from": v, "to": s,
                                        "amount": 5, "killed": False}})
    return "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in out)


def rows_for(p):
    rows = []
    pays = [(t, "fire", "arena", 1) for t in FIRES[p]] + [(t, "hit", f"p{s}", 5) for (t, s, v) in HITS if v == p]
    for tick, kind, to, amount in sorted(pays):
        t_int = T0 + (tick - 1) * DT
        t_gen = t_int + LAG[p] - (0.005 if p == 0 else 0.0)   # p0: generated 1 ms before intended (clocks)
        t_q = t_gen + 0.002
        t_s = t_q + 0.010
        t_c = t_s + CONFIRM[p]
        r = dict({f: "" for f in FIELDS}, id=f"uc2/{RUN}/p{p}/t{tick}/{kind}0", to=to, amount_wei=amount,
                 outcome="succeeded", t_intended=f"{t_int:.6f}", t_generated=f"{t_gen:.6f}",
                 t_queued=f"{t_q:.6f}", t_submitted=f"{t_s:.6f}", t_confirmed=f"{t_c:.6f}",
                 tx_hash="0x" + hashlib.sha256(f"{p}{tick}{kind}".encode()).hexdigest(), block=tick,
                 gas_used=FIRE_GAS if kind == "fire" else HIT_GAS, effective_gas_price=GAS_PRICE,
                 attempts=1, resigned=0, error="")
        if p == 1 and kind == "fire" and tick == 19:
            r["t_confirmed"] = f"{t_s + 0.3:.6f}"                  # after the next shot (tick 23)
        if p == 1 and kind == "fire" and tick == 23:
            r.update(outcome="failed", error="reverted")           # mined, reverted: the token never moved
        rows.append(r)
    return rows


def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    trace = trace_lines()
    sha = hashlib.sha256(trace.encode()).hexdigest()
    os.makedirs(os.path.join(OUT, "recorder"))
    env = (f"USE_CASE=uc2\nSTACK=arbitrum\nPLAYERS=(bath1 bath2)\nN_PLAYERS=2\nTICKRATE=20\nGAME_TICKS={TICKS}\n"
           f"GAME_SEED=42\nSTART_POINTS={START}\nMAX_QUEUE=10000\nDRAIN_S=60\n")
    open(os.path.join(OUT, "experiment.env"), "w").write(env)
    json.dump({"git_commit": "fixture", "git_dirty": False, "code_hash": "h1",
               "experiment": dict(l.split("=", 1) for l in env.splitlines())},
              open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
    res = os.path.join(OUT, "results")
    os.makedirs(res)
    open(os.path.join(res, "reference.sha256"), "w").write(f"{sha}  uc2-seed42.jsonl\n")
    ok = {p: [r for r in rows_for(p) if r["outcome"] == "succeeded"] for p in (0, 1)}
    bal = {f"p{p}": START - sum(r["amount_wei"] for r in ok[p])
           + sum(r["amount_wei"] for q in (0, 1) for r in ok[q] if r["to"] == f"p{p}") for p in (0, 1)}
    arena = sum(r["amount_wei"] for q in (0, 1) for r in ok[q] if r["to"] == "arena")
    json.dump({"players": bal, "arena": arena}, open(os.path.join(res, "points-balances.json"), "w"), indent=1)
    json.dump({"address": "0x" + "11" * 20}, open(os.path.join(res, "token.json"), "w"), indent=1)
    for p, board in enumerate(BOARDS):
        pc, game = os.path.join(res, f"p{p}", "pc"), os.path.join(res, f"p{p}", "game")
        os.makedirs(pc)
        os.makedirs(game)
        rows = rows_for(p)
        with open(os.path.join(pc, "ledger.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        life = {"system": "arbitrum", "asset": "points", "complete": True, "account": 111 + p,
                "open": {"t_start": T0 - 30.0, "t_l1_confirmed": T0 - 28.0, "t_l2_credited": T0 - 20.0 - p,
                         "l1_gas_used": 91067 + p, "l1_fee_wei": (91067 + p) * 10 ** 9},
                "ready_at": T0 - 10.0, "ended_at": T0 + 2.2, "fee_cap_wei": 10 ** 9,
                "start_nonce": 0, "submitted_nonce": len(rows), "close_nonce": len(rows),
                "executed_onchain": len(rows), "executed_after_close": 0, "points_before_close": bal[f"p{p}"],
                "close": {"t_start": T0 + 3.0, "t_confirmed": T0 + 3.5 + p, "status": 1,
                          "l2_gas_used": 67571 + p, "l2_fee_wei": (67571 + p) * 10 ** 8}}
        json.dump(life, open(os.path.join(pc, "lifecycle.json"), "w"), indent=1)
        if p == 1:   # gzipped as measure/compress.py leaves it; mtime 0 so the fixture is byte-stable
            with open(os.path.join(game, "trace.jsonl.gz"), "wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as g:
                    g.write(trace.encode())
        else:
            open(os.path.join(game, "trace.jsonl"), "w").write(trace)
        with open(os.path.join(game, "ticks.csv"), "w") as f:
            f.write("tick,t_intended,t_actual\n")
            for k in range(1, TICKS + 1):
                ti = T0 + (k - 1) * DT
                lag = 0.5 if (p == 1 and k == 20) else LAG[p]
                f.write(f"{k},{ti:.6f},{ti + lag:.6f}\n")
        counts = {"move": 2 * TICKS, "fire": len(FIRES[0]) + len(FIRES[1]), "hit": len(HITS), "break": 0}
        json.dump({"complete": True, "aborted": None, "ticks": TICKS, "t_start": T0,
                   "t_end": T0 + TICKS * DT + 0.05, "emitted": len(rows), "counts": counts,
                   "trace_sha256": sha}, open(os.path.join(game, "node-result.json"), "w"), indent=1)
        with open(os.path.join(OUT, "recorder", f"{board}.csv"), "w") as f:
            f.write("t,temp_c,throttled,arm_mhz,bssid,cpu_busy_pct\n")
            for i in range(5):     # one sample before the match (outside the window), four in it
                t = T0 - 5.0 + i * 1.5 if i == 0 else T0 + (i - 1) * 0.6
                thr = "0x4" if (p == 1 and i == 2) else "0x0"
                f.write(f"{t:.3f},{50.0 + i + p},{thr},1500,aa:bb:cc:dd:ee:0{p},{20.0 + 10 * p + i}\n")
    print(OUT)


if __name__ == "__main__":
    main()
