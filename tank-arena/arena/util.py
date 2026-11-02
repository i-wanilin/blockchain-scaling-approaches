"""Small math helpers shared by the simulation core and the bots."""

from __future__ import annotations

import math

TWO_PI = 2.0 * math.pi


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def wrap_angle(a: float) -> float:
    """Wrap an angle to (-pi, pi]."""
    a = math.fmod(a, TWO_PI)
    if a <= -math.pi:
        a += TWO_PI
    elif a > math.pi:
        a -= TWO_PI
    return a


def ang_diff(target: float, current: float) -> float:
    """Signed smallest rotation from `current` to `target`, in (-pi, pi]."""
    return wrap_angle(target - current)


def dist2(ax: float, ay: float, bx: float, by: float) -> float:
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy
