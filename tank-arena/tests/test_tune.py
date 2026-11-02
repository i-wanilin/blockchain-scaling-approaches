import unittest

from arena.tune import accept, config_digest, match_stats


class TuneTest(unittest.TestCase):
    def test_accept_rules(self):
        ok = {"shots_per_player": [5] * 6, "hit_share": 0.2, "kills": 2, "first_kill_tick": 61}
        self.assertEqual(accept(ok), [])
        self.assertTrue(accept({**ok, "hit_share": 0.05}))
        self.assertTrue(accept({**ok, "kills": 0}))
        self.assertTrue(accept({**ok, "first_kill_tick": 60}))
        self.assertTrue(accept({**ok, "shots_per_player": [5, 5, 5, 5, 5, 0]}))

    def test_stats_are_deterministic(self):
        self.assertEqual(match_stats(7), match_stats(7))

    def test_frozen_defaults_pass_on_every_seed(self):
        for seed in list(range(1, 21)) + [42]:
            self.assertEqual(accept(match_stats(seed)), [], f"seed {seed}")

    def test_digest_changes_with_damage(self):
        from arena.world import Config
        self.assertNotEqual(config_digest(Config(), None), config_digest(Config(projectile_damage=17), None))
