"""Tuning harness for the UC2 game: match statistics and the acceptance band.

Usage:
    python -m arena.tune --seeds 1-20,42 [--damage 16] [--plausible-lane 60] ...
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from dataclasses import asdict

from arena.bots import FireParams, make_bots
from arena.world import Config, World

FIRST_KILL_AFTER_TICK = 60   # 3 s at 20 Hz
HIT_SHARE_MIN, HIT_SHARE_MAX = 0.10, 0.40


def match_stats(seed: int, config: Config | None = None,
                fire: FireParams | None = None, players: int = 6,
                tickrate: int = 20, ticks: int = 1200) -> dict:
    config = config or Config()
    world = World(n_players=players, tickrate=tickrate, seed=seed, config=config)
    bots = make_bots(players, seed, tickrate, fire=fire)
    shots_per_player = [0] * players
    hits = kills = payments = 0
    first_kill = None
    for _ in range(ticks):
        controls = {pid: bot.decide(world) for pid, bot in bots.items()}
        for ev in world.step(controls):
            if ev["type"] == "fire":
                shots_per_player[ev["player"]] += 1
                if ev["payload"]["amount"] > 0:
                    payments += 1
            elif ev["type"] == "hit":
                hits += 1
                if ev["payload"]["amount"] > 0:
                    payments += 1
                if ev["payload"]["killed"]:
                    kills += 1
                    if first_kill is None:
                        first_kill = ev["tick"]
    shots = sum(shots_per_player)
    seconds = ticks / tickrate
    return {
        "seed": seed, "shots": shots, "hits": hits,
        "hit_share": hits / shots if shots else 0.0,
        "kills": kills, "first_kill_tick": first_kill,
        "shots_per_player": shots_per_player,
        "payments": payments, "payments_per_s": payments / seconds,
    }


def accept(stats: dict) -> list[str]:
    bad = []
    silent = [i for i, n in enumerate(stats["shots_per_player"]) if n == 0]
    if silent:
        bad.append(f"players never fired: {silent}")
    hs = stats["hit_share"]
    if not HIT_SHARE_MIN <= hs <= HIT_SHARE_MAX:
        bad.append(f"hit_share {hs:.3f} outside [{HIT_SHARE_MIN}, {HIT_SHARE_MAX}]")
    if stats["kills"] < 1:
        bad.append("no kill")
    fk = stats["first_kill_tick"]
    if fk is not None and fk <= FIRST_KILL_AFTER_TICK:
        bad.append(f"first kill at tick {fk} (must be > {FIRST_KILL_AFTER_TICK})")
    return bad


def config_digest(config: Config | None, fire: FireParams | None) -> str:
    config = config or Config()
    fire = fire or FireParams()
    blob = json.dumps({"config": asdict(config), "fire": asdict(fire)},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def parse_seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m arena.tune")
    ap.add_argument("--seeds", default="1-20,42")
    ap.add_argument("--damage", type=int)
    for f in dataclasses.fields(FireParams):
        ap.add_argument("--" + f.name.replace("_", "-"), type=type(f.default))
    args = ap.parse_args(argv)
    cfg = Config()
    if args.damage is not None:
        cfg = dataclasses.replace(cfg, projectile_damage=args.damage)
    fire = FireParams()
    over = {f.name: getattr(args, f.name) for f in dataclasses.fields(FireParams)
            if getattr(args, f.name) is not None}
    fire = dataclasses.replace(fire, **over)
    passed = 0
    shares, kills, pps = [], [], []
    for seed in parse_seeds(args.seeds):
        st = match_stats(seed, cfg, fire)
        bad = accept(st)
        passed += not bad
        shares.append(st["hit_share"]); kills.append(st["kills"]); pps.append(st["payments_per_s"])
        print(f"seed {seed:3d} shots {st['shots']:4d} hits {st['hits']:4d} "
              f"share {st['hit_share']:.3f} kills {st['kills']:2d} "
              f"first_kill {st['first_kill_tick']} pay/s {st['payments_per_s']:.1f} "
              f"min_shots {min(st['shots_per_player'])} "
              f"{'OK' if not bad else 'FAIL: ' + '; '.join(bad)}")
    n = len(shares)
    print(f"passed {passed}/{n}  mean_share {sum(shares)/n:.3f}  "
          f"mean_kills {sum(kills)/n:.2f}  mean_pay/s {sum(pps)/n:.1f}")
    print("digest", config_digest(cfg, fire))
    return 0 if passed == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
