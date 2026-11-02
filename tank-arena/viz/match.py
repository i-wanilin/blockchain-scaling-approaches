"""UC2 match viewer and figure export: shells in flight, bursts, payments.

The recorded event log (trace.jsonl) holds moves, shots, hits and broken blocks, but
no shell positions, so a plain replay cannot show bullets travelling. This module
re-runs the frozen game instead (World + bots, exactly as arena.sim does) and keeps
every tick's tanks and shells. The game is deterministic: for seed 42 the event
stream hashes to the UC2 reference log (bc1d6874…), which is checked and shown.

    python -m viz.match                          # watch seed 42 (the UC2 match)
    python -m viz.match --seed 4 --speed 0.5     # another seed, slow motion
    python -m viz.match --figures ../analysis/figures/uc2-match   # paper stills + a 2x2 panel, no window

Keys: SPACE pause, LEFT/RIGHT one tick (SHIFT: one second), UP/DOWN speed,
T trails on/off, S save the current frame (figure style), HOME restart, ESC quit.

Payments (UC2): every shot pays 1 point shooter -> arena, every hit pays 5 points
victim -> shooter; each player starts with 10 000 points.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from arena import render as R
from arena.bots import make_bots
from arena.world import Config, World
from viz import sprites

PLAYERS, TICKRATE, DURATION_S = 6, 20, 60.0
START_POINTS = 10_000
UC2_SEED = 42
VEHICLE = "classic"       # sprite style, viz/sprites.py STYLES (owner 2026-10-12: A, arcade classic)
UC2_REFERENCE_SHA256 = "bc1d68746004756ffcdea0f53266ef9c2f3e56ad28c544a1aea014fe854368f7"

TRAIL_TICKS = 12          # figure style: shell paths and hits of the last 0.6 s
TANK_TRAIL_TICKS = 40     # figure style: tank paths of the last 2 s
HIT_TICKS = 8             # live: hit burst duration
KILL_TICKS = 14           # live: kill explosion duration
LABEL_TICKS = 16          # live: floating payment label duration
WHITE = (255, 255, 255)
SPARK = (255, 236, 160)


@dataclass
class Frame:
    tick: int
    tanks: list[tuple]     # per pid: (x, y, heading, turret_heading, hp, alive, kills)
    shells: list[tuple]    # (owner, x, y, vx, vy)
    blocks: frozenset      # ids of the blocks still standing
    events: list[dict]     # the events this tick produced (fire, hit, break; moves dropped)


@dataclass
class Match:
    seed: int
    cfg: Config
    all_blocks: dict
    frames: list[Frame]
    sha256: str
    points: list[list[int]]     # per frame, per pid: balance
    shots: list[int]            # per frame: cumulative shot payments
    hits: list[int]             # per frame: cumulative hit payments

    @property
    def is_uc2_reference(self) -> bool:
        return self.sha256 == UC2_REFERENCE_SHA256


def simulate(seed: int) -> Match:
    """Run the frozen game (as arena.sim.iter_sim) and keep every tick's state."""
    cfg = Config()
    world = World(n_players=PLAYERS, tickrate=TICKRATE, seed=seed, config=cfg)
    bots = make_bots(PLAYERS, seed, TICKRATE)
    all_blocks = dict(world.blocks)
    digest = hashlib.sha256()

    def snap(events: list[dict]) -> Frame:
        tanks = [(t.x, t.y, t.heading, t.turret_heading, t.hp, t.alive, t.kills)
                 for t in (world.tanks[p] for p in range(PLAYERS))]
        shells = [(s.owner, s.x, s.y, s.vx, s.vy) for s in world.projectiles]
        return Frame(world.tick, tanks, shells, frozenset(world.blocks),
                     [e for e in events if e["type"] != "move"])

    frames = [snap([])]
    for _ in range(int(round(DURATION_S * TICKRATE))):
        controls = {pid: bot.decide(world) for pid, bot in bots.items()}
        events = world.step(controls)
        for event in events:
            digest.update((json.dumps(event, separators=(",", ":")) + "\n").encode())
        frames.append(snap(events))

    balance = [START_POINTS] * PLAYERS
    shots = hits = 0
    points, shot_counts, hit_counts = [], [], []
    for frame in frames:
        for event in frame.events:
            pay = event["payload"]
            if event["type"] == "fire":
                balance[event["player"]] -= pay["amount"]
                shots += 1
            elif event["type"] == "hit":
                balance[pay["from"]] -= pay["amount"]
                balance[pay["to"]] += pay["amount"]
                hits += 1
        points.append(list(balance))
        shot_counts.append(shots)
        hit_counts.append(hits)
    return Match(seed, cfg, all_blocks, frames, digest.hexdigest(), points, shot_counts, hit_counts)


