"""Autonomous agents obey the same cardinal, hull-fixed rules as humans."""

import math
import unittest

from arena.bots import (Bot, CARDINAL, DIRECTION_COMMIT_S, FireParams,
                        FlankerBot, make_bot, make_bots)
from arena.world import Controls, World


class TestArcadeBot(unittest.TestCase):
    def test_showcase_splits_strategies_fifty_fifty(self):
        bots = make_bots(6, 1990, 20)
        self.assertEqual(sum(type(bot) is Bot for bot in bots.values()), 3)
        self.assertEqual(sum(isinstance(bot, FlankerBot)
                             for bot in bots.values()), 3)

    def test_flanker_chooses_a_different_lane_than_hunter(self):
        world = World(n_players=2, tickrate=20, seed=1990)
        world.blocks.clear()
        me, enemy = world.tanks[0], world.tanks[1]
        me.x, me.y = 100.0, 100.0
        enemy.x, enemy.y = 240.0, 180.0
        hunter = Bot(0, 1990, 20).decide(world)
        flanker = FlankerBot(0, 1990, 20).decide(world)
        self.assertEqual(hunter.drive_angle, 0.0)
        self.assertEqual(flanker.drive_angle, math.pi / 2.0)

    def test_agents_cannot_spam_axes_into_fake_diagonal_motion(self):
        for agent_type in (Bot, FlankerBot):
            bot = agent_type(0, 1990, 20)
            headings = [bot._commit_heading(0.0)]
            hold_ticks = round(DIRECTION_COMMIT_S * bot.tickrate)
            for tick in range(hold_ticks):
                desired = (math.pi / 2.0 if tick % 2 == 0 else 0.0)
                headings.append(bot._commit_heading(desired))
            self.assertEqual(set(headings), {0.0})
            self.assertEqual(bot._commit_heading(math.pi / 2.0),
                             math.pi / 2.0)

    def test_collision_detour_can_override_heading_commitment(self):
        bot = Bot(0, 1990, 20)
        self.assertEqual(bot._commit_heading(0.0), 0.0)
        self.assertEqual(bot._commit_heading(math.pi / 2.0, force=True),
                         math.pi / 2.0)

    def test_default_pacing_is_heavy_and_death_is_costly(self):
        world = World(n_players=2, tickrate=20, seed=1990)
        cfg = world.config
        self.assertEqual((cfg.arena_width, cfg.arena_height), (800.0, 720.0))
        self.assertEqual(cfg.tank_max_speed / cfg.block_size, 1.5)
        self.assertAlmostEqual(cfg.arena_width / cfg.tank_max_speed,
                               13.3333333333)
        self.assertEqual(cfg.respawn_s, 6.0)
        hits_to_kill = math.ceil(cfg.max_hp / cfg.projectile_damage)
        self.assertEqual(hits_to_kill, 27)  # ceil(160 / 6)
        self.assertAlmostEqual(
            (hits_to_kill - 1) * cfg.fire_cooldown_s, 2.6)

    def test_agent_submits_cardinal_hull_fixed_action(self):
        world = World(n_players=2, tickrate=20, seed=1990)
        world.blocks.clear()
        me, enemy = world.tanks[0], world.tanks[1]
        me.x, me.y = 100.0, 100.0
        enemy.x, enemy.y = 240.0, 180.0
        control = Bot(0, 1990, 20).decide(world)
        self.assertIn(control.drive_angle, CARDINAL)
        self.assertEqual(control.fire_angle, control.drive_angle)

    def test_agent_motion_changes_only_one_axis_per_tick(self):
        world = World(n_players=2, tickrate=20, seed=1990)
        world.blocks.clear()
        me, enemy = world.tanks[0], world.tanks[1]
        me.x, me.y = 100.0, 100.0
        enemy.x, enemy.y = 240.0, 180.0
        control = Bot(0, 1990, 20).decide(world)
        before = (me.x, me.y)
        world.step({0: control,
                    1: Controls(drive_angle=enemy.heading,
                                fire_angle=enemy.heading)})
        dx, dy = me.x - before[0], me.y - before[1]
        self.assertNotEqual(math.isclose(dx, 0.0), math.isclose(dy, 0.0))

    def test_showcase_agents_never_submit_sideways_guns(self):
        world = World(n_players=6, tickrate=20, seed=1990)
        bots = make_bots(6, 1990, 20)
        for _ in range(100):
            controls = {pid: bot.decide(world)
                        for pid, bot in bots.items()}
            for control in controls.values():
                self.assertIn(control.drive_angle, CARDINAL)
                self.assertEqual(control.fire_angle, control.drive_angle)
            events = world.step(controls)
            for event in events:
                if event["type"] == "move":
                    payload = event["payload"]
                    self.assertEqual(payload["heading"],
                                     payload["turret_heading"])


