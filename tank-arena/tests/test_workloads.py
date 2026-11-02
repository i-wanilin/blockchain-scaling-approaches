"""Workload generators: schema conformance and cadence."""

import unittest

import workloads
from arena.sim import iter_sim
from workloads import game


class TestGameReplay(unittest.TestCase):
    def test_mapping(self):
        arena_events = list(iter_sim(4, 20, 10.0, 42))
        evs = list(game.from_events(arena_events))
        self.assertEqual(len(evs), len(arena_events))
        for ev in evs:
            workloads.validate(ev)
        moves = [e for e in evs if e["type"] == "message"]
        pays = [e for e in evs if e["type"] == "payment"]
        self.assertTrue(moves and pays)
        self.assertEqual({e["receiver"] for e in moves}, {"*"})
        breaks = [e for e in moves if e["meta"].get("for") == "block-break"]
        self.assertTrue(breaks)
        self.assertEqual({e["receiver"] for e in breaks}, {"*"})
        shot_pays = [e for e in pays if e["meta"]["for"] == "shot"]
        dmg_pays = [e for e in pays if e["meta"]["for"] == "damage"]
        self.assertEqual({e["receiver"] for e in shot_pays}, {"arena"})
        for e in dmg_pays:  # victim -> shooter
            self.assertTrue(e["sender"].startswith("player-"))
            self.assertTrue(e["receiver"].startswith("player-"))