# ---------------------------------------------------------------- drawing

def _color(pid: int) -> tuple[int, int, int]:
    return R.ARMOR[pid % len(R.ARMOR)]


def _fade(color, alpha: float) -> tuple[int, int, int, int]:
    return (*color[:3], max(0, min(255, round(255 * alpha))))


def _impact(match: Match, tick: int, event: dict) -> tuple[float, float]:
    """Where a hit lands: the victim's edge facing the shooter."""
    tanks = match.frames[tick].tanks
    vx, vy = tanks[event["payload"]["victim"]][:2]
    sx, sy = tanks[event["payload"]["shooter"]][:2]
    d = math.hypot(sx - vx, sy - vy) or 1.0
    r = match.cfg.tank_radius
    return vx + (sx - vx) / d * r, vy + (sy - vy) / d * r


def _burst(pygame, layer, x: float, y: float, age: int, life: int, size: float,
           color) -> None:
    """Expanding ring with eight sparks; fades out over `life` ticks."""
    if age > life:
        return
    k = age / life
    alpha = 1.0 - k
    radius = 4 + size * k
    pygame.draw.circle(layer, _fade(color, alpha), (round(x), round(y)), round(radius), 3)
    if age <= 2:
        pygame.draw.circle(layer, _fade(WHITE, 0.9), (round(x), round(y)), round(4 + size * 0.25))
    for i in range(8):
        a = i * math.pi / 4 + (0.3 if i % 2 else 0.0)
        r0, r1 = radius * 0.6, radius * 1.15 + 3
        p0 = (x + math.cos(a) * r0, y + math.sin(a) * r0)
        p1 = (x + math.cos(a) * r1, y + math.sin(a) * r1)
        pygame.draw.line(layer, _fade(SPARK, alpha), p0, p1, 2)


def _label(pygame, layer, text: str, x: float, y: float, color, alpha: float = 1.0,
           size: int = 13, placed: list | None = None) -> None:
    """Text with a drop shadow; with `placed`, moved up until it overlaps no earlier label."""
    font = R._font(pygame, size, True)
    shadow = font.render(text, True, (0, 0, 0))
    shadow.set_alpha(round(255 * alpha))
    face = font.render(text, True, color)
    face.set_alpha(round(255 * alpha))
    rect = face.get_rect(center=(round(x), round(y)))
    if placed is not None:
        while rect.collidelist(placed) != -1 and rect.top > 0:
            rect.move_ip(0, -(rect.height + 1))
        placed.append(rect.inflate(4, 0))
    layer.blit(shadow, rect.move(1, 1))
    layer.blit(face, rect)


def _still_hits(pygame, fx, match: Match, tick: int, placed: list) -> None:
    """Figure style: every hit of the last 0.6 s as a marker; one label per payment
    pair (P<victim>\u2192P<shooter>, with a count), stacked per victim so they do not overlap."""
    pairs: dict[tuple[int, int], list] = {}
    for t in range(max(0, tick - TRAIL_TICKS), tick + 1):
        age = tick - t
        for e in match.frames[t].events:
            if e["type"] != "hit":
                continue
            p = e["payload"]
            ix, iy = _impact(match, t, e)
            alpha = 1.0 - age / (TRAIL_TICKS + 2)
            big = p["killed"]
            pygame.draw.circle(fx, _fade(R.AMBER if big else SPARK, alpha),
                               (round(ix), round(iy)), 16 if big else 9, 3)
            for i in range(4):
                a = i * math.pi / 2 + math.pi / 4
                pygame.draw.line(fx, _fade(SPARK, alpha), (ix + math.cos(a) * 4, iy + math.sin(a) * 4),
                                 (ix + math.cos(a) * 13, iy + math.sin(a) * 13), 2)
            pairs.setdefault((p["from"], p["to"]), []).append((ix, iy, p))
    for (payer, payee), hits in sorted(pairs.items()):
        ix, iy, p = hits[-1]
        n = len(hits)
        text = f"+{p['amount']}{'x' + str(n) if n > 1 else ''} P{payer + 1}\u2192P{payee + 1}"
        _label(pygame, fx, text, ix, iy - 22, _color(payee), placed=placed)
        if any(h[2]["killed"] for h in hits):
            _label(pygame, fx, f"P{payee + 1} KO P{payer + 1}", ix, iy + 28, R.AMBER, size=15,
                   placed=placed)


