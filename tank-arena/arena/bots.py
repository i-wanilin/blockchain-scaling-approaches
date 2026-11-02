"""Deterministic four-direction autonomous tank agents.

The visual game and the workload generator share this controller.  Agents move
only along one cardinal axis per tick and fire only along the hull heading—the
same rules as a human multiplayer client.  A small deterministic stuck detector
chooses a perpendicular detour when a wall or another tank blocks progress.

Two deliberately different strategies share those physical rules:
``HunterBot`` closes on the nearest opponent, while ``FlankerBot`` hunts a
damaged opponent, establishes the shorter-axis firing lane, holds an ambush
range, and backs away when crowded.  Player-id parity provides a stable 50/50
split without adding anything to the lockstep wire protocol.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from random import Random

from arena.util import dist2
from arena.world import Controls, Tank, World

ENGAGE_RANGE = 320.0
ALIGN_LANE = 34.0
STUCK_TICKS = 4
DETOUR_S = 0.75
WANDER_PERIOD_S = 1.5
FLANK_HOLD_MIN = 120.0
DIRECTION_COMMIT_S = 0.60
CARDINAL = (0.0, math.pi / 2.0, math.pi, -math.pi / 2.0)


@dataclass(frozen=True)
class FireParams:
    """When a bot holds the trigger (frozen by Task 3, see TUNING.md)."""

    plausible_lane: float = 40.0   # off the line of fire, still a plausible hit
    lead_ticks: int = 6            # opponent entering the lane within this counts
    long_range: float = 240.0      # beyond it a bot taps instead of bursting
    burst_min: int = 3             # bullets per burst
    burst_max: int = 8
    spec_p: float = 0.02           # per-tick speculative tap, in range, no hit
    release_ticks: int = 4         # trigger released after every tap/burst
    # (4 ticks = 0.2 s at 20 Hz, two weapon cadences; provisional, Task 3)


class Bot:
    """Direct Hunter strategy: close on the nearest live opponent."""

    strategy = "HUNTER"

    def __init__(self, pid: int, seed: int, tickrate: int,
                 fire: FireParams | None = None) -> None:
        self.pid = pid
        self.fire = fire if fire is not None else FireParams()
        self._burst_left = 0
        self._release_left = 0
        self.rng = Random(f"bot-{seed}-{pid}")
        self.tickrate = tickrate
        self._last_pos: tuple[float, float] | None = None
        self._was_driving = False
        self._stuck_ticks = 0
        self._detour_ticks = 0
        self._detour_heading = 0.0
        self._wander_ticks = 0
        self._wander_heading = self.rng.choice(CARDINAL)
        self._committed_heading: float | None = None
        self._heading_hold_ticks = 0

    def decide(self, world: World) -> Controls:
        me = world.tanks[self.pid]
        if not me.alive:
            self._last_pos = None
            self._was_driving = False
            self._committed_heading = None
            self._heading_hold_ticks = 0
            self._burst_left = 0
            self._release_left = 0
            return Controls(drive_angle=me.heading,
                            fire_angle=me.heading)

        self._observe_motion(me)
        enemy = self._nearest_enemy(world, me)
        if enemy is None:
            return self._wander(me)

        dx, dy = enemy.x - me.x, enemy.y - me.y
        distance = math.sqrt(dx * dx + dy * dy)
        desired = self._cardinal_toward(dx, dy)

        candidate = self._detoured_heading(desired)
        heading = self._commit_heading(
            candidate, force=self._detour_ticks > 0)

        # The world's cadence and magazine decide when a held trigger fires.
        fire = self._trigger(world, me, heading)
        if not fire and self._stuck_ticks > 0 and distance <= ENGAGE_RANGE:
            fire = True     # shoot at whatever blocks the way
        self._was_driving = True
        return Controls(throttle=1.0, fire=fire, fire_angle=heading,
                        drive_angle=heading)

    def _trigger(self, world: World, me: Tank, heading: float) -> bool:
        """Hold the trigger for a burst, tap, or take a rare speculative shot.

        Both draws happen every tick, whatever the branch, so the random
        stream never depends on game state.
        """
        p = self.fire
        speculative = self.rng.random()
        burst_roll = self.rng.random()
        if self._burst_left > 0:
            self._burst_left -= 1
            if self._burst_left == 0:
                self._release_left = p.release_ticks
            return True
        if self._release_left > 0:
            self._release_left -= 1
            return False
        enemy = self._nearest_enemy(world, me)
        if enemy is None:
            return False
        dx, dy = enemy.x - me.x, enemy.y - me.y
        distance = math.hypot(dx, dy)
        if distance > ENGAGE_RANGE:
            return False
        if self._plausible(enemy, dx, dy, heading, 1.0 / self.tickrate):
            if distance > p.long_range:
                self._release_left = p.release_ticks
                return True
            span = p.burst_max - p.burst_min + 1
            length = p.burst_min + min(span - 1, int(burst_roll * span))
            self._burst_left = length - 1
            if self._burst_left == 0:
                self._release_left = p.release_ticks
            return True
        if speculative < p.spec_p:
            self._release_left = p.release_ticks
            return True
        return False

    def _plausible(self, enemy: Tank, dx: float, dy: float,
                   heading: float, dt: float) -> bool:
        """Is the enemy in, or about to enter, the lane ahead of the hull?"""
        fx, fy = math.cos(heading), math.sin(heading)
        vx = math.cos(enemy.heading) * enemy.speed * dt
        vy = math.sin(enemy.heading) * enemy.speed * dt
        for t in range(self.fire.lead_ticks + 1):
            px, py = dx + vx * t, dy + vy * t
            if (px * fx + py * fy > 0.0
                    and abs(-px * fy + py * fx) <= self.fire.plausible_lane):
                return True
        return False

    def _observe_motion(self, me: Tank) -> None:
        if self._last_pos is not None and self._was_driving:
            moved2 = dist2(me.x, me.y, *self._last_pos)
            self._stuck_ticks = (self._stuck_ticks + 1
                                 if moved2 < 0.25 else 0)
        else:
            self._stuck_ticks = 0
        self._last_pos = (me.x, me.y)

    def _nearest_enemy(self, world: World, me: Tank) -> Tank | None:
        candidates = [tank for pid, tank in world.tanks.items()
                      if pid != self.pid and tank.alive]
        return min(candidates,
                   key=lambda tank: (dist2(me.x, me.y, tank.x, tank.y),
                                     tank.pid),
                   default=None)

    def _detoured_heading(self, desired: float) -> float:
        """Apply the shared deterministic escape behavior to a heading."""
        if self._detour_ticks > 0:
            self._detour_ticks -= 1
            return self._detour_heading
        if self._stuck_ticks >= STUCK_TICKS:
            side = self.rng.choice((-1.0, 1.0))
            heading = self._snap_cardinal(desired + side * math.pi / 2.0)
            self._detour_heading = heading
            self._detour_ticks = max(1, round(DETOUR_S * self.tickrate))
            self._stuck_ticks = 0
            return heading
        return desired

    def _commit_heading(self, desired: float, force: bool = False) -> float:
        """Prevent legal cardinal inputs from becoming fake diagonal travel.

        A voluntary turn requires a short straight run. Collision detours may
        override the hold immediately so an agent cannot remain pinned to a
        wall. The value is tick-based and therefore deterministic in lockstep.
        """
        desired = self._snap_cardinal(desired)
        if self._committed_heading is None:
            self._committed_heading = desired
            self._heading_hold_ticks = max(
                1, round(DIRECTION_COMMIT_S * self.tickrate))
        elif desired != self._committed_heading:
            if force or self._heading_hold_ticks <= 0:
                self._committed_heading = desired
                self._heading_hold_ticks = max(
                    1, round(DIRECTION_COMMIT_S * self.tickrate))
            else:
                self._heading_hold_ticks -= 1
        elif self._heading_hold_ticks > 0:
            self._heading_hold_ticks -= 1
        return self._committed_heading

    @staticmethod
    def _cardinal_toward(dx: float, dy: float) -> float:
        if abs(dx) >= abs(dy):
            return 0.0 if dx >= 0.0 else math.pi
        return math.pi / 2.0 if dy >= 0.0 else -math.pi / 2.0

    @staticmethod
    def _snap_cardinal(angle: float) -> float:
        quarter = round(angle / (math.pi / 2.0)) % 4
        return CARDINAL[quarter]

    def _wander(self, me: Tank) -> Controls:
        self._wander_ticks -= 1
        if self._wander_ticks <= 0:
            self._wander_ticks = max(
                1, round(WANDER_PERIOD_S * self.tickrate))
            self._wander_heading = self.rng.choice(CARDINAL)
        self._was_driving = True
        return Controls(throttle=0.7, fire_angle=self._wander_heading,
                        drive_angle=self._wander_heading)


class FlankerBot(Bot):
    """Lane-control strategy: flank, ambush weak targets, avoid brawling."""

    strategy = "FLANKER"

    def decide(self, world: World) -> Controls:
        me = world.tanks[self.pid]
        if not me.alive:
            self._last_pos = None
            self._was_driving = False
            self._committed_heading = None
            self._heading_hold_ticks = 0
            self._burst_left = 0
            self._release_left = 0
            return Controls(drive_angle=me.heading,
                            fire_angle=me.heading)

        self._observe_motion(me)
        enemy = self._weakest_enemy(world, me)
        if enemy is None:
            return self._wander(me)

        dx, dy = enemy.x - me.x, enemy.y - me.y
        distance = math.hypot(dx, dy)
        horizontal_lane = abs(dy) <= ALIGN_LANE
        vertical_lane = abs(dx) <= ALIGN_LANE
        in_lane = horizontal_lane or vertical_lane
        backing = False

        if in_lane:
            aim = self._cardinal_toward(dx, dy)
            if distance < FLANK_HOLD_MIN:
                # A fixed-gun tank cannot retreat and fire behind itself.
                desired = self._snap_cardinal(aim + math.pi)
                throttle = 1.0
                backing = True
            elif distance <= ENGAGE_RANGE:
                # Stop in the lane: stationary ambush fire is the important
                # contrast with the Hunter's permanent forward pressure.
                desired = aim
                throttle = 0.0
            else:
                desired = aim
                throttle = 1.0
        else:
            # Reduce the smaller coordinate difference first. This produces
            # a perpendicular L-shaped flank instead of direct pursuit along
            # the dominant axis used by HunterBot.
            if abs(dx) < abs(dy):
                desired = 0.0 if dx >= 0.0 else math.pi
            elif abs(dy) < abs(dx):
                desired = math.pi / 2.0 if dy >= 0.0 else -math.pi / 2.0
            else:
                desired = self.rng.choice((
                    0.0 if dx >= 0.0 else math.pi,
                    math.pi / 2.0 if dy >= 0.0 else -math.pi / 2.0))
            throttle = 1.0

        heading = self._detoured_heading(desired)
        heading = self._commit_heading(
            heading, force=self._detour_ticks > 0)
        # A detour changes the hull/gun direction, so suppress a shot that was
        # computed for the original firing lane.
        fire = self._trigger(world, me, heading)
        fire = fire and not backing and heading == desired
        self._was_driving = throttle != 0.0
        return Controls(throttle=throttle, fire=fire, fire_angle=heading,
                        drive_angle=heading)

    def _weakest_enemy(self, world: World, me: Tank) -> Tank | None:
        candidates = [tank for pid, tank in world.tanks.items()
                      if pid != self.pid and tank.alive]
        return min(candidates,
                   key=lambda tank: (tank.hp,
                                     dist2(me.x, me.y, tank.x, tank.y),
                                     tank.pid),
                   default=None)


def make_bot(pid: int, seed: int, tickrate: int,
             fire: FireParams | None = None) -> Bot:
    """Return the strategy assigned to this stable player-id slot."""
    cls = Bot if pid % 2 == 0 else FlankerBot
    return cls(pid, seed, tickrate, fire)


def make_bots(n_players: int, seed: int, tickrate: int,
              fire: FireParams | None = None) -> dict[int, Bot]:
    return {pid: make_bot(pid, seed, tickrate, fire)
            for pid in range(n_players)}
