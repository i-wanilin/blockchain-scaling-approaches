"""Optional pygame renderer for live and replayed tank-arena battles.

The renderer deliberately stays asset-free and uses a chunky early-console
language: black playfield, orange brick mazes, bright steel, cardinal pixel
tanks, hard-edged shells, and a fixed grey status rail.

    pip install pygame
    python -m arena.render --players 6 --tickrate 20 --seed 42
    python -m arena.render --replay trace.jsonl --speed 2
    python -m arena.render --duration 5 --uncapped --screenshot arena.png
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from arena.bots import make_bots
from arena.world import Config, World

SCREEN_WIDTH = 960
SCREEN_HEIGHT = 720
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 960
HUD_X = 800
FIELD = (5, 6, 5)
FIELD_DARK = (0, 0, 0)
HUD_GREY = (119, 119, 113)
INK = (12, 12, 12)
PAPER = (242, 239, 211)
AMBER = (255, 177, 27)
RED = (241, 70, 54)
TRACK = (19, 19, 17)
ARMOR = [
    (255, 197, 36), (99, 226, 232), (239, 102, 161), (142, 222, 76),
    (255, 132, 45), (223, 218, 199), (157, 119, 224), (73, 158, 235),
]
FLASH_TICKS = 5


def _pygame():
    try:
        import pygame  # noqa: PLC0415
    except ImportError:
        print("arena.render needs pygame (the core does not): "
              "pip install pygame", file=sys.stderr)
        raise SystemExit(1) from None
    return pygame


def _shade(color: tuple[int, int, int], amount: int) -> tuple[int, int, int]:
    return tuple(max(0, min(255, channel + amount)) for channel in color)


def _point(x: float, y: float, angle: float, lx: float,
           ly: float) -> tuple[int, int]:
    ca, sa = math.cos(angle), math.sin(angle)
    return (round(x + lx * ca - ly * sa),
            round(y + lx * sa + ly * ca))


def _poly(x: float, y: float, angle: float,
          points: list[tuple[float, float]]) -> list[tuple[int, int]]:
    return [_point(x, y, angle, px, py) for px, py in points]


def _terrain(pygame, cfg: Config, seed: int):
    """Build the intentionally empty black arcade playfield."""
    del seed
    surface = pygame.Surface((int(cfg.arena_width), int(cfg.arena_height)))
    surface.fill(FIELD)
    pygame.draw.rect(surface, (31, 31, 29), surface.get_rect(), 4)
    return surface


def _font(pygame, size: int, bold: bool = False):
    return pygame.font.SysFont("consolas", size, bold=bold)


def _draw_tracks(pygame, layer, x: float, y: float, heading: float,
                 speed: float, tick: int) -> None:
    del pygame, layer, x, y, heading, speed, tick


def _draw_tank(pygame, screen, x: float, y: float, heading: float,
               turret_heading: float, hp: int, max_hp: int,
               color: tuple[int, int, int], radius: float,
               pid: int = 0, hit_flash: int = 0, recoil: int = 0) -> None:
    """Draw a deliberately low-resolution four-direction tank sprite."""
    del radius, hp, max_hp, pid
    hull_color = PAPER if hit_flash else color
    sprite = pygame.Surface((28, 30), pygame.SRCALPHA)
    # Two black tracks with chunky tread highlights.
    pygame.draw.rect(sprite, TRACK, (2, 8, 6, 21))
    pygame.draw.rect(sprite, TRACK, (20, 8, 6, 21))
    for sy in (9, 14, 19, 24):
        pygame.draw.rect(sprite, _shade(hull_color, -35), (3, sy, 4, 3))
        pygame.draw.rect(sprite, _shade(hull_color, -35), (21, sy, 4, 3))
    pygame.draw.rect(sprite, _shade(hull_color, -20), (8, 11, 12, 17))
    pygame.draw.rect(sprite, hull_color, (10, 8, 8, 17))
    pygame.draw.rect(sprite, _shade(hull_color, 35), (11, 10, 6, 5))
    pygame.draw.rect(sprite, INK, (12, 13, 4, 4))
    # Base sprite faces up; quantization is part of the visual language.
    quarter = round((heading + math.pi / 2.0) / (math.pi / 2.0))
    rotated = pygame.transform.rotate(sprite, -quarter * 90)
    screen.blit(rotated, rotated.get_rect(center=(round(x), round(y))))

    gun_angle = round(turret_heading / (math.pi / 2.0)) * math.pi / 2.0
    kick = min(4, recoil)
    start = _point(x, y, gun_angle, 2, 0)
    tip = _point(x, y, gun_angle, 19 - kick, 0)
    pygame.draw.line(screen, hull_color, start, tip, 4)
    if recoil:
        flame = _point(x, y, gun_angle, 23, 0)
        pygame.draw.rect(screen, PAPER, (flame[0] - 2, flame[1] - 2, 5, 5))
        pygame.draw.line(screen, AMBER, (flame[0] - 5, flame[1]),
                         (flame[0] + 5, flame[1]), 2)
        pygame.draw.line(screen, AMBER, (flame[0], flame[1] - 5),
                         (flame[0], flame[1] + 5), 2)


def _draw_shell(pygame, screen, projectile) -> None:
    angle = math.atan2(projectile.vy, projectile.vx)
    horizontal = abs(math.cos(angle)) > abs(math.sin(angle))
    rect = pygame.Rect(0, 0, 8 if horizontal else 4,
                       4 if horizontal else 8)
    rect.center = (round(projectile.x), round(projectile.y))
    pygame.draw.rect(screen, PAPER, rect)


def _draw_blocks(pygame, screen, blocks) -> None:
    """Draw orange brick tiles and high-contrast four-plate steel."""
    for block in blocks.values():
        size = round(block.size)
        rect = pygame.Rect(round(block.x - size / 2),
                           round(block.y - size / 2), size, size)
        if block.kind == "brick":
            pygame.draw.rect(screen, (63, 32, 15), rect)
            for row in range(5):
                y = rect.top + row * 8 + 1
                offset = -4 if row % 2 else 0
                for col in range(-1, 3):
                    x = rect.left + offset + col * 16 + 1
                    brick_rect = pygame.Rect(x, y, 14, 6).clip(rect)
                    if brick_rect.width <= 0 or brick_rect.height <= 0:
                        continue
                    pygame.draw.rect(screen, (219, 112, 22), brick_rect)
                    pygame.draw.line(screen, (255, 180, 42),
                                     brick_rect.topleft,
                                     (brick_rect.right - 1, brick_rect.top), 2)
        else:
            pygame.draw.rect(screen, (72, 74, 72), rect)
            half = size // 2
            for row in range(2):
                for col in range(2):
                    plate = pygame.Rect(rect.left + col * half + 1,
                                        rect.top + row * half + 1,
                                        half - 2, half - 2)
                    pygame.draw.rect(screen, (226, 226, 213), plate)
                    pygame.draw.line(screen, (145, 147, 143),
                                     plate.bottomleft, plate.topright, 3)
                    pygame.draw.rect(screen, (255, 255, 242), plate, 2)


def _draw_hud(pygame, screen, world: World, counts: dict[str, int],
              strategies: dict[int, str] | None = None) -> None:
    pygame.draw.rect(screen, HUD_GREY, (HUD_X, 0, SCREEN_WIDTH - HUD_X,
                                        SCREEN_HEIGHT))
    pygame.draw.line(screen, (51, 51, 49), (HUD_X, 0),
                     (HUD_X, SCREEN_HEIGHT), 6)
    title = _font(pygame, 22, True).render("TANKS", False, INK)
    screen.blit(title, title.get_rect(center=(HUD_X + 80, 28)))
    y = 62
    for pid in range(world.n_players):
        tank = world.tanks[pid]
        color = ARMOR[pid % len(ARMOR)]
        pygame.draw.rect(screen, color, (HUD_X + 22, y, 18, 18))
        pygame.draw.rect(screen, INK, (HUD_X + 22, y, 18, 18), 3)
        respawn = (f" R{math.ceil(tank.respawn_ticks / world.tickrate)}"
                   if not tank.alive else "")
        role = strategies[pid][0] if strategies and pid in strategies else ""
        label = _font(pygame, 15, True).render(
            f"P{pid + 1}{role} {tank.kills:02d}{respawn}", False, INK)
        screen.blit(label, (HUD_X + 48, y - 1))
        hp_blocks = (max(0, math.ceil(tank.hp / world.config.max_hp * 4))
                     if tank.alive else 0)
        for index in range(4):
            pygame.draw.rect(screen, RED if index < hp_blocks else (68, 68, 65),
                             (HUD_X + 48 + index * 14, y + 19, 10, 5))
        y += 48
    info_y = max(SCREEN_HEIGHT - 210, y + 12)
    for text_value, color in (
            (f"FIRE {counts['fire']:03d}", INK),
            (f"HIT  {counts['hit']:03d}", INK),
            (f"BRK  {counts.get('break', 0):03d}", INK),
            (f"TIME {world.t_ms / 1000:03.0f}", INK)):
        txt = _font(pygame, 15, True).render(text_value, False, color)
        screen.blit(txt, (HUD_X + 31, info_y))
        info_y += 24
    flag_y = SCREEN_HEIGHT - 52
    pygame.draw.rect(screen, AMBER, (HUD_X + 34, flag_y, 44, 20))
    pygame.draw.polygon(screen, AMBER,
                        [(HUD_X + 78, flag_y), (HUD_X + 108, flag_y + 10),
                         (HUD_X + 78, flag_y + 20)])
    pygame.draw.rect(screen, INK, (HUD_X + 30, flag_y - 5, 5, 32))
    stage = _font(pygame, 17, True).render("1", False, INK)
    screen.blit(stage, (HUD_X + 108, flag_y))


def _draw_replay_hud(pygame, screen, counts: dict[str, int], tick: int) -> None:
    pygame.draw.rect(screen, HUD_GREY, (HUD_X, 0, SCREEN_WIDTH - HUD_X,
                                        SCREEN_HEIGHT))
    pygame.draw.line(screen, (51, 51, 49), (HUD_X, 0),
                     (HUD_X, SCREEN_HEIGHT), 6)
    title = _font(pygame, 21, True).render("REPLAY", False, INK)
    screen.blit(title, title.get_rect(center=(HUD_X + 80, 42)))
    for index, value in enumerate((
            f"TICK {tick:05d}", f"FIRE {counts['fire']:03d}",
            f"HIT  {counts['hit']:03d}",
            f"BRK  {counts.get('break', 0):03d}")):
        text_value = _font(pygame, 14, True).render(value, False, INK)
        screen.blit(text_value, (HUD_X + 30, 110 + index * 34))


def _draw_debug(pygame, screen, world: World, local_id: int,
                connected: int, ready: int, queued: int,
                agent_mix: str = "") -> None:
    """Small always-on instrumentation strip for Pi/network test sessions."""
    tank = world.tanks[local_id]
    direction_index = round(
        (tank.heading + math.pi / 2.0) / (math.pi / 2.0)) % 4
    direction = ("UP", "RT", "DN", "LT")[direction_index]
    gameplay = (
        f"P{local_id + 1}  TICK {world.tick:05d}  {world.tickrate:02d}HZ  "
        f"XY {tank.x:03.0f},{tank.y:03.0f}  DIR {direction}  "
        f"SPD {abs(tank.speed):03.0f}  HP {tank.hp:03d}  AM {tank.ammo:02d}  "
        f"CD {tank.cooldown_ticks:02d}")
    network = (
        f"SHELL {len(world.projectiles):02d}  BLOCK {len(world.blocks):02d}  "
        f"NET {connected}/{world.n_players}  READY {ready}  QUEUE {queued}"
        + (f"  AI {agent_mix}" if agent_mix else ""))
    panel = pygame.Surface((HUD_X - 8, 42), pygame.SRCALPHA)
    panel.fill((0, 0, 0, 220))
    screen.blit(panel, (4, 4))
    font = _font(pygame, 13, True)
    screen.blit(font.render(gameplay, False, (130, 232, 133)), (8, 6))
    screen.blit(font.render(network, False, (104, 172, 109)), (8, 23))


def _show_frame(pygame, canvas, display) -> None:
    """Nearest-neighbour upscale keeps deliberate pixel edges crisp."""
    pygame.transform.scale(canvas, display.get_size(), display)
    pygame.display.flip()


def _present(pygame, screen, display, screenshot: str) -> None:
    _show_frame(pygame, screen, display)
    if screenshot:
        target = Path(screenshot)
        target.parent.mkdir(parents=True, exist_ok=True)
        pygame.image.save(screen, str(target))


def replay(path: str, speed: float, screenshot: str = "",
           uncapped: bool = False) -> None:
    pygame = _pygame()
    cfg = Config()
    by_tick: dict[int, list[dict]] = defaultdict(list)
    tickrate_guess = 20.0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            ev = json.loads(line)
            by_tick[ev["tick"]].append(ev)
            if ev["tick"] > 0 and ev["t_ms"] > 0:
                tickrate_guess = ev["tick"] * 1000.0 / ev["t_ms"]
    if not by_tick:
        print("empty trace", file=sys.stderr)
        return

    pygame.init()
    display = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
    screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption(f"tank-arena replay — {path}")
    clock = pygame.time.Clock()
    ground = _terrain(pygame, cfg, seed=42)
    trails = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    hp: dict[int, int] = defaultdict(lambda: cfg.max_hp)
    dead: set[int] = set()
    state: dict[int, dict] = {}
    hit_flash: dict[int, int] = defaultdict(int)
    recoil: dict[int, int] = defaultdict(int)
    counts = {"move": 0, "fire": 0, "hit": 0, "break": 0}
    blocks = World(n_players=1, tickrate=max(1, round(tickrate_guess)),
                   seed=42, config=cfg).blocks

    ticks = sorted(by_tick)
    for tick in ticks:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return
        for ev in by_tick[tick]:
            counts[ev["type"]] += 1
            if ev["type"] == "move":
                pid, move = ev["player"], ev["payload"]
                state[pid] = move
                if pid in dead:
                    dead.remove(pid)
                    hp[pid] = cfg.max_hp
            elif ev["type"] == "hit":
                victim = ev["payload"]["victim"]
                hp[victim] -= ev["payload"]["damage"]
                hit_flash[victim] = FLASH_TICKS
                if ev["payload"]["killed"]:
                    dead.add(victim)
            elif ev["type"] == "fire":
                recoil[ev["player"]] = FLASH_TICKS
            elif ev["type"] == "break":
                blocks.pop(ev["payload"]["block"], None)

        screen.fill(HUD_GREY)
        screen.blit(ground, (0, 0))
        screen.blit(trails, (0, 0))
        _draw_blocks(pygame, screen, blocks)
        for pid, move in state.items():
            if pid in dead:
                continue
            _draw_tracks(pygame, trails, move["x"], move["y"],
                         move["heading"], move["speed"], tick)
            _draw_tank(pygame, screen, move["x"], move["y"],
                       move["heading"],
                       move.get("turret_heading", move["heading"]),
                       hp[pid], cfg.max_hp, ARMOR[pid % len(ARMOR)],
                       cfg.tank_radius, pid, hit_flash[pid], recoil[pid])
            hit_flash[pid] = max(0, hit_flash[pid] - 1)
            recoil[pid] = max(0, recoil[pid] - 1)
        _draw_replay_hud(pygame, screen, counts, tick)
        pygame.draw.rect(screen, (45, 45, 42), (0, 0, HUD_X,
                                                SCREEN_HEIGHT), 5)
        _show_frame(pygame, screen, display)
        if not uncapped:
            clock.tick(tickrate_guess * speed)
    _present(pygame, screen, display, screenshot)
    pygame.quit()


def live(players: int, tickrate: int, seed: int, duration: float,
         screenshot: str = "", uncapped: bool = False) -> None:
    pygame = _pygame()
    cfg = Config()
    world = World(n_players=players, tickrate=tickrate, seed=seed,
                  config=cfg)
    bots = make_bots(players, seed, tickrate)

    pygame.init()
    display = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
    screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption(
        f"tank-arena — {players} vehicles / {tickrate} Hz / seed {seed}")
    clock = pygame.time.Clock()
    ground = _terrain(pygame, cfg, seed)
    trails = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    hit_flash: dict[int, int] = defaultdict(int)
    recoil: dict[int, int] = defaultdict(int)
    counts = {"move": 0, "fire": 0, "hit": 0, "break": 0}

    total_ticks = int(round(duration * tickrate))
    for _ in range(total_ticks):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return
        controls = {pid: bot.decide(world) for pid, bot in bots.items()}
        events = world.step(controls)
        for ev in events:
            counts[ev["type"]] += 1
            if ev["type"] == "hit":
                hit_flash[ev["payload"]["victim"]] = FLASH_TICKS
            elif ev["type"] == "fire":
                recoil[ev["player"]] = FLASH_TICKS

        screen.fill(HUD_GREY)
        screen.blit(ground, (0, 0))
        for pid in range(players):
            tank = world.tanks[pid]
            if tank.alive:
                _draw_tracks(pygame, trails, tank.x, tank.y, tank.heading,
                             tank.speed, world.tick)
        screen.blit(trails, (0, 0))
        _draw_blocks(pygame, screen, world.blocks)
        for projectile in world.projectiles:
            _draw_shell(pygame, screen, projectile)
        for pid in range(players):
            tank = world.tanks[pid]
            if not tank.alive:
                continue
            _draw_tank(pygame, screen, tank.x, tank.y, tank.heading,
                       tank.turret_heading, tank.hp, cfg.max_hp,
                       ARMOR[pid % len(ARMOR)], cfg.tank_radius, pid,
                       hit_flash[pid], recoil[pid])
            hit_flash[pid] = max(0, hit_flash[pid] - 1)
            recoil[pid] = max(0, recoil[pid] - 1)
        strategies = {pid: bot.strategy for pid, bot in bots.items()}
        _draw_hud(pygame, screen, world, counts, strategies)
        hunters = sum(bot.strategy == "HUNTER" for bot in bots.values())
        flankers = players - hunters
        _draw_debug(pygame, screen, world, 0, players, players, 0,
                    f"HUNT {hunters} / FLANK {flankers}")
        pygame.draw.rect(screen, (45, 45, 42), (0, 0, HUD_X,
                                                SCREEN_HEIGHT), 5)
        _show_frame(pygame, screen, display)
        if not uncapped:
            clock.tick(tickrate)
    _present(pygame, screen, display, screenshot)
    pygame.quit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m arena.render",
        description="Watch a tank-arena battle (optional pygame).")
    ap.add_argument("--replay", default="",
                    help="path to a trace.jsonl to replay")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="replay speed multiplier")
    ap.add_argument("--players", type=int, default=6)
    ap.add_argument("--tickrate", type=int, default=20)
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--screenshot", default="",
                    help="save the final rendered frame as PNG")
    ap.add_argument("--uncapped", action="store_true",
                    help="render without real-time pacing (capture/testing)")
    args = ap.parse_args(argv)
    if args.replay:
        replay(args.replay, args.speed, args.screenshot, args.uncapped)
    else:
        live(args.players, args.tickrate, args.seed, args.duration,
             args.screenshot, args.uncapped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
