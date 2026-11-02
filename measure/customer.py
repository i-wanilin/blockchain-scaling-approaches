# measure/customer.py
"""UC1 customer: receives the whole stream, closes windows on the GESL time
column and, postpay, emits one payment event per received window to the local
payment client (cluster spec §7.2). A window closes on the row whose successor
(one row period later) would fall into the next window, so the payment is not
delayed by waiting for that successor. A trailing partial window never closes
and is not paid (it does not occur when the run length is a whole number of
windows, e.g. 900 s)."""
from __future__ import annotations

import argparse
import asyncio
import csv
import math
import os
import sys
import time

from measure.events import PaymentEvent, event_id
from measure.wire import read_frame, send_frame

EPS = 1e-9


def window_of(t: float, window_s: float) -> int:
    return math.floor(t / window_s + EPS)


def closes_window(t: float, window_s: float, dt: float) -> bool:
    return window_of(t + dt, window_s) > window_of(t, window_s)


async def run(sensor, name, run_id, window_ms, pay_to, price_wei, emit, out_csv, rate_hz=120.0) -> dict:
    host, _, port = sensor.rpartition(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    await send_frame(writer, {"t": "hello", "c": name})
    window_s, dt = window_ms / 1000.0, 1.0 / rate_hz
    rows = windows = emit_failed = 0
    complete, expected = False, None
    with open(out_csv, "w", newline="") as f:
        log = csv.writer(f)
        log.writerow(["seq", "t_sent", "t_received"])
        while True:
            fr = await read_frame(reader)
            if fr is None:
                break
            if fr["t"] == "end":
                expected = fr.get("rows")
                complete = expected == rows
                break
            if fr["t"] != "row":
                continue
            now = time.time()
            rows += 1
            log.writerow([fr["s"], fr["ts"], now])
            gt = float(fr["row"].split(",", 1)[0])
            if closes_window(gt, window_s, dt):
                w = window_of(gt, window_s)
                windows += 1
                try:
                    await emit(PaymentEvent(event_id(run_id, name, w), pay_to, price_wei, now, time.time()))
                except Exception as e:      # the payment side must not disturb the data path
                    if not emit_failed:
                        print(f"customer {name}: emit failed at window {w}: {e!r}", file=sys.stderr, flush=True)
                    emit_failed += 1
    writer.close()
    return {"rows": rows, "windows": windows, "complete": complete, "emit_failed": emit_failed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sensor", required=True)
    ap.add_argument("--customer", type=int, required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--window-ms", type=int, required=True)
    ap.add_argument("--pay-to", required=True)
    ap.add_argument("--price-wei", type=int, required=True)
    ap.add_argument("--socket", default="/tmp/measure-pay.sock")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    if not os.path.exists(a.socket):
        sys.exit(f"customer {a.customer}: payment socket {a.socket} does not exist; start the payment client first")

    async def go():
        _, w = await asyncio.open_unix_connection(a.socket)

        async def emit(ev):
            w.write(ev.to_line())
            await w.drain()

        res = await run(a.sensor, a.customer, a.run, a.window_ms, a.pay_to, a.price_wei, emit, a.out)
        try:
            w.write(b'{"end":true}\n')
            await w.drain()
        except (ConnectionError, OSError):
            pass
        print(res, flush=True)

    asyncio.run(go())


if __name__ == "__main__":
    main()
