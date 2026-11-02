"""One board's view of a measured UC2 match: the game at the times that board lived it.

viz/match.py plays every tick evenly at 50 ms. This module replays a match from a
cluster run folder as one player's Raspberry Pi saw it:

- each tick appears when that board computed it (results/p<k>/game/ticks.csv,
  t_actual), so the lockstep waits for late inputs show as freezes, with the tick
  lag and a "waiting for inputs" warning in the HUD;
- that board's own payments come from its ledger (results/p<k>/pc/ledger.csv): which
  are paid and which still wait at the current moment, the latest confirmation time,
  and the share of its shots paid before it fired again (the next-shot deadline of
  measure/analyze_uc2.py).

The playfield is re-run from the frozen game (viz.match.simulate), as in viz/match.py.
The board's own event log must hash to the UC2 reference log, so the simulated frames
are exactly the events that board logged; otherwise the module refuses.

    python -m viz.board ../results/pi/uc2/<run> --player p3        # watch bath4 (p3)
    python -m viz.board ../results/pi/uc2/<run> --player bath4 --speed 0.5
    python -m viz.board ../results/pi/uc2/<run> --stats            # every board, no window

Keys: SPACE pause, LEFT/RIGHT one tick (SHIFT: one second), UP/DOWN speed,
T trails on/off, S save the current frame, HOME restart, ESC quit.

Player names: the run's p0..p5 are P1..P6 on screen (viz/match.py numbers from 1).
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from arena import render as R
from viz import match as M

WAIT_MS = 100.0          # a gap between two ticks longer than this shows as "waiting for inputs"
SPARK_S = 5.0            # HUD: tick lag of the last 5 s
STRIP_S = 3.0            # HUD: own payments generated in the last 3 s
LAG_SCALE_MS = 300.0     # HUD: full height of the lag sparkline
GREEN = (110, 220, 90)
_ID = re.compile(r"/t(\d+)/(fire|hit)\d+$")


def _open_text(path: Path):
    """path, or path.gz (run folders compress their large files)."""
    if path.exists():
        return open(path, encoding="utf-8")
    gz = path.with_name(path.name + ".gz")
    if gz.exists():
        return io.TextIOWrapper(gzip.open(gz), encoding="utf-8")
    raise FileNotFoundError(f"{path} (or .gz) not found")


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Payment:
    tick: int
    kind: str                  # "fire" or "hit"
    to: str
    outcome: str               # final outcome in the ledger: succeeded, unresolved, failed
    t_generated: float
    t_confirmed: float | None  # None when it never succeeded


@dataclass
class BoardView:
    run: str
    stack: str
    player: str                # "p3"
    board: str                 # "bath4"
    pid: int                   # 3
    times: list[float]         # wall time each frame appeared, frames 0..N (frame 0: one tick before tick 1)
    lag_ms: list[float]        # per frame: t_actual - t_intended (frame 0: 0)
    payments: list[Payment]    # this board's own payments, by t_generated
    trace_sha256: str

    @property
    def last(self) -> int:
        return len(self.times) - 1

    def frame_at(self, wall: float) -> int:
        """The last frame this board had computed at wall time `wall`."""
        return max(0, min(self.last, bisect.bisect_right(self.times, wall) - 1))

    def waiting_ms(self, wall: float) -> float:
        """How long this board has been waiting for the next tick at `wall` (0 after the last)."""
        i = self.frame_at(wall)
        return 0.0 if i >= self.last else max(0.0, (wall - self.times[i]) * 1000.0)

    def payment_state(self, wall: float) -> dict:
        """This board's payments at wall time `wall`: generated so far, paid, waiting, and
        the confirmation time of the latest one paid."""
        gen = [p for p in self.payments if p.t_generated <= wall]
        paid = [p for p in gen if p.t_confirmed is not None and p.t_confirmed <= wall]
        last = max(paid, key=lambda p: p.t_confirmed, default=None)
        return {"generated": len(gen), "paid": len(paid), "waiting": len(gen) - len(paid),
                "last_latency_s": (last.t_confirmed - last.t_generated) if last else None}

    def next_shot(self, wall: float | None = None) -> tuple[int, int]:
        """(shots paid before this board fired again, shots judged) up to `wall`, as
        measure/analyze_uc2.py judges them: each shot but the last is judged once the
        next one has been fired; it is on time if it succeeded before that moment."""
        fires = sorted((p for p in self.payments if p.kind == "fire"), key=lambda p: p.tick)
        ok = judged = 0
        for cur, nxt in zip(fires, fires[1:]):
            if wall is not None and nxt.t_generated > wall:
                break
            judged += 1
            if cur.t_confirmed is not None and cur.t_confirmed <= nxt.t_generated:
                ok += 1
        return ok, judged


def _players(run: Path) -> dict[str, str]:
    """p<k> -> board, from summary.json, else from experiment.env."""
    try:
        with open(run / "summary.json", encoding="utf-8") as fh:
            players = json.load(fh).get("players")
        if players:
            return dict(players)
    except (OSError, ValueError):
        pass
    with open(run / "experiment.env", encoding="utf-8") as fh:
        m = re.search(r"^PLAYERS=\(([^)]*)\)", fh.read(), re.M)
    if not m:
        raise ValueError(f"{run}: no players in summary.json or experiment.env")
    return {f"p{k}": b for k, b in enumerate(m.group(1).split())}


def _stack(run: Path) -> str:
    try:
        with open(run / "experiment.env", encoding="utf-8") as fh:
            m = re.search(r"^STACK=(\S+)", fh.read(), re.M)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def resolve_player(run: Path, who: str) -> tuple[str, str]:
    """'p3', '3' or 'bath4' -> ('p3', 'bath4')."""
    players = _players(run)
    key = f"p{who}" if who.isdigit() else who
    if key in players:
        return key, players[key]
    for p, b in players.items():
        if b == who:
            return p, b
    raise ValueError(f"{who!r} is not a player of {run.name}: {players}")


def load_board(run: Path, who: str) -> BoardView:
    run = Path(run)
    player, board = resolve_player(run, who)
    base = run / "results" / player
    digest = hashlib.sha256()
    with _open_text(base / "game" / "trace.jsonl") as fh:
        for line in fh:
            digest.update(line.encode("utf-8"))
    rows = list(csv.DictReader(_open_text(base / "game" / "ticks.csv")))
    ticks = [int(r["tick"]) for r in rows]
    if ticks != list(range(1, len(ticks) + 1)):
        raise ValueError(f"{base}/game/ticks.csv: ticks are not 1..{len(ticks)} in order")
    actual = [float(r["t_actual"]) for r in rows]
    intended = [float(r["t_intended"]) for r in rows]
    times = [actual[0] - 1.0 / M.TICKRATE] + actual
    lag = [0.0] + [(a - i) * 1000.0 for a, i in zip(actual, intended)]
    payments = []
    for r in csv.DictReader(_open_text(base / "pc" / "ledger.csv")):
        m = _ID.search(r["id"])
        t_gen = _num(r["t_generated"])
        if not m or t_gen is None:
            continue
        ok = r["outcome"] == "succeeded"
        payments.append(Payment(int(m.group(1)), m.group(2), r["to"], r["outcome"], t_gen,
                                _num(r["t_confirmed"]) if ok else None))
    payments.sort(key=lambda p: p.t_generated)
    return BoardView(run.name, _stack(run), player, board, int(player[1:]), times, lag, payments,
                     digest.hexdigest())


def board_stats(view: BoardView) -> dict:
    lags = sorted(view.lag_ms[1:])
    gaps = [(b - a) * 1000.0 for a, b in zip(view.times[1:], view.times[2:])]
    pct = lambda q: lags[min(len(lags) - 1, int(q * len(lags)))]
    ok, judged = view.next_shot()
    final = view.payment_state(float("inf"))
    return {"board": view.board, "player": view.player, "ticks": view.last,
            "lag_p50_ms": pct(0.5), "lag_p99_ms": pct(0.99), "lag_max_ms": lags[-1],
            "waits_over_100ms": sum(g > WAIT_MS for g in gaps), "longest_wait_ms": max(gaps),
            "payments": final["generated"], "paid": final["paid"], "never_paid": final["waiting"],
            "shots_on_time": ok, "shots_judged": judged}


# ---------------------------------------------------------------- drawing

def _text(pygame, screen, text, x, y, size=11, color=R.INK):
    screen.blit(R._font(pygame, size, True).render(text, True, color), (x, y))


def draw_board_panel(pygame, screen, view: BoardView, wall: float, speed: float | None,
                     paused: bool) -> None:
    """The lower HUD (below viz.match.draw_hud's player list): this board's timing and payments."""
    x0, y = R.HUD_X + 14, 462
    w = R.SCREEN_WIDTH - R.HUD_X - 28
    pygame.draw.rect(screen, R.HUD_GREY, (R.HUD_X + 3, y - 4, R.SCREEN_WIDTH - R.HUD_X - 3,
                                          R.SCREEN_HEIGHT - y + 4))
    pygame.draw.line(screen, (68, 68, 65), (x0, y - 3), (x0 + w, y - 3), 2)
    _text(pygame, screen, f"VIEW: {view.board} = P{view.pid + 1}", x0, y, 13)
    rep = re.search(r"-(smoke-)?rep(\d+)$", view.run)
    _text(pygame, screen, f"{view.stack} {(rep.group(1) or '') + 'rep' + rep.group(2) if rep else ''}",
          x0, y + 17)   # the full run name is in the window title
    i = view.frame_at(wall)
    _text(pygame, screen, f"tick lag {view.lag_ms[i]:4.0f} ms", x0, y + 33, 13)
    waiting = view.waiting_ms(wall)
    if waiting > WAIT_MS:
        _text(pygame, screen, f"WAITING {waiting:4.0f} ms", x0, y + 50, 13, R.RED)

    # tick lag of the last SPARK_S seconds, one bar per tick
    top, h = y + 70, 30
    pygame.draw.rect(screen, (40, 40, 38), (x0, top, w, h))
    n = int(SPARK_S * M.TICKRATE)
    for k, f in enumerate(range(max(1, i - n + 1), i + 1)):
        lag = view.lag_ms[f]
        bh = max(1, round(h * min(1.0, lag / LAG_SCALE_MS)))
        bx = x0 + round((k + n - min(n, i)) * w / n)
        pygame.draw.line(screen, R.RED if lag > WAIT_MS else GREEN, (bx, top + h), (bx, top + h - bh))
    _text(pygame, screen, f"lag, last {SPARK_S:g} s (top {LAG_SCALE_MS:g})", x0, top + h + 2, 10)

    st = view.payment_state(wall)
    y2 = top + h + 20
    _text(pygame, screen, "MY PAYMENTS", x0, y2, 13)
    _text(pygame, screen, f"paid     {st['paid']:4d}", x0, y2 + 17)
    _text(pygame, screen, f"waiting  {st['waiting']:4d}", x0, y2 + 30,
          color=R.RED if st["waiting"] > 5 else R.INK)
    lat = st["last_latency_s"]
    _text(pygame, screen, "last paid " + (f"{lat:6.2f} s" if lat is not None else "     -"), x0, y2 + 43)
    ok, judged = view.next_shot(wall)
    share = f"{100 * ok / judged:3.0f} %" if judged else "  -"
    _text(pygame, screen, f"paid before next", x0, y2 + 56)
    _text(pygame, screen, f"own shot  {share}", x0, y2 + 69)

    # own payments of the last STRIP_S seconds: green paid, amber still waiting
    top2 = y2 + 86
    pygame.draw.rect(screen, (40, 40, 38), (x0, top2, w, 14))
    for p in view.payments:
        if wall - STRIP_S < p.t_generated <= wall:
            px = x0 + round((p.t_generated - (wall - STRIP_S)) / STRIP_S * (w - 4)) + 2
            paid = p.t_confirmed is not None and p.t_confirmed <= wall
            pygame.draw.circle(screen, GREEN if paid else R.AMBER, (px, top2 + (4 if p.kind == "fire" else 10)), 2)
    _text(pygame, screen, f"my payments, last {STRIP_S:g} s", x0, top2 + 16, 10)

    if speed is not None:
        state = "PAUSED" if paused else f"x{speed:g}"
        _text(pygame, screen, f"{state}  SPC ←→ ↑↓ S", x0, R.SCREEN_HEIGHT - 16, 10)


def draw_view(pygame, screen, match: M.Match, view: BoardView, wall: float, trails: bool,
              speed: float | None = None, paused: bool = False) -> None:
    tick = view.frame_at(wall)
    screen.fill(R.HUD_GREY)
    M.draw_field(pygame, screen, match, tick, still=paused, trails=trails)
    x, y, *_rest, alive, _k = match.frames[tick].tanks[view.pid]
    if alive:   # mark this board's own tank
        pygame.draw.circle(screen, (255, 255, 255), (round(x), round(y)), 25, 2)
    if view.waiting_ms(wall) > WAIT_MS:   # the frozen field gets a red frame while inputs are late
        pygame.draw.rect(screen, R.RED, (0, 0, R.HUD_X, R.SCREEN_HEIGHT), 5)
    M.draw_hud(pygame, screen, match, tick)
    draw_board_panel(pygame, screen, view, wall, speed, paused)


def save_still(pygame, match, view, wall, path: Path, trails: bool = True) -> None:
    screen = pygame.Surface((R.SCREEN_WIDTH, R.SCREEN_HEIGHT))
    draw_view(pygame, screen, match, view, wall, trails)
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(pygame.transform.scale(screen, (R.SCREEN_WIDTH * 2, R.SCREEN_HEIGHT * 2)), str(path))


# ---------------------------------------------------------------- viewer

def watch(match: M.Match, view: BoardView, speed: float, shots_dir: Path) -> None:
    pygame = R._pygame()
    pygame.init()
    display = pygame.display.set_mode((R.WINDOW_WIDTH, R.WINDOW_HEIGHT))
    pygame.display.set_caption(f"UC2 {view.run} — as {view.board} ({view.player}) saw it")
    screen = pygame.Surface((R.SCREEN_WIDTH, R.SCREEN_HEIGHT))
    clock = pygame.time.Clock()
    wall, end = view.times[0], view.times[-1] + 1.0
    paused, trails = False, True
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return
            if event.type != pygame.KEYDOWN:
                continue
            i = view.frame_at(wall)
            if event.key == pygame.K_ESCAPE:
                pygame.quit()
                return
            if event.key == pygame.K_SPACE:
                paused = not paused
            elif event.key in (pygame.K_RIGHT, pygame.K_LEFT):
                sign = 1 if event.key == pygame.K_RIGHT else -1
                if event.mod & pygame.KMOD_SHIFT:
                    wall = min(end, max(view.times[0], wall + sign * 1.0))
                else:
                    wall = view.times[max(0, min(view.last, i + sign))]
                paused = True
            elif event.key == pygame.K_UP:
                speed = min(8.0, speed * 2)
            elif event.key == pygame.K_DOWN:
                speed = max(0.125, speed / 2)
            elif event.key == pygame.K_t:
                trails = not trails
            elif event.key == pygame.K_HOME:
                wall, paused = view.times[0], False
            elif event.key == pygame.K_s:
                path = shots_dir / f"uc2-{view.run}-{view.player}-t{i:04d}.png"
                save_still(pygame, match, view, wall, path)
                print(f"saved {path}")
        draw_view(pygame, screen, match, view, wall, trails, speed, paused)
        R._show_frame(pygame, screen, display)
        dt = clock.tick(60) / 1000.0
        if not paused:
            wall = min(end, wall + dt * speed)
            if wall >= end:
                paused = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run", help="a UC2 run folder, results/pi/uc2/<run>")
    ap.add_argument("--player", default="p1", help="p0..p5, 0..5 or a board name (default p1)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--stats", action="store_true", help="print every board's timing and payments, no window")
    ap.add_argument("--still", default="", help="write the frame at --at seconds to this PNG, no window")
    ap.add_argument("--at", type=float, default=0.0, help="seconds after the first tick, for --still")
    ap.add_argument("--shots", default=str(Path(__file__).resolve().parents[1] / "screenshots"),
                    help="folder for S-key screenshots")
    args = ap.parse_args(argv)
    run = Path(args.run)
    if args.stats:
        for p in sorted(_players(run)):
            s = board_stats(load_board(run, p))
            print(f"{s['player']} {s['board']}: lag p50 {s['lag_p50_ms']:.0f} / p99 {s['lag_p99_ms']:.0f} / "
                  f"max {s['lag_max_ms']:.0f} ms; waits > {WAIT_MS:g} ms: {s['waits_over_100ms']}, longest "
                  f"{s['longest_wait_ms']:.0f} ms; payments {s['paid']}/{s['payments']} paid; shots paid "
                  f"before the next: {s['shots_on_time']}/{s['shots_judged']}")
        return 0
    view = load_board(run, args.player)
    if view.trace_sha256 != M.UC2_REFERENCE_SHA256:
        raise SystemExit(f"{view.board}'s event log is not the UC2 reference log ({view.trace_sha256[:16]}…): "
                         "the re-run frames would not be the events it logged")
    match = M.simulate(M.UC2_SEED)
    if not match.is_uc2_reference or len(match.frames) - 1 != view.last:
        raise SystemExit("the frozen game no longer reproduces the UC2 reference log")
    print(f"{view.run}: {view.board} ({view.player}) — event log = UC2 reference (bc1d6874…), "
          f"{view.last} ticks, {len(view.payments)} own payments")
    if args.still:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame = R._pygame()
        pygame.init()
        save_still(pygame, match, view, view.times[0] + args.at, Path(args.still))
        pygame.quit()
        print(f"wrote {args.still}")
        return 0
    watch(match, view, args.speed, Path(args.shots))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
