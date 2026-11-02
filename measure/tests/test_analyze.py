"""The analysis turns a run folder into the thesis numbers, and says so when
something does not add up."""
import csv
import json
import os
import shutil
import tempfile
import unittest

from measure import aggregate, analyze_uc1, manifest
from measure.ledger import FIELDS

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "uc1_smoke_c1")


def make_run(latencies, unresolved=0, sensor_delta=None, commit="abc", complete=True, dirty=False, run_failed=False,
             code_hash="h1", env="WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\n", extra_rows=(), sensor_log=True):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "results", "c1"))
    if sensor_log:                                   # a readable sensor log that dropped nobody
        os.makedirs(os.path.join(d, "logs"))
        open(os.path.join(d, "logs", "bath1-sensor.log"), "w").write("{'rows': 9, 'dropped': []}\n")
    open(os.path.join(d, "experiment.env"), "w").write(env)
    rows, total = [], 0
    for k, lat in enumerate(latencies):
        t = 1000.0 + k
        rows.append(dict({f: "" for f in FIELDS}, id=f"uc1/r/c1/w{k}", amount_wei=10, outcome="succeeded",
                         t_intended=t, t_generated=t, t_queued=t, t_submitted=t + lat / 4, t_confirmed=t + lat,
                         gas_used=21000, effective_gas_price=10**8))
        total += 10
    for k in range(unresolved):
        rows.append(dict({f: "" for f in FIELDS}, id=f"uc1/r/c1/u{k}", amount_wei=10, outcome="unresolved",
                         t_intended=2000.0, t_generated=2000.0))
    rows += [dict({f: "" for f in FIELDS}, **r) for r in extra_rows]
    with open(os.path.join(d, "results", "c1", "ledger.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    life = {"complete": complete, "resyncs": 2, "open": {"t_start": 900.0, "t_l2_credited": 902.5, "l1_gas_used": 91067,
                                                        "l1_fee_wei": 91067 * 10**9},
            "close": {"t_start": 3000.0, "t_confirmed": 3000.03, "l2_gas_used": 67571, "l2_fee_wei": 67571 * 10**8}}
    json.dump(life, open(os.path.join(d, "results", "c1", "lifecycle.json"), "w"))
    json.dump({"l2_balance_before_wei": 0, "l2_balance_after_wei": total if sensor_delta is None else sensor_delta},
              open(os.path.join(d, "results", "sensor-balance.json"), "w"))
    json.dump({"git_commit": commit, "git_dirty": dirty, "code_hash": code_hash,
               "experiment": dict(l.split("=") for l in env.split())}, open(os.path.join(d, "manifest.json"), "w"))
    if run_failed:
        open(os.path.join(d, "RUN_FAILED"), "w").write("boom\n")
    return d


def summarize(run):
    json.dump(analyze_uc1.analyze(run), open(os.path.join(run, "summary.json"), "w"))


