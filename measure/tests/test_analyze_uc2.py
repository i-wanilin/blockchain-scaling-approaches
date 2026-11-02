"""The UC2 analysis turns one match's run folder into the thesis numbers
(spec §7.3 "Reported per system"), and flags a run whose boards did not
play the same game."""
import contextlib
import csv
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

from measure import aggregate, analyze_uc2
from measure.ledger import FIELDS

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "uc2-run")
TANK_ARENA = os.path.join(os.path.dirname(__file__), "..", "..", "tank-arena")
T0 = 1791300000.0


def jload(path):
    with open(path) as f:
        return json.load(f)


def jdump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f)


def rewrite(path, text):
    with open(path, "w") as f:
        f.write(text)


def copy_fixture():
    d = os.path.join(tempfile.mkdtemp(), "run")
    shutil.copytree(FIXTURE, d)
    return d


def read_rows(run, p):
    with open(os.path.join(run, "results", p, "pc", "ledger.csv"), newline="") as f:
        return list(csv.DictReader(f))


def write_rows(run, p, rows):
    with open(os.path.join(run, "results", p, "pc", "ledger.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def unresolve(run, p, suffix):
    """Mark one ledger row as given up at the drain deadline after it was sent (no receipt)."""
    rows = read_rows(run, p)
    for r in rows:
        if r["id"].endswith(suffix):
            r.update(outcome="unresolved", t_confirmed="", gas_used="", effective_gas_price="",
                     error="drain deadline")
    write_rows(run, p, rows)


def row(tick, outcome="succeeded", t_confirmed=None, kind="fire"):
    t = T0 + (tick - 1) * 0.05
    return {"id": f"uc2/r/p1/t{tick}/{kind}0", "to": "arena", "amount_wei": "1", "outcome": outcome,
            "t_intended": str(t), "t_generated": str(t), "t_submitted": str(t + 0.01),
            "t_confirmed": "" if t_confirmed is None else str(t_confirmed)}


class AnalyzeUC2Test(unittest.TestCase):
    def test_summary_fields(self):
        s = analyze_uc2.analyze(FIXTURE)
        for k in ("complete", "hash_ok", "hash_mismatch", "match", "offered", "outcomes", "latency_ms", "latency_lower_bound_ms",
                  "goodput_per_s", "next_shot_miss_share", "reconciliation", "open", "close", "gas", "cpu",
                  "throttle", "analysis_commit"):
            self.assertIn(k, s)
        self.assertIs(s["complete"], True, s["complete_checks"])
        self.assertIs(s["hash_ok"], True)
        self.assertEqual(s["hash_mismatch"], [])
        self.assertEqual(s["use_case"], "uc2")
        self.assertEqual(s["players"], {"p0": "bath1", "p1": "bath2"})
        self.assertEqual({k: s["offered"][k] for k in ("payments", "fire", "hit")},
                         {"payments": 25, "fire": 20, "hit": 5})
        self.assertAlmostEqual(s["offered"]["per_s"], 25 / s["match"]["duration_s"])
        o = s["outcomes"]
        self.assertEqual((o["succeeded"], o["failed"], o["unresolved"], o["late"]), (24, 1, 0, 0))
        self.assertAlmostEqual(o["shares"]["succeeded"], 24 / 25)
        self.assertAlmostEqual(o["shares"]["late"], 0.0)
        self.assertIsInstance(s["goodput_per_s"], float)
        self.assertEqual(s["gas"], {"per_fire": 51000, "per_hit": 34000, "open_median": 91067.5,
                                    "close_median": 67571.5})
        self.assertAlmostEqual(s["open"]["p1"]["s"], 9.0)
        self.assertEqual(s["open"]["p0"]["gas"], 91067)
        self.assertAlmostEqual(s["close"]["p1"]["s"], 1.5)
        self.assertEqual(s["close"]["p1"]["gas"], 67572)
        self.assertAlmostEqual(s["cpu"]["bath2"]["mean_cpu_busy_pct"], 32.5)   # match window only
        self.assertAlmostEqual(s["throttle"]["bath2"]["throttled_share"], 0.25)
        self.assertAlmostEqual(s["throttle"]["bath1"]["throttled_share"], 0.0)
        r = s["reconciliation"]
        self.assertIs(r["ok"], True)
        self.assertEqual(r["players"]["p0"]["game"], 9985)
        self.assertEqual(r["players"]["p1"]["game"], 9995)   # the reverted shot moved points only in the game
        self.assertEqual(r["players"]["p1"]["ledger"], 9996)
        self.assertEqual(r["players"]["p1"]["chain"], 9996)
        self.assertEqual(r["players"]["p1"]["diff"], 0)
        self.assertEqual(r["arena"], {"game": 20, "ledger": 19, "chain": 19, "diff": 0, "explained": 0,
                                      "ok": True, "ok_explained": True, "late_in": 0, "ok_with_late": True})
        self.assertEqual(s["late_executed"], {"p0": 0, "p1": 0})   # every nonce has its row (the revert too)
        self.assertIs(r["ok_with_late"], True)
        json.dumps(s)                                          # summary.json must serialise

    def test_main_writes_summary(self):
        run = copy_fixture()
        sys_argv = sys.argv
        try:
            sys.argv = ["analyze_uc2", run]
            with contextlib.redirect_stdout(io.StringIO()):
                analyze_uc2.main()
        finally:
            sys.argv = sys_argv
        self.assertTrue(jload(os.path.join(run, "summary.json"))["hash_ok"])

    def test_next_shot_miss(self):
        rows = [row(2, t_confirmed=T0 + 0.1),           # before the next shot's tick (tick 6, T0+0.25): ok
                row(6, t_confirmed=T0 + 0.5),           # after tick 10's T0+0.45: miss
                row(10, outcome="failed"),              # not succeeded: miss
                row(14, outcome="unresolved"),          # miss
                row(18, t_confirmed=T0 + 99)]           # the last shot has no next shot: left out
        self.assertEqual(analyze_uc2.next_shot_misses(rows), (3, 4))
        hits = [row(3, kind="hit", outcome="failed")]   # hits are not shots
        self.assertEqual(analyze_uc2.next_shot_misses(rows + hits), (3, 4))
        self.assertEqual(analyze_uc2.next_shot_misses(list(reversed(rows))), (3, 4))   # tick order, not file order
        s = analyze_uc2.analyze(FIXTURE)
        self.assertEqual(s["next_shot_miss_share"]["guests"], {"misses": 2, "shots": 9, "share": 2 / 9})
        self.assertEqual(s["next_shot_miss_share"]["host"], {"misses": 0, "shots": 9, "share": 0.0})

    def test_next_shot_deadline_is_when_the_next_shot_was_fired(self):
        # tick 6 is due at T0+0.25 but the game ran it 60 ms late (T0+0.31); a payment confirmed at T0+0.28 is
        # in time for the shot as fired (cluster spec §7.3), although after its schedule
        first, nxt = row(2, t_confirmed=T0 + 0.28), row(6, t_confirmed=T0 + 9)
        nxt["t_generated"] = str(T0 + 0.31)
        self.assertEqual(analyze_uc2.next_shot_misses([first, nxt]), (0, 1))
        nxt["t_generated"] = ""                       # no actual time recorded: the schedule is the deadline
        self.assertEqual(analyze_uc2.next_shot_misses([first, nxt]), (1, 1))

    def test_guests_and_host_reported_apart(self):
        s = analyze_uc2.analyze(FIXTURE)
        lat = s["latency_ms"]
        self.assertAlmostEqual(lat["host"]["hit"]["submitted_to_confirmed"]["p50"], 100.0, places=3)
        self.assertAlmostEqual(lat["guests"]["hit"]["submitted_to_confirmed"]["p50"], 150.0, places=3)
        self.assertAlmostEqual(lat["host"]["fire"]["generated_to_confirmed"]["p50"], 112.0, places=3)
        self.assertAlmostEqual(lat["guests"]["fire"]["generated_to_confirmed"]["p50"], 162.0, places=3)
        self.assertEqual(lat["guests"]["all"]["generated_to_confirmed"]["n"], 11)   # 12 rows, one reverted
        self.assertEqual(lat["host"]["all"]["generated_to_confirmed"]["n"], 13)
        self.assertEqual(lat["guests"]["hit"]["generated_to_confirmed"]["n"], 2)
        self.assertAlmostEqual(lat["guests"]["fire"]["submitted_to_confirmed"]["p99"], 150 + 0.92 * 150, places=3)

    def test_tick_lag_and_stretch(self):
        s = analyze_uc2.analyze(FIXTURE)
        m = s["match"]
        self.assertAlmostEqual(m["tick_lag_ms"]["bath2"]["max"], 500.0, places=3)   # the 0.5 s stall at tick 20
        self.assertAlmostEqual(m["tick_lag_ms"]["bath2"]["p50"], 6.0, places=3)
        self.assertAlmostEqual(m["tick_lag_ms"]["bath1"]["max"], 4.0, places=3)
        self.assertAlmostEqual(m["tick_lag_ms"]["pooled"]["max"], 500.0, places=3)
        self.assertAlmostEqual(m["tick_lag_ms"]["pooled"]["p50"], 5.0, places=3)
        self.assertAlmostEqual(m["duration_s"], 39 * 0.05 + 0.006 + 0.05, places=6)
        self.assertEqual(m["nominal_s"], 2.0)                  # GAME_TICKS / TICKRATE (60 s in the suite)
        self.assertAlmostEqual(m["stretch"], m["duration_s"] / 2.0)
        self.assertEqual(m["ticks"], {"bath1": 40, "bath2": 40})

    def test_reconciliation_explains_late_rollup_payments(self):
        run = copy_fixture()
        rows = read_rows(run, "p0")
        for r in rows:                                         # p0 -> p1 at tick 13: on chain, but the ledger
            if r["id"].endswith("/t13/hit0"):                   # gave up at the drain deadline
                r.update(outcome="unresolved", t_confirmed="", gas_used="", effective_gas_price="",
                         error="drain deadline")
        write_rows(run, "p0", rows)
        s = analyze_uc2.analyze(run)
        p0, p1 = s["reconciliation"]["players"]["p0"], s["reconciliation"]["players"]["p1"]
        self.assertEqual(p1["diff"], 5)
        self.assertEqual(p1["diff"], p1["explained"])
        self.assertIs(p1["ok"], False)
        self.assertIs(p1["ok_explained"], True)
        self.assertEqual((p0["diff"], p0["explained"]), (-5, 5))   # the payer's chain is below its ledger
        self.assertIs(p0["ok_explained"], True)
        self.assertIs(s["reconciliation"]["ok"], False)
        self.assertIs(s["reconciliation"]["ok_explained"], True)
        rows[[r["id"].endswith("/t13/hit0") for r in rows].index(True)]["t_submitted"] = ""   # never sent:
        write_rows(run, "p0", rows)                                                           # not explained
        s = analyze_uc2.analyze(run)
        self.assertIs(s["reconciliation"]["players"]["p1"]["ok_explained"], False)

    def test_rollup_late_executed_from_nonces(self):
        run = copy_fixture()
        unresolve(run, "p0", "/t13/hit0")       # in flight at the drain deadline; its nonce executed (13 nonces)
        s = analyze_uc2.analyze(run)
        self.assertEqual(s["late_executed"], {"p0": 1, "p1": 0})
        self.assertEqual((s["late_executed_total"], s["late_executed_points"]), (1, 5))
        self.assertEqual(s["outcomes"]["late"], 1)
        self.assertAlmostEqual(s["outcomes"]["shares"]["late"], 1 / 25)
        self.assertEqual(s["outcomes"]["by_group"]["host"]["late"], 1)
        r = s["reconciliation"]
        self.assertIs(r["ok"], False)
        self.assertIs(r["ok_with_late"], True)                   # an equality, proven by the nonces
        self.assertEqual((r["players"]["p0"]["late_out"], r["players"]["p1"]["late_in"]), (5, 5))
        bal = os.path.join(run, "results", "points-balances.json")
        b = jload(bal)
        b["arena"] += 1                                            # a point the nonces cannot account for
        jdump(b, bal)
        self.assertIs(analyze_uc2.analyze(run)["reconciliation"]["ok_with_late"], False)
        life_p = os.path.join(run, "results", "p0", "pc", "lifecycle.json")
        life = jload(life_p)
        life["executed_onchain"] = 15                              # 3 late, but only 1 row was in flight
        jdump(life, life_p)
        s = analyze_uc2.analyze(run)
        self.assertEqual(s["late_executed"]["p0"], 3)
        self.assertIsNone(s["late_executed_points"])
        self.assertIsNone(s["reconciliation"]["ok_with_late"])
        self.assertIn("p0", s["late_executed_reason"])

    def test_rollup_late_executed_null_without_nonce_facts(self):
        run = copy_fixture()
        life_p = os.path.join(run, "results", "p1", "pc", "lifecycle.json")
        life = jload(life_p)
        life["executed_after_close"] = None                        # recorded as null (UC1: late is null)
        jdump(life, life_p)
        s = analyze_uc2.analyze(run)
        self.assertEqual(s["late_executed"], {"p0": 0, "p1": None})
        self.assertIsNone(s["late_executed_total"])
        self.assertIsNone(s["late_executed_points"])
        self.assertIsNone(s["outcomes"]["late"])
        self.assertIsNone(s["outcomes"]["shares"]["late"])
        self.assertIsNone(s["reconciliation"]["ok_with_late"])
        self.assertIs(s["reconciliation"]["ok"], True)             # the other checks are unaffected
        self.assertIs(s["complete"], False)                        # unknown late is a missing fact, not complete
        self.assertIs(s["complete_checks"]["late_executed_unknown"], True)
        self.assertIn("nonce", s["late_executed_reason"]["p1"])

    def test_hash_mismatch_flags_run(self):
        run = copy_fixture()
        tr = os.path.join(run, "results", "p1", "game", "trace.jsonl.gz")
        with gzip.open(tr, "rt") as f:
            body = f.read() + '{"tick":41}\n'      # one board played on (node-result unchanged)
        with gzip.open(tr, "wt") as f:
            f.write(body)
        s = analyze_uc2.analyze(run)
        self.assertIs(s["hash_ok"], False)
        self.assertEqual(s["hash_mismatch"], ["bath2"])
        self.assertIs(s["complete"], False)
        jdump(s, os.path.join(run, "summary.json"))
        good = copy_fixture()
        jdump(analyze_uc2.analyze(good), os.path.join(good, "summary.json"))
        with self.assertRaisesRegex(ValueError, "hash"):
            aggregate.aggregate([good, run])
        agg = aggregate.aggregate([good, run], include_incomplete=True)
        self.assertEqual(agg["_hash_mismatch_runs"], [run])
        nr = os.path.join(good, "results", "p0", "game", "node-result.json")   # the node's own hash differs
        res = jload(nr)
        res["trace_sha256"] = "0" * 64
        jdump(res, nr)
        self.assertEqual(analyze_uc2.analyze(good)["hash_mismatch"], ["bath1"])
        os.unlink(os.path.join(good, "results", "reference.sha256"))
        s = analyze_uc2.analyze(good)
        self.assertIs(s["hash_ok"], False)
        self.assertIn("results/reference.sha256", s["complete_checks"]["missing"])

    def test_game_score_from_trace(self):
        sys.path.insert(0, os.path.abspath(TANK_ARENA))
        try:
            from arena.bots import make_bot
            from arena.world import Config, World
        finally:
            sys.path.remove(os.path.abspath(TANK_ARENA))
        w = World(n_players=2, tickrate=20, seed=42, config=Config())
        bots = [make_bot(i, 42, 20) for i in range(2)]
        path = os.path.join(tempfile.mkdtemp(), "trace.jsonl")
        with open(path, "w") as f:
            for _ in range(400):
                for e in w.step({i: b.decide(w) for i, b in enumerate(bots)}):
                    f.write(json.dumps(e, separators=(",", ":")) + "\n")
        score = analyze_uc2.game_score(path, ["p0", "p1"], Config().start_tokens)
        self.assertEqual(score, {"p0": w.balances[0], "p1": w.balances[1], "arena": w.balances["arena"]})
        self.assertNotEqual(score["p0"], Config().start_tokens)     # the match moved points
        s = analyze_uc2.analyze(FIXTURE)
        self.assertEqual(s["reconciliation"]["game_trace"], "results/p0/game/trace.jsonl")

    @staticmethod
    def perun_run():
        """The fixture as a Perun run: channels after quiesce as the succeeded rows leave them (stake 1 680,
        deposit 10 000; the ledger channel shows free funds: deposit - shots - stakes)."""
        run = copy_fixture()
        env = os.path.join(run, "experiment.env")
        with open(env) as f:
            text = f.read()
        rewrite(env, text.replace("STACK=arbitrum", "STACK=perun"))
        chans = {"p0": {"hub": {"version": 30, "balances_wei": ["8310", "8400"], "broken": False},
                        "p1": {"version": 7, "balances_wei": ["1675", "1685"], "broken": False}},
                 "p1": {"hub": {"version": 31, "balances_wei": ["8311", "8400"], "broken": False},
                        "p0": {"version": 7, "balances_wei": ["1685", "1675"], "broken": False}}}
        for p in ("p0", "p1"):
            rows = read_rows(run, p)
            for r in rows:
                r.update(gas_used="0", effective_gas_price="0", tx_hash="")
            write_rows(run, p, rows)
            life = {"system": "perun", "use_case": "uc2", "player": p, "complete": True,
                    "open": {"t_start": T0 - 30, "t_ledger_funded": T0 - 25, "t_virtual_start": T0 - 24,
                             "t_virtual_open": T0 - 22, "l1_gas_used": 200000, "l1_fee_wei": "2000"},
                    "quiesce_sent_at": T0 + 2.2, "ended_at": T0 + 2.5,
                    "channels_before_close": chans[p], "broken_channels": [],
                    "close": {"t_start": T0 + 3, "t_withdrawn": T0 + 5, "l1_gas_used": 150000, "l1_fee_wei": "1"}}
            jdump(life, os.path.join(run, "results", p, "pc", "lifecycle.json"))
        return run, chans

    def test_perun_run(self):
        run, chans = self.perun_run()
        s = analyze_uc2.analyze(run)
        self.assertEqual(s["stack"], "perun")
        self.assertEqual((s["gas"]["per_fire"], s["gas"]["per_hit"]), (0, 0))
        self.assertEqual(s["open"]["p0"], {"s": 8, "ledger_s": 5, "virtual_s": 2, "gas": 200000, "fee_wei": 2000})
        self.assertEqual(s["close"]["p1"]["s"], 2)
        self.assertEqual(s["late_executed"], {"p0": 0, "p1": 0})
        self.assertIs(s["reconciliation"]["ok_with_late"], True)
        ch = s["perun_channels"]["p0-p1"]
        self.assertIs(ch["mirror"], True)
        self.assertEqual((ch["channel_net_wei"], ch["ledger_net_wei"]), (-10, -10))
        self.assertIs(ch["ok"], True)
        chans["p1"]["p0"]["balances_wei"] = ["1690", "1670"]   # the ends disagree
        life_p = os.path.join(run, "results", "p1", "pc", "lifecycle.json")
        life = jload(life_p)
        life["channels_before_close"] = chans["p1"]
        jdump(life, life_p)
        self.assertIs(analyze_uc2.analyze(run)["perun_channels"]["p0-p1"]["ok"], False)

    def test_perun_late_executed_from_channels(self):
        run, _ = self.perun_run()
        unresolve(run, "p0", "/t13/hit0")   # both in flight at the deadline, and the channels show them done:
        unresolve(run, "p0", "/t2/fire0")   # the virtual channel to p1 and the ledger channel to the hub
        s = analyze_uc2.analyze(run)
        self.assertEqual(s["late_executed"], {"p0": 2, "p1": 0})
        self.assertEqual((s["late_executed_total"], s["late_executed_points"]), (2, 6))
        r = s["reconciliation"]
        self.assertEqual((r["players"]["p1"]["late_in"], r["arena"]["late_in"]), (5, 1))
        self.assertIs(r["ok"], False)
        self.assertIs(r["ok_with_late"], True)
        unresolve(run, "p1", "/t8/hit0")    # now both ends of p0-p1 were in flight: only the net is known
        s = analyze_uc2.analyze(run)
        self.assertIsNone(s["late_executed"]["p0"])
        self.assertIn("both ends", s["late_executed_reason"]["p0"])
        self.assertIsNone(s["late_executed_total"])
        self.assertIsNone(s["reconciliation"]["ok_with_late"])

    def test_aggregate_uc2(self):
        runs = []
        for _ in range(2):
            run = copy_fixture()
            jdump(analyze_uc2.analyze(run), os.path.join(run, "summary.json"))
            runs.append(run)
        agg = aggregate.aggregate(runs)
        self.assertEqual(agg["_hash_mismatch_runs"], [])
        self.assertEqual(agg["guests_generated_to_confirmed_p50_ms"]["n"], 2)
        self.assertAlmostEqual(agg["guests_generated_to_confirmed_p50_ms"]["mean"], 162.0, places=3)
        self.assertAlmostEqual(agg["next_shot_miss_share_guests"]["mean"], 2 / 9)
        self.assertAlmostEqual(agg["tick_lag_max_ms"]["mean"], 500.0, places=3)
        self.assertEqual(agg["gas_per_fire"]["mean"], 51000)
        self.assertNotIn("payment_latency_p50_s", agg)          # UC1 metrics are not UC2's
        self.assertEqual(agg["_warnings"], [])


if __name__ == "__main__":
    unittest.main()
