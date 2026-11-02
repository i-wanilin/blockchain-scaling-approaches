"""viz/board.py: one board's timing and payments from a UC2 run folder (no pygame needed)."""

import csv
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from viz.board import WAIT_MS, board_stats, load_board, resolve_player

T0 = 1000.0
RUN = "20261006-000000-arbitrum-uc2-arbitrum-rep1"
REAL_RUN = Path(__file__).resolve().parents[2] / "results/pi/uc2/20261006-195224-arbitrum-uc2-arbitrum-rep2"


def _ledger_row(tick, kind, t_gen, t_conf, outcome="succeeded"):
    return {"id": f"uc2/{RUN}/p1/t{tick}/{kind}0", "to": "arena" if kind == "fire" else "p0",
            "amount_wei": 1, "outcome": outcome, "t_intended": t_gen, "t_generated": t_gen,
            "t_queued": t_gen, "t_submitted": t_gen, "t_confirmed": t_conf if t_conf else "",
            "tx_hash": "", "block": "", "gas_used": "", "effective_gas_price": "", "attempts": 1,
            "resigned": 0, "error": ""}


def make_run(root: Path, gz: bool = False) -> Path:
    """Six ticks for p1 (bath2): tick 4 arrives 250 ms late; three shots and one hit."""
    run = root / RUN
    base = run / "results" / "p1"
    (base / "game").mkdir(parents=True)
    (base / "pc").mkdir()
    (run / "summary.json").write_text(json.dumps({"players": {"p0": "bath1", "p1": "bath2"}}))
    (run / "experiment.env").write_text("STACK=arbitrum\nPLAYERS=(bath1 bath2)\n")
    (base / "game" / "trace.jsonl").write_text('{"tick":1}\n')
    actual = [T0 + 0.05, T0 + 0.10, T0 + 0.15, T0 + 0.45, T0 + 0.46, T0 + 0.50]
    with open(base / "game" / "ticks.csv", "w", newline="") as fh:
        fh.write("tick,t_intended,t_actual\n")
        for k, a in enumerate(actual, 1):
            fh.write(f"{k},{T0 + 0.05 * k:.6f},{a:.6f}\n")
    rows = [_ledger_row(1, "fire", T0 + 0.05, T0 + 0.08),      # paid before the next shot
            _ledger_row(2, "fire", T0 + 0.10, T0 + 0.47),      # paid after the next shot (0.45): a miss
            _ledger_row(3, "hit", T0 + 0.15, T0 + 0.20),
            _ledger_row(4, "fire", T0 + 0.45, None, "unresolved")]   # never paid; the last shot is not judged
    path = base / "pc" / "ledger.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    if gz:
        for p in (path, base / "game" / "ticks.csv"):
            with open(p, "rb") as src, gzip.open(str(p) + ".gz", "wb") as dst:
                dst.write(src.read())
            p.unlink()
    return run


class TestBoardView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = make_run(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_player_by_key_number_or_board(self):
        for who in ("p1", "1", "bath2"):
            self.assertEqual(resolve_player(self.run, who), ("p1", "bath2"))
        with self.assertRaises(ValueError):
            resolve_player(self.run, "bath9")

    def test_frames_follow_the_recorded_tick_times(self):
        v = load_board(self.run, "p1")
        self.assertEqual(v.last, 6)
        self.assertEqual(v.frame_at(T0 + 0.149), 2)
        self.assertEqual(v.frame_at(T0 + 0.30), 3)        # tick 4 has not arrived yet: frozen on 3
        self.assertAlmostEqual(v.waiting_ms(T0 + 0.30), 150.0, places=3)
        self.assertEqual(v.frame_at(T0 + 9), 6)
        self.assertEqual(v.waiting_ms(T0 + 9), 0.0)       # after the last tick nothing is awaited
        self.assertAlmostEqual(v.lag_ms[4], 250.0, places=3)

    def test_payment_state_at_a_moment(self):
        v = load_board(self.run, "p1")
        st = v.payment_state(T0 + 0.16)
        self.assertEqual((st["generated"], st["paid"], st["waiting"]), (3, 1, 2))
        self.assertAlmostEqual(st["last_latency_s"], 0.03, places=6)
        end = v.payment_state(float("inf"))
        self.assertEqual((end["generated"], end["paid"], end["waiting"]), (4, 3, 1))

    def test_next_shot_as_the_analysis_judges_it(self):
        v = load_board(self.run, "p1")
        self.assertEqual(v.next_shot(), (1, 2))           # shot 1 on time, shot 2 late, shot 4 last
        self.assertEqual(v.next_shot(T0 + 0.12), (1, 1))  # shot 2 not judged before shot 4 is fired

    def test_stats(self):
        s = board_stats(load_board(self.run, "bath2"))
        self.assertEqual(s["waits_over_100ms"], 1)
        self.assertAlmostEqual(s["longest_wait_ms"], 300.0, places=3)
        self.assertEqual((s["paid"], s["never_paid"]), (3, 1))
        self.assertGreater(s["longest_wait_ms"], WAIT_MS)

    def test_gzipped_run_files(self):
        with tempfile.TemporaryDirectory() as d:
            v = load_board(make_run(Path(d), gz=True), "p1")
            self.assertEqual((v.last, len(v.payments)), (6, 4))


@unittest.skipUnless(REAL_RUN.is_dir(), "committed UC2 run folder not present")
class TestCommittedRun(unittest.TestCase):
    def test_next_shot_matches_the_analysis_for_every_player(self):
        summary = json.loads((REAL_RUN / "summary.json").read_text())
        per = summary["next_shot_miss_share"]["per_player"]
        for p in summary["players"]:
            ok, judged = load_board(REAL_RUN, p).next_shot()
            self.assertEqual((judged, judged - ok), (per[p]["shots"], per[p]["misses"]), p)

    def test_board_log_is_the_reference(self):
        from viz.match import UC2_REFERENCE_SHA256
        self.assertEqual(load_board(REAL_RUN, "p3").trace_sha256, UC2_REFERENCE_SHA256)


if __name__ == "__main__":
    unittest.main()
