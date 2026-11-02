"""Human multiplayer client with a connected/ready/countdown lobby.

The simulation remains deterministic lockstep: every machine controls exactly
one tank, exchanges only per-tick input, and writes the same JSONL trace.  The
hub is still ``python -m net.relay``; this module is the player-facing client.

Relay example (hub + two player PCs)::

    # hub
    python -m net.relay --host 0.0.0.0 --port 9000 --expect 2

    # player PCs
    python -m arena.multiplayer --id 0 --players 2 --relay HUB_IP:9000
    python -m arena.multiplayer --id 1 --players 2 --relay HUB_IP:9000

Controls: arrows or WASD move, Space fires, Enter/R or the button readies.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from pathlib import Path

from arena.node import Node
from arena.render import (
    AMBER, ARMOR, HUD_GREY, PAPER, SCREEN_HEIGHT, SCREEN_WIDTH,
    WINDOW_HEIGHT, WINDOW_WIDTH, _draw_blocks, _draw_debug, _draw_hud,
    _draw_shell, _draw_tank, _draw_tracks, _font, _pygame, _terrain,
    _show_frame,
)
from arena.world import Controls, World
from net.protocol import parse_addr


class HumanController:
    """Translate keyboard state into Battle City-style cardinal controls."""

    def __init__(self, pygame) -> None:
        self.pygame = pygame
        self.closed = False
        self.last_direction = -math.pi / 2.0
        self._keys = {
            pygame.K_UP: -math.pi / 2.0,
            pygame.K_w: -math.pi / 2.0,
            pygame.K_DOWN: math.pi / 2.0,
            pygame.K_s: math.pi / 2.0,
            pygame.K_LEFT: math.pi,
            pygame.K_a: math.pi,
            pygame.K_RIGHT: 0.0,
            pygame.K_d: 0.0,
        }

    def decide(self, world: World) -> Controls | None:
        for event in self.pygame.event.get():
            if event.type == self.pygame.QUIT:
                self.closed = True
                return None
            if event.type == self.pygame.KEYDOWN and event.key in self._keys:
                self.last_direction = self._keys[event.key]

        pressed = self.pygame.key.get_pressed()
        active = [angle for key, angle in self._keys.items() if pressed[key]]
        direction = (self.last_direction if self.last_direction in active
                     else active[-1] if active else None)
        me = world.tanks[self._local_id]
        aim = direction if direction is not None else me.heading
        return Controls(
            throttle=1.0 if direction is not None else 0.0,
            fire=bool(pressed[self.pygame.K_SPACE]),
            fire_angle=aim,
            # Keeping drive_angle set while idle is intentional: it keeps the
            # world in cardinal arcade mode and makes key release stop now.
            drive_angle=aim,
        )

    def bind_player(self, player_id: int) -> None:
        self._local_id = player_id


class ArenaView:
    def __init__(self, pygame, screen, display, world: World, seed: int,
                 node: Node | None = None) -> None:
        self.pygame = pygame
        self.screen = screen
        self.display = display
        self.ground = _terrain(pygame, world.config, seed)
        self.trails = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        self.hit_flash = {pid: 0 for pid in range(world.n_players)}
        self.recoil = {pid: 0 for pid in range(world.n_players)}
        self.node = node

    def draw_match(self, world: World, events: list[dict],
                   counts: dict[str, int]) -> None:
        for event in events:
            if event["type"] == "hit":
                self.hit_flash[event["payload"]["victim"]] = 5
            elif event["type"] == "fire":
                self.recoil[event["player"]] = 5

        self.screen.fill(HUD_GREY)
        self.screen.blit(self.ground, (0, 0))
        for pid, tank in world.tanks.items():
            if tank.alive:
                _draw_tracks(self.pygame, self.trails, tank.x, tank.y,
                             tank.heading, tank.speed, world.tick)
        self.screen.blit(self.trails, (0, 0))
        _draw_blocks(self.pygame, self.screen, world.blocks)
        for projectile in world.projectiles:
            _draw_shell(self.pygame, self.screen, projectile)
        for pid, tank in world.tanks.items():
            if not tank.alive:
                continue
            _draw_tank(
                self.pygame, self.screen, tank.x, tank.y, tank.heading,
                tank.turret_heading, tank.hp, world.config.max_hp,
                ARMOR[pid % len(ARMOR)], world.config.tank_radius, pid,
                self.hit_flash[pid], self.recoil[pid])
            self.hit_flash[pid] = max(0, self.hit_flash[pid] - 1)
            self.recoil[pid] = max(0, self.recoil[pid] - 1)
        _draw_hud(self.pygame, self.screen, world, counts)
        if self.node is not None:
            _draw_debug(
                self.pygame, self.screen, world, self.node.id,
                len(self.node.connected_players),
                len(self.node.ready_players), len(self.node.inbox))
        self.pygame.draw.rect(
            self.screen, (14, 16, 14), self.screen.get_rect(), 5)
        _show_frame(self.pygame, self.screen, self.display)

    def draw_lobby(self, node: Node, ready_rect, setup_done: bool,
                   countdown: int | None = None, message: str = "") -> None:
        self.screen.fill(HUD_GREY)
        self.screen.blit(self.ground, (0, 0))
        _draw_blocks(self.pygame, self.screen, node.world.blocks)
        veil = self.pygame.Surface(self.screen.get_size(), self.pygame.SRCALPHA)
        veil.fill((8, 10, 9, 145))
        self.screen.blit(veil, (0, 0))
        panel = self.pygame.Rect(
            (SCREEN_WIDTH - 490) // 2, (SCREEN_HEIGHT - 440) // 2, 490, 440)
        self.pygame.draw.rect(self.screen, (18, 21, 18), panel)
        self.pygame.draw.rect(self.screen, (105, 116, 91), panel, 2)

        title = _font(self.pygame, 25, True).render(
            "MULTIPLAYER LINK", True, PAPER)
        subtitle = _font(self.pygame, 12).render(
            "LOCKSTEP FIELD SESSION  /  WAIT FOR EVERY CREW",
            True, (145, 151, 132))
        self.screen.blit(title, title.get_rect(
            center=(panel.centerx, panel.top + 36)))
        self.screen.blit(subtitle, subtitle.get_rect(
            center=(panel.centerx, panel.top + 66)))

        if countdown is not None:
            big = _font(self.pygame, 96, True).render(
                str(countdown) if countdown else "MOVE OUT", True, AMBER)
            self.screen.blit(big, big.get_rect(center=panel.center))
        else:
            for pid in range(node.n):
                y = panel.top + 100 + pid * 42
                connected = pid in node.connected_players
                ready = pid in node.ready_players
                color = ARMOR[pid % len(ARMOR)] if connected else (55, 59, 52)
                self.pygame.draw.rect(self.screen, color,
                                      (panel.left + 50, y, 28, 28))
                self.pygame.draw.rect(self.screen, (20, 22, 19),
                                      (panel.left + 50, y, 28, 28), 2)
                label = _font(self.pygame, 14, True).render(
                    f"PLAYER {pid + 1:02d}" + ("  YOU" if pid == node.id else ""),
                    True, PAPER)
                status = ("READY" if ready else
                          "CONNECTED" if connected else "WAITING")
                status_color = AMBER if ready else (149, 157, 138)
                status_text = _font(self.pygame, 13, True).render(
                    status, True, status_color)
                self.screen.blit(label, (panel.left + 93, y + 6))
                self.screen.blit(status_text,
                                 status_text.get_rect(
                                     right=panel.right - 55, top=y + 7))

            local_ready = node.id in node.ready_players
            enabled = setup_done and not local_ready
            button_color = ((172, 116, 48) if enabled else (57, 62, 54))
            self.pygame.draw.rect(self.screen, button_color, ready_rect)
            self.pygame.draw.rect(self.screen, PAPER, ready_rect, 2)
            button_label = "READY LOCKED" if local_ready else "CLICK READY"
            button = _font(self.pygame, 17, True).render(
                button_label, True, PAPER)
            self.screen.blit(button, button.get_rect(center=ready_rect.center))
            hint = message or ("ENTER / R also readies" if setup_done else
                               "Connecting to the other crews…")
            hint_text = _font(self.pygame, 11).render(
                hint, True, (151, 157, 139))
            self.screen.blit(hint_text, hint_text.get_rect(
                center=(panel.centerx, panel.bottom - 25)))

        _show_frame(self.pygame, self.screen, self.display)


async def _countdown(pygame, view: ArenaView, node: Node, ready_rect) -> None:
    for number in (3, 2, 1, 0):
        until = asyncio.get_running_loop().time() + (0.65 if number else 0.45)
        while asyncio.get_running_loop().time() < until:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise KeyboardInterrupt
            view.draw_lobby(node, ready_rect, True, countdown=number)
            await asyncio.sleep(1 / 30)


async def play(args: argparse.Namespace) -> int:
    if args.topology == "mesh":
        if not args.peers:
            raise SystemExit("mesh topology requires --peers")
        peers = [parse_addr(value) for value in args.peers.split(",")]
        players = args.players or len(peers)
        if players != len(peers):
            raise SystemExit("--players must match --peers")
    else:
        if not args.relay or not args.players:
            raise SystemExit("relay topology requires --relay and --players")
        players = args.players
        peers = []
    if not 0 <= args.id < players:
        raise SystemExit("--id must be between 0 and players-1")

    pygame = _pygame()
    pygame.init()
    display = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
    screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption(
        f"tank-arena multiplayer — player {args.id + 1}")
    out_path = args.out or f"trace-player{args.id}.jsonl"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    node = Node(args.id, players, args.tickrate, args.duration,
                args.seed, out_path)
    view = ArenaView(pygame, screen, display, node.world, args.seed, node)
    ready_rect = pygame.Rect(
        (SCREEN_WIDTH - 230) // 2, (SCREEN_HEIGHT - 440) // 2 + 350,
        230, 48)

    setup_task = asyncio.create_task(
        node.setup_mesh(peers) if args.topology == "mesh"
        else node.setup_relay(parse_addr(args.relay)))
    ready_sent = False
    try:
        while len(node.ready_players) < players:
            setup_done = setup_task.done() and setup_task.exception() is None
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise KeyboardInterrupt
                wants_ready = (
                    event.type == pygame.KEYDOWN
                    and event.key in (pygame.K_RETURN, pygame.K_r))
                wants_ready = wants_ready or (
                    event.type == pygame.MOUSEBUTTONDOWN
                    and event.button == 1
                    and ready_rect.collidepoint(
                        event.pos[0] * SCREEN_WIDTH / WINDOW_WIDTH,
                        event.pos[1] * SCREEN_HEIGHT / WINDOW_HEIGHT))
                if wants_ready and setup_done and not ready_sent:
                    await node.mark_ready()
                    ready_sent = True
            if args.auto_ready and setup_done and not ready_sent:
                await node.mark_ready()
                ready_sent = True
            view.draw_lobby(node, ready_rect, setup_done)
            if setup_task.done():
                await setup_task
            await asyncio.sleep(1 / 30)

        await node.wait_until_ready()
        await _countdown(pygame, view, node, ready_rect)
        controller = HumanController(pygame)
        controller.bind_player(args.id)
        counts = await node.run(controller.decide, view.draw_match)
        if not controller.closed:
            until = asyncio.get_running_loop().time() + 2.5
            while asyncio.get_running_loop().time() < until:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        break
                view.draw_lobby(node, ready_rect, True,
                                message=(f"MATCH COMPLETE — {counts['hit']} HITS, "
                                         f"{counts['break']} BRICKS"))
                await asyncio.sleep(1 / 30)
    except KeyboardInterrupt:
        if node.writers:
            await node.broadcast({"t": "abort", "id": node.id,
                                  "reason": f"player {node.id + 1} left"})
    finally:
        pygame.quit()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m arena.multiplayer",
        description="Human Battle City-style deterministic multiplayer.")
    parser.add_argument("--id", type=int, required=True,
                        help="local player id, zero based")
    parser.add_argument("--players", type=int, default=0)
    parser.add_argument("--topology", choices=("mesh", "relay"),
                        default="relay")
    parser.add_argument("--relay", default="")
    parser.add_argument("--peers", default="")
    parser.add_argument("--tickrate", type=int, default=20)
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="")
    parser.add_argument("--auto-ready", action="store_true",
                        help="testing only: skip the Ready click")
    return asyncio.run(play(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
