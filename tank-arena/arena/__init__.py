"""arena — deterministic multiplayer 2D tank-arena simulation core (UC2).

The game loop IS the workload generator: every simulation tick each alive
tank emits a movement state-update event; shot and hit events are token
transfers. The whole simulation is deterministic given
(seed, players, tickrate, duration) — a requirement for both reproducible
workload traces and lockstep distributed execution (see arena/node.py and
arena/multiplayer.py).

Stdlib only. pygame is an optional dependency of arena.render exclusively.
"""

from arena.world import Config, Controls, Projectile, Tank, World  # noqa: F401

__all__ = ["Config", "Controls", "Projectile", "Tank", "World"]
