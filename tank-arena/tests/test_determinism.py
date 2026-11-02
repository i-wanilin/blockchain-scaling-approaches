"""Determinism: same (seed, N, tickrate, duration) => identical trace."""

import hashlib
import json
import unittest

from arena.sim import iter_sim


def trace_hash(players=4, tickrate=20, duration=5.0, seed=42) -> str:
    h = hashlib.sha256()
    for ev in iter_sim(players, tickrate, duration, seed):
        h.update(json.dumps(ev, separators=(",", ":")).encode())
        h.update(b"\n")
    return h.hexdigest()


class TestDeterminism(unittest.TestCase):
    def test_same_seed_identical_trace(self):
        self.assertEqual(trace_hash(seed=42), trace_hash(seed=42))

    def test_different_seed_different_trace(self):
        self.assertNotEqual(trace_hash(seed=42), trace_hash(seed=43))

    def test_different_players_different_trace(self):
        self.assertNotEqual(trace_hash(players=4), trace_hash(players=5))

    def test_trace_is_nonempty_and_has_all_types(self):
        types = {ev["type"] for ev in iter_sim(6, 20, 20.0, 42)}
        self.assertEqual(types, {"move", "fire", "hit", "break"})


if __name__ == "__main__":
    unittest.main()