def _blocks(match: Match, frame: Frame) -> dict:
    return {bid: b for bid, b in match.all_blocks.items() if bid in frame.blocks}


def draw_field(pygame, screen, match: Match, tick: int, still: bool, trails: bool,
               vehicle: str = VEHICLE) -> None:
    """The playfield at `tick`. still=True: the figure style (paths of the last moments
    drawn as fading dots, every hit of the last 0.6 s kept with its payment).
    vehicle: the sprite style, a key of viz/sprites.py STYLES."""
    cfg, frame = match.cfg, match.frames[tick]
    screen.blit(R._terrain(pygame, cfg, match.seed), (0, 0))
    fx = pygame.Surface(screen.get_size(), pygame.SRCALPHA)

    if trails:      # tank paths: faint dots of the last 2 s
        for back in range(TANK_TRAIL_TICKS, 0, -2):
            t = tick - back
            if t < 0:
                continue
            for pid, (x, y, *_rest, alive, _k) in enumerate(match.frames[t].tanks):
                if alive:
                    pygame.draw.circle(fx, _fade(_color(pid), 0.35 * (1 - back / TANK_TRAIL_TICKS)),
                                       (round(x), round(y)), 2)
    screen.blit(fx, (0, 0))
    fx.fill((0, 0, 0, 0))
    R._draw_blocks(pygame, screen, _blocks(match, frame))

    if trails and still:   # shell paths: every shell position of the last 0.6 s
        for back in range(TRAIL_TICKS, 0, -1):
            t = tick - back
            if t < 0:
                continue
            for owner, x, y, _vx, _vy in match.frames[t].shells:
                pygame.draw.circle(fx, _fade(_color(owner), 0.85 * (1 - back / (TRAIL_TICKS + 1))),
                                   (round(x), round(y)), 2)

    # tanks
    recent_hit = {e["payload"]["victim"] for t in range(max(0, tick - 1), tick + 1)
                  for e in match.frames[t].events if e["type"] == "hit"}
    names: list = []
    draw_tank = sprites.STYLES[vehicle]
    for pid, (x, y, heading, turret, hp, alive, _kills) in enumerate(frame.tanks):
        if not alive:
            continue
        # recoil=0: the muzzle burst below is the only flash (the frozen "current" sprite would draw a second one)
        draw_tank(pygame, screen, x, y, heading, turret, hp, cfg.max_hp, _color(pid),
                  cfg.tank_radius, pid, 2 if pid in recent_hit and not still else 0, 0)
        # hp bar and name above the tank
        w = 28
        pygame.draw.rect(screen, (40, 40, 38), (round(x - w / 2), round(y - 27), w, 4))
        pygame.draw.rect(screen, R.RED if hp < cfg.max_hp * 0.35 else (110, 220, 90),
                         (round(x - w / 2), round(y - 27), round(w * max(0, hp) / cfg.max_hp), 4))
        _label(pygame, screen, f"P{pid + 1}", x, y - 36, _color(pid), size=12, placed=names)
        names.append(pygame.Rect(round(x - w / 2), round(y - 28), w, 6))   # its hp bar

    # shells in flight: a bright head and a streak along its last tick of travel
    dt = 1.0 / TICKRATE
    for owner, x, y, vx, vy in frame.shells:
        tail = (x - vx * dt * 1.2, y - vy * dt * 1.2)
        pygame.draw.line(fx, _fade(_color(owner), 0.75), tail, (x, y), 3)
        pygame.draw.circle(fx, _fade(WHITE, 1.0), (round(x), round(y)), 3)

    # hits, kills and their payments: frozen markers in a still, animated bursts otherwise
    if still:
        _still_hits(pygame, fx, match, tick, names)
    else:
        for t in range(max(0, tick - max(KILL_TICKS + 6, LABEL_TICKS)), tick + 1):
            age = tick - t
            for e in match.frames[t].events:
                if e["type"] != "hit":
                    continue
                p = e["payload"]
                ix, iy = _impact(match, t, e)
                _burst(pygame, fx, ix, iy, age, HIT_TICKS, 16, SPARK)
                if p["killed"]:
                    _burst(pygame, fx, ix, iy, age, KILL_TICKS, 46, R.AMBER)
                    _label(pygame, fx, f"P{p['shooter'] + 1} KO P{p['victim'] + 1}", ix, iy + 26,
                           R.AMBER, 1 - age / (KILL_TICKS + 7), size=15)
                if age <= LABEL_TICKS:
                    _label(pygame, fx, f"+{p['amount']}", ix, iy - 14 - age * 1.6,
                           _color(p["shooter"]), 1 - age / (LABEL_TICKS + 1))
    # muzzle bursts of this tick and the last, at the gun's tip (cfg.gun_length, where the world spawns the shell)
    for t in range(max(0, tick - 1), tick + 1):
        for e in match.frames[t].events:
            if e["type"] == "fire":
                x, y, _h, turret = match.frames[t].tanks[e["player"]][:4]
                gun = round(turret / (math.pi / 2)) * (math.pi / 2)
                mx, my = x + math.cos(gun) * cfg.gun_length, y + math.sin(gun) * cfg.gun_length
                pygame.draw.circle(fx, _fade(R.AMBER, 0.9 if t == tick else 0.45), (round(mx), round(my)), 6)
                pygame.draw.circle(fx, _fade(WHITE, 0.9 if t == tick else 0.4), (round(mx), round(my)), 3)
    screen.blit(fx, (0, 0))
    pygame.draw.rect(screen, (45, 45, 42), (0, 0, R.HUD_X, R.SCREEN_HEIGHT), 5)