class TestAnalyze(unittest.TestCase):
    def test_latency_and_deadline(self):
        s = analyze_uc1.analyze(make_run([0.2, 0.3, 1.5, 0.25]))
        self.assertAlmostEqual(s["payment_latency_s"]["p50"], 0.275, places=6)   # linear interpolation
        self.assertAlmostEqual(s["payment_latency_s"]["max"], 1.5)
        self.assertAlmostEqual(s["deadline_miss_share"], 1 / 4)   # 1.5 s > the 1 s to the next window
        self.assertEqual(s["deadline_miss_share_by_window_ms"], {"1000": 1 / 4})
        self.assertAlmostEqual(s["queueing_s"]["max"], 1.5 / 4)
        self.assertAlmostEqual(s["confirmation_s"]["max"], 1.5 * 3 / 4)
        self.assertAlmostEqual(s["open_s"]["c1"], 2.5)
        self.assertAlmostEqual(s["close_s"]["c1"], 0.03, places=6)
        self.assertEqual(s["diagnostics"]["customers"]["c1"]["resyncs"], 2)
        self.assertTrue(s["complete"])

    def test_unresolved_is_counted_and_reconciliation_flags_a_gap(self):
        s = analyze_uc1.analyze(make_run([0.2, 0.2], unresolved=1, sensor_delta=10))
        self.assertEqual(s["outcomes"]["unresolved"], 1)
        self.assertEqual(s["reconciliation"]["difference_wei"], -10)
        self.assertFalse(s["reconciliation"]["ok"])

    def test_latency_lower_bound_counts_the_payments_that_never_succeeded(self):
        # 4 succeeded (0.2 s each, last confirmed at 1003.2); 6 unresolved arrived at 1000.0: still unconfirmed
        # at 1003.2, so each is >= 3.2 s; one arrived after the last confirmation and counts as >= 0
        unres = [dict(id=f"uc1/r/c1/x{k}", amount_wei=10, outcome="unresolved", t_intended=1000.0,
                      t_generated=1000.0) for k in range(6)]
        late = dict(id="uc1/r/c1/y", amount_wei=10, outcome="unresolved", t_intended=1010.0, t_generated=1010.0)
        s = analyze_uc1.analyze(make_run([0.2] * 4, extra_rows=unres + [late]))
        self.assertAlmostEqual(s["payment_latency_s"]["p50"], 0.2)
        lb = s["payment_latency_lower_bound_s"]
        self.assertEqual(lb["n"], 11)          # sorted: 0, 0.2 x4, 3.2 x6 -> the median is 3.2
        self.assertAlmostEqual(lb["p50"], 3.2)
        self.assertEqual(s["headline"]["payment_latency_lower_bound_s"], lb)
        self.assertIn("survivors only", s["headline"]["latency_note"])
        self.assertIsNone(analyze_uc1.analyze(make_run([0.2] * 4))["headline"]["latency_note"])

    def test_a_crashed_payment_client_marks_the_run_incomplete(self):
        self.assertFalse(analyze_uc1.analyze(make_run([0.2, 0.2], complete=False))["complete"])

    def test_run_failed_or_missing_customers_mark_the_run_incomplete(self):
        self.assertFalse(analyze_uc1.analyze(make_run([0.2], run_failed=True))["complete"])
        run = make_run([0.2])
        open(os.path.join(run, "experiment.env"), "w").write("WINDOW_MS=1000\n")   # expects 6 customers
        self.assertFalse(analyze_uc1.analyze(run)["complete"])

    def _logs(self, run, sensor, customer="{'rows': 1, 'windows': 1, 'complete': True, 'emit_failed': 0}"):
        os.makedirs(os.path.join(run, "logs"), exist_ok=True)
        open(os.path.join(run, "logs", "bath1-sensor.log"), "w").write("noise\n" + sensor + "\n")
        open(os.path.join(run, "logs", "bath2-customer.log"), "w").write(customer + "\n")

    def test_sensor_drop_marks_the_run_incomplete(self):
        run = make_run([0.2])
        self._logs(run, "{'rows': 9, 'dropped': [2]}")
        s = analyze_uc1.analyze(run)
        self.assertFalse(s["complete"])
        self.assertEqual(s["complete_checks"]["dropped_customers"], [2])

    def test_short_ledger_marks_the_run_incomplete(self):
        run = make_run([0.2, 0.2], env="WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\nDURATION_S=3\n")
        self._logs(run, "{'rows': 9, 'dropped': []}")
        s = analyze_uc1.analyze(run)
        self.assertFalse(s["complete"])
        self.assertEqual(s["complete_checks"]["short_customers"], {"c1": [2, 3]})

    def test_customer_stream_incomplete_marks_the_run_incomplete(self):
        run = make_run([0.2])
        self._logs(run, "{'dropped': []}", "{'complete': False}")
        s = analyze_uc1.analyze(run)
        self.assertFalse(s["complete"])
        self.assertEqual(s["complete_checks"]["stream_incomplete_customers"], ["bath2"])

    def test_normal_logs_stay_complete_and_an_unreadable_sensor_log_does_not(self):
        env = "WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\nDURATION_S=3\n"
        run = make_run([0.2, 0.2, 0.2], env=env, sensor_log=False)
        s = analyze_uc1.analyze(run)                                # no sensor log: drops cannot be checked
        self.assertFalse(s["complete"])
        self.assertEqual(s["complete_checks"]["unreadable_logs"], ["logs/bath1-sensor.log"])
        run = make_run([0.2, 0.2, 0.2], env=env)
        open(os.path.join(run, "logs", "bath1-sensor.log"), "w").write("Traceback (most recent call last):\n")
        s = analyze_uc1.analyze(run)                                # a sensor that died without its result line
        self.assertFalse(s["complete"])
        self.assertEqual(s["complete_checks"]["unreadable_logs"], ["logs/bath1-sensor.log"])
        run = make_run([0.2, 0.2, 0.2], env=env)
        self._logs(run, "{'rows': 9, 'dropped': []}")
        s = analyze_uc1.analyze(run)
        self.assertTrue(s["complete"])
        self.assertEqual(s["complete_checks"]["unreadable_logs"], [])

    def test_aggregate_mean_sd_and_refuses_mixed_commits(self):
        runs = [make_run([0.2, 0.2]), make_run([0.4, 0.4]), make_run([0.3, 0.3])]
        for r in runs:
            summarize(r)
        agg = aggregate.aggregate(runs)
        self.assertAlmostEqual(agg["payment_latency_p50_s"]["mean"], 0.3, places=6)
        self.assertAlmostEqual(agg["payment_latency_p50_s"]["sd"], 0.1, places=6)
        mixed = runs[:2] + [make_run([0.3], commit="other")]
        summarize(mixed[2])
        with self.assertRaises(ValueError):
            aggregate.aggregate(mixed)

    def test_aggregate_refuses_dirty_and_incomplete_unless_allowed(self):
        dirty = [make_run([0.2, 0.2]), make_run([0.2, 0.2], dirty=True)]
        inc = [make_run([0.2, 0.2]), make_run([0.2, 0.2], complete=False)]
        for r in dirty + inc:
            summarize(r)
        with self.assertRaises(ValueError):
            aggregate.aggregate(dirty)
        aggregate.aggregate(dirty, allow_dirty=True)
        with self.assertRaises(ValueError):
            aggregate.aggregate(inc)
        self.assertEqual(aggregate.aggregate(inc, include_incomplete=True)["_incomplete_runs"], [inc[1]])

    def test_deadline_miss_counts_unresolved_and_uses_the_window_not_the_neighbour(self):
        s = analyze_uc1.analyze(make_run([0.2, 0.2, 0.2], unresolved=1))
        self.assertAlmostEqual(s["deadline_miss_share"], 1 / 4)
        # an unresolved row and a non-terminal (rebuilt) row both count as misses
        s = analyze_uc1.analyze(make_run([0.2], extra_rows=[dict(id="x", amount_wei=10, outcome="queued", t_intended=5.0)]))
        self.assertEqual(s["mapped_to_unresolved"], 1)
        self.assertEqual(s["outcomes"]["unresolved"], 1)
        # lost events: a late payment misses its own window even when the next ledger row is far away
        s = analyze_uc1.analyze(make_run([1.5, 0.1]))
        self.assertAlmostEqual(s["deadline_miss_share"], 1 / 2)
        s = analyze_uc1.analyze(make_run([0.2], env="WINDOW_MS=1000\nDURATION_S=60\nEXPECTED_CUSTOMERS=1\n"))
        self.assertEqual(s["windows"]["expected_per_customer"], 60)
        self.assertEqual(s["windows"]["per_customer"]["c1"]["ledger_rows"], 1)

    def test_missing_window_ms_fails_loudly(self):
        with self.assertRaises(ValueError):
            analyze_uc1.analyze(make_run([0.2], env="EXPECTED_CUSTOMERS=1\n"))

    def test_reverted_payments_cost_fees(self):
        rev = dict(id="rv", amount_wei=10, outcome="failed", error="reverted", t_intended=9.0,
                   gas_used=30000, effective_gas_price=10**8)
        s = analyze_uc1.analyze(make_run([0.2], extra_rows=[rev]))
        self.assertAlmostEqual(s["fees_eth"]["payments_l2"], (21000 + 30000) * 10**8 / 1e18)
        self.assertAlmostEqual(s["fees_eth"]["payments_l2_reverted"], 30000 * 10**8 / 1e18)
        self.assertEqual(s["gas"]["payment_l2_median"], 21000)

    def test_effective_gas_price_max_wei_is_the_highest_among_succeeded(self):
        """New summary key (arbitrum/zksync only): how close the run's base fee came to
        lifecycle.json's fee_cap_wei. Only succeeded rows count -- a reverted row's
        effective_gas_price is not admission headroom used by a real payment."""
        hi = dict(id="hi", amount_wei=10, outcome="succeeded", t_intended=20.0, t_generated=20.0,
                 t_queued=20.0, t_submitted=20.0, t_confirmed=20.05, gas_used=21000,
                 effective_gas_price=2 * 10**8)
        rev = dict(id="rv", amount_wei=10, outcome="failed", error="reverted", t_intended=9.0,
                   gas_used=30000, effective_gas_price=9 * 10**8)       # higher, but not succeeded: excluded
        s = analyze_uc1.analyze(make_run([0.2], extra_rows=[hi, rev]))
        self.assertEqual(s["gas"]["effective_gas_price_max_wei"], 2 * 10**8)

    def test_aggregate_tolerates_incomplete_runs_without_data(self):
        good, empty = make_run([0.2, 0.2]), make_run([], complete=False, sensor_delta=0)
        os.remove(os.path.join(empty, "results", "c1", "lifecycle.json"))
        for r in (good, empty):
            summarize(r)
        agg = aggregate.aggregate([good, empty], include_incomplete=True)
        self.assertEqual(agg["payment_latency_p50_s"]["n"], 1)
        self.assertEqual(agg["payment_latency_p50_s"]["runs"][1], None)
        self.assertEqual(agg["close_s_median"]["runs"][1], None)
        self.assertEqual(agg["deadline_miss_share"]["n"], 1)

    def test_aggregate_refuses_different_config_or_code_hash(self):
        a, b = make_run([0.2]), make_run([0.2], env="WINDOW_MS=10\nEXPECTED_CUSTOMERS=1\n")
        c, d = make_run([0.2]), make_run([0.2], code_hash="h2")
        for r in (a, b, c, d):
            summarize(r)
        with self.assertRaises(ValueError):
            aggregate.aggregate([a, b])
        with self.assertRaises(ValueError):
            aggregate.aggregate([c, d])

    def test_git_dirty_none_is_treated_as_dirty(self):
        a = make_run([0.2]); b = make_run([0.2])
        m = json.load(open(os.path.join(b, "manifest.json"))); m["git_dirty"] = None
        json.dump(m, open(os.path.join(b, "manifest.json"), "w"))
        for r in (a, b):
            summarize(r)
        with self.assertRaises(ValueError):
            aggregate.aggregate([a, b])

    def test_run_failed_folder_with_missing_files_gives_an_incomplete_summary(self):
        run = make_run([0.2], run_failed=True)
        os.remove(os.path.join(run, "results", "sensor-balance.json"))
        os.remove(os.path.join(run, "results", "c1", "ledger.csv"))
        s = analyze_uc1.analyze(run)
        self.assertFalse(s["complete"])
        self.assertIsNone(s["reconciliation"])
        self.assertIn("results/sensor-balance.json", s["complete_checks"]["missing"])
        self.assertIn("results/c1/ledger", s["complete_checks"]["missing"])

    def test_recorder_summary_skips_bad_lines_and_uses_the_stream_window(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "b.csv")
        open(p, "w").write("t,temp_c,throttled,arm_mhz,bssid\n1,50,0x1,1800,aa\n2,60,0x0,1800,aa\n3,70\n4,x,0x0,1,aa\n")
        s = manifest.recorder_summary(p)
        self.assertEqual((s["samples"], s["skipped_lines"], s["scope"]), (2, 2, "whole file"))
        s = manifest.recorder_summary(p, (2, 3))
        self.assertEqual((s["samples"], s["max_temp_c"], s["scope"]), (1, 60.0, "stream window"))

    def test_real_layout_fixture_reconciles(self):
        """A few rows of a real smoke run (public test addresses only)."""
        run = tempfile.mkdtemp()
        shutil.copytree(FIXTURE, run, dirs_exist_ok=True)
        open(os.path.join(run, "experiment.env"), "w").write("WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\n")
        os.makedirs(os.path.join(run, "logs"))
        open(os.path.join(run, "logs", "bath1-sensor.log"), "w").write("{'rows': 5, 'dropped': []}\n")
        s = analyze_uc1.analyze(run)
        self.assertEqual(s["outcomes"], {"succeeded": 5})
        self.assertTrue(s["reconciliation"]["ok"])
        self.assertTrue(s["complete"])
        self.assertEqual(s["deadline_miss_share"], 0.0)
        self.assertGreater(s["open_s"]["c1"], 0)

    def test_manifest_survives_an_unreachable_bath0(self):
        run = make_run([0.2])
        os.remove(os.path.join(run, "manifest.json"))
        open(os.path.join(run, "start-temps.txt"), "w").write("# c\nbath0 49.6 50.6\n")
        open(os.path.join(run, "code-hash.txt"), "w").write("abc123\n")
        orig = manifest.images
        manifest.images = lambda d, env: {"error": "ssh failed"}
        try:
            m = manifest.build(run)
        finally:
            manifest.images = orig
        self.assertEqual(m["code_hash"], "abc123")
        self.assertIsNone(m["run_failed"])
        self.assertEqual(m["idle_and_start_temps"]["bath0"], {"idle_c": 49.6, "start_c": 50.6})
        self.assertEqual(m["images"], {"error": "ssh failed"})
        self.assertIsNone(m["stack"])                              # Arbitrum folders carry no STACK key (today's env)

    def test_build_records_stack_zksync_for_a_zksync_run_folder(self):
        run = make_run([0.2], env="STACK=zksync\nWINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\n")
        orig = manifest.images
        manifest.images = lambda d, env: {"stub": True, "seen_stack": env.get("STACK"), "seen_dir": d}
        try:
            m = manifest.build(run)
        finally:
            manifest.images = orig
        self.assertEqual(m["stack"], "zksync")
        self.assertEqual(m["images"], {"stub": True, "seen_stack": "zksync", "seen_dir": run})

    def test_manifest_perun_records_binary_and_anvil(self):
        """Task 5 (controller ruling R-P4): unit test, no boards needed -- bath0 is unreachable,
        as in test_manifest_survives_an_unreachable_bath0."""
        run = make_run([0.2], env="STACK=perun\nWINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\n")
        open(os.path.join(run, "perun-node-sha256.txt"), "w").write("a" * 64 + "\n")
        json.dump({"chain_id": 31337, "adjudicator": "0xabc", "asset_holder": "0xdef"},
                  open(os.path.join(run, "results", "contracts.json"), "w"))
        orig_sh = manifest.sh
        manifest.sh = lambda *a, **k: ""            # bath0 unreachable: no anvil version
        try:
            m = manifest.build(run)
        finally:
            manifest.sh = orig_sh
        self.assertEqual(m["stack"], "perun")
        self.assertNotIn("images", m)
        self.assertIsNone(m["stack_versions"]["anvil"])
        self.assertEqual(m["stack_versions"]["error"], "ssh bath0 gave no anvil version")
        self.assertEqual(m["stack_versions"]["perun_node_sha256"], "a" * 64)
        self.assertEqual(m["stack_versions"]["contracts"],
                         {"chain_id": 31337, "adjudicator": "0xabc", "asset_holder": "0xdef"})

    def test_images_zksync_reads_binary_sha_config_and_compose_images(self):
        """Unit test, no boards needed: manifest.sh (the ssh/shell runner) is faked."""
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "results"))
        open(os.path.join(d, "results", "config.txt"), "w").write("block_commit_deadline_ms: 50\n")
        open(os.path.join(d, "results", "config.sha256"), "w").write("f" * 64 + "  general.yaml\n")

        def fake_sh(*cmd, cwd=None, timeout=30):
            joined = " ".join(cmd)
            if f"docker compose -p {manifest.ZK_PROJECT} images" in joined:
                return '[{"Name":"reth"}]'
            if "sha256sum" in joined and manifest.ZK_BIN in joined:
                return "e" * 64 + "  " + manifest.ZK_BIN
            return ""

        orig_sh = manifest.sh
        manifest.sh = fake_sh
        try:
            out = manifest.images(d, {"STACK": "zksync"})
        finally:
            manifest.sh = orig_sh
        self.assertEqual(out["compose_images"], '[{"Name":"reth"}]')
        self.assertEqual(out["zk_bin_sha256"], "e" * 64)
        self.assertIn("block_commit_deadline_ms", out["config_txt"])
        self.assertIn("general.yaml", out["config_sha256"])
        self.assertNotIn("error", out)

    def test_images_arbitrum_path_is_unchanged_stack_missing_or_arbitrum(self):
        orig_sh = manifest.sh
        manifest.sh = lambda *a, **k: ""
        try:
            out_no_env = manifest.images(tempfile.mkdtemp(), {})
            out_arbitrum = manifest.images(tempfile.mkdtemp(), {"STACK": "arbitrum"})
        finally:
            manifest.sh = orig_sh
        expected = {"error": "ssh bath0 gave no images (unreachable, or no compose config)"}
        self.assertEqual(out_no_env, expected)
        self.assertEqual(out_arbitrum, expected)


