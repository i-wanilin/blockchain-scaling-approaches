"""The game -> payment bridge: a node hands its payments to the local client.

Each board pays for what it owes in the match: its own shots (to the
arena) and the hits it takes (to the shooter). The lines go over a Unix
socket to the payment client (measure/payclient.py serve_socket), in the
format of measure/events.py PaymentEvent, copied here because tank-arena
stays stdlib only:

    {"id": str, "to": str, "amount_wei": int,
     "t_intended": float, "t_generated": float}\\n

one JSON object per line; the line {"end": true} ends the stream.

Event ID (plan ruling R3): uc2/<run>/p<payer>/t<tick>/<kind><seq>, where
kind is fire or hit and seq counts the payer's payments of that kind in
that tick from 0. The ID depends only on the deterministic event stream,
so a resent line never pays twice.

The game never waits for the client: emit() only calls writer.write
(never drain), and the transport's buffer may grow to 64 MiB without
pausing the writer. While the transport still holds unsent bytes, new
lines are collected in one bytearray and handed to writer.write once per
event-loop turn: asyncio's transport buffer is a deque whose size is
summed on every write, so one write per line would cost O(n^2) when the
client falls behind.
"""

from __future__ import annotations

import asyncio
import json

WRITE_BUFFER_HIGH = 64 << 20
END_LINE = b'{"end":true}\n'


def payments_for(events: list[dict], me: int, run: str) -> list[dict]:
    """The payments board ``me`` owes for these events, in event order.

    Its own fire pays the arena; a hit it took pays the shooter. A hit
    with amount 0 moves nothing and is not paid. Times are set by emit().
    """
    out: list[dict] = []
    seq: dict[tuple[int, str], int] = {}
    for ev in events:
        kind = ev["type"]
        p = ev["payload"]
        if kind == "fire" and ev["player"] == me:
            to = "arena"
        elif kind == "hit" and p["victim"] == me and p["amount"] > 0:
            to = f"p{p['shooter']}"
        else:
            continue
        tick = ev["tick"]
        n = seq.get((tick, kind), 0)
        seq[(tick, kind)] = n + 1
        out.append({"id": f"uc2/{run}/p{me}/t{tick}/{kind}{n}",
                    "to": to, "amount_wei": int(p["amount"])})
    return out


class PayBridge:
    """One Unix-socket connection to the local payment client."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.emitted = 0
        self._writer: asyncio.StreamWriter | None = None
        self._pending = bytearray()
        self._flush_scheduled = False

    async def connect(self) -> None:
        """Connect; raises ConnectionError if nothing listens on the path."""
        try:
            _, writer = await asyncio.open_unix_connection(self.path)
        except (FileNotFoundError, ConnectionError) as e:
            raise ConnectionError(
                f"payment client not listening on {self.path}: {e}") from e
        writer.transport.set_write_buffer_limits(high=WRITE_BUFFER_HIGH)
        self._writer = writer

    def emit(self, lines: list[dict], t_intended: float,
             t_generated: float) -> None:
        """Queue the lines; never waits (writer.write only, no drain)."""
        if not lines:
            return
        buf = "".join(
            json.dumps({"id": ln["id"], "to": ln["to"],
                        "amount_wei": ln["amount_wei"],
                        "t_intended": float(t_intended),
                        "t_generated": float(t_generated)},
                       separators=(",", ":")) + "\n"
            for ln in lines)
        data = buf.encode()
        self.emitted += len(lines)
        w = self._writer
        if self._pending or w.transport.get_write_buffer_size() > 0:
            self._pending += data
            if not self._flush_scheduled:
                self._flush_scheduled = True
                asyncio.get_running_loop().call_soon(self._flush)
        else:
            w.write(data)

    def _flush(self) -> None:
        self._flush_scheduled = False
        if self._pending and self._writer is not None:
            self._writer.write(bytes(self._pending))
            self._pending.clear()

    async def close(self) -> None:
        """Write the end marker and drain. The caller bounds the wait
        (asyncio.wait_for); if it is cancelled the connection is dropped."""
        w = self._writer
        if w is None:
            return
        try:
            self._flush()
            w.write(END_LINE)
            await w.drain()
            w.close()
            await w.wait_closed()
        except asyncio.CancelledError:
            w.transport.abort()
            raise
        except ConnectionError:
            w.transport.abort()
        finally:
            self._writer = None
