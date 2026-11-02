# measure/tests/test_uc1_stream.py
"""Sensor streams every row to every customer; customers pay once per window."""
import asyncio
import csv
import inspect
import os
import socket
import sys
import tempfile
import unittest
from unittest import mock

from measure import customer, sensor
from measure.wire import read_frame, send_frame


def csv_120hz(seconds):
    p = os.path.join(tempfile.mkdtemp(), "g.csv")
    with open(p, "w") as f:
        f.write("Time,a\n")
        for k in range(int(seconds * 120)):
            f.write(f"{k / 120!r},{k}\n")
    return p


ADDR = "0x" + "22" * 20


def csv_wide(seconds, pad):
    p = os.path.join(tempfile.mkdtemp(), "wide.csv")
    with open(p, "w") as f:
        f.write("Time,a\n")
        for k in range(int(seconds * 120)):
            f.write(f"{k / 120!r},{'x' * pad}\n")
    return p


async def start_sensor(*args, **kw):
    """Start sensor.serve on a free loopback port; returns (task, port)."""
    loop = asyncio.get_running_loop()
    port = loop.create_future()
    task = asyncio.create_task(sensor.serve(*args, host="127.0.0.1", on_listen=port.set_result, **kw))
    done, _ = await asyncio.wait({task, port}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        task.result()
    return task, port.result()


def received_times(path):
    with open(path) as f:
        return [float(r["t_received"]) for r in csv.DictReader(f)]


def frames_in(data):
    """Number of length-prefixed frames in one write's bytes."""
    n = pos = 0
    while pos < len(data):
        pos += 4 + int.from_bytes(data[pos:pos + 4], "big")
        n += 1
    assert pos == len(data)
    return n


class TestWindows(unittest.TestCase):
    def test_ten_ms_windows_are_never_empty(self):
        ts = [k / 120 for k in range(240)]
        wins = {customer.window_of(t, 0.010) for t in ts}
        closed = [t for t in ts if customer.closes_window(t, 0.010, 1 / 120)]
        self.assertEqual([customer.window_of(t, 0.010) for t in closed], sorted(wins))  # each window closes once
        self.assertEqual(wins, set(range(min(wins), max(wins) + 1)))  # no gaps

    def test_one_second_window_closes_on_its_last_row(self):
        self.assertTrue(customer.closes_window(119 / 120, 1.0, 1 / 120))
        self.assertFalse(customer.closes_window(118 / 120, 1.0, 1 / 120))


class TestStream(unittest.IsolatedAsyncioTestCase):
    async def test_two_customers_get_all_rows_and_pay_per_window(self):
        path = csv_120hz(2.0)
        tmp = tempfile.mkdtemp()
        events = {1: [], 2: []}

        def emitter(n):
            async def emit(ev):
                events[n].append(ev)
            return emit

        srv, port = await start_sensor(path, 0, customers=2, duration_s=2.0, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"))
        res = await asyncio.gather(*[
            customer.run(f"127.0.0.1:{port}", n, "t", 100, "0x" + "22" * 20, 7, emitter(n),
                         os.path.join(tmp, f"recv{n}.csv")) for n in (1, 2)])
        sent = await srv
        self.assertEqual(sent["rows"], 240)
        for r in res:
            self.assertEqual(r["rows"], 240)
            self.assertTrue(r["complete"])
            self.assertEqual(r["emit_failed"], 0)
        for n in (1, 2):
            self.assertEqual(len(events[n]), 20)              # 2 s / 100 ms
            self.assertEqual(len({e.id for e in events[n]}), 20)
            self.assertTrue(all(e.amount_wei == 7 for e in events[n]))

    async def test_sensor_waits_for_a_late_customer(self):
        path = csv_120hz(0.5)
        tmp = tempfile.mkdtemp()
        got = []

        async def emit(ev):
            got.append(ev)

        srv, port = await start_sensor(path, 0, customers=2, duration_s=0.5, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"))
        first = asyncio.create_task(customer.run(f"127.0.0.1:{port}", 1, "t", 1000, "0x" + "22" * 20, 1, emit,
                                                 os.path.join(tmp, "r1.csv")))
        await asyncio.sleep(1.0)                               # the second customer is late
        second = asyncio.create_task(customer.run(f"127.0.0.1:{port}", 2, "t", 1000, "0x" + "22" * 20, 1, emit,
                                                  os.path.join(tmp, "r2.csv")))
        r1, r2 = await asyncio.gather(first, second)
        await srv
        self.assertEqual(r1["rows"], r2["rows"])               # nobody missed the beginning

    async def test_dropped_customer_is_reported_and_others_continue(self):
        path = csv_120hz(1.0)
        tmp = tempfile.mkdtemp()

        async def emit(ev):
            pass

        srv, port = await start_sensor(path, 0, customers=3, duration_s=1.0, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"))

        async def flaky():
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await send_frame(writer, {"t": "hello", "c": 3})
            assert (await read_frame(reader))["t"] == "start"
            for _ in range(5):
                assert (await read_frame(reader))["t"] == "row"
            writer.close()                                     # disconnect mid-stream

        res = await asyncio.gather(
            customer.run(f"127.0.0.1:{port}", 1, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r1.csv")),
            customer.run(f"127.0.0.1:{port}", 2, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r2.csv")),
            flaky())
        sent = await srv
        self.assertEqual(sent["rows"], 120)
        self.assertEqual(sent["dropped"], [3])
        self.assertEqual(res[0]["rows"], 120)
        self.assertEqual(res[1]["rows"], 120)

    async def test_customer_that_never_reads_does_not_stall_the_others(self):
        path = csv_wide(2.0, 50_000)                            # ~6 MB/s per customer
        tmp = tempfile.mkdtemp()

        async def emit(ev):
            pass

        srv, port = await start_sensor(path, 0, customers=3, duration_s=2.0, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"),
                                       queue_max=40, write_timeout=0.5)

        async def stuck():
            sock = socket.socket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            sock.setblocking(False)
            await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
            reader, writer = await asyncio.open_connection(sock=sock)
            await send_frame(writer, {"t": "hello", "c": 3})
            await asyncio.sleep(4.5)                            # then never reads
            writer.close()

        res = await asyncio.gather(
            customer.run(f"127.0.0.1:{port}", 1, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r1.csv")),
            customer.run(f"127.0.0.1:{port}", 2, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r2.csv")),
            stuck())
        sent = await srv
        self.assertEqual(sent["dropped"], [3])
        self.assertEqual(sent["rows"], 240)
        for n in (1, 2):
            self.assertEqual(res[n - 1]["rows"], 240)
            self.assertTrue(res[n - 1]["complete"])
            ts = received_times(os.path.join(tmp, f"r{n}.csv"))
            self.assertLess(max(b - a for a, b in zip(ts, ts[1:])), 0.5)
        self.assertLess(sent["max_late_s"], 0.5)

    async def test_pause_under_the_configured_queue_max_is_tolerated(self):
        """Task 10a: a customer whose reader pauses well past the point where a
        small queue_max would drop it is not dropped once queue_max is raised
        well above the pause's backlog (the zkSync stall-tolerance fix), as long
        as it resumes and drains its backlog before the run ends, and the other
        customer is not delayed by it. The pause is timed from the hello, so it
        includes the sensor's fixed 2 s start grace (serve()'s t0 = now + 2.0)
        before any row frame -- and thus any backpressure -- can flow, plus
        margin for how many rows an OS-buffered write can absorb before a
        blocked drain() actually stalls the queue. queue_max=2000 here is far
        above the backlog this pause can produce (measured ~280 frames; see
        test_pause_over_queue_max_is_dropped's docstring), so the assertion does
        not depend on exactly where OS backpressure happens to kick in."""
        path = csv_wide(6.0, 50_000)
        tmp = tempfile.mkdtemp()

        async def emit(ev):
            pass

        srv, port = await start_sensor(path, 0, customers=2, duration_s=6.0, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"),
                                       queue_max=2000, write_timeout=5.0)

        async def pauses_then_recovers():
            sock = socket.socket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            sock.setblocking(False)
            await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
            reader, writer = await asyncio.open_connection(sock=sock)
            await send_frame(writer, {"t": "hello", "c": 2})
            await asyncio.sleep(5.5)                            # 2 s start grace + 3.5 s stall...
            while await read_frame(reader) is not None:         # ...but it recovers and drains the backlog
                pass

        res = await asyncio.gather(
            customer.run(f"127.0.0.1:{port}", 1, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r1.csv")),
            pauses_then_recovers())
        sent = await srv
        self.assertEqual(sent["dropped"], [])
        self.assertEqual(sent["rows"], 720)
        self.assertEqual(res[0]["rows"], 720)
        self.assertTrue(res[0]["complete"])
        self.assertLess(sent["max_late_s"], 0.5)                # customer 1 was not held up by customer 2's backlog

    async def test_pause_over_queue_max_is_dropped(self):
        """Same pause as above, but with an explicit, much smaller queue_max=100:
        a board that is genuinely too slow -- or, here, paused far longer than
        queue_max tolerates -- is still dropped. This asserts the mechanism
        (queue_max exceeded -> drop), not an exact backlog number: the pause
        above was measured (by instrumenting serve() during development, see the
        task 10a report) to build a backlog of ~280 frames by the time the
        customer resumes reading, so queue_max=100 is overshot by ~2.8x -- wide
        enough that the test does not depend on exactly where, within that
        range, OS-level TCP backpressure happens to start blocking the writer."""
        path = csv_wide(6.0, 50_000)
        tmp = tempfile.mkdtemp()

        async def emit(ev):
            pass

        srv, port = await start_sensor(path, 0, customers=2, duration_s=6.0, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"), queue_max=100)

        async def pauses_then_recovers():
            sock = socket.socket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            sock.setblocking(False)
            await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
            reader, writer = await asyncio.open_connection(sock=sock)
            await send_frame(writer, {"t": "hello", "c": 2})
            await asyncio.sleep(5.5)                            # same pause as above
            while await read_frame(reader) is not None:
                pass

        res = await asyncio.gather(
            customer.run(f"127.0.0.1:{port}", 1, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r1.csv")),
            pauses_then_recovers())
        sent = await srv
        self.assertEqual(sent["dropped"], [2])
        self.assertEqual(sent["rows"], 720)
        self.assertEqual(res[0]["rows"], 720)

    def test_queue_max_and_write_timeout_default_to_todays_values(self):
        """Task 10a: queue_max/write_timeout became configurable (serve() kwargs,
        --queue-max/--write-timeout CLI flags), but Arbitrum must see identical
        behaviour -- so both must still default to today's values everywhere:
        serve()'s own keyword defaults, and the CLI defaults main() passes
        through when a caller (deploy/cluster, Arbitrum env files) does not
        override them."""
        sig = inspect.signature(sensor.serve)
        self.assertEqual(sig.parameters["queue_max"].default, 240)
        self.assertEqual(sig.parameters["write_timeout"].default, 5.0)

        captured = {}

        async def fake_serve(*a, **kw):
            captured.update(kw)
            return {"rows": 0, "start": 0.0, "dropped": [], "max_late_s": 0.0}

        argv = ["sensor.py", "--csv", "unused.csv", "--out", "unused-out.csv"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(sensor, "serve", fake_serve):
            sensor.main()
        self.assertEqual(captured["queue_max"], 240)
        self.assertEqual(captured["write_timeout"], 5.0)

    async def test_hello_then_close_before_start_is_dropped_others_run(self):
        path = csv_120hz(0.5)
        tmp = tempfile.mkdtemp()

        async def emit(ev):
            pass

        srv, port = await start_sensor(path, 0, customers=3, duration_s=0.5, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"))
        _, w = await asyncio.open_connection("127.0.0.1", port)
        await send_frame(w, {"t": "hello", "c": 3})
        w.close()
        await asyncio.sleep(0.3)
        res = await asyncio.gather(
            customer.run(f"127.0.0.1:{port}", 1, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r1.csv")),
            customer.run(f"127.0.0.1:{port}", 2, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r2.csv")))
        sent = await srv
        self.assertEqual(sent["dropped"], [3])
        self.assertEqual([r["rows"] for r in res], [60, 60])

    async def test_duplicate_customer_id_is_rejected(self):
        path = csv_120hz(0.5)
        tmp = tempfile.mkdtemp()

        async def emit(ev):
            pass

        srv, port = await start_sensor(path, 0, customers=2, duration_s=0.5, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"))
        r1, w1 = await asyncio.open_connection("127.0.0.1", port)
        await send_frame(w1, {"t": "hello", "c": 1})
        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        await send_frame(w2, {"t": "hello", "c": 1})
        self.assertIsNone(await asyncio.wait_for(read_frame(r2), 2))     # closed by the sensor
        res = await customer.run(f"127.0.0.1:{port}", 2, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r2.csv"))
        sent = await srv
        self.assertEqual(res["rows"], 60)
        self.assertEqual(sent["dropped"], [])
        w1.close()
        w2.close()

    async def test_wait_timeout_names_missing_customers(self):
        tmp = tempfile.mkdtemp()
        srv, port = await start_sensor(csv_120hz(0.5), 0, customers=3, duration_s=0.5, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"), wait_timeout=0.5)
        _, w = await asyncio.open_connection("127.0.0.1", port)
        await send_frame(w, {"t": "hello", "c": 2})
        with self.assertRaisesRegex(RuntimeError, r"missing customers \[1, 3\]"):
            await srv
        w.close()

    async def test_emit_failure_does_not_stop_reception(self):
        path = csv_120hz(1.0)
        tmp = tempfile.mkdtemp()
        calls = []

        async def emit(ev):
            calls.append(ev)
            if len(calls) > 3:
                raise ConnectionResetError("payment client died")

        srv, port = await start_sensor(path, 0, customers=1, duration_s=1.0, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"))
        res = await customer.run(f"127.0.0.1:{port}", 1, "t", 100, ADDR, 1, emit, os.path.join(tmp, "r.csv"))
        await srv
        self.assertEqual(res["rows"], 120)
        self.assertEqual(res["windows"], 10)
        self.assertEqual(res["emit_failed"], 7)
        self.assertTrue(res["complete"])

    async def test_backlogged_customer_is_coalesced_into_fewer_writes(self):
        """Task 10b: a customer that pauses and falls behind still gets every
        row, in order, none lost or duplicated, but the sensor issues fewer
        writes than frames while it is catching up -- the batch-per-write
        fix for the delivery collapse (debug-delivery-collapse.md). Reuses
        the pause/resume shape of test_pause_under_the_configured_queue_max
        _is_tolerated (task 10a), with margin >= 2x the observed backlog."""
        path = csv_wide(6.0, 50_000)
        tmp = tempfile.mkdtemp()

        async def emit(ev):
            pass

        calls = []
        orig = sensor._send_batch

        async def counting(cn, data):
            calls.append(cn.c)
            return await orig(cn, data)

        with mock.patch.object(sensor, "_send_batch", counting):
            srv, port = await start_sensor(path, 0, customers=2, duration_s=6.0, rate_hz=120,
                                           out_csv=os.path.join(tmp, "sent.csv"),
                                           queue_max=2000, write_timeout=5.0)

            seqs = []

            async def pauses_then_recovers():
                sock = socket.socket()
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                sock.setblocking(False)
                await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
                reader, writer = await asyncio.open_connection(sock=sock)
                await send_frame(writer, {"t": "hello", "c": 2})
                await asyncio.sleep(5.5)
                while True:
                    fr = await read_frame(reader)
                    if fr is None:
                        break
                    if fr["t"] == "row":
                        seqs.append(fr["s"])

            res = await asyncio.gather(
                customer.run(f"127.0.0.1:{port}", 1, "t", 1000, ADDR, 1, emit, os.path.join(tmp, "r1.csv")),
                pauses_then_recovers())
            sent = await srv

        self.assertEqual(sent["dropped"], [])
        self.assertEqual(sent["rows"], 720)
        self.assertEqual(res[0]["rows"], 720)                # the keeping-up customer is unaffected
        self.assertTrue(res[0]["complete"])
        self.assertLess(sent["max_late_s"], 0.5)
        writes_to_c2 = sum(1 for c in calls if c == 2)
        frames_to_c2 = sent["rows"] + 2                  # + start + end
        self.assertLess(writes_to_c2, frames_to_c2 // 2)  # well under one write per frame
        self.assertEqual(seqs, list(range(720)))          # all rows, in order, none lost/duplicated

    async def test_caught_up_customer_still_gets_one_write_per_frame(self):
        """Task 10b: coalescing must not change anything when the customer
        keeps up: one write per frame (start, then each row), except for one
        deterministic merge -- the producer queues the last row and the end
        frame back to back with no await between, so they share a write."""
        path = csv_120hz(1.0)
        tmp = tempfile.mkdtemp()

        async def emit(ev):
            pass

        sizes = []
        orig = sensor._send_batch

        async def counting(cn, data):
            sizes.append(frames_in(data))
            return await orig(cn, data)

        with mock.patch.object(sensor, "_send_batch", counting):
            srv, port = await start_sensor(path, 0, customers=1, duration_s=1.0, rate_hz=120,
                                           out_csv=os.path.join(tmp, "sent.csv"))
            res = await customer.run(f"127.0.0.1:{port}", 1, "t", 1000, ADDR, 1, emit,
                                     os.path.join(tmp, "r1.csv"))
            sent = await srv

        self.assertTrue(res["complete"])
        self.assertEqual(sent["rows"], 120)
        self.assertEqual(sizes, [1] * 120 + [2])    # start + rows 0..118 alone; row 119 + end together

    async def test_end_frame_is_last_in_a_coalesced_batch_and_closes(self):
        """Task 10b: even when the end frame lands in the same coalesced
        batch as the last rows, it is still processed last -- the customer
        sees every row before "end", and the connection closes right after."""
        path = csv_wide(1.0, 50_000)
        tmp = tempfile.mkdtemp()

        srv, port = await start_sensor(path, 0, customers=1, duration_s=1.0, rate_hz=120,
                                       out_csv=os.path.join(tmp, "sent.csv"),
                                       queue_max=2000, write_timeout=5.0)

        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.setblocking(False)
        await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
        reader, writer = await asyncio.open_connection(sock=sock)
        await send_frame(writer, {"t": "hello", "c": 1})
        await asyncio.sleep(3.0)                           # >= 2x the 1 s run: everything backs up

        frames = []
        while True:
            fr = await read_frame(reader)
            if fr is None:
                break
            frames.append(fr)

        sent = await srv
        self.assertEqual(sent["dropped"], [])
        self.assertEqual(sent["rows"], 120)
        row_frames = [fr for fr in frames if fr["t"] == "row"]
        self.assertEqual([fr["s"] for fr in row_frames], list(range(120)))  # in order, none lost
        self.assertEqual(frames[-1]["t"], "end")            # end processed last
        self.assertEqual(frames[-1]["rows"], 120)
        self.assertIsNone(await read_frame(reader))         # the sensor closed the connection

    async def test_truncated_stream_is_not_complete(self):
        async def fake(reader, writer):
            await read_frame(reader)
            await send_frame(writer, {"t": "start", "at": 0})
            for k in range(3):
                await send_frame(writer, {"t": "row", "s": k, "ts": 0.0, "row": f"{k / 120!r},{k}"})
            writer.close()                                     # no "end" frame

        async def emit(ev):
            pass

        server = await asyncio.start_server(fake, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        res = await customer.run(f"127.0.0.1:{port}", 1, "t", 1000, ADDR, 1, emit,
                                 os.path.join(tempfile.mkdtemp(), "r.csv"))
        server.close()
        self.assertEqual(res["rows"], 3)
        self.assertFalse(res["complete"])
