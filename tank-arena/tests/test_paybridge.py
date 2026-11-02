"""The game -> payment bridge: which payments a board owes, and the socket.

The line format equals measure/events.py PaymentEvent (copied, not
imported: tank-arena stays stdlib only).
"""

import asyncio
import json
import os
import socket
import tempfile
import threading
import time
import unittest

from arena.paybridge import PayBridge, payments_for

PAYMENT_KEYS = {"id", "to", "amount_wei", "t_intended", "t_generated"}


def fire(t, p):
    return {"tick": t, "type": "fire", "player": p, "payload": {"amount": 1}}


def hit(t, s, v, a=5):
    return {"tick": t, "type": "hit", "player": s,
            "payload": {"victim": v, "shooter": s, "amount": a}}


class Collector:
    """A Unix-socket server that accepts one connection and reads to EOF."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.data = b""
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        self.srv.settimeout(60)
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(60)
            chunks = []
            while True:
                try:
                    b = conn.recv(65536)
                except OSError:
                    break
                if not b:
                    break
                chunks.append(b)
            self.data = b"".join(chunks)

    def lines(self) -> list[dict]:
        self.thread.join(60)
        self.srv.close()
        return [json.loads(x) for x in self.data.split(b"\n") if x]


class PaymentsForTest(unittest.TestCase):
    def test_own_fire_and_hits_taken(self):
        evs = [fire(7, 2), fire(7, 3), hit(7, 3, 2), hit(7, 4, 2),
               hit(7, 2, 5),
               {"tick": 7, "type": "move", "player": 2, "payload": {}}]
        got = payments_for(evs, 2, "R")
        self.assertEqual(
            [(g["id"], g["to"], g["amount_wei"]) for g in got],
            [("uc2/R/p2/t7/fire0", "arena", 1),
             ("uc2/R/p2/t7/hit0", "p3", 5),
             ("uc2/R/p2/t7/hit1", "p4", 5)])

    def test_zero_amount_hit_is_not_paid(self):
        self.assertEqual(payments_for([hit(3, 1, 0, a=0)], 0, "R"), [])

    def test_seq_counts_per_tick_and_kind(self):
        evs = [fire(4, 1), fire(4, 1), fire(5, 1), hit(5, 0, 1), hit(5, 2, 1)]
        ids = [g["id"] for g in payments_for(evs, 1, "X")]
        self.assertEqual(ids, ["uc2/X/p1/t4/fire0", "uc2/X/p1/t4/fire1",
                               "uc2/X/p1/t5/fire0", "uc2/X/p1/t5/hit0",
                               "uc2/X/p1/t5/hit1"])

    def test_times_are_not_set_here(self):
        got = payments_for([fire(1, 0)], 0, "R")
        self.assertEqual(set(got[0]), {"id", "to", "amount_wei"})


class BridgeTest(unittest.TestCase):
    def test_lines_parse_as_payment_events(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pay.sock")
            col = Collector(path)

            async def go():
                b = PayBridge(path)
                await b.connect()
                b.emit(payments_for([fire(1, 0), hit(1, 3, 0)], 0, "R"),
                       100.0, 100.01)
                b.emit([], 100.05, 100.06)
                b.emit(payments_for([fire(2, 0)], 0, "R"), 100.05, 100.07)
                await asyncio.wait_for(b.close(), 10)
                return b.emitted

            emitted = asyncio.run(go())
            lines = col.lines()
        self.assertEqual(emitted, 3)
        self.assertEqual(lines[-1], {"end": True})
        body = lines[:-1]
        self.assertEqual(len(body), 3)
        for ln in body:
            self.assertEqual(set(ln), PAYMENT_KEYS)
            self.assertIsInstance(ln["id"], str)
            self.assertIsInstance(ln["to"], str)
            self.assertIsInstance(ln["amount_wei"], int)
            self.assertIsInstance(ln["t_intended"], float)
            self.assertIsInstance(ln["t_generated"], float)
        self.assertEqual(body[0], {"id": "uc2/R/p0/t1/fire0", "to": "arena",
                                   "amount_wei": 1, "t_intended": 100.0,
                                   "t_generated": 100.01})
        self.assertEqual(body[1]["id"], "uc2/R/p0/t1/hit0")
        self.assertEqual(body[1]["to"], "p3")
        self.assertEqual(body[1]["amount_wei"], 5)
        self.assertEqual(body[2]["id"], "uc2/R/p0/t2/fire0")
        self.assertEqual(body[2]["t_intended"], 100.05)

    def test_bridge_never_blocks_the_tick_loop(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stuck.sock")
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(path)
            srv.listen(1)          # the kernel accepts; nobody ever reads
            try:
                async def go():
                    b = PayBridge(path)
                    await b.connect()
                    line = payments_for([fire(1, 0)], 0, "R")
                    t = time.perf_counter()
                    for i in range(20_000):
                        b.emit(line, float(i), float(i))
                    spent = time.perf_counter() - t
                    t = time.perf_counter()
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(b.close(), 1.0)
                    return spent, time.perf_counter() - t, b.emitted

                spent, closing, emitted = asyncio.run(go())
            finally:
                srv.close()
        self.assertLess(spent, 0.5)
        self.assertLess(closing, 3.0)
        self.assertEqual(emitted, 20_000)

    def test_connect_fails_without_listener(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ConnectionError):
                asyncio.run(PayBridge(os.path.join(d, "none.sock")).connect())


if __name__ == "__main__":
    unittest.main()
