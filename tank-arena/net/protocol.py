"""Framed JSON over TCP — the entire wire protocol.

Framing: 4-byte big-endian unsigned length prefix, then that many bytes of
UTF-8 JSON (one object per frame). No compression, no encryption — this is
a benchmark harness, and simplicity beats cleverness on a Pi cluster.

Frame types used by the lockstep protocol (arena/node.py, net/relay.py):

    {"t": "hello", "id": <int>}                    node announces itself
    {"t": "ready", "id": <int>}                    player locked in
    {"t": "in", "k": <tick>, "id": <int>,
     "c": [throttle, turn, fire, fire_angle,
           drive_angle_or_null]}                     input for tick k
    {"t": "abort", "id": <int>, "reason": <str>} session cancelled
    {"t": "bye", "id": <int>}                      clean shutdown

The relay forwards every frame to all clients except the sender, so the
node logic is identical in mesh and relay topologies.
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

MAX_FRAME = 1 << 20  # 1 MiB sanity cap

_HDR = struct.Struct(">I")


def encode_frame(obj: Any) -> bytes:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ValueError("frame too large")
    return _HDR.pack(len(body)) + body


async def send_frame(writer: asyncio.StreamWriter, obj: Any) -> None:
    writer.write(encode_frame(obj))
    await writer.drain()


async def read_frame(reader: asyncio.StreamReader) -> Any | None:
    """Read one frame; returns None on clean EOF."""
    try:
        hdr = await reader.readexactly(_HDR.size)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        return None
    (length,) = _HDR.unpack(hdr)
    if length > MAX_FRAME:
        raise ValueError(f"oversized frame: {length} bytes")
    try:
        body = await reader.readexactly(length)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        return None
    return json.loads(body.decode("utf-8"))


def parse_addr(spec: str) -> tuple[str, int]:
    """Parse 'host:port' (host may be empty => 127.0.0.1)."""
    host, _, port = spec.rpartition(":")
    return (host or "127.0.0.1", int(port))
