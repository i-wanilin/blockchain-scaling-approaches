"""Distributed lockstep node — one player per OS process.

Every node runs the SAME deterministic simulation; only the inputs travel
over the network (classic lockstep). Each tick a node computes its own
tank's controls (bot AI), broadcasts them to all peers, waits for every
other player's input for that tick, then steps the shared World. Because
the World is deterministic, all nodes stay in perfect sync and each writes
a byte-identical trace — comparing trace hashes across nodes doubles as a
lockstep-consistency check.

Topologies:
  mesh  — full mesh; node i listens on peers[i] and connects to all j < i.
  relay — every node connects only to a relay (net/relay.py) which
          rebroadcasts frames (star / hub topology).

Usage (3-player mesh on one machine):
  python -m arena.node --id 0 --peers :9100,:9101,:9102 --tickrate 20 ...
  python -m arena.node --id 1 --peers :9100,:9101,:9102 --tickrate 20 ...
  python -m arena.node --id 2 --peers :9100,:9101,:9102 --tickrate 20 ...

Relay:
  python -m net.relay --port 9000 --expect 3
  python -m arena.node --id 0 --players 3 --topology relay \
      --relay :9000 --tickrate 20 --duration 30 --seed 42 --out n0.jsonl

The --peers list is ordered by node id and must be identical on every
node (it also defines N in mesh mode). Wire protocol: net/protocol.py.

UC2 payments (optional, --pay-socket PATH): the node connects to the local
payment client before it marks itself ready, and after every tick hands it
the payments this board owes (arena/paybridge.py). It never waits for the
client. --ticks-out writes tick,t_intended,t_actual per tick; --result-out
writes node-result.json. Exit codes: 0 complete match; 3 stall (one tick
waited TICK_TIMEOUT_S, env ARENA_TICK_TIMEOUT_S); 4 any other abort.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import traceback
from collections.abc import Callable

from arena.bots import make_bot
from arena.paybridge import PayBridge, payments_for
from arena.world import Config, Controls, World
from net.protocol import parse_addr, read_frame, send_frame

CONNECT_TIMEOUT_S = 15.0
TICK_TIMEOUT_S = float(os.environ.get("ARENA_TICK_TIMEOUT_S", "30"))
BRIDGE_CLOSE_S = 120.0
EXIT_STALL = 3
EXIT_ABORT = 4
STALL_PREFIX = "stall at tick"


class Node:
    def __init__(self, node_id: int, n_players: int, tickrate: int,
                 duration: float, seed: int, out_path: str) -> None:
        self.id = node_id
        self.n = n_players
        self.tickrate = tickrate
        self.total_ticks = int(round(duration * tickrate))
        self.seed = seed
        self.out_path = out_path
        self.world = World(n_players=n_players, tickrate=tickrate,
                           seed=seed, config=Config())
        self.bot = make_bot(node_id, seed, tickrate)
        self.writers: list[asyncio.StreamWriter] = []
        self.inbox: dict[int, dict[int, Controls]] = {}   # tick -> id -> ctl
        self.hellos: set[int] = set()
        self.ready_players: set[int] = set()
        self.aborted_reason = ""
        self.got_frame = asyncio.Condition()
        self._server = None
        # UC2 payment bridge and logs (set by amain; unused by the GUI)
        self.bridge: PayBridge | None = None
        self.run_id = ""
        self.ticks_out: str | None = None
        self.t0_wall: float | None = None
        self.ticks_done = 0
        self.counts = {"move": 0, "fire": 0, "hit": 0, "break": 0}

    # ------------------------------------------------------------ wiring

    async def _reader_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            frame = await read_frame(reader)
            if frame is None or frame.get("t") == "bye":
                return
            async with self.got_frame:
                # any frame from a node proves it is present (barrier)
                self.hellos.add(frame["id"])
                if frame["t"] == "in":
                    self.inbox.setdefault(frame["k"], {})[frame["id"]] = \
                        Controls.from_wire(frame["c"])
                elif frame["t"] == "ready":
                    self.ready_players.add(frame["id"])
                elif frame["t"] == "abort":
                    self.aborted_reason = frame.get("reason", "peer left")
                self.got_frame.notify_all()

    async def _connect_with_retry(self, host: str, port: int
                                  ) -> tuple[asyncio.StreamReader,
                                             asyncio.StreamWriter]:
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        while True:
            try:
                return await asyncio.open_connection(host, port)
            except OSError:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.2)

    async def setup_mesh(self, peers: list[tuple[str, int]]) -> None:
        my_host, my_port = peers[self.id]

        async def on_accept(reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> None:
            self.writers.append(writer)
            asyncio.ensure_future(self._reader_loop(reader))

        server = await asyncio.start_server(
            on_accept, my_host or "127.0.0.1", my_port)
        self._server = server

        for j in range(self.id):  # dial every lower-id node
            reader, writer = await self._connect_with_retry(*peers[j])
            self.writers.append(writer)
            asyncio.ensure_future(self._reader_loop(reader))

        # wait until incoming connections from all higher-id nodes arrived
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        while len(self.writers) < self.n - 1:
            if time.monotonic() > deadline:
                raise TimeoutError("mesh: not all peers connected")
            await asyncio.sleep(0.05)
        await self._barrier()

    async def setup_relay(self, relay: tuple[str, int]) -> None:
        reader, writer = await self._connect_with_retry(*relay)
        self.writers.append(writer)
        asyncio.ensure_future(self._reader_loop(reader))
        self._server = None
        await self._barrier()

    async def _barrier(self) -> None:
        """Exchange hellos until all N nodes are present.

        Hellos are re-sent periodically: in relay topology a frame sent
        before a peer has connected to the relay is lost, so a single
        hello could deadlock the barrier. Duplicates are harmless (set).
        """
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        while True:
            await self.broadcast({"t": "hello", "id": self.id})
            try:
                async with self.got_frame:
                    await asyncio.wait_for(
                        self.got_frame.wait_for(
                            lambda: len(self.hellos) >= self.n - 1),
                        0.5)
                # one final hello: a peer that connected late may have
                # missed ours while we already saw its (relay topology)
                await self.broadcast({"t": "hello", "id": self.id})
                return
            except TimeoutError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"barrier: only saw hellos from {sorted(self.hellos)}"
                    ) from None

    async def broadcast(self, frame: dict) -> None:
        """Send to every peer. A peer whose connection fails (reset, broken
        pipe) is dropped and the others still get the frame; its missing
        input then shows up as a stall at the next tick it owes."""
        for w in list(self.writers):
            try:
                await send_frame(w, frame)
            except OSError:
                self.writers.remove(w)
                w.transport.abort()

    async def broadcast_abort(self, reason: str) -> None:
        """Tell every peer the match is off; a peer that is gone is skipped."""
        for w in self.writers:
            try:
                await asyncio.wait_for(
                    send_frame(w, {"t": "abort", "id": self.id,
                                   "reason": reason}), 2.0)
            except (OSError, TimeoutError):
                pass

    @property
    def connected_players(self) -> set[int]:
        return {self.id, *self.hellos}

    async def mark_ready(self) -> None:
        self.ready_players.add(self.id)
        await self.broadcast({"t": "ready", "id": self.id})

    async def wait_until_ready(self) -> None:
        async with self.got_frame:
            await asyncio.wait_for(
                self.got_frame.wait_for(
                    lambda: (len(self.ready_players) >= self.n
                             or bool(self.aborted_reason))),
                TICK_TIMEOUT_S)
        if self.aborted_reason:
            raise RuntimeError(f"session aborted: {self.aborted_reason}")

    # ------------------------------------------------------------- game

    async def run(
            self,
            control_source: Callable[[World], Controls | None] | None = None,
            on_step: Callable[[World, list[dict], dict[str, int]], None]
            | None = None) -> dict[str, int]:
        """Run lockstep with the bot or a local human input callback.

        Returning ``None`` from ``control_source`` cleanly aborts the whole
        multiplayer session. ``on_step`` is used by the pygame client only;
        headless Pi nodes retain the original path.
        """
        counts = self.counts
        dt = 1.0 / self.tickrate
        start = time.monotonic()
        # match start on the wall clock (R4): right after wait_until_ready
        self.t0_wall = time.time()
        ticks_fh = (open(self.ticks_out, "w", encoding="utf-8", buffering=1)
                    if self.ticks_out else None)
        if ticks_fh:
            ticks_fh.write("tick,t_intended,t_actual\n")
        try:
            await self._loop(control_source, on_step, counts, dt, start,
                             ticks_fh)
        finally:
            if ticks_fh:
                ticks_fh.close()
        if not self.aborted_reason:
            await self.broadcast({"t": "bye", "id": self.id})
        self.shutdown()
        return counts

    async def _loop(self, control_source, on_step, counts, dt, start,
                    ticks_fh) -> None:
        with open(self.out_path, "w", encoding="utf-8") as out:
            for tick in range(self.total_ticks):
                ctl = (control_source(self.world) if control_source
                       else self.bot.decide(self.world))
                if ctl is None:
                    self.aborted_reason = f"player {self.id + 1} left"
                    await self.broadcast({"t": "abort", "id": self.id,
                                          "reason": self.aborted_reason})
                    break
                await self.broadcast(
                    {"t": "in", "k": tick, "id": self.id,
                     "c": ctl.to_wire()})
                stalled = False
                async with self.got_frame:
                    try:
                        await asyncio.wait_for(
                            self.got_frame.wait_for(
                                lambda: (len(self.inbox.get(tick, {}))
                                         >= self.n - 1
                                         or bool(self.aborted_reason))),
                            TICK_TIMEOUT_S)
                    except TimeoutError:
                        have = self.inbox.get(tick, {})
                        missing = [p for p in range(self.n)
                                   if p != self.id and p not in have]
                        # world tick = loop index + 1
                        self.aborted_reason = (
                            f"{STALL_PREFIX} {tick + 1} "
                            f"(no input from players {missing})")
                        stalled = True
                    if self.aborted_reason and not stalled:
                        break
                    controls = self.inbox.pop(tick, {})
                if stalled:
                    await self.broadcast_abort(self.aborted_reason)
                    break
                controls[self.id] = ctl
                events_now = self.world.step(controls)
                t_gen = time.time()
                if self.bridge is not None:
                    # never waits: writer.write only (R4 intended time)
                    self.bridge.emit(
                        payments_for(events_now, self.id, self.run_id),
                        self.t0_wall + tick * dt, t_gen)
                if ticks_fh:
                    ticks_fh.write(f"{tick + 1},{self.t0_wall + tick * dt:.6f},"
                                   f"{t_gen:.6f}\n")
                self.ticks_done = tick + 1
                for event in events_now:
                    counts[event["type"]] += 1
                    out.write(json.dumps(event, separators=(",", ":"))
                              + "\n")
                if on_step:
                    on_step(self.world, events_now, counts)
                # real-time pacing (lockstep may lag; never sleep negative)
                target = start + (tick + 1) * dt
                delay = target - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)

    def shutdown(self) -> None:
        for w in self.writers:
            w.close()
        if self._server is not None:
            self._server.close()


async def amain(args: argparse.Namespace) -> int:
    if args.topology == "mesh":
        if not args.peers:
            print("mesh topology requires --peers", file=sys.stderr)
            return 2
        peers = [parse_addr(p) for p in args.peers.split(",")]
        n = args.players or len(peers)
        if n != len(peers):
            print("--players must match the --peers list length",
                  file=sys.stderr)
            return 2
    else:
        if not args.relay or not args.players:
            print("relay topology requires --relay and --players",
                  file=sys.stderr)
            return 2
        n = args.players

    node = Node(args.id, n, args.tickrate, args.duration, args.seed,
                args.out)
    node.run_id = args.run
    node.ticks_out = args.ticks_out
    t_setup = time.time()
    try:
        if args.topology == "mesh":
            await node.setup_mesh(peers)
        else:
            await node.setup_relay(parse_addr(args.relay))
        if args.pay_socket:
            node.bridge = PayBridge(args.pay_socket)
            try:
                await node.bridge.connect()
            except ConnectionError as e:
                print(f"node {args.id}: {e}", file=sys.stderr)
                node.bridge = None
                node.aborted_reason = "payment client not listening"
                await node.broadcast_abort(
                    f"payment client not listening (player {args.id})")
        if not node.aborted_reason:
            await node.mark_ready()
            try:
                await node.wait_until_ready()
            except RuntimeError:
                pass   # aborted_reason holds the peer's reason
        if node.aborted_reason:
            open(args.out, "w", encoding="utf-8").close()   # empty trace
            node.shutdown()
        else:
            await node.run()
    except Exception as e:   # anything unexpected: still end cleanly
        traceback.print_exc()
        if not node.aborted_reason:
            node.aborted_reason = f"node error: {type(e).__name__}: {e}"
        try:
            await node.broadcast_abort(node.aborted_reason)
        except Exception:
            pass
        node.shutdown()
    if not os.path.exists(args.out):
        open(args.out, "w", encoding="utf-8").close()   # empty trace
    if node.bridge is not None:
        try:
            await asyncio.wait_for(node.bridge.close(), BRIDGE_CLOSE_S)
        except TimeoutError:
            print(f"node {args.id}: payment client did not take the end "
                  f"marker within {BRIDGE_CLOSE_S:.0f} s", file=sys.stderr)
        except Exception as e:
            print(f"node {args.id}: closing the payment stream failed: {e!r}",
                  file=sys.stderr)
    t_end = time.time()
    aborted = node.aborted_reason or None
    if args.result_out:
        with open(args.out, "rb") as fh:
            trace_sha = hashlib.sha256(fh.read()).hexdigest()
        result = {
            "complete": aborted is None and node.ticks_done == node.total_ticks,
            "aborted": aborted,
            "ticks": node.ticks_done,
            "t_start": node.t0_wall if node.t0_wall is not None else t_setup,
            "t_end": t_end,
            "emitted": node.bridge.emitted if node.bridge else 0,
            "counts": node.counts,
            "trace_sha256": trace_sha,
        }
        with open(args.result_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
            fh.write("\n")
    counts = node.counts
    total = sum(counts.values())
    print(f"node {args.id}: {node.ticks_done} ticks, {total} events "
          f"(move {counts['move']}, fire {counts['fire']}, "
          f"hit {counts['hit']}, break {counts['break']}) -> {args.out}"
          + (f"; aborted: {aborted}" if aborted else ""),
          file=sys.stderr)
    if aborted is None:
        return 0
    return EXIT_STALL if aborted.startswith(STALL_PREFIX) else EXIT_ABORT


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m arena.node",
        description="Lockstep tank-arena node (one player per process).")
    ap.add_argument("--id", type=int, required=True, help="player id, 0-based")
    ap.add_argument("--peers", default="",
                    help="comma list of host:port for ALL nodes, ordered "
                         "by id (mesh topology)")
    ap.add_argument("--players", type=int, default=0,
                    help="total player count (required for relay topology)")
    ap.add_argument("--topology", choices=("mesh", "relay"), default="mesh")
    ap.add_argument("--relay", default="", help="relay host:port")
    ap.add_argument("--tickrate", type=int, default=20)
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="trace-node.jsonl")
    ap.add_argument("--pay-socket", default="",
                    help="Unix socket of the local payment client (UC2); "
                         "without it no payments are emitted")
    ap.add_argument("--run", default="run",
                    help="run ID, part of every payment ID")
    ap.add_argument("--ticks-out", default=None,
                    help="CSV tick,t_intended,t_actual, one row per tick")
    ap.add_argument("--result-out", default=None,
                    help="node-result.json path")
    args = ap.parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
