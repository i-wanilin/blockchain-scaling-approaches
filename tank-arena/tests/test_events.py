"""Event schema conformance and stream invariants."""

import unittest

from arena import events
from arena.sim import iter_sim


class TestEventSchema(unittest.TestCase):
    def test_all_events_validate(self):
        for ev in iter_sim(4, 20, 10.0, 42):
            events.validate(ev)  # raises on violation

    def test_ticks_monotone_and_t_ms_consistent(self):
        last_tick = 0
        for ev in iter_sim(4, 20, 5.0, 42):
            self.assertGreaterEqual(ev["tick"], last_tick)
            last_tick = ev["tick"]
            self.assertAlmostEqual(ev["t_ms"],
                                   ev["tick"] * 1000.0 / 20, places=3)

    def test_move_rate_is_bounded_by_players_per_tick(self):
        per_tick: dict[int, int] = {}
        for ev in iter_sim(4, 20, 5.0, 42):
            if ev["type"] == "move":
                per_tick[ev["tick"]] = per_tick.get(ev["tick"], 0) + 1
        self.assertTrue(per_tick)
        self.assertLessEqual(max(per_tick.values()), 4)

    def test_validate_rejects_bad_events(self):
        with self.assertRaises(ValueError):
            events.validate({"tick": 1, "t_ms": 50.0, "type": "move",
                             "player": 0})  # no payload
        with self.assertRaises(ValueError):
            events.validate({"tick": 1, "t_ms": 50.0, "type": "warp",
                             "player": 0, "payload": {}})
        with self.assertRaises(ValueError):
            events.validate(events.move_event(1, 50.0, 0, 1, 2, 3, 4, 5)
                            | {"payload": {"x": 1}})

    def test_transfer_payload_directions(self):
        fire = events.fire_event(3, 150.0, 2, 0.5, 7, 1)
        self.assertEqual(fire["payload"]["from"], 2)
        self.assertEqual(fire["payload"]["to"], "arena")
        hit = events.hit_event(3, 150.0, shooter=1, victim=4,
                               damage=20, amount=5, killed=False)
        self.assertEqual(hit["player"], 1)          # shooter
        self.assertEqual(hit["payload"]["from"], 4)  # victim pays
        self.assertEqual(hit["payload"]["to"], 1)

    def test_break_is_a_valid_non_payment_state_event(self):
        broken = events.break_event(4, 200.0, player=1, block=9,
                                    kind="brick", x=100.0, y=140.0)
        events.validate(broken)
        self.assertEqual(broken["type"], "break")
        self.assertNotIn("amount", broken["payload"])


if __name__ == "__main__":
    unittest.main()
