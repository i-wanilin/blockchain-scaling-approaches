"""Headless single-process simulation runner (the workload generator).

Usage:
    python -m arena.sim --players 6 --tickrate 20 --duration 60 \
                        --seed 42 --out trace.jsonl

Runs the deterministic World with bot players and writes the event stream
as a JSONL trace (see arena/events.py for the schema). The trace is the
workload: feed it to workloads/game.py or arena.stats.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterator
from typing import IO

from arena.bots import make_bots
from arena.world import Config, World


def iter_sim(players: int, tickrate: int, duration: float, seed: int,
             config: Config | None = None) -> Iterator[dict]:
    """Yield every event of a full bot-vs-bot game, in order."""
    world = World(n_players=players, tickrate=tickrate, seed=seed,
                  config=config or Config())
    bots = make_bots(players, seed, tickrate)
    total_ticks = int(round(duration * tickrate))
    for _ in range(total_ticks):
        controls = {pid: bot.decide(world) for pid, bot in bots.items()}
        yield from world.step(controls)


def run(players: int, tickrate: int, duration: float, seed: int,
        out: IO[str], config: Config | None = None) -> dict[str, int]:
    """Run a full game, write JSONL to `out`, return event counts."""
    counts = {"move": 0, "fire": 0, "hit": 0, "break": 0}
    for event in iter_sim(players, tickrate, duration, seed, config):
        counts[event["type"]] += 1
        out.write(json.dumps(event, separators=(",", ":")) + "\n")
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m arena.sim",
        description="Deterministic tank-arena workload generator (headless).")
    ap.add_argument("--players", type=int, default=6)
    ap.add_argument("--tickrate", type=int, default=20,
                    help="simulation ticks per second (move-update rate)")
    ap.add_argument("--duration", type=float, default=60.0,
                    help="game length in simulated seconds")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="-",
                    help="output JSONL trace path, '-' for stdout")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    if args.out == "-":
        counts = run(args.players, args.tickrate, args.duration,
                     args.seed, sys.stdout)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            counts = run(args.players, args.tickrate, args.duration,
                         args.seed, fh)
    elapsed = time.perf_counter() - t0

    total = sum(counts.values())
    print(f"simulated {args.duration:g}s @ {args.tickrate}Hz, "
          f"{args.players} players, seed {args.seed} "
          f"in {elapsed:.2f}s wall time", file=sys.stderr)
    print(f"events: {total} total  "
          f"(move {counts['move']}, fire {counts['fire']}, "
          f"hit {counts['hit']}, break {counts['break']})", file=sys.stderr)
    if args.out != "-":
        print(f"trace written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
