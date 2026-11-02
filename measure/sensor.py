# measure/sensor.py
"""UC1 sensor: replays one GESL recording at 120 rows per second and sends the
whole stream to every customer over its own TCP connection (cluster spec §7.2).
It waits until all customers have said hello, announces a common start instant
2 s ahead, then paces rows by the monotonic clock. A customer that disconnects
is logged and dropped; the others continue. Customers are decoupled by
per-customer queues, see serve(). --queue-max and --write-timeout (both
default to today's values, 240 frames / 5.0 s) set how long a customer may lag
before it is dropped; raise both together to ride out a longer network stall
(e.g. the zkSync experiments: 3600 frames / 30.0 s, cluster spec plan
2026-10-01, Q4a) -- queue_max alone would still let a stalled socket's
write_timeout drop the customer well before the queue fills.

Task 10b: when a customer's writer dequeues a row and finds more already
queued (it has fallen behind), it sends all of them in one write and one
drain instead of one write per row. On 2026-10-01, bath1's six per-row-frame
flows -- ~720 small TCP segments/s -- were found to take 60-65% of the 2.4 GHz
channel's airtime (see debug-delivery-collapse.md); a payment-traffic burst
pushed them into a backlog that never recovered. Coalescing only changes
anything once a customer is behind: a customer that keeps up still gets one
frame per write, unchanged from before (except that the last row and the end
frame, queued back to back, always share one write)."""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time

from measure import gesl
from measure.wire import encode_frame, read_frame

# Cap on frames coalesced into one write/drain when a customer's queue is
# backed up. 512 bounds how much memory and latency one flush can hold even
# on a deep backlog (queue_max up to 3600 for zkSync runs); at the measured
# ~630 B/row that is ~320 KB per write, well under MAX_FRAME per frame and
# small next to the multi-MB kernel send buffers already in play
# (debug-delivery-collapse.md section 1.2). A customer that keeps up never
# comes close to the cap. One exception is deterministic: the producer queues
# the last row and the end frame back to back, so even a keeping-up customer
# gets those two in a single write. Consequence for drops: a stalled socket
# now blocks on one large write, so such a customer is more likely dropped by
# write_timeout than by queue overflow; the drop log's seq is the last row
# actually written, not the last one queued.
_BATCH_MAX = 512


async def _send_batch(cn, data: bytes) -> None:
    """Write one coalesced batch and drain it once. Kept as a free function
    (rather than inlined in writer_loop) so tests can wrap/count calls per
    connection without touching asyncio internals."""
    cn.writer.write(data)
    await cn.writer.drain()


class _Conn:
    def __init__(self, c, writer, q):
        self.c, self.writer, self.q = c, writer, q
        self.task = None
        self.dead = self.done = False
        self.seq = -1