def draw_hud(pygame, screen, match: Match, tick: int, speed: float | None = None,
             paused: bool = False, legend: bool = False) -> None:
    x0, w = R.HUD_X, R.SCREEN_WIDTH - R.HUD_X
    pygame.draw.rect(screen, R.HUD_GREY, (x0, 0, w, R.SCREEN_HEIGHT))
    pygame.draw.line(screen, (51, 51, 49), (x0, 0), (x0, R.SCREEN_HEIGHT), 6)
    f_big, f_mid, f_small = R._font(pygame, 18, True), R._font(pygame, 13, True), R._font(pygame, 11, True)
    screen.blit(f_big.render("UC2 MATCH", True, R.INK), (x0 + 14, 12))
    tag = "seed 42 = UC2" if match.is_uc2_reference else f"seed {match.seed}"
    screen.blit(f_small.render(tag, True, R.INK), (x0 + 14, 36))
    screen.blit(f_mid.render(f"t {tick / TICKRATE:5.2f} s", True, R.INK), (x0 + 14, 52))
    frame = match.frames[tick]
    y = 78
    for pid, (_x, _y, _h, _t, hp, alive, kills) in enumerate(frame.tanks):
        pygame.draw.rect(screen, _color(pid), (x0 + 14, y, 14, 14))
        pygame.draw.rect(screen, R.INK, (x0 + 14, y, 14, 14), 2)
        status = f"K{kills}" + ("" if alive else "  down")
        screen.blit(f_mid.render(f"P{pid + 1} {status}", True, R.INK), (x0 + 34, y - 1))
        pygame.draw.rect(screen, (68, 68, 65), (x0 + 34, y + 16, 104, 5))
        if alive:
            pygame.draw.rect(screen, R.RED, (x0 + 34, y + 16, round(104 * max(0, hp) / match.cfg.max_hp), 5))
        pts = match.points[tick][pid]
        screen.blit(f_small.render(f"{pts:,} pts".replace(",", " "), True, R.INK), (x0 + 34, y + 23))
        y += 50
    y += 4
    shots, hits = match.shots[tick], match.hits[tick]
    recent = match.shots[tick] + match.hits[tick] - (
        match.shots[max(0, tick - TICKRATE)] + match.hits[max(0, tick - TICKRATE)])
    for text in (f"SHOTS  {shots:4d}", f"HITS   {hits:4d}", f"PAYMTS {shots + hits:4d}",
                 f"LAST 1s {recent:3d}/s"):
        screen.blit(f_mid.render(text, True, R.INK), (x0 + 14, y))
        y += 18
    if legend:
        y += 8
        for text in ("shot: 1 pt", " shooter→arena", "hit: 5 pts", " victim→shooter",
                     "shell dots: 0.6 s", "faint dots: tank", " paths, last 2 s"):
            screen.blit(f_small.render(text, True, R.INK), (x0 + 14, y))
            y += 14
    elif speed is not None:
        y = R.SCREEN_HEIGHT - 64
        state = "PAUSED" if paused else f"x{speed:g}"
        screen.blit(f_mid.render(state, True, R.INK), (x0 + 14, y))
        for i, text in enumerate(("SPC pause  ←→ step", "↑↓ speed  T trails",
                                  "S save  HOME restart")):
            screen.blit(f_small.render(text, True, R.INK), (x0 + 14, y + 18 + i * 13))


