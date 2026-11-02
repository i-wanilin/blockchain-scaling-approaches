"""results_table.py extracts exactly the metrics of metrics.md, and its numbers are the spec's.

Runs on the committed final runs (results/pi/), not on fixtures: the point is the thesis numbers.

    .venv/bin/python -m unittest discover -s analysis/tests
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import results_table as tt  # noqa: E402

METRICS_MD = os.path.join(os.path.dirname(HERE), "metrics.md")


def ids_in_metrics_md():
    """Every id in the 'id' column of the tables in metrics.md, with the short forms expanded:
    `cpu_bath0_mean` / `_max`, `undervolted_bath4` / `6` / `7`, `undervolted_bathN` (bath4..bath7)."""
    ids, col = set(), None
    with open(METRICS_MD) as f:
        lines = f.read().splitlines()
    for line in lines:
        if not line.startswith("|"):
            col = None
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if col is None:                                  # a table's header row
            col = cells.index("id") if "id" in cells else -1
            continue
        if col < 0 or set(cells[0]) <= set("-"):
            continue
        tokens = re.findall(r"`([^`]+)`", cells[col])
        first = tokens[0] if tokens else None
        for t in tokens:
            if t.startswith("_"):                        # `_max` after `cpu_bath0_mean`
                ids.add(first.rsplit("_", 1)[0] + t)
            elif t.isdigit():                            # `6` after `undervolted_bath4`
                ids.add(first[:-1] + t)
            elif t.endswith("_bathN"):
                ids.update(t[:-1] + n for n in "4567")
            else:
                ids.add(t)
    return ids


class MetricList(unittest.TestCase):
    def test_code_extracts_exactly_the_reviewed_list(self):
        code = ({m[0] for m in tt.UC1_METRICS + tt.UC2_METRICS} | {tt.UC1_PER_BOARD[0]}
                | {m[0] for m in tt.UC2_HUB_METRICS} | {tt.UC1_BLOCK_METRIC[0]}
                | {m[0] for m in tt.UC1_DEADLINE_METRICS} | {tt.UC1_FLOOR_METRIC[0]})
        self.assertEqual(code, ids_in_metrics_md())


class ThesisNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cells = tt.cells()
        tt.check_runs(cls.cells)
        cls.rows = tt.build_rows(cls.cells)

    def get(self, uc, system, metric, window="", board=""):
        hits = [r for r in self.rows if (r["use_case"], r["system"], r["window"], r["metric"], r["board"])
                == (uc, system, window, metric, board)]
        self.assertEqual(len(hits), 1, (uc, system, window, metric, board))
        return hits[0]

    def test_final_runs(self):
        self.assertEqual(len(self.cells), 12)
        self.assertEqual(sum(len(c[3]) for c in self.cells), 36)

    def test_equal_to_the_aggregates(self):
        self.assertEqual(tt.check_against_aggregates(self.rows), 150)

    def test_uc1_spec_table(self):
        # spec §8, "Final UC1 series": mean ± sd, n = 3
        self.assertAlmostEqual(self.get("uc1", "perun", "latency_p50", "w100")["mean"], 0.0350, places=4)
        self.assertEqual(round(self.get("uc1", "zksync", "latency_lb_p50", "w100")["mean"]), 444)
        self.assertEqual(round(self.get("uc1", "arbitrum", "latency_p99", "w100")["rep1"], 1), 56.4)
        for w, goodput in (("w1000", 4.12), ("w100", 4.66), ("w10", 4.73)):
            self.assertEqual(round(self.get("uc1", "zksync", "goodput", w)["mean"], 2), goodput)
        self.assertEqual(round(self.get("uc1", "perun", "close_confirmed_s", "w100")["mean"], 3), 2.543)
        self.assertEqual(round(self.get("uc1", "arbitrum", "withdrawal_submitted_s", "w1000")["mean"], 3), 0.397)
        self.assertEqual(self.get("uc1", "arbitrum", "gas_payment", "w1000")["mean"], 21000)
        uv = self.get("uc1", "perun", "undervolted_bath4", "w10")
        self.assertEqual((round(uv["min"], 2), round(uv["max"], 2)), (0.91, 0.93))

    def test_uc2_spec_table(self):
        # spec §8, "UC2 series": the guest p99 by kind that no aggregate had
        fire, hit = self.get("uc2", "perun", "guests_fire_p99"), self.get("uc2", "perun", "guests_hit_p99")
        self.assertEqual((round(fire["mean"]), round(fire["sd"])), (82, 9))
        self.assertEqual((round(hit["mean"]), round(hit["sd"])), (315, 259))
        for system, gas in (("perun", 0), ("arbitrum", 34398), ("zksync", 85549)):
            self.assertEqual(self.get("uc2", system, "gas_per_shot")["mean"], gas)
        for system, cpu in (("perun", 40), ("arbitrum", 42), ("zksync", 37)):
            self.assertEqual(round(self.get("uc2", system, "cpu_others_max")["mean"]), cpu)

    def test_counterpart(self):
        # metrics.md E: the hub's log line of rep1 reads ledger_open=646104 gas, ledger_settle=326880 gas
        self.assertEqual(self.get("uc2", "perun", "hub_gas_open_per_channel")["rep1"], 646104 / 6)
        self.assertEqual(self.get("uc2", "perun", "hub_gas_close_per_channel")["rep1"], 326880 / 6)
        self.assertEqual(self.get("uc1", "perun", "sensor_gas_close_per_channel", "w100")["rep1"], 42816)

    def test_block_interval(self):
        # metrics.md F: at 50 payments per second every Arbitrum block carries payments,
        # so the spacing is the sequencer's 250 ms gap (3589 blocks in 900 s in rep1)
        r = self.get("uc1", "arbitrum", "block_interval_s", "w100")
        self.assertAlmostEqual(r["rep1"], 0.251, places=3)

    def test_deadline_sweep(self):
        # metrics.md G: Perun needs about 50 ms for 95 % of the windows at 50 per s; zkSync at 500 per s
        # confirms too few payments for any payment deadline to reach 5 %
        self.assertAlmostEqual(self.get("uc1", "perun", "deadline_for_miss_5pct", "w100")["mean"], 0.051, places=3)
        self.assertFalse([r for r in self.rows if (r["system"], r["window"], r["metric"])
                          == ("zksync", "w10", "deadline_for_miss_5pct")])
        self.assertEqual(self.get("uc1", "perun", "miss_share_floor", "w1000")["mean"], 0.0)

    def test_ranges(self):
        for system, metric, lo, hi in (("perun", "open_ledger_s", 1.4, 2.5), ("perun", "open_virtual_s", 0.6, 3.1),
                                       ("zksync", "close_s_range", 2.0, 39.7),
                                       ("perun", "close_ledger_settle_s", 2.55, 3.34),
                                       ("perun", "close_virtual_settle_s", 0.25, 1.5)):
            r = self.get("uc2", system, metric)
            self.assertEqual((r["stat"], r["n"], r["sd"]), ("range", 18, ""))
            digits = 2 if metric.startswith("close_") and metric.endswith("settle_s") else 1
            self.assertEqual((round(r["min"], digits), round(r["max"], digits)), (lo, hi))


if __name__ == "__main__":
    unittest.main()