async def serve(csv_path, port, customers, duration_s, rate_hz, out_csv, *,
                queue_max=240, write_timeout=5.0, wait_timeout=300.0, hello_timeout=10.0,
                host="0.0.0.0", on_listen=None) -> dict:
    """Every customer gets its own writer task and bounded queue, so the pacing
    loop never awaits a socket and a slow or dead customer cannot delay the
    others. A customer is dropped (logged to stderr, listed in `dropped`) when
    it disconnects, its queue holds more than queue_max frames, or a socket
    write takes longer than write_timeout."""
    conns: dict[int, _Conn] = {}
    arrived: set[int] = set()
    dropped: list[int] = []
    all_in = asyncio.Event()
    state = {"started": False, "t0": 0.0}

    def log(msg):
        print(f"sensor: {msg}", file=sys.stderr, flush=True)

    def drop(cn, reason, seq=None):
        if cn.dead or cn.done:
            return
        cn.dead = True
        dropped.append(cn.c)
        log(f"dropped customer {cn.c} at seq {seq if seq is not None else cn.seq}: {reason}")
        cn.writer.transport.abort()
        if cn.task is not None and cn.task is not asyncio.current_task():
            cn.task.cancel()

    async def writer_loop(cn):
        try:
            while True:
                item = await cn.q.get()
                frames = []
                last_seq = cn.seq
                while True:
                    if isinstance(item, tuple):
                        last_seq = item[0]
                        frame = {"t": "row", "s": item[0], "ts": time.time(), "row": item[1]}
                    else:
                        frame = item
                    frames.append(frame)
                    # A backlogged queue gets flushed in one write+drain instead of
                    # one per row (task 10b); an end frame always ends the batch, and
                    # a caught-up customer (queue empty here) behaves exactly as
                    # before: one frame, one write, one drain.
                    if frame["t"] == "end" or len(frames) >= _BATCH_MAX:
                        break
                    try:
                        item = cn.q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                data = b"".join(encode_frame(f) for f in frames)
                await asyncio.wait_for(_send_batch(cn, data), write_timeout)
                cn.seq = last_seq       # only rows actually written, for the drop log
                if frames[-1]["t"] == "end":
                    cn.done = True
                    cn.writer.close()
                    return
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            drop(cn, f"write failed ({type(e).__name__})")

    async def on_connect(reader, writer):
        try:
            hello = await asyncio.wait_for(read_frame(reader), hello_timeout)
        except (asyncio.TimeoutError, ValueError, OSError):
            hello = None
        if not hello or hello.get("t") != "hello":
            writer.close()
            return
        c = int(hello["c"])
        if c in arrived or state["started"]:
            log(f"rejected connection for customer {c}: duplicate or too late")
            writer.close()
            return
        arrived.add(c)
        cn = _Conn(c, writer, asyncio.Queue(queue_max))
        conns[c] = cn
        cn.task = asyncio.create_task(writer_loop(cn))
        if len(arrived) >= customers:
            all_in.set()
        while await read_frame(reader) is not None:     # customers only ever say hello
            pass
        drop(cn, "disconnected")

    server = await asyncio.start_server(on_connect, host, port)
    if on_listen:
        on_listen(server.sockets[0].getsockname()[1])
    try:
        try:
            await asyncio.wait_for(all_in.wait(), wait_timeout)
        except asyncio.TimeoutError:
            missing = sorted(set(range(1, customers + 1)) - arrived)
            for cn in conns.values():
                drop(cn, "sensor gave up waiting")
            raise RuntimeError(f"only {len(arrived)} of {customers} customers arrived within "
                               f"{wait_timeout}s; missing customers {missing}") from None
        state["started"] = True
        t0 = state["t0"] = time.time() + 2.0
        for cn in list(conns.values()):
            if not cn.dead:
                try:
                    cn.q.put_nowait({"t": "start", "at": t0})
                except asyncio.QueueFull:
                    drop(cn, "queue overflow", -1)
        await asyncio.sleep(max(0.0, t0 - time.time()))
        m0, w0 = time.monotonic(), time.time()
        n = 0
        max_late = 0.0
        with open(out_csv, "w", newline="") as f:
            log_csv = csv.writer(f)
            log_csv.writerow(["seq", "gesl_time", "t_intended", "t_sent"])
            for seq, (gt, line) in enumerate(gesl.rows(csv_path)):
                if seq >= int(duration_s * rate_hz):
                    break
                due = m0 + seq / rate_hz
                await asyncio.sleep(max(0.0, due - time.monotonic()))
                now = time.monotonic()
                max_late = max(max_late, now - due)
                for cn in list(conns.values()):
                    if cn.dead:
                        continue
                    try:
                        cn.q.put_nowait((seq, line))
                    except asyncio.QueueFull:
                        drop(cn, f"queue over {queue_max} frames", seq)
                log_csv.writerow([seq, gt, w0 + seq / rate_hz, w0 + (now - m0)])
                n += 1
        live = []
        for cn in list(conns.values()):
            if cn.dead:
                continue
            try:
                cn.q.put_nowait({"t": "end", "rows": n})
                live.append(cn.task)
            except asyncio.QueueFull:
                drop(cn, "queue overflow at end", n)
        await asyncio.gather(*live, return_exceptions=True)
        return {"rows": n, "start": t0, "dropped": dropped, "max_late_s": max_late}
    finally:
        server.close()
        for cn in conns.values():
            if cn.task is not None and not cn.task.done():
                cn.task.cancel()
            cn.writer.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--port", type=int, default=7000)
    ap.add_argument("--customers", type=int, default=6)
    ap.add_argument("--duration", type=float, default=300)
    ap.add_argument("--rate", type=float, default=120)
    ap.add_argument("--wait-timeout", type=float, default=300)
    ap.add_argument("--queue-max", type=int, default=240)
    ap.add_argument("--write-timeout", type=float, default=5.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(asyncio.run(serve(a.csv, a.port, a.customers, a.duration, a.rate, a.out,
                            wait_timeout=a.wait_timeout, queue_max=a.queue_max,
                            write_timeout=a.write_timeout)), flush=True)


if __name__ == "__main__":
    main()