def render_still(pygame, match: Match, tick: int, scale: int = 2, vehicle: str = VEHICLE):
    screen = pygame.Surface((R.SCREEN_WIDTH, R.SCREEN_HEIGHT))
    screen.fill(R.HUD_GREY)
    draw_field(pygame, screen, match, tick, still=True, trails=True, vehicle=vehicle)
    draw_hud(pygame, screen, match, tick, legend=True)
    return pygame.transform.scale(screen, (R.SCREEN_WIDTH * scale, R.SCREEN_HEIGHT * scale))


# ---------------------------------------------------------------- figures

def pick_moments(match: Match) -> list[tuple[int, str]]:
    """Four informative ticks: the first hit, the busiest crossfire, the first kill,
    and the tick with the most hits in the 0.6 s before it. The first hit and the first
    kill are shown at their own tick, so the caption's time is the event's time."""
    hits_at = [t for t, f in enumerate(match.frames) if any(e["type"] == "hit" for e in f.events)]
    kills_at = [t for t, f in enumerate(match.frames)
                if any(e["type"] == "hit" and e["payload"]["killed"] for e in f.events)]
    first_hit = hits_at[0] if hits_at else 1
    lo, hi = 2 * TICKRATE, len(match.frames) - 1
    busiest = max(range(lo, hi), key=lambda t: (len(match.frames[t].shells), -t))
    first_kill = kills_at[0] if kills_at else hi // 2
    def hits_window(t):
        return sum(1 for u in range(max(0, t - TRAIL_TICKS), t + 1)
                   for e in match.frames[u].events if e["type"] == "hit")
    rush = max(range(lo, hi), key=lambda t: (hits_window(t), -t))
    picks, seen = [], set()
    for t, why in ((first_hit, "first hit"), (busiest, "most shells in flight"),
                   (first_kill, "first kill"), (rush, "most hits in 0.6 s")):
        if t not in seen:
            seen.add(t)
            picks.append((t, why))
    return picks


