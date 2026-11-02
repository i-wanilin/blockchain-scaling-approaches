"""Stats aggregation: buckets, mean/peak, burstiness."""

import unittest

from arena.stats import compute_stats


def ev(t_ms, etype="move", player=0):
    return {"tick": int(t_ms // 50), "t_ms": t_ms, "type": etype,
            "player": player, "payload": {}}


class TestStats(unittest.TestCase):
    def test_known_distribution(self):
        # second 0: 4 events, second 1: 1 event, second 2: 3 events
        events = ([ev(t) for t in (10.0, 200.0, 500.0, 900.0)]
                  + [ev(1500.0, "fire")]
                  + [ev(t, "hit") for t in (2100.0, 2200.0)]
                  + [ev(2300.0)])
        s = compute_stats(events)
        self.assertEqual(s["total"], 8)
        self.assertEqual(s["duration_s"], 3)
        self.assertEqual(s["per_sec"], [4, 1, 3])
        self.assertAlmostEqual(s["mean_tx_per_sec"], 8 / 3)
        self.assertEqual(s["peak_tx_per_sec"], 4)
        self.assertAlmostEqual(s["burstiness"], 4 / (8 / 3))
        self.assertEqual(s["counts"], {"move": 5, "fire": 1, "hit": 2})
        # transfers: fire in second 1, two hits in second 2
        self.assertEqual(s["transfers_per_sec"], [0, 1, 2])
        self.assertEqual(s["transfer_peak_per_sec"], 2)
        self.assertAlmostEqual(s["transfer_burstiness"], 2 / (3 / 3))

    def test_empty_trace(self):
        s = compute_stats([])
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["duration_s"], 0)
        self.assertEqual(s["burstiness"], 0.0)

    def test_gap_seconds_count_as_zero(self):
        s = compute_stats([ev(100.0), ev(3500.0)])
        self.assertEqual(s["per_sec"], [1, 0, 0, 1])
        self.assertEqual(s["duration_s"], 4)


if __name__ == "__main__":
    unittest.main()
