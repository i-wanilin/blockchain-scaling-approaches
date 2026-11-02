# measure/wire.py
"""Length-prefixed JSON frames over TCP, as tank-arena/net/protocol.py."""
import asyncio
import json
import struct

_HDR = struct.Struct(">I")
MAX_FRAME = 1 << 20


def encode_frame(obj) -> bytes:
    """Length-prefixed JSON bytes for one frame, with no write/drain -- callers
    that need to put several frames on the wire in one write() (the sensor's
    writer_loop, when a customer has fallen behind) concatenate this."""
    body = json.dumps(obj, separators=(",", ":")).encode()
    return _HDR.pack(len(body)) + body


async def send_frame(writer: asyncio.StreamWriter, obj) -> None:
    writer.write(encode_frame(obj))
    await writer.drain()


async def read_frame(reader: asyncio.StreamReader):
    try:
        (n,) = _HDR.unpack(await reader.readexactly(_HDR.size))
        if n > MAX_FRAME:
            raise ValueError(f"oversized frame: {n}")
        return json.loads(await reader.readexactly(n))
    except (asyncio.IncompleteReadError, ConnectionResetError):
        return None