def export_figures(match: Match, out: Path, ticks: list[int] | None,
                   vehicle: str = VEHICLE) -> list[Path]:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame = R._pygame()
    pygame.init()
    out.mkdir(parents=True, exist_ok=True)
    moments = [(t, "") for t in ticks] if ticks else pick_moments(match)
    written = []
    stills = []
    moments = sorted(moments)
    for t, why in moments:
        img = render_still(pygame, match, t, vehicle=vehicle)
        path = out / f"uc2-seed{match.seed}-t{t:04d}.png"
        pygame.image.save(img, str(path))
        written.append(path)
        stills.append((t, why, render_still(pygame, match, t, scale=1, vehicle=vehicle)))
    # 2x2 panel: playfield crops with captions
    if len(stills) >= 2:
        cols = 2
        rows = math.ceil(len(stills) / cols)
        cw, ch, cap, pad = R.HUD_X, R.SCREEN_HEIGHT, 34, 12
        legend_h = 74
        sheet = pygame.Surface((cols * cw + (cols + 1) * pad,
                                rows * (ch + cap) + (rows + 1) * pad + legend_h))
        sheet.fill((255, 255, 255))
        font = R._font(pygame, 20, True)
        for i, (t, why, img) in enumerate(stills):
            cx = pad + (i % cols) * (cw + pad)
            cy = pad + (i // cols) * (ch + cap + pad)
            sheet.blit(img, (cx, cy), pygame.Rect(0, 0, cw, ch))
            text = f"({'abcd'[i] if i < 4 else i + 1}) t = {t / TICKRATE:.2f} s" + (f" — {why}" if why else "")
            sheet.blit(font.render(text, True, (0, 0, 0)), (cx + 4, cy + ch + 6))
        small = R._font(pygame, 16, True)
        for i, line in enumerate((
                "Dots and streaks: shells of the last 0.6 s (colour = shooter).  Rings: hits;  large ring: kill (KO).",
                "Faint dots: tank paths of the last 2 s (colour = player).",
                "+5 Pa\u2192Pb: 5 points paid by the hit player a to the shooter b.  Every shot also pays 1 point to the arena.")):
            sheet.blit(small.render(line, True, (0, 0, 0)), (pad + 4, sheet.get_height() - legend_h + i * 22))
        path = out / f"uc2-seed{match.seed}-panel.png"
        pygame.image.save(sheet, str(path))
        written.append(path)
    pygame.quit()
    return written


# ---------------------------------------------------------------- viewer

def watch(match: Match, speed: float, shots_dir: Path, vehicle: str = VEHICLE) -> None:
    pygame = R._pygame()
    pygame.init()
    display = pygame.display.set_mode((R.WINDOW_WIDTH, R.WINDOW_HEIGHT))
    ref = "  (= UC2 reference log)" if match.is_uc2_reference else ""
    pygame.display.set_caption(f"tank-arena UC2 match — seed {match.seed}{ref}")
    screen = pygame.Surface((R.SCREEN_WIDTH, R.SCREEN_HEIGHT))
    clock = pygame.time.Clock()
    tick, paused, trails, last = 0, False, True, len(match.frames) - 1
    acc = 0.0
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return
            if event.type == pygame.KEYDOWN:
                step = TICKRATE if event.mod & pygame.KMOD_SHIFT else 1
                if event.key == pygame.K_ESCAPE:
                    pygame.quit()
                    return
                if event.key == pygame.K_SPACE:
                    paused = not paused
                elif event.key == pygame.K_RIGHT:
                    tick, paused = min(last, tick + step), True
                elif event.key == pygame.K_LEFT:
                    tick, paused = max(0, tick - step), True
                elif event.key == pygame.K_UP:
                    speed = min(8.0, speed * 2)
                elif event.key == pygame.K_DOWN:
                    speed = max(0.125, speed / 2)
                elif event.key == pygame.K_t:
                    trails = not trails
                elif event.key == pygame.K_HOME:
                    tick, paused = 0, False
                elif event.key == pygame.K_s:
                    shots_dir.mkdir(parents=True, exist_ok=True)
                    path = shots_dir / f"uc2-seed{match.seed}-t{tick:04d}.png"
                    pygame.image.save(render_still(pygame, match, tick, vehicle=vehicle), str(path))
                    print(f"saved {path}")
        screen.fill(R.HUD_GREY)
        draw_field(pygame, screen, match, tick, still=paused, trails=trails, vehicle=vehicle)
        draw_hud(pygame, screen, match, tick, speed, paused)
        R._show_frame(pygame, screen, display)
        dt = clock.tick(60) / 1000.0
        if not paused and tick < last:
            acc += dt * TICKRATE * speed
            while acc >= 1 and tick < last:
                tick += 1
                acc -= 1
        elif tick >= last:
            paused = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=UC2_SEED)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--vehicle", default=VEHICLE, choices=sorted(sprites.STYLES),
                    help="sprite style (viz/sprites.py)")
    ap.add_argument("--figures", default="", help="write stills + a panel to this folder, no window")
    ap.add_argument("--ticks", default="", help="figure ticks, e.g. 120,331,800 (default: picked)")
    ap.add_argument("--shots", default=str(Path(__file__).resolve().parents[1] / "screenshots"),
                    help="folder for S-key screenshots")
    args = ap.parse_args(argv)
    match = simulate(args.seed)
    check = ("matches the UC2 reference log" if match.is_uc2_reference
             else "not the UC2 match (that is seed 42)")
    print(f"seed {args.seed}: {match.shots[-1]} shots, {match.hits[-1]} hits, event log "
          f"sha256 {match.sha256[:16]}… — {check}")
    if args.seed == UC2_SEED and not match.is_uc2_reference:
        print("WARNING: seed 42 no longer reproduces the UC2 reference log")
    if args.figures:
        ticks = [int(t) for t in args.ticks.split(",") if t.strip()] or None
        for path in export_figures(match, Path(args.figures), ticks, vehicle=args.vehicle):
            print(f"wrote {path}")
        return 0
    watch(match, args.speed, Path(args.shots), vehicle=args.vehicle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
