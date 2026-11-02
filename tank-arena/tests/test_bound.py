"""The rules-derived points bound (cluster spec §7.3 *Perun channel capacity*)."""
import unittest

from arena.bound import amounts, max_shots
from arena.world import Config


class BoundTest(unittest.TestCase):
    def test_max_shots_matches_the_tick_arithmetic(self):
        # first bullet at tick 1, a magazine every 108 ticks (29 gaps of 2 + 50 reload):
        # 11 full magazines up to tick 1188, then bullets at 1189..1199 (6)
        self.assertEqual(max_shots(Config(), 20, 1200), 11 * 30 + 6)

    def test_amounts_and_condition(self):
        a = amounts(Config(), 20, 1200, 6)
        s = a["S"]
        self.assertEqual(a["virtual_stake"], 5 * s)
        self.assertEqual(a["hub_deposit"], 5 * 5 * s)
        self.assertEqual(a["player_need"], (1 + 5 * 5) * s)
        self.assertTrue(a["respawn_ok"])
        self.assertTrue(a["ok"])                        # 26 * 336 = 8 736 <= 10 000

    def test_condition_fails_when_the_balance_is_too_small(self):
        self.assertFalse(amounts(Config(start_tokens=5000), 20, 1200, 6)["ok"])

    def test_respawn_shorter_than_a_magazine_cycle_breaks_the_bound(self):
        self.assertFalse(amounts(Config(respawn_s=1.0), 20, 1200, 6)["respawn_ok"])