def _duel(enemy_dx, enemy_dy, tickrate=20):
    """Two tanks on an empty field: tank 0 at (100, 100) facing +x."""
    world = World(n_players=2, tickrate=tickrate, seed=1990)
    world.blocks.clear()
    me, enemy = world.tanks[0], world.tanks[1]
    me.x, me.y = 100.0, 100.0
    me.heading = me.turret_heading = 0.0
    enemy.x, enemy.y = 100.0 + enemy_dx, 100.0 + enemy_dy
    return world, me, enemy


class TriggerHappyTest(unittest.TestCase):
    def test_bursts_hold_the_trigger_on_consecutive_ticks(self):
        params = FireParams(spec_p=0.0)
        for seed in range(8):
            world, me, enemy = _duel(200.0, 0.0)
            bot = Bot(0, seed, 20, params)
            idle = Controls(drive_angle=enemy.heading,
                            fire_angle=enemy.heading)
            fires = []
            for _ in range(params.burst_max + 2):
                control = bot.decide(world)
                fires.append(control.fire)
                world.step({0: control, 1: idle})
            run = 0
            while run < len(fires) and fires[run]:
                run += 1
            self.assertGreaterEqual(run, params.burst_min, (seed, fires))

    def test_burst_outlasts_the_target_but_a_tap_does_not(self):
        params = FireParams(spec_p=0.0, burst_min=5, burst_max=5)
        world, me, enemy = _duel(200.0, 0.0)
        bot = Bot(0, 7, 20, params)
        self.assertTrue(bot._trigger(world, me, 0.0))
        enemy.y += 150.0        # target leaves the lane
        held = [bot._trigger(world, me, 0.0) for _ in range(6)]
        self.assertEqual(held, [True] * 4 + [False] * 2)

    def test_long_range_is_a_tap(self):
        params = FireParams(spec_p=0.0)
        world, me, enemy = _duel(params.long_range + 40.0, 0.0)
        bot = Bot(0, 7, 20, params)
        self.assertTrue(bot._trigger(world, me, 0.0))
        self.assertEqual(bot._burst_left, 0)
        enemy.y += 150.0        # a tap leaves no trigger hold behind
        self.assertFalse(bot._trigger(world, me, 0.0))
        # the same alignment at mid range commits to a burst
        enemy.x, enemy.y = me.x + 200.0, me.y
        bot = Bot(0, 7, 20, params)
        self.assertTrue(bot._trigger(world, me, 0.0))
        self.assertGreaterEqual(bot._burst_left, params.burst_min - 1)

    def test_target_arriving_within_lead_ticks_counts(self):
        params = FireParams(spec_p=0.0)
        # off the line by 55, closing along y at 60/s: 5 ticks to the lane edge (lane 40)
        world, me, enemy = _duel(150.0, 55.0)
        enemy.heading, enemy.speed = -math.pi / 2.0, 60.0
        self.assertTrue(Bot(0, 1, 20, params)._trigger(world, me, 0.0))
        enemy.speed = 0.0
        self.assertFalse(Bot(0, 1, 20, params)._trigger(world, me, 0.0))

    def test_speculative_shots_are_rare_and_seeded(self):
        params = FireParams()
        world, me, enemy = _duel(100.0, 100.0)   # in range, 100 off the line

        def trace(seed):
            bot = Bot(0, seed, 20, params)
            return [bot._trigger(world, me, 0.0) for _ in range(2000)]

        first = trace(42)
        self.assertEqual(first, trace(42))
        self.assertNotEqual(first, trace(43))
        share = sum(first) / len(first)
        self.assertAlmostEqual(share, params.spec_p, delta=0.01)
        # nobody in range: no shots at all
        enemy.x += 1000.0
        bot = Bot(0, 42, 20, params)
        self.assertFalse(any(bot._trigger(world, me, 0.0)
                             for _ in range(500)))

    def test_random_stream_does_not_depend_on_the_branch(self):
        quiet, busy = Bot(0, 5, 20), Bot(0, 5, 20)
        world, me, enemy = _duel(200.0, 0.0)
        far_world, far_me, _ = _duel(2000.0, 0.0)
        for _ in range(40):
            quiet._trigger(far_world, far_me, 0.0)
            busy._trigger(world, me, 0.0)
        self.assertEqual(quiet.rng.random(), busy.rng.random())

    def test_persistent_long_range_target_gets_single_spaced_taps(self):
        params = FireParams(spec_p=0.0)
        world, me, enemy = _duel(params.long_range + 40.0, 0.0)
        enemy.x = 100.0 + params.long_range + 40.0
        bot = Bot(0, 3, 20, params)
        idle = Controls(drive_angle=enemy.heading, fire_angle=enemy.heading)
        shots = fired = 0
        trace = []
        for _ in range(60):
            fire = bot._trigger(world, me, 0.0)
            trace.append(fire)
            fired += fire
            me.cooldown_ticks = 0
            me.ammo = 30
            before = len(world.projectiles)
            world.step({0: Controls(fire=fire, fire_angle=0.0,
                                    drive_angle=0.0), 1: idle})
            shots += len(world.projectiles) - before if fire else 0
            me.x, me.y = 100.0, 100.0       # hold the range
            world.projectiles.clear()
        gap = params.release_ticks
        for i, fire in enumerate(trace):
            if fire:
                self.assertEqual(trace[i + 1:i + 1 + gap], [False] * gap)
        self.assertEqual(fired, len([i for i in range(0, 60, gap + 1)]))
        self.assertEqual(shots, fired)

    def test_persistent_mid_range_target_bursts_then_releases(self):
        params = FireParams(spec_p=0.0, burst_min=4, burst_max=4)
        world, me, enemy = _duel(200.0, 0.0)
        bot = Bot(0, 3, 20, params)
        trace = [bot._trigger(world, me, 0.0) for _ in range(30)]
        cycle = [True] * 4 + [False] * params.release_ticks
        self.assertEqual(trace[:len(cycle)], cycle)
        self.assertEqual(trace, (cycle * 4)[:30])

    def test_speculative_taps_obey_the_gap(self):
        params = FireParams(spec_p=0.5)
        world, me, enemy = _duel(100.0, 100.0)
        bot = Bot(0, 9, 20, params)
        trace = [bot._trigger(world, me, 0.0) for _ in range(400)]
        for i, fire in enumerate(trace):
            if fire:
                self.assertFalse(any(trace[i + 1:i + 1 + params.release_ticks]))

    def test_same_seed_same_controls(self):
        runs = []
        for _ in range(2):
            world = World(n_players=6, tickrate=20, seed=42)
            bots = make_bots(6, 42, 20)
            trace = []
            for _ in range(300):
                controls = {pid: bot.decide(world)
                            for pid, bot in bots.items()}
                trace.append(controls)
                world.step(controls)
            runs.append(trace)
        self.assertEqual(runs[0], runs[1])
        self.assertTrue(any(c.fire for t in runs[0] for c in t.values()))

    def test_fire_params_reach_the_bots(self):
        params = FireParams(spec_p=0.5)
        self.assertEqual(make_bot(0, 1, 20).fire, FireParams())
        self.assertEqual(make_bot(1, 1, 20, params).fire, params)
        self.assertTrue(all(b.fire is params
                            for b in make_bots(4, 1, 20, params).values()))


if __name__ == "__main__":
    unittest.main()
