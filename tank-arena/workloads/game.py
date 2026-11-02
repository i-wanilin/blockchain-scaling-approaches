"""UC2 — replay a tank-arena JSONL trace as a normalized workload.

Maps arena events (arena/events.py) onto the common schema:

* move -> message, sender = player, receiver = "*" (shared-state broadcast:
  every participant needs every tank's position — the many-to-many
  topology rollups support natively and channels must fake via a hub).
* fire -> payment, shooter -> "arena" (ammo spent).
* hit  -> payment, victim -> shooter (damage payment).
* break -> message, shooter -> "*" (shared destructible-map state).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

from workloads import MESSAGE, PAYMENT, make_event


def from_events(events: Iterable[dict]) -> Iterator[dict]:
    """Map an iterable of arena events to normalized workload events."""
    for ev in events:
        t_ms = ev["t_ms"]
        etype = ev["type"]
        p = ev["payload"]
        if etype == "move":
            yield make_event(t_ms, MESSAGE, f"player-{ev['player']}", "*", 0,
                             {"x": p["x"], "y": p["y"],
                              "heading": p["heading"], "speed": p["speed"],
                              "tick": ev["tick"]})
        elif etype == "fire":
            yield make_event(t_ms, PAYMENT, f"player-{p['from']}", "arena",
                             p["amount"], {"for": "shot", "tick": ev["tick"]})
        elif etype == "hit":
            yield make_event(t_ms, PAYMENT, f"player-{p['from']}",
                             f"player-{p['to']}", p["amount"],
                             {"for": "damage", "damage": p["damage"],
                              "killed": p["killed"], "tick": ev["tick"]})
        elif etype == "break":
            yield make_event(t_ms, MESSAGE, f"player-{ev['player']}", "*", 0,
                             {"for": "block-break", "block": p["block"],
                              "kind": p["kind"], "x": p["x"], "y": p["y"],
                              "tick": ev["tick"]})
        else:
            raise ValueError(f"unknown arena event type {etype!r}")


def generate(trace_path: str) -> Iterator[dict]:
    """Yield normalized events from a trace.jsonl produced by arena.sim."""
    def _read() -> Iterator[dict]:
        with open(trace_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)
    yield from from_events(_read())
