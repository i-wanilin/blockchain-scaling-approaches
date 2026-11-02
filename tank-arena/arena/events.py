"""Event (transaction) schema of the tank-arena workload trace.

Every event is one JSON object per line (JSONL). Common envelope:

    {"tick": int, "t_ms": float, "type": "move"|"fire"|"hit"|"break",
     "player": int, "payload": {...}}

Types and their transaction semantics (thesis mapping):

* ``move`` — movement state-update, one per alive tank per tick. This is a
  signed state-channel update / rollup message, NOT a token transfer. It is
  the sustained throughput floor: N players x tickrate.
  payload: {x, y, heading, turret_heading, speed}

* ``fire`` — shot fired = token transfer (ammo spent), shooter -> arena.
  payload: {angle, ammo_left, from, to: "arena", amount}

* ``hit`` — projectile hit = token transfer victim -> shooter (damage
  payment). ``player`` is the shooter (payment receiver).
  payload: {victim, shooter, damage, from, to, amount, killed}

* ``break`` — a projectile destroyed a brick block. This is a shared-state
  message, not a value transfer. Stone never emits this event.
  payload: {block, kind, x, y}

Floats are rounded to 4 decimals so traces are compact and byte-stable.
"""

from __future__ import annotations

from typing import Any

MOVE = "move"
FIRE = "fire"
HIT = "hit"
BREAK = "break"

EVENT_TYPES = (MOVE, FIRE, HIT, BREAK)

#: Token transfers (as opposed to plain state messages).
TRANSFER_TYPES = (FIRE, HIT)


def _r(v: float) -> float:
    return round(v, 4)


def _envelope(tick: int, t_ms: float, etype: str, player: int,
              payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "tick": tick,
        "t_ms": _r(t_ms),
        "type": etype,
        "player": player,
        "payload": payload,
    }


def move_event(tick: int, t_ms: float, player: int,
               x: float, y: float, heading: float, turret_heading: float,
               speed: float) -> dict[str, Any]:
    return _envelope(tick, t_ms, MOVE, player, {
        "x": _r(x), "y": _r(y), "heading": _r(heading),
        "turret_heading": _r(turret_heading), "speed": _r(speed),
    })


def fire_event(tick: int, t_ms: float, player: int,
               angle: float, ammo_left: int, amount: int) -> dict[str, Any]:
    return _envelope(tick, t_ms, FIRE, player, {
        "angle": _r(angle), "ammo_left": ammo_left,
        "from": player, "to": "arena", "amount": amount,
    })


def hit_event(tick: int, t_ms: float, shooter: int, victim: int,
              damage: int, amount: int, killed: bool) -> dict[str, Any]:
    return _envelope(tick, t_ms, HIT, shooter, {
        "victim": victim, "shooter": shooter, "damage": damage,
        "from": victim, "to": shooter, "amount": amount, "killed": killed,
    })


def break_event(tick: int, t_ms: float, player: int, block: int,
                kind: str, x: float, y: float) -> dict[str, Any]:
    return _envelope(tick, t_ms, BREAK, player, {
        "block": block, "kind": kind, "x": _r(x), "y": _r(y),
    })


def validate(event: dict[str, Any]) -> None:
    """Raise ValueError if `event` does not conform to the schema."""
    for key in ("tick", "t_ms", "type", "player", "payload"):
        if key not in event:
            raise ValueError(f"event missing key {key!r}: {event}")
    if event["type"] not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event['type']!r}")
    p = event["payload"]
    required = {
        MOVE: ("x", "y", "heading", "turret_heading", "speed"),
        FIRE: ("angle", "ammo_left", "from", "to", "amount"),
        HIT: ("victim", "shooter", "damage", "from", "to", "amount", "killed"),
        BREAK: ("block", "kind", "x", "y"),
    }[event["type"]]
    for key in required:
        if key not in p:
            raise ValueError(f"{event['type']} payload missing {key!r}: {event}")
