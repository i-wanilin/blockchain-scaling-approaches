"""Collision math, bounds, damage payments, kill/respawn."""

import math
import unittest

from arena.world import Block, Config, Controls, Projectile, World


def make_world(n=2, tickrate=20, seed=1) -> World:
    w = World(n_players=n, tickrate=tickrate, seed=seed)
    # park the tanks far apart, facing +x, motionless
    for pid in range(n):
        t = w.tanks[pid]
        t.x, t.y = 100.0 + 300.0 * pid, 300.0
        t.heading, t.turret_heading, t.speed = 0.0, 0.0, 0.0
    w.projectiles.clear()
    return w


def static_projectile(w: World, owner: int, x: float, y: float) -> Projectile:
    return Projectile(owner=owner, x=x, y=y, vx=0.0, vy=0.0, ttl_ticks=100)


class TestCollision(unittest.TestCase):
    def test_projectile_inside_radius_hits(self):
        w = make_world()
        r = w.config.tank_radius + w.config.projectile_radius
        w.projectiles.append(
            static_projectile(w, 0, 400.0 - (r - 0.5), 300.0))
        events = w.step({})
        hits = [e for e in events if e["type"] == "hit"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["payload"]["victim"], 1)
        self.assertEqual(hits[0]["payload"]["shooter"], 0)
        self.assertEqual(w.tanks[1].hp,
                         w.config.max_hp - w.config.projectile_damage)

    def test_projectile_outside_radius_misses(self):
        w = make_world()
        r = w.config.tank_radius + w.config.projectile_radius
        w.projectiles.append(
            static_projectile(w, 0, 400.0 - (r + 0.5), 300.0))
        events = w.step({})
        self.assertEqual([e for e in events if e["type"] == "hit"], [])
        self.assertEqual(len(w.projectiles), 1)  # still flying

    def test_no_self_hit(self):
        w = make_world()
        w.projectiles.append(static_projectile(w, 0, 100.0, 300.0))
        events = w.step({})
        self.assertEqual([e for e in events if e["type"] == "hit"], [])

    def test_hit_transfers_tokens_victim_to_shooter(self):
        w = make_world()
        r = w.config.tank_radius
        w.projectiles.append(static_projectile(w, 0, 400.0 - r, 300.0))
        before = dict(w.balances)
        w.step({})
        pay = w.config.hit_payment
        self.assertEqual(w.balances[0], before[0] + pay)
        self.assertEqual(w.balances[1], before[1] - pay)

    def test_fire_spends_ammo_and_pays_arena(self):
        w = make_world()
        before_ammo = w.tanks[0].ammo
        before = dict(w.balances)
        events = w.step({0: Controls(fire=True, fire_angle=0.0)})
        fires = [e for e in events if e["type"] == "fire"]
        self.assertEqual(len(fires), 1)
        self.assertEqual(w.tanks[0].ammo, before_ammo - 1)
        self.assertEqual(w.balances[0], before[0] - w.config.shot_cost)
        self.assertEqual(w.balances["arena"],
                         before["arena"] + w.config.shot_cost)
        self.assertGreater(w.tanks[0].cooldown_ticks, 0)

    def test_cooldown_blocks_second_shot(self):
        w = make_world()
        w.step({0: Controls(fire=True)})
        events = w.step({0: Controls(fire=True)})
        self.assertEqual([e for e in events if e["type"] == "fire"], [])

    def test_turret_must_traverse_before_firing(self):
        w = make_world(tickrate=20)
        w.config.hull_fixed_gun = False
        ctl = Controls(fire=True, fire_angle=3.14159265 / 2.0)
        first = w.step({0: ctl})
        self.assertEqual([e for e in first if e["type"] == "fire"], [])
        fired = []
        for _ in range(30):
            fired.extend(e for e in w.step({0: ctl})
                         if e["type"] == "fire")
            if fired:
                break
        self.assertEqual(len(fired), 1)
        self.assertAlmostEqual(
            fired[0]["payload"]["angle"], 3.14159265 / 2.0,
            delta=w.config.fire_alignment_rad)

    def test_arcade_gun_is_fixed_to_hull(self):
        w = make_world(n=1)
        tank = w.tanks[0]
        tank.heading = tank.turret_heading = 0.0
        events = w.step({0: Controls(fire=True,
                                     fire_angle=math.pi / 2.0)})
        fire = next(e for e in events if e["type"] == "fire")
        self.assertEqual(fire["payload"]["angle"], 0.0)
        self.assertEqual(tank.turret_heading, tank.heading)

    def test_default_tank_takes_27_hits(self):
        cfg = Config()
        self.assertEqual(math.ceil(cfg.max_hp / cfg.projectile_damage), 27)

    def test_fast_shell_uses_swept_collision(self):
        w = make_world(tickrate=10)
        w.blocks.clear()
        target = w.tanks[1]
        target.x, target.y = 120.0, 300.0
        # A fast shell advances farther than a tank diameter at 10 Hz,
        # crossing the target completely between point samples.
        # completely between point samples.
        w.projectiles.append(Projectile(
            owner=0, x=100.0, y=300.0, vx=400.0, vy=0.0,
            ttl_ticks=10))
        events = w.step({})
        self.assertEqual(len([e for e in events if e["type"] == "hit"]), 1)

    def test_brick_breaks_and_emits_state_event(self):
        w = make_world(n=1)
        brick = Block(900, "brick", 200.0, 200.0, 32.0)
        w.blocks = {brick.bid: brick}
        w.projectiles.append(static_projectile(w, 0, brick.x, brick.y))
        events = w.step({})
        broken = [e for e in events if e["type"] == "break"]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0]["payload"]["block"], brick.bid)
        self.assertNotIn(brick.bid, w.blocks)

    def test_stone_absorbs_shell_without_breaking(self):
        w = make_world(n=1)
        stone = Block(901, "stone", 200.0, 200.0, 32.0)
        w.blocks = {stone.bid: stone}
        w.projectiles.append(static_projectile(w, 0, stone.x, stone.y))
        events = w.step({})
        self.assertEqual([e for e in events if e["type"] == "break"], [])
        self.assertIn(stone.bid, w.blocks)
        self.assertEqual(w.projectiles, [])

    def test_cardinal_controls_stop_at_blocks(self):
        w = make_world(n=1, tickrate=20)
        stone = Block(902, "stone", 160.0, 300.0, 32.0)
        w.blocks = {stone.bid: stone}
        tank = w.tanks[0]
        tank.x, tank.y = 120.0, 300.0
        for _ in range(20):
            w.step({0: Controls(throttle=1.0, fire_angle=0.0,
                                drive_angle=0.0)})
        self.assertLessEqual(tank.x, stone.x - stone.size / 2
                             - w.config.tank_radius)

    def test_cardinal_control_round_trips_over_wire(self):
        control = Controls(throttle=1.0, fire=True, fire_angle=1.5,
                           drive_angle=1.5)
        self.assertEqual(Controls.from_wire(control.to_wire()), control)

    def test_cardinal_idle_input_stops_immediately(self):
        w = make_world(n=1)
        tank = w.tanks[0]
        tank.speed = w.config.tank_max_speed
        w.step({0: Controls(throttle=0.0, fire_angle=tank.heading,
                            drive_angle=tank.heading)})
        self.assertEqual(tank.speed, 0.0)

    def test_tanks_cannot_drive_through_each_other(self):
        w = make_world(n=2, tickrate=20)
        w.blocks.clear()
        first, second = w.tanks[0], w.tanks[1]
        first.x, first.y = 100.0, 300.0
        second.x, second.y = 145.0, 300.0
        for _ in range(20):
            w.step({0: Controls(throttle=1.0, fire_angle=0.0,
                                drive_angle=0.0),
                    1: Controls(throttle=0.0, fire_angle=math.pi,
                                drive_angle=math.pi)})
        self.assertGreaterEqual(second.x - first.x,
                                2.0 * w.config.tank_radius)
        self.assertEqual(first.speed, 0.0)

    def test_kill_and_respawn(self):
        w = make_world()
        w.tanks[1].hp = w.config.projectile_damage  # one hit kills
        r = w.config.tank_radius
        w.projectiles.append(static_projectile(w, 0, 400.0 - r, 300.0))
        events = w.step({})
        hit = next(e for e in events if e["type"] == "hit")
        self.assertTrue(hit["payload"]["killed"])
        self.assertFalse(w.tanks[1].alive)
        # dead tanks emit no move events
        moves = [e for e in events if e["type"] == "move"]
        self.assertEqual({e["player"] for e in moves}, {0})
        # step until respawn
        for _ in range(w._ticks(w.config.respawn_s)):
            events = w.step({})
        self.assertTrue(w.tanks[1].alive)
        self.assertEqual(w.tanks[1].hp, w.config.max_hp)
        self.assertIn(1, {e["player"] for e in events
                          if e["type"] == "move"})

    def test_bounds_clamp(self):
        w = make_world(n=1)
        t = w.tanks[0]
        t.x, t.y = w.config.tank_radius + 1.0, 300.0
        t.heading = 3.14159265  # facing -x, into the wall
        t.speed = w.config.tank_max_speed
        for _ in range(20):
            w.step({0: Controls(throttle=1.0)})
        self.assertGreaterEqual(t.x, w.config.tank_radius)
        self.assertLessEqual(t.x, w.config.arena_width - w.config.tank_radius)

    def test_token_supply_conserved(self):
        cfg = Config()
        w = World(n_players=4, tickrate=20, seed=7, config=cfg)
        supply = sum(w.balances.values())
        from arena.bots import make_bots
        bots = make_bots(4, 7, 20)
        for _ in range(200):
            w.step({pid: b.decide(w) for pid, b in bots.items()})
        self.assertEqual(sum(w.balances.values()), supply)


if __name__ == "__main__":
    unittest.main()
