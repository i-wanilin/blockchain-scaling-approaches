"""The points bound of a match (cluster spec §7.3 *Perun channel capacity*).

S = the most bullets one tank can fire in a match: the trigger held from the
first tick to the last, never killed, computed by running the real World so it
always matches the implementation. A tank that dies cannot gain bullets as long
as its respawn time is at least one magazine cycle (it respawns with a full
magazine); respawn_ok checks that, otherwise S is not a bound.
"""
from __future__ import annotations

import argparse
import json
import sys

from arena.world import Config, Controls, World


def max_shots(config: Config, tickrate: int, ticks: int) -> int:
    cfg = Config(**{**config.__dict__, "enable_blocks": False, "start_tokens": 10**9})
    w = World(n_players=1, tickrate=tickrate, seed=0, config=cfg)
    n = 0
    for _ in range(ticks):
        n += sum(e["type"] == "fire" for e in w.step({0: Controls(fire=True, drive_angle=w.tanks[0].heading)}))
    return n


def amounts(config: Config, tickrate: int, ticks: int, n_players: int) -> dict:
    s = max_shots(config, tickrate, ticks)
    tick = lambda sec: max(1, round(sec * tickrate))
    cycle = (config.max_ammo - 1) * tick(config.fire_cooldown_s) + tick(config.reload_s)
    stake = config.hit_payment * s
    need = (config.shot_cost + config.hit_payment * (n_players - 1)) * s
    respawn_ok = tick(config.respawn_s) >= cycle
    return {"S": s, "ammo": config.shot_cost * s, "virtual_stake": stake,
            "hub_deposit": (n_players - 1) * stake, "player_need": need,
            "start_points": config.start_tokens, "respawn_ok": respawn_ok,
            "ok": respawn_ok and need <= config.start_tokens}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m arena.bound")
    ap.add_argument("--players", type=int, default=6)
    ap.add_argument("--tickrate", type=int, default=20)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = amounts(Config(), a.tickrate, int(round(a.duration * a.tickrate)), a.players)
    print(json.dumps(r) if a.json else "\n".join(f"{k}: {v}" for k, v in r.items()))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