def q(k, **kw):
    """A ledger row refused by the client's full queue (payclient error "queue full")."""
    return dict(dict(id=f"q{k}", amount_wei=10, outcome="failed", error="queue full", t_intended=5000.0 + k,
                     t_generated=5000.0 + k), **kw)


class TestFinalReviewFields(unittest.TestCase):
    """Fields added after the final review of the UC1 Arbitrum path (C1, C2, I2, I4, M1-M5, M11)."""

    def test_unsaturated_run_has_a_headline_and_no_saturation(self):
        s = analyze_uc1.analyze(make_run([0.2, 0.2, 0.2]))
        self.assertFalse(s["saturated"])
        h = s["headline"]
        self.assertFalse(h["saturated"])
        self.assertIsNone(h["latency_note"])
        self.assertEqual((h["offered"], h["succeeded_share"], h["dropped_share"], h["admitted_share"]), (3, 1.0, 0.0, 1.0))

    def test_queue_full_rows_mark_the_run_saturated_and_the_headline_says_why(self):
        env = "WINDOW_MS=10\nEXPECTED_CUSTOMERS=1\nMAX_QUEUE=10000\nDRAIN_S=60\n"
        unres = dict(id="u", amount_wei=10, outcome="unresolved", error="drain deadline", t_intended=9.0)
        s = analyze_uc1.analyze(make_run([0.2, 0.2, 0.2, 0.2], env=env, extra_rows=[q(0), q(1), q(2), unres]))
        self.assertTrue(s["saturated"])
        h = s["headline"]
        self.assertTrue(h["saturated"])
        self.assertEqual(h["offered"], 8)
        self.assertAlmostEqual(h["succeeded_share"], 4 / 8)
        self.assertAlmostEqual(h["dropped_share"], 3 / 8)
        self.assertAlmostEqual(h["admitted_share"], 5 / 8)
        self.assertAlmostEqual(h["unresolved_share"], 1 / 8)
        self.assertIn("MAX_QUEUE=10000", h["latency_note"])
        self.assertIn("DRAIN_S=60", h["latency_note"])
        self.assertEqual(h["confirmation_s"], s["confirmation_s"])
        self.assertEqual(s["per_customer"]["c1"]["queue_full"], 3)
        # a failure for another reason is not saturation
        other = dict(q(0), error="reverted")
        self.assertFalse(analyze_uc1.analyze(make_run([0.2], extra_rows=[other]))["saturated"])

    def test_goodput_per_customer_uses_the_confirmation_span_and_names_the_board(self):
        run = make_run([0.5, 0.5, 0.5, 0.5, 0.5])            # confirmed at 1000.5 ... 1004.5: span 4 s
        os.makedirs(os.path.join(run, "facts-start"))
        json.dump({"host": "bath2", "model": "Raspberry Pi 3 Model B Plus Rev 1.4"},
                  open(os.path.join(run, "facts-start", "bath2.json"), "w"))
        s = analyze_uc1.analyze(run)
        c = s["per_customer"]["c1"]
        self.assertEqual((c["board"], c["board_model"]), ("bath2", "Raspberry Pi 3 Model B Plus Rev 1.4"))
        self.assertEqual((c["offered"], c["succeeded"]), (5, 5))
        self.assertAlmostEqual(c["confirmation_span_s"], 4.0)
        self.assertAlmostEqual(c["goodput_per_s"], 5 / 4)             # not 5 / DURATION_S
        self.assertAlmostEqual(c["confirmation_p50_s"], 0.5 * 3 / 4)
        self.assertAlmostEqual(s["headline"]["goodput_per_s"], 5 / 4)
        # no facts: the model is unknown, not an error; one success: no span, no goodput
        s = analyze_uc1.analyze(make_run([0.5]))
        self.assertIsNone(s["per_customer"]["c1"]["board_model"])
        self.assertIsNone(s["per_customer"]["c1"]["goodput_per_s"])

    def test_board_comes_from_the_run_folders_customers(self):
        """Final review Minor 5: c<n> ran on the n-th board of experiment.env's CUSTOMERS (deploy/cluster
        array syntax, trailing comment allowed); without CUSTOMERS, deploy/cluster's default list."""
        for line, board in (("CUSTOMERS=(bath5 bath3)\n", "bath5"),
                            ("CUSTOMERS=(bath4 bath6)   # customers 1..2\n", "bath4"),
                            ("CUSTOMERS=( bath6 )\n", "bath6"),
                            ("", "bath2")):
            run = make_run([0.5, 0.5])
            open(os.path.join(run, "experiment.env"), "w").write("WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\n" + line)
            self.assertEqual(analyze_uc1.analyze(run)["per_customer"]["c1"]["board"], board, line)

    def test_unreadable_customers_fails_loudly(self):
        run = make_run([0.5])
        open(os.path.join(run, "experiment.env"), "w").write(
            'WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\nCUSTOMERS=("${ALL[@]:0:5}")\n')
        with self.assertRaises(ValueError):
            analyze_uc1.analyze(run)

    def test_receive_lag_per_customer_and_pooled(self):
        run = make_run([0.2])
        with open(os.path.join(run, "results", "c1", "rows_received.csv"), "w") as f:
            f.write("seq,t_sent,t_received\n0,100.0,100.01\n1,100.1,100.13\n2,100.2,100.25\n3,100.3,101.3\n")
        s = analyze_uc1.analyze(run)
        lag = s["per_customer"]["c1"]["receive_lag_s"]
        self.assertEqual(lag["n"], 4)
        self.assertAlmostEqual(lag["p50"], 0.04, places=6)
        self.assertAlmostEqual(lag["max"], 1.0, places=6)
        self.assertAlmostEqual(lag["p99"], 0.05 + (1.0 - 0.05) * 0.97, places=6)
        self.assertEqual(s["receive_lag_s"]["n"], 4)
        self.assertAlmostEqual(s["receive_lag_s"]["max"], 1.0, places=6)
        # no rows_received.csv: None, not an error
        self.assertIsNone(analyze_uc1.analyze(make_run([0.2]))["per_customer"]["c1"]["receive_lag_s"])

    def test_payments_in_flight_at_the_drain_deadline_explain_the_reconciliation_gap(self):
        sub = [dict(id=f"s{k}", amount_wei=10, outcome="unresolved", error="drain deadline", t_intended=9.0,
                    t_queued=9.0, t_submitted=9.1) for k in range(3)]
        never = dict(id="n", amount_wei=10, outcome="unresolved", error="drain deadline", t_intended=9.0)
        # two of the three submitted-but-unresolved payments executed after the ledger closed
        s = analyze_uc1.analyze(make_run([0.2, 0.2], sensor_delta=40, extra_rows=sub + [never]))
        r = s["reconciliation"]
        self.assertEqual((r["difference_wei"], r["explained_wei"]), (20, 30))
        self.assertFalse(r["ok"])
        self.assertTrue(r["ok_explained"])
        self.assertEqual(s["fees_unpriced_submitted_rows"], 3)
        # more received than was ever submitted, or less than the ledger says: not explained
        s = analyze_uc1.analyze(make_run([0.2, 0.2], sensor_delta=60, extra_rows=sub))
        self.assertFalse(s["reconciliation"]["ok_explained"])
        s = analyze_uc1.analyze(make_run([0.2, 0.2], sensor_delta=10, extra_rows=sub))
        self.assertFalse(s["reconciliation"]["ok_explained"])
        s = analyze_uc1.analyze(make_run([0.2, 0.2]))
        self.assertTrue(s["reconciliation"]["ok"] and s["reconciliation"]["ok_explained"])

    def test_null_balance_gives_no_reconciliation_and_is_listed(self):
        run = make_run([0.2])
        open(os.path.join(run, "results", "sensor-balance.json"), "w").write(
            '{"sensor": "0x0", "l2_balance_before_wei": null, "l2_balance_after_wei": 10}')
        s = analyze_uc1.analyze(run)
        self.assertIsNone(s["reconciliation"])
        self.assertFalse(s["complete"])
        self.assertTrue(any(m.startswith("results/sensor-balance.json") for m in s["complete_checks"]["missing"]))

    def test_summary_records_the_analysis_commit(self):
        s = analyze_uc1.analyze(make_run([0.2]))
        self.assertRegex(s["analysis_commit"] or "", r"^[0-9a-f]{40}$")
        self.assertIn(s["analysis_measure_dirty"], (True, False))

    def test_aggregate_warns_when_a_metric_has_fewer_values_than_runs(self):
        good, empty = make_run([0.2, 0.2]), make_run([], complete=False, sensor_delta=0)
        for r in (good, empty):
            summarize(r)
        agg = aggregate.aggregate([good, empty], include_incomplete=True)
        self.assertIn("payment_latency_p50_s: n=1 of 2 runs (the others have no value)", agg["_warnings"])
        both = [make_run([0.2, 0.2]), make_run([0.3, 0.3])]
        for r in both:
            open(os.path.join(r, "results", "c1", "rows_received.csv"), "w").write("seq,t_sent,t_received\n0,1.0,1.01\n")
            set_life(r, "c1", start_nonce=0, close_nonce=2, executed_onchain=2, pending_before_close=0)
            summarize(r)
        self.assertEqual(aggregate.aggregate(both)["_warnings"], [])
        # runs from before the nonce bookkeeping (all Arbitrum folders): late_executed_total is unknown, and said so
        old = [make_run([0.2, 0.2]), make_run([0.3, 0.3])]
        for r in old:
            summarize(r)
        self.assertIn("late_executed_total: n=0 of 2 runs (the others have no value)",
                      aggregate.aggregate(old)["_warnings"])

    def test_aggregate_marks_latency_of_saturated_runs(self):
        env = "WINDOW_MS=10\nEXPECTED_CUSTOMERS=1\nMAX_QUEUE=10000\nDRAIN_S=60\n"
        runs = [make_run([0.2, 0.2], env=env, extra_rows=[q(0)]), make_run([0.2, 0.2], env=env)]
        for r in runs:
            summarize(r)
        agg = aggregate.aggregate(runs)
        self.assertEqual(agg["_saturated_runs"], [runs[0]])
        self.assertEqual(agg["_saturated"], [True, False])
        self.assertEqual(agg["payment_latency_p50_s"]["note"], "1 of 2 runs saturated: set by MAX_QUEUE=10000 and DRAIN_S=60")
        self.assertNotIn("note", agg["confirmation_p50_s"])
        self.assertAlmostEqual(agg["succeeded_share"]["runs"][0], 2 / 3)
        self.assertAlmostEqual(agg["dropped_share"]["runs"][0], 1 / 3)
        unsat = [make_run([0.2, 0.2]), make_run([0.2, 0.2])]
        for r in unsat:
            summarize(r)
        self.assertNotIn("note", aggregate.aggregate(unsat)["payment_latency_p50_s"])

    def test_manifest_prefers_the_run_start_commit_and_reads_gesl_and_stack_config(self):
        run = make_run([0.2])
        open(os.path.join(run, "run-start-commit.txt"), "w").write("a" * 40 + "\n")
        json.dump({"sig": 6063, "sha256": "87b2"}, open(os.path.join(run, "gesl.json"), "w"))
        open(os.path.join(run, "stack-config.sha256"), "w").write(
            "e" * 64 + "  docker-compose.yaml\n" + "1" * 64 + "  SHA256SUMS\n" + "9" * 64 + "  config.tgz:sequencer_config.json\n")
        orig = manifest.images
        manifest.images = lambda d, env: {"error": "offline"}
        try:
            m = manifest.build(run)
            self.assertEqual((m["git_commit"], m["git_commit_source"]), ("a" * 40, "run-start-commit.txt"))
            self.assertEqual(m["gesl"], {"sig": 6063, "sha256": "87b2"})
            self.assertEqual(m["stack_config_sha256"]["docker-compose.yaml"], "e" * 64)
            self.assertEqual(m["stack_config_sha256"]["config.tgz:sequencer_config.json"], "9" * 64)
            open(os.path.join(run, "run-start-commit.txt"), "w").write("b" * 40 + " dirty\n")
            self.assertTrue(manifest.build(run)["git_dirty"])
            old = make_run([0.2])                                # a run folder from before these files
            m = manifest.build(old)
            self.assertEqual(m["git_commit_source"], "HEAD at manifest time")
            self.assertEqual(m["git_commit"], m["git_commit_at_manifest"])
            self.assertIsNone(m["gesl"])
            self.assertIsNone(m["stack_config_sha256"])
        finally:
            manifest.images = orig

    def test_recorder_cpu_column_is_summarised_and_old_files_are_tolerated(self):
        d = tempfile.mkdtemp()
        new, old = os.path.join(d, "new.csv"), os.path.join(d, "old.csv")
        open(new, "w").write("t,temp_c,throttled,arm_mhz,bssid,cpu_busy_pct\n1,50,0x0,1800,aa,40.0\n"
                             "2,51,0x0,1800,aa,80.0\n3,52,0x0,1800,aa,\n")
        open(old, "w").write("t,temp_c,throttled,arm_mhz,bssid\n1,50,0x0,1800,aa\n")
        s = manifest.recorder_summary(new)
        self.assertEqual(s["samples"], 3)                          # an empty CPU field keeps the line
        self.assertAlmostEqual(s["mean_cpu_busy_pct"], 60.0)
        self.assertAlmostEqual(s["max_cpu_busy_pct"], 80.0)
        s = manifest.recorder_summary(old)
        self.assertEqual(s["samples"], 1)
        self.assertIsNone(s["mean_cpu_busy_pct"])


