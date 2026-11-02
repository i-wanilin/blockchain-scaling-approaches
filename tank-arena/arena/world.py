"""Deterministic fixed-timestep simulation core.

Top-down 2D arena on float coordinates. The default arcade controllers move
tanks along one cardinal axis per tick with a hull-fixed gun; the optional
continuous-control branch remains available for non-arcade experiments.
Projectiles use deterministic swept collision.

Determinism contract: World.step() is a pure function of
(current state, controls dict). All randomness (spawn positions) comes from
one seeded random.Random owned by the World and is consumed in a fixed
order (player-id order). Two Worlds constructed with identical arguments
and stepped with identical controls produce byte-identical event streams.

Weapon (cluster spec 7.3): AK-style. Holding fire shoots one bullet every
fire_cooldown_s (10/s) from a 30-round magazine; the reload (reload_s) starts
when the last bullet leaves and refills the magazine. No-negative rule: a shot
needs a balance of at least shot_cost, and a hit moves at most the victim's
remaining balance, so no balance ever goes below zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from random import Random
from typing import Any

from arena import events
from arena.util import ang_diff, clamp, dist2, wrap_angle


@dataclass
class Config:
    """Tuning constants. Distances in world units, times in seconds."""

    arena_width: float = 800.0
    arena_height: float = 720.0
    tank_radius: float = 14.0
    tank_max_speed: float = 60.0        # 1.5 tiles/s: deliberately heavy
    tank_max_reverse: float = 24.0      # units/s backward
    tank_accel: float = 120.0           # units/s^2
    tank_turn_rate: float = math.pi * 1.25  # rad/s
    wall_damping: float = 0.0           # speed multiplier on wall contact
    turret_turn_rate: float = math.pi * 0.9  # rad/s; independent of hull
    fire_alignment_rad: float = 0.055        # gun must be ~3.2 deg on target
    hull_fixed_gun: bool = True
    gun_length: float = 24.0
    projectile_speed: float = 320.0
    projectile_radius: float = 2.5
    projectile_ttl_s: float = 2.4
    projectile_damage: int = 6          # frozen by Task 3 (see TUNING.md)
    fire_cooldown_s: float = 0.10       # AK-style: 10 bullets/s while held
    max_hp: int = 160
    max_ammo: int = 30                  # the magazine
    reload_s: float = 2.5               # starts when the last bullet leaves
    respawn_s: float = 6.0
    start_tokens: int = 10000
    shot_cost: int = 1                  # tokens shooter -> arena per shot
    hit_payment: int = 5                # tokens victim -> shooter per hit
    block_size: float = 40.0
    enable_blocks: bool = True


@dataclass
class Controls:
    """Per-tick input of one player. All fields are clamped by the world."""

    throttle: float = 0.0     # [-1, 1]
    turn: float = 0.0         # [-1, 1]
    fire: bool = False
    fire_angle: float = 0.0   # radians; desired turret bearing
    drive_angle: float | None = None  # cardinal arcade steering when set

    def to_wire(self) -> list:
        """Compact representation for the lockstep network protocol.

        Floats are sent at full precision: JSON round-trips Python floats
        exactly, and full precision keeps a distributed lockstep run
        byte-identical to a single-process run of the same seed.
        """
        return [self.throttle, self.turn, 1 if self.fire else 0,
                self.fire_angle, self.drive_angle]

    @classmethod
    def from_wire(cls, w: list) -> "Controls":
        return cls(throttle=float(w[0]), turn=float(w[1]),
                   fire=bool(w[2]), fire_angle=float(w[3]),
                   drive_angle=(None if len(w) < 5 or w[4] is None
                                else float(w[4])))


@dataclass
class Tank:
    pid: int
    x: float
    y: float
    heading: float = 0.0
    turret_heading: float = 0.0
    speed: float = 0.0
    hp: int = 100
    ammo: int = 30
    cooldown_ticks: int = 0
    respawn_ticks: int = 0     # > 0 means dead, counting down
    reload_ticks: int = 0      # > 0 while reloading
    kills: int = 0
    deaths: int = 0

    @property
    def alive(self) -> bool:
        return self.respawn_ticks == 0


@dataclass
class Projectile:
    owner: int
    x: float
    y: float
    vx: float
    vy: float
    ttl_ticks: int


@dataclass
class Block:
    bid: int
    kind: str
    x: float
    y: float
    size: float


@dataclass
class World:
    n_players: int
    tickrate: int
    seed: int
    config: Config = field(default_factory=Config)

    tick: int = 0
    tanks: dict[int, Tank] = field(default_factory=dict)
    projectiles: list[Projectile] = field(default_factory=list)
    blocks: dict[int, Block] = field(default_factory=dict)
    balances: dict[Any, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.n_players < 1:
            raise ValueError("need at least 1 player")
        if self.tickrate < 1:
            raise ValueError("tickrate must be >= 1")
        self._rng = Random(f"world-{self.seed}")
        self.dt = 1.0 / self.tickrate
        self.balances["arena"] = 0
        if self.config.enable_blocks and not self.blocks:
            self.blocks = {b.bid: b for b in self._stage_blocks()}
        for pid in range(self.n_players):
            self.tanks[pid] = self._spawn(pid)
            self.balances[pid] = self.config.start_tokens

    # ------------------------------------------------------------- helpers

    @property
    def t_ms(self) -> float:
        return self.tick * 1000.0 / self.tickrate

    def _spawn(self, pid: int) -> Tank:
        """Place a tank at a collision-free random position (seeded RNG)."""
        c = self.config
        margin = c.tank_radius * 2.0
        for _ in range(64):
            x = self._rng.uniform(margin, c.arena_width - margin)
            y = self._rng.uniform(margin, c.arena_height - margin)
            min_d2 = (4.0 * c.tank_radius) ** 2
            clear_tanks = all(
                not (t.alive and dist2(x, y, t.x, t.y) < min_d2)
                for t in self.tanks.values())
            clear_blocks = all(not self._circle_hits_block(
                x, y, c.tank_radius + 5.0, block)
                for block in self.blocks.values())
            if clear_tanks and clear_blocks:
                break
        heading = self._rng.uniform(-math.pi, math.pi)
        heading = wrap_angle(heading)
        return Tank(pid=pid, x=x, y=y, heading=heading,
                    turret_heading=heading, hp=c.max_hp, ammo=c.max_ammo)

    def _ticks(self, seconds: float) -> int:
        return max(1, round(seconds * self.tickrate))

    def _stage_blocks(self) -> list[Block]:
        """A dense, original 20x18 arcade maze with several fighting lanes."""
        stone = {
            (9, 3), (10, 3), (0, 8), (19, 8),
            (9, 9), (10, 9), (5, 7), (14, 7),
        }
        brick: set[tuple[int, int]] = set()
        for col, rows in (
                (2, range(1, 6)), (6, range(1, 7)),
                (10, range(1, 3)), (13, range(1, 6)),
                (17, range(1, 7)),
                (2, range(12, 17)), (6, range(12, 17)),
                (13, range(12, 17)), (17, range(12, 17))):
            brick.update((col, row) for row in rows)
        brick.update({
            (8, 2), (8, 4), (9, 4), (10, 4), (11, 4),
            (3, 7), (4, 7), (6, 7), (7, 7), (8, 7),
            (11, 7), (12, 7), (13, 7), (15, 7), (16, 7),
            (3, 9), (4, 9), (6, 9), (7, 9), (8, 9),
            (11, 9), (12, 9), (13, 9), (15, 9), (16, 9),
            (8, 12), (9, 12), (10, 12), (11, 12),
            (8, 13), (11, 13), (8, 14), (11, 14),
            (8, 15), (11, 15), (9, 17), (10, 17),
        })
        blocks: list[Block] = []
        size = self.config.block_size
        for bid, (kind, cell) in enumerate(
                [("stone", cell) for cell in sorted(stone)]
                + [("brick", cell) for cell in sorted(brick)]):
            col, row = cell
            blocks.append(Block(bid, kind, col * size + size / 2.0,
                                row * size + size / 2.0, size))
        return blocks

    # ---------------------------------------------------------------- step

    def step(self, controls: dict[int, Controls]) -> list[dict]:
        """Advance one fixed timestep. Returns the events emitted this tick.

        `controls` maps player id -> Controls; missing players coast
        (zero throttle/turn, no fire).
        """
        c = self.config
        dt = self.dt
        self.tick += 1
        t_ms = self.t_ms
        out: list[dict] = []

        # --- tanks: respawn timers, movement, firing (fixed pid order) ---
        for pid in range(self.n_players):
            tank = self.tanks[pid]

            if tank.respawn_ticks > 0:
                tank.respawn_ticks -= 1
                if tank.respawn_ticks == 0:
                    self.tanks[pid] = tank = self._spawn(pid)
                else:
                    continue

            ctl = controls.get(pid)
            if ctl is None:
                ctl = Controls(fire_angle=tank.turret_heading)
            throttle = clamp(ctl.throttle, -1.0, 1.0)
            turn = clamp(ctl.turn, -1.0, 1.0)

            # velocity-based movement: accelerate, turn, integrate
            old_x, old_y = tank.x, tank.y
            if ctl.drive_angle is not None:
                tank.heading = wrap_angle(ctl.drive_angle)
                tank.turret_heading = tank.heading
                tank.speed = throttle * c.tank_max_speed
            else:
                tank.speed += throttle * c.tank_accel * dt
                tank.speed = clamp(
                    tank.speed, -c.tank_max_reverse, c.tank_max_speed)
                tank.heading = wrap_angle(
                    tank.heading + turn * c.tank_turn_rate * dt)
            tank.x += math.cos(tank.heading) * tank.speed * dt
            tank.y += math.sin(tank.heading) * tank.speed * dt

            if c.hull_fixed_gun:
                tank.turret_heading = tank.heading
            else:
                aim_error = ang_diff(wrap_angle(ctl.fire_angle),
                                     tank.turret_heading)
                traverse = c.turret_turn_rate * dt
                tank.turret_heading = wrap_angle(
                    tank.turret_heading
                    + clamp(aim_error, -traverse, traverse))

            # arena bounds (circle vs walls)
            lo_x, hi_x = c.tank_radius, c.arena_width - c.tank_radius
            lo_y, hi_y = c.tank_radius, c.arena_height - c.tank_radius
            if not (lo_x <= tank.x <= hi_x and lo_y <= tank.y <= hi_y):
                tank.x = clamp(tank.x, lo_x, hi_x)
                tank.y = clamp(tank.y, lo_y, hi_y)
                tank.speed *= c.wall_damping
            if any(self._circle_hits_block(tank.x, tank.y, c.tank_radius,
                                           block)
                   for block in self.blocks.values()):
                tank.x, tank.y, tank.speed = old_x, old_y, 0.0
            tank_spacing2 = (2.0 * c.tank_radius) ** 2
            if any(other.pid != pid and other.alive
                   and dist2(tank.x, tank.y, other.x, other.y) < tank_spacing2
                   for other in self.tanks.values()):
                tank.x, tank.y, tank.speed = old_x, old_y, 0.0

            # timers
            if tank.cooldown_ticks > 0:
                tank.cooldown_ticks -= 1
            if tank.reload_ticks > 0:
                tank.reload_ticks -= 1
                if tank.reload_ticks == 0:
                    tank.ammo = c.max_ammo

            # firing -> token transfer shooter -> arena
            desired_fire = (tank.heading if c.hull_fixed_gun
                            else wrap_angle(ctl.fire_angle))
            aligned = abs(ang_diff(desired_fire, tank.turret_heading))
            if (ctl.fire and aligned <= c.fire_alignment_rad
                    and tank.cooldown_ticks == 0 and tank.ammo > 0
                    and self.balances[pid] >= c.shot_cost):
                angle = tank.turret_heading
                tank.ammo -= 1
                if tank.ammo == 0:
                    tank.reload_ticks = self._ticks(c.reload_s)
                tank.cooldown_ticks = self._ticks(c.fire_cooldown_s)
                muzzle = c.gun_length + c.projectile_radius + 0.1
                self.projectiles.append(Projectile(
                    owner=pid,
                    x=tank.x + math.cos(angle) * muzzle,
                    y=tank.y + math.sin(angle) * muzzle,
                    vx=math.cos(angle) * c.projectile_speed,
                    vy=math.sin(angle) * c.projectile_speed,
                    ttl_ticks=self._ticks(c.projectile_ttl_s),
                ))
                self.balances[pid] -= c.shot_cost
                self.balances["arena"] += c.shot_cost
                out.append(events.fire_event(
                    self.tick, t_ms, pid, angle, tank.ammo, c.shot_cost))

        # --- projectiles: fly, expire, collide -> hit token transfers ---
        survivors: list[Projectile] = []
        for proj in self.projectiles:
            old_x, old_y = proj.x, proj.y
            proj.x += proj.vx * dt
            proj.y += proj.vy * dt
            proj.ttl_ticks -= 1
            collision = self._first_projectile_hit(proj, old_x, old_y)
            if collision is None:
                if (proj.ttl_ticks <= 0
                        or not (0.0 <= proj.x <= c.arena_width)
                        or not (0.0 <= proj.y <= c.arena_height)):
                    continue
                survivors.append(proj)
                continue
            _, hit_kind, hit_id = collision
            if hit_kind == "block":
                block = self.blocks[hit_id]
                if block.kind == "brick":
                    del self.blocks[hit_id]
                    out.append(events.break_event(
                        self.tick, t_ms, proj.owner, block.bid, block.kind,
                        block.x, block.y))
                continue
            victim = self.tanks[hit_id]
            victim.hp -= c.projectile_damage
            killed = victim.hp <= 0
            amount = min(c.hit_payment, max(self.balances[victim.pid], 0))
            self.balances[victim.pid] -= amount
            self.balances[proj.owner] += amount
            shooter = self.tanks[proj.owner]
            if killed:
                victim.respawn_ticks = self._ticks(c.respawn_s)
                victim.deaths += 1
                shooter.kills += 1
            out.append(events.hit_event(
                self.tick, t_ms, proj.owner, victim.pid,
                c.projectile_damage, amount, killed))
        self.projectiles = survivors

        # --- movement state-updates: one per alive tank, every tick ---
        for pid in range(self.n_players):
            tank = self.tanks[pid]
            if tank.alive:
                out.append(events.move_event(
                    self.tick, t_ms, pid,
                    tank.x, tank.y, tank.heading, tank.turret_heading,
                    tank.speed))
        return out

    def _circle_hits_block(self, x: float, y: float, radius: float,
                           block: Block) -> bool:
        half = block.size / 2.0
        near_x = clamp(x, block.x - half, block.x + half)
        near_y = clamp(y, block.y - half, block.y + half)
        return dist2(x, y, near_x, near_y) <= radius * radius

    @staticmethod
    def _segment_aabb_u(x0: float, y0: float, x1: float, y1: float,
                        left: float, top: float, right: float,
                        bottom: float) -> float | None:
        """Entry fraction for a segment vs axis-aligned rectangle."""
        t0, t1 = 0.0, 1.0
        dx, dy = x1 - x0, y1 - y0
        for start, delta, lo, hi in (
                (x0, dx, left, right), (y0, dy, top, bottom)):
            if delta == 0.0:
                if start < lo or start > hi:
                    return None
                continue
            enter, leave = (lo - start) / delta, (hi - start) / delta
            if enter > leave:
                enter, leave = leave, enter
            t0, t1 = max(t0, enter), min(t1, leave)
            if t0 > t1:
                return None
        return t0

    def _first_projectile_hit(self, proj: Projectile, x0: float,
                              y0: float) -> tuple[float, str, int] | None:
        """Nearest tank or terrain block crossed by a shell this tick.

        Swept collision matters for tank shells: their speed is deliberately
        high enough that point sampling would tunnel through a target at low
        simulation tick rates.
        """
        c = self.config
        dx, dy = proj.x - x0, proj.y - y0
        seg_len2 = dx * dx + dy * dy
        radius2 = (c.tank_radius + c.projectile_radius) ** 2
        best: tuple[float, int, int, str] | None = None
        for bid, block in self.blocks.items():
            half = block.size / 2.0 + c.projectile_radius
            u = self._segment_aabb_u(
                x0, y0, proj.x, proj.y, block.x - half, block.y - half,
                block.x + half, block.y + half)
            if u is not None:
                candidate = (u, 0, bid, "block")
                if best is None or candidate[:3] < best[:3]:
                    best = candidate
        for pid in range(self.n_players):
            tank = self.tanks[pid]
            if pid == proj.owner or not tank.alive:
                continue
            if seg_len2 == 0.0:
                u = 0.0
            else:
                u = clamp(((tank.x - x0) * dx + (tank.y - y0) * dy)
                          / seg_len2, 0.0, 1.0)
            near_x, near_y = x0 + u * dx, y0 + u * dy
            if dist2(near_x, near_y, tank.x, tank.y) <= radius2:
                candidate = (u, 1, pid, "tank")
                if best is None or candidate[:3] < best[:3]:
                    best = candidate
        return ((best[0], best[3], best[2])
                if best is not None else None)
