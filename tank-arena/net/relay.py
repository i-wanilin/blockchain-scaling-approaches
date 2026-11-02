"""Relay node for `--topology relay`: forwards every frame to all other
clients. Star topology — the network analogue of the state-channel hub.

Usage:
    python -m net.relay --port 9000 [--expect N]

With --expect N the relay exits once N clients have connected and all of
them have disconnected again (convenient for scripted runs and tests);
without it, it serves forever.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from net.protocol import read_frame, send_frame


class Relay:
    def __init__(self, expect: int | None = None) -> None:
        self.clients: dict[asyncio.StreamWriter, int | None] = {}
        self.expect = expect
        self.seen = 0
        self.done = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        self.clients[writer] = None
        self.seen += 1
        try:
            while True:
                frame = await read_frame(reader)
                if frame is None:
                    break
                if frame.get("t") == "hello":
                    self.clients[writer] = frame.get("id")
                await self.broadcast(frame, exclude=writer)
                if frame.get("t") == "bye":
                    break
        finally:
            self.clients.pop(writer, None)
            writer.close()
            if (self.expect is not None and self.seen >= self.expect
                    and not self.clients):
                self.done.set()

    async def broadcast(self, frame: dict, exclude: asyncio.StreamWriter) -> None:
        for w in list(self.clients):
            if w is exclude:
                continue
            try:
                await send_frame(w, frame)
            except (ConnectionResetError, BrokenPipeError):
                self.clients.pop(w, None)


async def serve(port: int, host: str = "127.0.0.1",
                expect: int | None = None) -> None:
    relay = Relay(expect)
    server = await asyncio.start_server(relay.handle, host, port)
    print(f"relay listening on {host}:{port}"
          + (f", expecting {expect} clients" if expect else ""),
          file=sys.stderr, flush=True)
    async with server:
        if expect is None:
            await server.serve_forever()
        else:
            await relay.done.wait()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m net.relay",
        description="Frame relay for tank-arena --topology relay.")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--expect", type=int, default=None,
                    help="exit after N clients have come and gone")
    args = ap.parse_args(argv)
    asyncio.run(serve(args.port, args.host, args.expect))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