def set_life(run, c, **kw):
    """Add keys (e.g. the Task 4 nonce bookkeeping) to a customer's lifecycle.json."""
    p = os.path.join(run, "results", c, "lifecycle.json")
    life = json.load(open(p))
    life.update(kw)
    json.dump(life, open(p, "w"))


class TestBacklogAndLateExecutions(unittest.TestCase):
    """zkSync UC1 measurement path, Task 7: offered rate, backlog, late executions."""

    def _two_customers(self):
        """c1 arrives at 1000..1003 and is confirmed at 1002.5, 1005, 1007.5, 1010 (slower than arrivals);
        c2 is the same 0.1 s later."""
        env = "WINDOW_MS=1000\nEXPECTED_CUSTOMERS=2\n"
        run = make_run([2.5, 4, 5.5, 7], env=env)
        src = os.path.join(run, "results", "c1")
        dst = os.path.join(run, "results", "c2")
        shutil.copytree(src, dst)
        with open(os.path.join(src, "ledger.csv")) as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            for k in ("t_intended", "t_generated", "t_queued", "t_submitted", "t_confirmed"):
                r[k] = float(r[k]) + 0.1
        with open(os.path.join(dst, "ledger.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
        json.dump({"l2_balance_before_wei": 0, "l2_balance_after_wei": 80},
                  open(os.path.join(run, "results", "sensor-balance.json"), "w"))
        return run

    def test_backlog_offered_rate_and_confirmations_per_10s(self):
        s = analyze_uc1.analyze(self._two_customers())
        self.assertEqual(s["per_customer"]["c1"]["backlog_max"], 3)    # 1000, 1001, 1002 queued before 1002.5
        self.assertEqual(s["per_customer"]["c2"]["backlog_max"], 3)
        self.assertEqual(s["backlog_max"], 6)
        self.assertAlmostEqual(s["offered_rate_per_s"], 2.0)          # 2 customers x 1000 / WINDOW_MS
        self.assertAlmostEqual(s["headline"]["goodput_per_s"], 8 / (1010.1 - 1002.5))   # R1: the achieved rate
        self.assertNotIn("achieved_rate_per_s", s)
        self.assertEqual(s["confirmed_per_10s"], [6, 2])              # from the first t_intended (1000.0)

    def test_unresolved_rows_stay_in_the_backlog(self):
        unres = [dict(id=f"u{k}", amount_wei=10, outcome="unresolved", error="drain deadline",
                      t_intended=1001.5, t_queued=1001.5, t_submitted=1001.6) for k in range(2)]
        s = analyze_uc1.analyze(make_run([0.2, 0.2, 0.2], extra_rows=unres))
        self.assertEqual(s["backlog_max"], 3)          # the two never leave; one confirmed row at a time

    def test_a_rejected_row_stays_in_the_backlog_and_a_submit_failure_leaves_it(self):
        # rejected by the node mid-run: no receipt, and the ledger keeps no time for the rejection
        rejected = dict(id="rj", amount_wei=10, outcome="failed", error="nonce too low", t_intended=999.5,
                        t_queued=999.5, t_submitted=999.6)
        s = analyze_uc1.analyze(make_run([0.2, 0.2, 0.2], extra_rows=[rejected]))
        self.assertEqual(s["backlog_max"], 2)          # the rejected row + one row at a time
        # submit failed before sending: payclient records the failure time as t_confirmed, so it leaves
        sub_failed = dict(rejected, error="submit failed: ValueError: x", t_submitted="", t_confirmed=999.6)
        s = analyze_uc1.analyze(make_run([0.2, 0.2, 0.2], extra_rows=[sub_failed]))
        self.assertEqual(s["backlog_max"], 1)

    def test_offered_rate_uses_the_expected_customers_not_the_folders_found(self):
        s = analyze_uc1.analyze(make_run([0.2], env="WINDOW_MS=10\n"))    # EXPECTED_CUSTOMERS defaults to 6
        self.assertAlmostEqual(s["offered_rate_per_s"], 600.0)
        s = analyze_uc1.analyze(make_run([0.2], env="WINDOW_MS=100\nEXPECTED_CUSTOMERS=3\n"))
        self.assertAlmostEqual(s["offered_rate_per_s"], 30.0)

    def test_late_executions_explain_the_reconciliation_by_nonce(self):
        env = "WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\nPRICE_WEI=10\n"
        sub = [dict(id=f"s{k}", amount_wei=10, outcome="unresolved", error="drain deadline", t_intended=1005.0,
                    t_queued=1005.0, t_submitted=1005.1) for k in range(3)]
        rev = dict(id="rv", amount_wei=10, outcome="failed", error="reverted", t_intended=1006.0, t_queued=1006.0,
                   t_submitted=1006.1, t_confirmed=1006.5, gas_used=21000, effective_gas_price=10**8)
        run = make_run([0.2, 0.2], env=env, sensor_delta=50, extra_rows=sub + [rev])
        # 2 succeeded + 1 reverted + 3 late = 6 nonces between start and Close
        set_life(run, "c1", start_nonce=5, close_nonce=11, executed_onchain=6, pending_before_close=0)
        s = analyze_uc1.analyze(run)
        self.assertEqual(s["late_executed"], {"c1": 3})
        self.assertEqual(s["per_customer"]["c1"]["late_executed"], 3)
        self.assertEqual(s["late_executed_total"], 3)
        self.assertEqual(s["close_pending"], {"c1": 0})
        r = s["reconciliation"]
        self.assertFalse(r["ok"])
        self.assertEqual(r["late_executed_wei"], 30)
        self.assertTrue(r["ok_with_late"])
        self.assertTrue(r["ok_explained"])
        # the nonces say only 2 executed late, but the sensor got 3: not explained by the nonces
        set_life(run, "c1", close_nonce=10, executed_onchain=5)
        r = analyze_uc1.analyze(run)["reconciliation"]
        self.assertEqual(r["late_executed_wei"], 20)
        self.assertFalse(r["ok_with_late"])

    def test_dropped_payments_are_not_counted_as_late(self):
        """Close-nonce fix: 3 payments unresolved at the drain deadline, 1 never admitted by the
        node (not_admitted=1, Close took the node's pending nonce, no gap), 2 executed late. The
        nonces count exactly the 2 late ones, and the sensor (2 ok + 2 late) agrees."""
        env = "WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\nPRICE_WEI=10\n"
        sub = [dict(id=f"s{k}", amount_wei=10, outcome="unresolved", error="drain deadline", t_intended=1005.0,
                    t_queued=1005.0, t_submitted=1005.1) for k in range(3)]
        run = make_run([0.2, 0.2], env=env, sensor_delta=40, extra_rows=sub)
        set_life(run, "c1", start_nonce=5, submitted_nonce=10, not_admitted=1, close_nonce=9,
                 executed_onchain=4, pending_before_close=0)
        s = analyze_uc1.analyze(run)
        self.assertEqual(s["late_executed"], {"c1": 2})
        self.assertEqual(s["not_admitted"], {"c1": 1})
        self.assertTrue(s["reconciliation"]["ok_with_late"])

    def test_payments_executed_after_close_count_as_late(self):
        """Review Important 1: payments queued behind a gap mine after Close. executed_onchain
        (nonces below Close) misses them; executed_after_close adds them back so the late count
        and ok_with_late stay exact. A recorded-but-unknown executed_after_close gives null."""
        env = "WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\nPRICE_WEI=10\n"
        sub = [dict(id=f"s{k}", amount_wei=10, outcome="unresolved", error="drain deadline", t_intended=1005.0,
                    t_queued=1005.0, t_submitted=1005.1) for k in range(4)]
        run = make_run([0.2, 0.2], env=env, sensor_delta=50, extra_rows=sub)
        # start 5, 2 ok -> pending stops at 7 (nonce 7 lost), 8..10 queued; Close = 7; 8..10 mine after it
        set_life(run, "c1", start_nonce=5, submitted_nonce=11, not_admitted=4, close_nonce=7,
                 executed_onchain=2, final_nonce=11, executed_after_close=3, pending_before_close=0)
        s = analyze_uc1.analyze(run)
        self.assertEqual(s["late_executed"], {"c1": 3})
        self.assertTrue(s["reconciliation"]["ok_with_late"])
        set_life(run, "c1", final_nonce=None, executed_after_close=None)
        self.assertEqual(analyze_uc1.analyze(run)["late_executed"], {"c1": None})

    def test_old_layout_without_nonces_gives_null_not_an_error(self):
        s = analyze_uc1.analyze(make_run([0.2, 0.2]))
        self.assertEqual(s["late_executed"], {"c1": None})
        self.assertIsNone(s["per_customer"]["c1"]["late_executed"])
        self.assertIsNone(s["late_executed_total"])
        self.assertEqual(s["close_pending"], {"c1": None})
        self.assertEqual(s["not_admitted"], {"c1": None})
        self.assertIsNone(s["reconciliation"]["ok_with_late"])
        self.assertIsNone(s["reconciliation"]["late_executed_wei"])
        self.assertTrue(s["reconciliation"]["ok"])
        self.assertEqual(s["backlog_max"], 1)

    def test_aggregate_headline_has_backlog_and_late_executions(self):
        self.assertIn("backlog_max", aggregate.HEADLINE)
        self.assertIn("late_executed_total", aggregate.HEADLINE)
        self.assertNotIn("achieved_rate_per_s", aggregate.HEADLINE)
        runs = [self._two_customers(), make_run([0.2, 0.2], env="WINDOW_MS=1000\nEXPECTED_CUSTOMERS=2\n")]
        for r in runs:
            summarize(r)
        agg = aggregate.aggregate(runs, include_incomplete=True)       # same env; the second has one customer
        self.assertEqual(agg["backlog_max"]["runs"], [6, 1])
        self.assertEqual(agg["late_executed_total"]["runs"], [None, None])
        self.assertEqual(agg["late_executed_total"]["n"], 0)


PERUN_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "perun-run")
BEFORE_PERUN = os.path.join(os.path.dirname(__file__), "fixtures", "uc1_smoke_c1.summary-before-perun.json")
PRICE = 10**12


def perun_run():
    """A copy of the Perun fixture: 2 customers x 5 succeeded windows, both channels settled (shapes from
    measure/perun.py main_async and uc1_sensor.go). The sensor log is written here (*.log is git-ignored)."""
    run = tempfile.mkdtemp()
    shutil.copytree(PERUN_FIXTURE, run, dirs_exist_ok=True)
    os.makedirs(os.path.join(run, "logs"))
    open(os.path.join(run, "logs", "bath1-sensor.log"), "w").write("{'rows': 10, 'dropped': []}\n")
    return run


def edit_json(run, rel, fn):
    p = os.path.join(run, *rel.split("/"))
    d = json.load(open(p))
    fn(d)
    json.dump(d, open(p, "w"))


class TestPerunAnalysis(unittest.TestCase):
    """Perun UC1 measurement path, Task 6 (Review Focus 1 and 4)."""

    def test_perun_fixture_reconciles_three_ways(self):
        s = analyze_uc1.analyze(perun_run())
        self.assertTrue(s["complete"], s["complete_checks"])
        self.assertTrue(s["complete_checks"]["sensor_result_complete"])
        self.assertEqual(s["outcomes"], {"succeeded": 10})
        self.assertEqual(s["open_s"], {"c1": 2.0, "c2": 2.0})                        # t_funded - t_start
        self.assertAlmostEqual(s["close_s"]["c1"], 6.0)                             # t_withdrawn - t_start
        self.assertAlmostEqual(s["close_finalize_s"]["c2"], 0.05)                   # t_finalized - t_start
        self.assertIn("cooperative close", s["close_definition"])
        r = s["reconciliation"]
        self.assertTrue(r["ok"])
        self.assertEqual((r["succeeded_wei"], r["sensor_final_balances_wei"]), (10 * PRICE, 10 * PRICE))
        self.assertEqual(r["sensor_close_fee_wei"], 98000 * 10**9)
        self.assertEqual(r["sensor_received_wei"], 10 * PRICE)                      # L1 change + own close fee
        self.assertEqual(r["sensor_l1_balance_change_wei"], 10 * PRICE - 98000 * 10**9)
        self.assertEqual(r["difference_wei"], 0)
        self.assertTrue(r["l1_matches_final_balances"])
        for c in ("c1", "c2"):
            self.assertTrue(r["per_customer"][c]["succeeded_matches_sensor"])
            self.assertTrue(r["per_customer"][c]["customer_matches_sensor"])
        self.assertTrue(r["ok_explained"])
        self.assertTrue(r["ok_with_late"])                                          # exact from the channel
        self.assertEqual(r["late_executed_wei"], 0)
        self.assertEqual(s["late_executed"], {"c1": 0, "c2": 0})
        self.assertEqual(s["late_executed_total"], 0)
        ch = s["channels"]["c1"]
        self.assertEqual((ch["final_version"], ch["broken"], ch["updates_accepted"]), (6, False, 6))
        self.assertEqual(ch["unresolved_in_final_state"], 0)
        self.assertTrue(ch["settled"])
        self.assertNotIn("effective_gas_price_max_wei", s["gas"])   # perun: off-chain, no base fee to report

    def test_perun_channel_view_mismatch_fails_reconciliation(self):
        run = perun_run()
        edit_json(run, "results/c2/lifecycle.json",                                 # the customer saw one less
                  lambda l: l["close"].update(final_balances_wei=[str(10**18 - 4 * PRICE), str(4 * PRICE)]))
        r = analyze_uc1.analyze(run)["reconciliation"]
        self.assertFalse(r["ok"])
        self.assertFalse(r["ok_explained"])
        self.assertFalse(r["per_customer"]["c2"]["customer_matches_sensor"])
        self.assertTrue(r["per_customer"]["c2"]["succeeded_matches_sensor"])
        self.assertTrue(r["per_customer"]["c1"]["customer_matches_sensor"])
        run = perun_run()                                                           # same balances, other version
        edit_json(run, "results/c1/lifecycle.json", lambda l: l["close"].update(final_version=7))
        r = analyze_uc1.analyze(run)["reconciliation"]
        self.assertFalse(r["ok"])
        self.assertFalse(r["per_customer"]["c1"]["customer_matches_sensor"])
        run = perun_run()                                                           # the L1 does not add up
        edit_json(run, "results/sensor-balance.json", lambda b: b.update(l1_balance_after_wei=b["l1_balance_after_wei"] - 1))
        r = analyze_uc1.analyze(run)["reconciliation"]
        self.assertFalse(r["ok"])
        self.assertFalse(r["l1_matches_final_balances"])
        self.assertTrue(all(v["succeeded_matches_sensor"] and v["customer_matches_sensor"]
                            for v in r["per_customer"].values()))

    def test_perun_unresolved_update_in_final_state_is_reported(self):
        """Review Focus 1 and ruling R-P6: c2's update w3 timed out after the sensor accepted it, so the
        sensor holds version 4 with 4 payments; the customer marked w3 unresolved and w4 failed (channel
        broken), never closed the broken channel (close_error) and is incomplete. The sensor never settled
        that channel (settle deadline)."""
        run = perun_run()
        p = os.path.join(run, "results", "c2", "ledger.csv")
        with open(p) as f:
            rows = list(csv.DictReader(f))
        rows[3].update(outcome="unresolved", t_confirmed="", block="", gas_used="", effective_gas_price="",
                       error="unresolved: update timed out after 10s")
        rows[4].update(outcome="failed", t_submitted="", t_confirmed="", block="", gas_used="",
                       effective_gas_price="", error="channel broken: an earlier update is unresolved")
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
        held = [str(10**18 - 4 * PRICE), str(4 * PRICE)]

        def broken(l):
            l.update(complete=False, close_error="close: channel broken: version 4 is not signed here",
                     channel_before_close={"version": 3, "balances_wei": [str(10**18 - 3 * PRICE), str(3 * PRICE)],
                                           "broken": True})
            del l["close"]
        edit_json(run, "results/c2/lifecycle.json", broken)

        def sensor(r):
            r.update(complete=False, exit_reason="settle-deadline")
            r["channels"][1].update(accepted_updates=4, payments=4, last_version=4, balances_wei=held, final=False,
                                    t_final=0, t_settle_start=0, t_settle_end=0, settled=False)
            r["close_gas"].update(gas=49000, txs=1, fee_wei=str(49000 * 10**9))
        edit_json(run, "results/sensor/sensor-result.json", sensor)
        edit_json(run, "results/sensor-balance.json",
                  lambda b: b.update(l1_balance_after_wei=b["l1_balance_before_wei"] + 5 * PRICE - 49000 * 10**9))
        s = analyze_uc1.analyze(run)
        ch = s["channels"]["c2"]
        self.assertEqual(ch["unresolved_in_final_state"], 1)
        self.assertEqual((ch["final_version"], ch["broken"], ch["updates_accepted"]), (4, True, 4))
        self.assertFalse(ch["settled"])
        self.assertEqual(ch["sensor_balance_wei"], 4 * PRICE)
        self.assertEqual(ch["succeeded_wei"], 3 * PRICE)
        self.assertIn("channel broken", ch["close_error"])
        self.assertEqual(s["channels"]["c1"]["unresolved_in_final_state"], 0)
        self.assertNotIn("c2", s["close_s"])
        self.assertAlmostEqual(s["close_s"]["c1"], 6.0)
        r = s["reconciliation"]
        self.assertFalse(r["ok"])
        self.assertFalse(r["ok_explained"])                    # the held payment never reached the L1
        self.assertFalse(r["per_customer"]["c2"]["succeeded_matches_sensor"])
        self.assertIsNone(r["per_customer"]["c2"]["customer_matches_sensor"])          # no customer close
        self.assertEqual(r["per_customer"]["c2"]["explained_wei"], PRICE)
        self.assertFalse(r["l1_matches_final_balances"])       # 4 PRICE sit in the unsettled channel
        self.assertTrue(r["l1_matches_settled_channels"])
        self.assertEqual(r["difference_wei"], 5 * PRICE - 8 * PRICE)
        self.assertEqual(s["outcomes"], {"succeeded": 8, "unresolved": 1, "failed": 1})
        self.assertFalse(s["complete"])
        self.assertIn("c2", s["complete_checks"]["incomplete_customers"])
        self.assertFalse(s["complete_checks"]["sensor_result_complete"])
        self.assertEqual(s["late_executed"], {"c1": 0, "c2": 1})    # the held update, exact
        self.assertEqual(r["late_executed_wei"], PRICE)
        self.assertFalse(r["ok_with_late"])                    # no customer close, L1 short of the final state

    def test_perun_late_executions_are_counted_exactly_from_the_channel(self):
        """Final review I2 (the w10 pattern): c1's last two pays were still in the sidecar at the drain
        deadline, so the ledger says unresolved, but the sidecar ran them before quiesce: the sensor, the
        customer's close view and the L1 all hold 5. Every Perun payment is PRICE_WEI, so (sensor balance -
        succeeded) / PRICE_WEI is an exact late count; ok_with_late holds, strict ok does not."""
        run = perun_run()
        p = os.path.join(run, "results", "c1", "ledger.csv")
        with open(p) as f:
            rows = list(csv.DictReader(f))
        for r in rows[3:]:
            r.update(outcome="unresolved", t_confirmed="", block="", gas_used="", effective_gas_price="",
                     error="drain deadline")
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
        s = analyze_uc1.analyze(run)
        self.assertEqual(s["late_executed"], {"c1": 2, "c2": 0})
        self.assertEqual(s["per_customer"]["c1"]["late_executed"], 2)
        self.assertEqual(s["late_executed_total"], 2)
        self.assertEqual(s["channels"]["c1"]["unresolved_in_final_state"], 2)
        r = s["reconciliation"]
        self.assertFalse(r["ok"])
        self.assertTrue(r["ok_explained"])
        self.assertEqual(r["late_executed_wei"], 2 * PRICE)
        self.assertEqual(r["difference_wei"], 2 * PRICE)
        self.assertTrue(r["ok_with_late"])
        edit_json(run, "results/sensor-balance.json",                               # the L1 does not add up
                  lambda b: b.update(l1_balance_after_wei=b["l1_balance_after_wei"] - 1))
        r = analyze_uc1.analyze(run)["reconciliation"]
        self.assertEqual(r["late_executed_wei"], 2 * PRICE)
        self.assertFalse(r["ok_with_late"])

    def test_perun_payments_cost_no_gas_and_open_close_do(self):
        s = analyze_uc1.analyze(perun_run())
        g, f = s["gas"], s["fees_eth"]
        self.assertEqual(g["payment_l2_median"], 0)
        self.assertEqual(f["payments_l2"], 0)
        self.assertEqual(s["fees_unpriced_submitted_rows"], 0)
        self.assertEqual(g["deposit_l1_median"], 65000)
        self.assertAlmostEqual(f["deposit_l1"], 2 * 65000 * 10**9 / 1e18)
        self.assertEqual(g["close_l1_median"], 120000)
        self.assertAlmostEqual(f["close_l1"], 2 * 120000 * 10**9 / 1e18)
        self.assertEqual(g["close_sensor_l1_total"], 98000)
        self.assertEqual(g["close_sensor_l1_per_channel"], 49000)
        self.assertAlmostEqual(f["close_sensor_l1"], 98000 * 10**9 / 1e18)
        self.assertIsNone(g["withdraw_l2_median"])             # no L2 in a Perun run
        self.assertIsNone(f["withdraw_l2"])

    def test_perun_missing_sensor_result_is_incomplete(self):
        run = perun_run()
        os.remove(os.path.join(run, "results", "sensor", "sensor-result.json"))
        s = analyze_uc1.analyze(run)
        self.assertFalse(s["complete"])
        self.assertIsNone(s["reconciliation"])
        self.assertIn("results/sensor/sensor-result.json", s["complete_checks"]["missing"])
        self.assertIsNone(s["complete_checks"]["sensor_result_complete"])
        self.assertIsNone(s["channels"]["c1"]["final_version"])                     # still listed per customer
        run = perun_run()
        edit_json(run, "results/sensor/sensor-result.json", lambda r: r.update(complete=False))
        s = analyze_uc1.analyze(run)
        self.assertFalse(s["complete"])
        self.assertFalse(s["complete_checks"]["sensor_result_complete"])
        self.assertTrue(s["reconciliation"]["ok"])             # the money still adds up

    def test_old_arbitrum_folder_without_stack_is_unchanged(self):
        """The real-layout fixture (no STACK) gives today's summary exactly; only close_definition is new.
        The golden file was written by the analysis at 7b15516, before Perun."""
        run = tempfile.mkdtemp()
        shutil.copytree(FIXTURE, run, dirs_exist_ok=True)
        open(os.path.join(run, "experiment.env"), "w").write("WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\n")
        os.makedirs(os.path.join(run, "logs"))
        open(os.path.join(run, "logs", "bath1-sensor.log"), "w").write("{'rows': 5, 'dropped': []}\n")
        s = analyze_uc1.analyze(run)
        for k in ("analysis_commit", "analysis_measure_dirty"):
            s.pop(k)
        self.assertIn("withdrawal submission on the L2", s.pop("close_definition"))
        # the survivorship lower bound is new (2026-10-12); every payment here succeeded, so it equals the latency
        self.assertEqual(s.pop("payment_latency_lower_bound_s"), s["payment_latency_s"])
        self.assertEqual(s["headline"].pop("payment_latency_lower_bound_s"), s["payment_latency_s"])
        self.assertEqual(json.dumps(s, sort_keys=True), json.dumps(json.load(open(BEFORE_PERUN)), sort_keys=True))
        for k in ("channels", "close_finalize_s"):
            self.assertNotIn(k, s)
        open(os.path.join(run, "experiment.env"), "w").write("STACK=zksync\nWINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\n")
        self.assertIn("L2BaseToken.withdraw", analyze_uc1.analyze(run)["close_definition"])
        open(os.path.join(run, "experiment.env"), "w").write("STACK=fabric\nWINDOW_MS=1000\n")
        with self.assertRaises(ValueError):
            analyze_uc1.analyze(run)

    def test_aggregate_headline_has_close_finalize_median(self):
        perun = analyze_uc1.analyze(perun_run())
        arb = analyze_uc1.analyze(make_run([0.2]))
        get = aggregate.HEADLINE["close_finalize_s_median"]
        self.assertAlmostEqual(aggregate._get(get, perun), 0.05)
        self.assertIsNone(aggregate._get(get, arb))

    def test_aggregate_warns_about_close_finalize_only_for_perun_runs(self):
        arb = [make_run([0.2]), make_run([0.3])]
        for r in arb:
            summarize(r)
        agg = aggregate.aggregate(arb)
        self.assertEqual(agg["close_finalize_s_median"]["n"], 0)
        self.assertFalse([w for w in agg["_warnings"] if w.startswith("close_finalize_s_median")])
        runs = [perun_run(), perun_run()]
        for r in runs:
            json.dump({"git_commit": "abc", "git_dirty": False, "code_hash": "h", "experiment": {"STACK": "perun"}},
                      open(os.path.join(r, "manifest.json"), "w"))
            summarize(r)
        edit_json(runs[1], "summary.json", lambda s: s.update(close_finalize_s={}))
        agg = aggregate.aggregate(runs)
        self.assertAlmostEqual(agg["close_finalize_s_median"]["runs"][0], 0.05)
        self.assertIsNone(agg["close_finalize_s_median"]["runs"][1])
        self.assertTrue(any(w.startswith("close_finalize_s_median") for w in agg["_warnings"]))
