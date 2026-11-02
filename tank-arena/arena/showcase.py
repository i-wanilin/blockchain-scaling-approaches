"""Stable-seed autonomous tank showcase.

Six independent bot agents repeatedly perceive the deterministic shared world,
select the nearest live opponent, choose a movement/aim action, and submit that
action to the same fixed-timestep core used by multiplayer.  Seed 1990 fixes
spawns, aim noise, wandering, and every later decision, so visual demonstrations
and generated traces are repeatable.

Visual run::

    python -m arena.showcase

Matching headless trace for Perun / zkSync / Arbitrum replay::

    python -m arena.sim --players 6 --tickrate 20 --duration 180 \
        --seed 1990 --out trace-showcase-seed1990.jsonl
"""

from __future__ import annotations

import argparse

from arena.render import live

SHOWCASE_PLAYERS = 6
SHOWCASE_TICKRATE = 20
SHOWCASE_SEED = 1990
SHOWCASE_DURATION_S = 180.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m arena.showcase",
        description="Deterministic six-agent tank interaction showcase.")
    parser.add_argument("--duration", type=float,
                        default=SHOWCASE_DURATION_S)
    parser.add_argument("--seed", type=int, default=SHOWCASE_SEED)
    parser.add_argument("--players", type=int, default=SHOWCASE_PLAYERS)
    parser.add_argument("--screenshot", default="")
    parser.add_argument("--uncapped", action="store_true")
    args = parser.parse_args(argv)
    live(args.players, SHOWCASE_TICKRATE, args.seed, args.duration,
         args.screenshot, args.uncapped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
