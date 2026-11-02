"""The AK-style weapon and the no-negative rule (cluster spec §7.3)."""
import unittest

from arena.world import Config, Controls, World


def hold(world, ticks, pid=0):
    fires = []
    for _ in range(ticks):
        ev = world.step({pid: Controls(fire=True, drive_angle=world.tanks[pid].heading)})
        fires += [e for e in ev if e["type"] == "fire" and e["player"] == pid]
    return fires


class WeaponTest(unittest.TestCase):
    def world(self, **kw):
        return World(n_players=1, tickrate=20, seed=1, config=Config(enable_blocks=False, **kw))

    def test_holding_fires_every_second_tick(self):
        fires = hold(self.world(), 10)
        self.assertEqual([e["tick"] for e in fires], [1, 3, 5, 7, 9])

    def test_magazine_then_reload(self):
        w = self.world()
        fires = hold(w, 200)
        ticks = [e["tick"] for e in fires]
        self.assertEqual(ticks[29], 59)                 # 30th bullet
        self.assertEqual(ticks[30], 59 + 50)            # 50 ticks of reload after the last bullet
        self.assertEqual(fires[29]["payload"]["ammo_left"], 0)

    def test_tap_fires_one_bullet(self):
        w = self.world()
        ev = w.step({0: Controls(fire=True, drive_angle=w.tanks[0].heading)})
        ev += w.step({0: Controls(fire=False, drive_angle=w.tanks[0].heading)})
        self.assertEqual(sum(e["type"] == "fire" for e in ev), 1)

    def test_no_shot_below_the_shot_cost(self):
        w = self.world(start_tokens=2)
        fires = hold(w, 20)
        self.assertEqual(len(fires), 2)
        self.assertEqual(w.balances[0], 0)

    def test_hit_moves_at_most_the_victims_balance(self):
        w = World(n_players=2, tickrate=20, seed=3, config=Config(enable_blocks=False))
        w.balances[1] = 3
        w.tanks[1].x, w.tanks[1].y = w.tanks[0].x + 100, w.tanks[0].y
        w.tanks[0].heading = w.tanks[0].turret_heading = 0.0
        hits = []
        for _ in range(20):
            ev = w.step({0: Controls(fire=True, drive_angle=0.0), 1: Controls(drive_angle=w.tanks[1].heading)})
            hits += [e for e in ev if e["type"] == "hit"]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["payload"]["amount"], 3)
        self.assertTrue(all(h["payload"]["amount"] == 0 for h in hits[1:]))
        self.assertGreaterEqual(w.balances[1], 0)
