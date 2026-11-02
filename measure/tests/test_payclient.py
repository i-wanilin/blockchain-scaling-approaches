# measure/tests/test_payclient.py
"""Events in over the socket, one ledger row each, drained or unresolved."""
import asyncio
import json
import os
import tempfile
import time
import types
import unittest
from unittest import mock

from measure import payclient
from measure.events import PaymentEvent
from measure.ledger import PaymentLedger
from measure.submitter import SubmitResult


class FakeSubmitter:
    def __init__(self, delay, fail_ids=(), max_in_flight=None):
        self.delay, self.fail_ids = delay, set(fail_ids)
        self.sem = asyncio.Semaphore(max_in_flight) if max_in_flight else None

    async def submit(self, tx):
        if self.sem:
            await self.sem.acquire()                  # like the real submitter: slot held until resolved
        fut = asyncio.get_running_loop().create_future()

        async def later():
            await asyncio.sleep(self.delay)
            if self.sem:
                self.sem.release()
            ok = tx["_id"] not in self.fail_ids
            if fut.done():                            # tracker cancelled after the drain
                return
            fut.set_result(SubmitResult("0x" + tx["_id"][-2:].rjust(64, "0"), time.time(), time.time(),
                                        1 if ok else 0, 1, 21000, 10**8, 1, "" if ok else "reverted"))
        asyncio.create_task(later())
        return fut


def ev(w):
    return PaymentEvent(f"uc1/r/c1/w{w:02d}", "0x" + "33" * 20, 5, time.time(), time.time())


class TestPayLoop(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, sub, n, drain):
        d = tempfile.mkdtemp()
        led = PaymentLedger(os.path.join(d, "ledger.log.jsonl"), max_queue=100)
        q = payclient.EventQueue()
        for w in range(n):
            payclient.accept(led, q, ev(w))
        q.put_nowait(None)
        await payclient.pay_loop(led, sub, lambda e: {"to": e.to, "value": e.amount_wei, "_id": e.id}, q, drain)
        return led

    async def test_all_paid(self):
        led = await self.run_loop(FakeSubmitter(0.01), 10, drain=5)
        self.assertEqual([r["outcome"] for r in led.rows()], ["succeeded"] * 10)

    async def test_revert_is_failed(self):
        led = await self.run_loop(FakeSubmitter(0.01, fail_ids={"uc1/r/c1/w03"}), 5, drain=5)
        self.assertEqual(sum(r["outcome"] == "failed" for r in led.rows()), 1)

    async def test_slow_payments_end_unresolved_at_the_drain_deadline(self):
        led = await self.run_loop(FakeSubmitter(2.0), 3, drain=0.2)
        self.assertEqual({r["outcome"] for r in led.rows()}, {"unresolved"})

    async def test_socket_delivers_events_and_end(self):
        path = os.path.join(tempfile.mkdtemp(), "p.sock")
        got, ended = [], asyncio.Event()

        async def on_event(e):
            got.append(e)

        server = asyncio.create_task(payclient.serve_socket(path, on_event, ended.set))
        await asyncio.sleep(0.1)
        _, w = await asyncio.open_unix_connection(path)
        for k in range(3):
            w.write(ev(k).to_line())
        w.write(b'{"end":true}\n')
        await w.drain()
        await asyncio.wait_for(ended.wait(), 2)
        server.cancel()
        self.assertEqual(len(got), 3)

    async def test_late_result_after_drain_does_not_change_the_outcome(self):
        led = await self.run_loop(FakeSubmitter(0.4), 3, drain=0.1)
        self.assertEqual({r["outcome"] for r in led.rows()}, {"unresolved"})
        await asyncio.sleep(0.6)                      # the fake now delivers its results
        self.assertEqual({r["outcome"] for r in led.rows()}, {"unresolved"})
        lines = [json.loads(l) for l in open(led._log.name)]
        self.assertEqual(sum(l.get("outcome") == "succeeded" for l in lines), 0)

    async def test_deadline_hook_runs_once_at_the_deadline_before_unresolve(self):
        """The optional at_deadline hook (Perun: send quiesce) runs at the drain
        deadline while the rows are still pending, before they are marked unresolved."""
        d = tempfile.mkdtemp()
        led = PaymentLedger(os.path.join(d, "ledger.log.jsonl"), max_queue=100)
        q = payclient.EventQueue()
        for w in range(3):
            payclient.accept(led, q, ev(w))
        q.put_nowait(None)
        calls = []
        orig = led.unresolve_pending

        def unresolve():
            calls.append("unresolve")
            orig()
        led.unresolve_pending = unresolve

        def hook():
            calls.append(("hook", sorted(r["outcome"] for r in led.rows())))
        t0 = time.monotonic()
        await payclient.pay_loop(led, FakeSubmitter(2.0), lambda e: {"_id": e.id}, q, 0.2, at_deadline=hook)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(calls, [("hook", ["submitted"] * 3), "unresolve"])
        self.assertEqual({r["outcome"] for r in led.rows()}, {"unresolved"})

    async def test_failing_deadline_hook_keeps_the_drain_guarantees(self):
        def hook():
            raise RuntimeError("hook failed")
        d = tempfile.mkdtemp()
        led = PaymentLedger(os.path.join(d, "ledger.log.jsonl"), max_queue=100)
        q = payclient.EventQueue()
        for w in range(3):
            payclient.accept(led, q, ev(w))
        q.put_nowait(None)
        await payclient.pay_loop(led, FakeSubmitter(0.4), lambda e: {"_id": e.id}, q, 0.1, at_deadline=hook)
        self.assertEqual({r["outcome"] for r in led.rows()}, {"unresolved"})
        await asyncio.sleep(0.6)                      # late results never give a second outcome
        lines = [json.loads(l) for l in open(led._log.name)]
        self.assertEqual(sum(l.get("outcome") == "succeeded" for l in lines), 0)

    async def test_malformed_line_is_skipped(self):
        path = os.path.join(tempfile.mkdtemp(), "p.sock")
        got, ended = [], asyncio.Event()

        async def on_event(e):
            got.append(e)

        server = asyncio.create_task(payclient.serve_socket(path, on_event, ended.set))
        await asyncio.sleep(0.1)
        _, w = await asyncio.open_unix_connection(path)
        w.write(b"not json\n" + b'{"foo":1}\n' + ev(0).to_line() + b'{"end":true}\n')
        await w.drain()
        await asyncio.wait_for(ended.wait(), 2)
        server.cancel()
        self.assertEqual(len(got), 1)


class TestBacklog(unittest.IsolatedAsyncioTestCase):
    def mk(self, max_queue):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "ledger.log.jsonl")
        return PaymentLedger(path, max_queue=max_queue), path, payclient.EventQueue()

    async def test_queue_bound_binds_behind_a_slow_submitter(self):
        led, _, q = self.mk(max_queue=10)
        sub = FakeSubmitter(0.3, max_in_flight=2)
        loop = asyncio.create_task(payclient.pay_loop(
            led, sub, lambda e: {"to": e.to, "value": 1, "_id": e.id}, q, drain_s=0.2))
        peak = 0
        res = []
        for w in range(40):
            res.append(payclient.accept(led, q, ev(w)))
            peak = max(peak, led.pending())
            await asyncio.sleep(0.002)
        q.put_nowait(None)
        await loop
        self.assertLessEqual(peak, 10)
        self.assertGreater(res.count("full"), 0)
        failed = [r for r in led.rows() if r["outcome"] == "failed"]
        self.assertTrue(failed and all(r["error"] == "queue full" for r in failed))
        self.assertEqual(led.pending(), 0)

    async def test_drain_clock_starts_at_the_end_marker(self):
        led, _, q = self.mk(max_queue=1000)
        sub = FakeSubmitter(0.3, max_in_flight=1)     # 50 events would need 15 s
        for w in range(50):
            payclient.accept(led, q, ev(w))
        loop = asyncio.create_task(payclient.pay_loop(
            led, sub, lambda e: {"to": e.to, "value": 1, "_id": e.id}, q, drain_s=0.5))
        await asyncio.sleep(0.1)
        t0 = time.monotonic()
        q.put_nowait(None)
        await loop
        self.assertLess(time.monotonic() - t0, 0.5 + 0.3)
        outcomes = [r["outcome"] for r in led.rows()]
        self.assertGreater(outcomes.count("unresolved"), 40)
        self.assertEqual(led.pending(), 0)
        await asyncio.sleep(0.5)                       # nothing changes afterwards
        self.assertEqual([r["outcome"] for r in led.rows()], outcomes)

    async def test_every_received_event_has_a_log_line_after_a_crash(self):
        led, path, q = self.mk(max_queue=1000)
        sub = FakeSubmitter(5, max_in_flight=1)
        loop = asyncio.create_task(payclient.pay_loop(
            led, sub, lambda e: {"to": e.to, "value": 1, "_id": e.id}, q, drain_s=5))
        for w in range(20):
            payclient.accept(led, q, ev(w))
        await asyncio.sleep(0.1)
        loop.cancel()                                  # crash mid-run
        await asyncio.gather(loop, return_exceptions=True)
        from measure.ledger import load_log
        rows = load_log(path)
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(r["outcome"] for r in rows))
        led.close()

    async def test_worker_exception_propagates(self):
        led, _, q = self.mk(max_queue=10)
        payclient.accept(led, q, ev(0))
        q.put_nowait(None)

        def bad_submitted(*a):
            raise OSError("disk full")
        led.submitted = bad_submitted                  # ledger I/O error outside the submit try
        with self.assertRaises(OSError):
            await payclient.pay_loop(led, FakeSubmitter(0.01), lambda e: {"_id": e.id}, q, drain_s=0.2)

    async def test_pre_send_failure_has_no_t_submitted(self):
        class Resolved:
            async def submit(self, tx):
                f = asyncio.get_running_loop().create_future()
                f.set_result(SubmitResult(None, None, time.time(), None, None, None, None, 0, "send failed"))
                return f
        led, _, q = self.mk(max_queue=10)
        payclient.accept(led, q, ev(0))
        q.put_nowait(None)
        await payclient.pay_loop(led, Resolved(), lambda e: {}, q, drain_s=0.2)
        r = led.rows()[0]
        self.assertEqual(r["outcome"], "failed")
        self.assertIsNone(r["t_submitted"])

    async def test_event_after_end_is_a_failed_row(self):
        led, _, q = self.mk(max_queue=10)
        q.put_nowait(None)
        self.assertEqual(payclient.accept(led, q, ev(0)), "late")
        r = led.rows()[0]
        self.assertEqual((r["outcome"], r["error"]), ("failed", "after end"))
        self.assertEqual(led.pending(), 0)

    async def test_duplicate_is_reported(self):
        led, _, q = self.mk(max_queue=5)
        e = ev(1)
        self.assertEqual(payclient.accept(led, q, e), "queued")
        self.assertEqual(payclient.accept(led, q, e), "duplicate")

    async def test_ready_only_after_listening_with_a_stale_socket(self):
        path = os.path.join(tempfile.mkdtemp(), "p.sock")
        open(path, "w").close()                        # stale file from a killed run
        listening = asyncio.Event()

        async def on_event(e):
            pass

        server = asyncio.create_task(payclient.serve_socket(path, on_event, lambda: None, listening))
        await asyncio.wait_for(listening.wait(), 2)
        _, w = await asyncio.open_unix_connection(path)   # connects: the socket is really bound
        w.close()
        server.cancel()


class FakeEth:
    gas_price = 10**8
    nonce = {"latest": 0, "pending": 0}            # per test; a fake Close advances both
    fail_reads = {}                                # {(block tag, after Close?): failures left}
    closed = False
    reads = []
    pending_script = []                            # successive pre-Close pending values (admission catching up)
    after_close_latest = []                        # successive post-Close latest values (queued payments mining)

    def __init__(self, balance):
        self.balance = balance

    def get_balance(self, addr):
        return self.balance

    points_balance = 1234                          # balanceOf result; an Exception instance is raised
    calls = []

    def call(self, tx):
        FakeEth.calls.append(tx)
        if isinstance(FakeEth.points_balance, Exception):
            raise FakeEth.points_balance
        return FakeEth.points_balance.to_bytes(32, "big")

    def estimate_gas(self, tx):
        return 50000

    def get_transaction_count(self, addr, block="latest"):
        if FakeEth.fail_reads.get((block, FakeEth.closed), 0) > 0:     # injected RPC failures
            FakeEth.fail_reads[(block, FakeEth.closed)] -= 1
            raise ConnectionError(f"rpc down ({block})")
        if block == "pending" and not FakeEth.closed and FakeEth.pending_script:
            FakeEth.nonce["pending"] = FakeEth.pending_script.pop(0)
        if block == "latest" and FakeEth.closed and FakeEth.after_close_latest:
            v = FakeEth.after_close_latest.pop(0)
            FakeEth.nonce = {"latest": v, "pending": max(v, FakeEth.nonce["pending"])}
        FakeEth.reads.append(block)
        return FakeEth.nonce[block]


class FakeWeb3:
    balance = 0

    class HTTPProvider:
        def __init__(self, url):
            pass

    def __init__(self, provider=None):
        self.eth = FakeEth(FakeWeb3.balance)


class FakeSub:
    resyncs, reconnects, outage_seconds, quiesce_timeouts = 1, 2, 3.5, 4
    receipt_mode, receipt_fallback, receipt_fallback_error = "own", True, "-32601: no such method"
    start_nonce = 7
    next_nonce = 9                    # distinct from start_nonce: proves payclient plumbs THIS value through
    last_kw = None
    txs = []

    def __init__(self, *a, **k):
        FakeSub.last_kw = k
        self.receipt_mode = k.get("receipt_mode", type(self).receipt_mode)   # like the real OrderedSubmitter

    async def start(self):
        pass

    async def close(self):
        pass

    async def submit(self, tx):
        FakeSub.txs.append(tx)
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(SubmitResult("0x" + "1" * 64, time.time(), time.time(), 1, 1, 21000, 10**8, 1, ""))
        return fut


class TestSystems(unittest.TestCase):
    def test_system_modules(self):
        from measure import arbitrum, zksync
        self.assertIs(payclient.SYSTEMS["arbitrum"], arbitrum)
        self.assertIs(payclient.SYSTEMS["zksync"], zksync)


class TestReceiptModeArg(unittest.TestCase):
    """--receipt-mode: selectable per experiment, overriding the system module's own choice
    (task: Arbitrum at w10 needs "own" like zkSync already uses, to stop overloading the Wi-Fi
    with eth_getBlockReceipts for every block)."""

    BASE = ["--system", "arbitrum", "--l1", "http://x:8545", "--l2", "ws://x:8548",
            "--account", "101", "--pay-to", "0x" + "33" * 20, "--deposit-wei", "1", "--out", "/tmp/x"]

    def test_default_is_none_the_module_keeps_its_own_choice(self):
        a = payclient.parse_args(self.BASE)
        self.assertIsNone(a.receipt_mode)

    def test_own_and_block_are_accepted(self):
        for mode in ("own", "block"):
            a = payclient.parse_args([*self.BASE, "--receipt-mode", mode])
            self.assertEqual(a.receipt_mode, mode)

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(SystemExit):
            payclient.parse_args([*self.BASE, "--receipt-mode", "bogus"])

    def test_rejected_for_perun(self):
        """perun has its own lifecycle (a sidecar, no OrderedSubmitter): --receipt-mode does not apply."""
        perun_argv = ["--system", "perun", "--chain", "ws://x:8545", "--chain-id", "1",
                      "--sensor-control", "http://x:8090", "--sidecar", "/bin/perun-node",
                      "--wire-bind", "0.0.0.0:1", "--wire-endpoint", "x:1",
                      "--account", "101", "--pay-to", "0x" + "33" * 20, "--deposit-wei", "1", "--out", "/tmp/x"]
        payclient.parse_args(perun_argv)                       # sanity: valid without --receipt-mode
        with self.assertRaises(SystemExit):
            payclient.parse_args([*perun_argv, "--receipt-mode", "own"])


class TestSlots(unittest.IsolatedAsyncioTestCase):
    async def test_slot_is_not_freed_before_the_receipt_timeout(self):
        """Receipts take 1.0 s, the receipt timeout is 5 s: a slot is only freed by the
        receipt, so at no time are more than max_in_flight=2 rows submitted but unfinished."""
        class TimeoutSubmitter(FakeSubmitter):          # like OrderedSubmitter: slot held until receipt or timeout
            def __init__(self, delay, max_in_flight, receipt_timeout):
                super().__init__(min(delay, receipt_timeout), max_in_flight=max_in_flight)
        d = tempfile.mkdtemp()
        led = PaymentLedger(os.path.join(d, "ledger.log.jsonl"), max_queue=100)
        q = payclient.EventQueue()
        sub = TimeoutSubmitter(1.0, max_in_flight=2, receipt_timeout=5.0)
        for w in range(6):
            payclient.accept(led, q, ev(w))
        q.put_nowait(None)
        loop = asyncio.create_task(payclient.pay_loop(
            led, sub, lambda e: {"to": e.to, "value": 1, "_id": e.id}, q, drain_s=10))
        peak = 0
        while not loop.done():
            peak = max(peak, sum(1 for r in led.rows() if r["outcome"] == "submitted"))
            await asyncio.sleep(0.02)
        await loop
        self.assertEqual(peak, 2)
        self.assertEqual([r["outcome"] for r in led.rows()], ["succeeded"] * 6)


class TestAdmissionWait(unittest.IsolatedAsyncioTestCase):
    """wait_for_admission: the bounded wait before Close for the node's pending nonce to reach
    the submitter's next nonce (fix for the Task 10d max() nonce gap)."""

    def reader(self, values):
        self.calls = 0

        def read():
            v = values[min(self.calls, len(values) - 1)]
            self.calls += 1
            return v
        return read

    async def test_already_at_or_above_submitted_returns_at_once(self):
        for pending in (9, 12):
            pend, waited = await payclient.wait_for_admission(self.reader([pending]), 9, wait_s=5, poll_s=0.5)
            self.assertEqual(pend, pending)
            self.assertEqual(self.calls, 1)
            self.assertLess(waited, 0.1)

    async def test_catches_up_within_the_bound(self):
        pend, waited = await payclient.wait_for_admission(self.reader([5, 7, 9, 9]), 9, wait_s=5, poll_s=0.02)
        self.assertEqual(pend, 9)
        self.assertEqual(self.calls, 3)
        self.assertLess(waited, 1)

    async def test_never_catches_up_returns_the_node_pending_after_the_bound(self):
        t0 = time.monotonic()
        pend, waited = await payclient.wait_for_admission(self.reader([5, 6, 6]), 9, wait_s=0.2, poll_s=0.02)
        self.assertEqual(pend, 6)                       # the node's value: no gap, 3 not admitted
        self.assertGreaterEqual(waited, 0.2)
        self.assertGreaterEqual(time.monotonic() - t0, 0.2)
        self.assertLess(waited, 1)

    async def test_read_failure_raises(self):
        def read():
            raise ConnectionError("down")
        with mock.patch.object(payclient, "RPC_RETRY_DELAYS", (0.01,)):
            with self.assertRaises(ConnectionError):
                await payclient.wait_for_admission(read, 9, wait_s=1, poll_s=0.02)

    def test_defaults(self):
        self.assertEqual((payclient.ADMISSION_WAIT_S, payclient.ADMISSION_POLL_S), (30.0, 0.5))


class TestSettleLatest(unittest.IsolatedAsyncioTestCase):
    """settle_latest: the bounded wait after Close for payments queued behind Close to mine."""

    def reader(self, values):
        self.calls = 0

        def read():
            v = values[min(self.calls, len(values) - 1)]
            self.calls += 1
            return v
        return read

    async def test_at_target_returns_at_once(self):
        latest, waited = await payclient.settle_latest(self.reader([10]), 10, quiet_s=5, max_s=5, poll_s=0.5)
        self.assertEqual((latest, self.calls), (10, 1))
        self.assertLess(waited, 0.1)

    async def test_follows_until_target(self):
        latest, waited = await payclient.settle_latest(self.reader([5, 6, 8, 10]), 10, quiet_s=5, max_s=5,
                                                       poll_s=0.02)
        self.assertEqual((latest, self.calls), (10, 4))

    async def test_stops_when_quiet(self):
        latest, waited = await payclient.settle_latest(self.reader([5, 6, 6]), 10, quiet_s=0.1, max_s=5,
                                                       poll_s=0.02)
        self.assertEqual(latest, 6)
        self.assertGreaterEqual(waited, 0.1)
        self.assertLess(waited, 1)

    async def test_stops_at_the_bound_while_still_moving(self):
        n = iter(range(100))
        latest, waited = await payclient.settle_latest(lambda: next(n), 1000, quiet_s=5, max_s=0.2, poll_s=0.02)
        self.assertGreaterEqual(waited, 0.2)
        self.assertLess(waited, 1)

    def test_defaults(self):
        self.assertEqual((payclient.SETTLE_MAX_S, payclient.SETTLE_QUIET_S, payclient.SETTLE_POLL_S),
                         (30.0, 3.0, 0.5))


class TestLifecycle(unittest.IsolatedAsyncioTestCase):
    async def run_main(self, close_status=1, close_raises=False, open_raises=False, balance=0,
                       system="arbitrum", pending_before_close=0, honor_min_nonce=False, fail_reads=None,
                       pending_script=None, after_close_latest=None, submitted=9, interrupt=None,
                       receipt_mode=None, fakesys_submitter_kw=None, extra=None, event=None):
        import argparse
        from unittest import mock
        d = tempfile.mkdtemp()
        a = argparse.Namespace(system=system, l1="http://x:8545", l2="ws://x:8548", account=101,
                               pay_to="0x" + "33" * 20, deposit_wei=10**18, max_in_flight=4, max_queue=100,
                               drain=1, socket=os.path.join(d, "p.sock"), out=os.path.join(d, "out"),
                               receipt_timeout=1200, close_timeout=77, receipt_mode=receipt_mode, **(extra or {}))
        FakeEth.nonce = {"latest": 8, "pending": 8 + pending_before_close}   # start 7, one payment executed
        FakeEth.fail_reads, FakeEth.closed = dict(fail_reads or {}), False
        FakeEth.pending_script, FakeEth.reads = list(pending_script or []), []
        FakeEth.after_close_latest = list(after_close_latest or [])
        self.close_kw = None
        FakeSub.txs, FakeEth.calls = [], []

        def close(*_, **kw):
            self.close_kw = kw
            if close_raises:
                raise RuntimeError("boom")
            n = FakeEth.nonce["pending"]
            if honor_min_nonce and kw.get("min_nonce") is not None:   # same max() as the real _send
                n = max(n, kw["min_nonce"])
            FakeEth.nonce = {"latest": n + 1, "pending": n + 1}
            FakeEth.closed = True
            return {"status": close_status, **({"nonce": n} if system != "arbitrum" else {})}

        def opn(*_):
            if open_raises:
                raise RuntimeError("no credit")
            return {"t_start": 1.0}

        FakeWeb3.balance = balance
        from measure import arbitrum
        fake_sys = types.SimpleNamespace(
            L2_CHAIN_ID=271, http_url=lambda u: u.replace("ws://", "http://"), open_deposit=opn,
            close_withdraw=close, estimate_payment_gas=lambda *_: 400_000, payment_tx=arbitrum.payment_tx,
            SUBMITTER_KW={"receipt_sweep_s": 10.0, **(fakesys_submitter_kw or {})})
        with mock.patch.object(payclient, "Web3", FakeWeb3), \
                mock.patch.object(payclient, "OrderedSubmitter", FakeSub), \
                mock.patch.dict(payclient.SYSTEMS, {"fakesys": fake_sys}), \
                mock.patch.object(payclient.arbitrum, "open_deposit", opn), \
                mock.patch.object(payclient.arbitrum, "close_withdraw", close), \
                mock.patch.object(payclient.arbitrum, "estimate_payment_gas", lambda *_: 21000), \
                mock.patch.object(payclient, "RPC_RETRY_DELAYS", (0.01, 0.01, 0.01)), \
                mock.patch.object(payclient, "ADMISSION_WAIT_S", 0.3), \
                mock.patch.object(payclient, "ADMISSION_POLL_S", 0.02), \
                mock.patch.object(payclient, "SETTLE_MAX_S", 0.5), \
                mock.patch.object(payclient, "SETTLE_QUIET_S", 0.1), \
                mock.patch.object(payclient, "SETTLE_POLL_S", 0.02), \
                mock.patch.object(FakeSub, "next_nonce", submitted):
            entered = asyncio.Event()
            if interrupt:                                 # SIGTERM while main_async is inside this wait
                inner = getattr(payclient, interrupt)

                async def wrapped(*args, **kw):
                    entered.set()
                    return await inner(*args, **kw)
                stack = mock.patch.object(payclient, interrupt, wrapped)
                stack.start()
                self.addCleanup(stack.stop)

            async def client():
                while not os.path.exists(os.path.join(a.out, "ready")):
                    await asyncio.sleep(0.02)
                _, w = await asyncio.open_unix_connection(a.socket)
                w.write((event or ev(0)).to_line() + b'{"end":true}\n')
                await w.drain()
            c = asyncio.create_task(client())
            try:
                if interrupt:
                    import signal
                    t = asyncio.create_task(payclient.run(a))
                    await asyncio.wait_for(entered.wait(), 10)
                    await asyncio.sleep(0.05)
                    os.kill(os.getpid(), signal.SIGTERM)
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(t, 10)
                    code = None
                else:
                    code = await asyncio.wait_for(payclient.main_async(a), 10)
            finally:
                c.cancel()
        with open(os.path.join(a.out, "lifecycle.json")) as f:
            return code, json.load(f), a.out

    async def test_success_is_complete(self):
        code, life, out = await self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(life["complete"])
        self.assertEqual((life["resyncs"], life["reconnects"], life["outage_seconds"], life["quiesce_timeouts"]),
                         (1, 2, 3.5, 4))
        self.assertTrue(os.path.exists(os.path.join(out, "ledger.csv")))

    async def test_fee_cap_wei_is_recorded(self):
        """lifecycle.json records the admission ceiling payclient actually used for
        payments: arbitrum.FEE_CAP_MULTIPLIER * gas_price + 1 (FakeEth.gas_price here)."""
        from measure import arbitrum
        code, life, _ = await self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(life["fee_cap_wei"], arbitrum.FEE_CAP_MULTIPLIER * FakeEth.gas_price + 1)

    async def test_receipt_path_state_is_recorded(self):
        """Final review Important 1: which receipt path the submitter ended on is in lifecycle.json."""
        code, life, _ = await self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual((life["receipt_mode"], life["receipt_fallback"], life["receipt_fallback_error"]),
                         ("own", True, "-32601: no such method"))

    async def test_reverted_close_is_not_complete(self):
        code, life, out = await self.run_main(close_status=0)
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIn("close_error", life)
        self.assertTrue(os.path.exists(os.path.join(out, "ledger.csv")))

    async def test_raising_close_is_not_complete(self):
        code, life, _ = await self.run_main(close_raises=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIn("close_error", life)

    async def test_failed_open_is_recorded_and_no_socket(self):
        code, life, out = await self.run_main(open_raises=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIn("open_error", life)
        self.assertFalse(os.path.exists(os.path.join(out, "ready")))

    async def test_sigterm_still_writes_lifecycle_and_csv(self):
        import argparse
        import signal
        from unittest import mock
        d = tempfile.mkdtemp()
        a = argparse.Namespace(system="arbitrum", l1="x", l2="ws://x:8548", account=101,
                               pay_to="0x" + "33" * 20, deposit_wei=1, max_in_flight=4, max_queue=100,
                               drain=1, socket=os.path.join(d, "p.sock"), out=os.path.join(d, "out"),
                               receipt_timeout=120, close_timeout=300)
        FakeWeb3.balance = 0
        with mock.patch.object(payclient, "Web3", FakeWeb3), \
                mock.patch.object(payclient, "OrderedSubmitter", FakeSub), \
                mock.patch.object(payclient.arbitrum, "open_deposit", lambda *_: {"t_start": 1.0}), \
                mock.patch.object(payclient.arbitrum, "estimate_payment_gas", lambda *_: 21000):
            t = asyncio.create_task(payclient.run(a))
            while not os.path.exists(os.path.join(a.out, "ready")):
                await asyncio.sleep(0.02)
            os.kill(os.getpid(), signal.SIGTERM)
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(t, 5)
        with open(os.path.join(a.out, "lifecycle.json")) as f:
            life = json.load(f)
        self.assertFalse(life["complete"])
        self.assertTrue(os.path.exists(os.path.join(a.out, "ledger.csv")))

    async def test_nonce_bookkeeping_for_an_injected_system(self):
        code, life, _ = await self.run_main(system="fakesys")
        self.assertEqual(code, 0)
        self.assertTrue(life["complete"])
        self.assertEqual((life["start_nonce"], life["pending_before_close"], life["close_nonce"],
                          life["executed_onchain"], life["receipt_timeout"]), (7, 0, 8, 1, 1200))
        self.assertEqual(life["submitted_nonce"], 9)
        self.assertEqual(life["not_admitted"], 1)        # FakeEth pending stays 8: nonce 8 never admitted
        self.assertEqual(self.close_kw, {"timeout": 77, "min_nonce": 8})
        self.assertEqual(FakeSub.last_kw, {"receipt_timeout": 1200, "receipt_sweep_s": 10.0})

    async def test_receipt_mode_absent_leaves_module_default_unchanged(self):
        """No --receipt-mode: the module's own SUBMITTER_KW choice reaches the submitter untouched,
        whether the module sets it (zkSync: "own", simulated here) or not (arbitrum: no key at all,
        OrderedSubmitter's own "block" default governs, see test_nonce_bookkeeping_for_an_injected_system)."""
        code, life, _ = await self.run_main(system="fakesys", fakesys_submitter_kw={"receipt_mode": "own"})
        self.assertEqual(code, 0)
        self.assertEqual(FakeSub.last_kw["receipt_mode"], "own")
        self.assertEqual(life["receipt_mode"], "own")

    async def test_receipt_mode_override_replaces_module_default(self):
        """--receipt-mode overrides whatever the module chose (here simulating zkSync's "own"
        being overridden to "block", and the reverse: arbitrum's implicit "block" to "own")."""
        code, life, _ = await self.run_main(system="fakesys", fakesys_submitter_kw={"receipt_mode": "own"},
                                            receipt_mode="block")
        self.assertEqual(code, 0)
        self.assertEqual(FakeSub.last_kw["receipt_mode"], "block")
        self.assertEqual(life["receipt_mode"], "block")              # the mode actually used, not the module's

        code, life, _ = await self.run_main(system="fakesys", receipt_mode="own")   # no module default at all
        self.assertEqual(FakeSub.last_kw["receipt_mode"], "own")
        self.assertEqual(life["receipt_mode"], "own")

    async def test_pending_before_close_counts_unexecuted_nonces(self):
        code, life, _ = await self.run_main(system="fakesys", pending_before_close=3)
        self.assertEqual(life["pending_before_close"], 3)
        self.assertEqual((life["close_nonce"], life["executed_onchain"]), (11, 4))

    async def test_close_waits_for_the_node_to_admit_every_sent_payment(self):
        """(a) Admission race (debug-late-offbyone.md): the node's pending nonce (8) lags the
        submitter's next_nonce (9) and catches up within the bound. Close uses 9, so it never
        collides with the payment at nonce 8, and executed_onchain counts that payment."""
        for system in ("fakesys", "arbitrum"):
            code, life, _ = await self.run_main(system=system, honor_min_nonce=True, pending_script=[8, 8, 9])
            self.assertEqual(code, 0, system)
            self.assertEqual(life["submitted_nonce"], 9)
            self.assertEqual(self.close_kw["min_nonce"], 9, system)
            self.assertEqual(life["not_admitted"], 0)
            self.assertGreater(life["admission_wait_s"], 0)
            self.assertLess(life["admission_wait_s"], 0.3)       # caught up before the bound
            self.assertEqual((life["close_nonce"], life["executed_onchain"]), (9, 2), system)

    async def test_close_uses_the_node_pending_nonce_when_sent_payments_were_dropped(self):
        """(b) Dropped payments (run 20261003-024642 ... w10-rep3, "nonce too high"): the node's
        pending nonce (8) never reaches submitted_nonce (9) within the bound. Close uses 8 -- no
        nonce gap -- and not_admitted = 9 - 8 = 1 is recorded; executed_onchain excludes it."""
        for system in ("fakesys", "arbitrum"):
            t0 = time.monotonic()
            code, life, _ = await self.run_main(system=system, honor_min_nonce=True)
            self.assertEqual(code, 0, system)
            self.assertTrue(life["complete"], system)
            self.assertEqual(life["submitted_nonce"], 9)
            self.assertEqual(self.close_kw["min_nonce"], 8, system)          # not 9: that would leave a gap
            self.assertEqual(life["not_admitted"], 1)
            self.assertGreaterEqual(life["admission_wait_s"], 0.3)
            self.assertGreaterEqual(time.monotonic() - t0, 0.3)
            self.assertEqual((life["close_nonce"], life["executed_onchain"]), (8, 1), system)

    async def test_close_is_immediate_when_pending_is_already_at_submitted_nonce(self):
        """(c) The node's pending nonce (13) is already >= submitted_nonce (9): one read, no wait,
        Close uses the node's value as before."""
        code, life, _ = await self.run_main(system="fakesys", honor_min_nonce=True, pending_before_close=5)
        self.assertEqual(life["submitted_nonce"], 9)
        self.assertEqual(self.close_kw["min_nonce"], 13)
        self.assertEqual(life["not_admitted"], 0)
        self.assertLess(life["admission_wait_s"], 0.1)
        self.assertEqual(FakeEth.reads.count("pending"), 2)   # one admission read + the pending_before_close one
        self.assertEqual((life["close_nonce"], life["executed_onchain"]), (13, 6))

    async def test_payments_queued_behind_a_gap_that_execute_after_close_are_counted(self):
        """Review Important 1 (zkSync): pending stops at the first missing nonce (8) while 9..11
        sit queued. Close takes 8 (no gap); 9..11 then mine after Close. final_nonce 12 and
        executed_after_close = 12 - (8 + 1) = 3 keep the accounting exact."""
        code, life, _ = await self.run_main(system="fakesys", honor_min_nonce=True, submitted=12,
                                            after_close_latest=[9, 10, 12])
        self.assertEqual(code, 0)
        self.assertTrue(life["complete"])
        self.assertEqual((life["not_admitted"], life["close_nonce"], life["executed_onchain"]), (4, 8, 1))
        self.assertEqual((life["final_nonce"], life["executed_after_close"]), (12, 3))
        self.assertLess(life["settle_wait_s"], 0.5)                     # stopped at the target, not the bound

    async def test_settle_stops_when_latest_is_quiet(self):
        """Nothing executes after Close (really dropped): stops after SETTLE_QUIET_S, reads 0."""
        for system in ("fakesys", "arbitrum"):
            code, life, _ = await self.run_main(system=system, honor_min_nonce=True, submitted=12)
            self.assertEqual(code, 0, system)
            self.assertEqual((life["close_nonce"], life["final_nonce"], life["executed_after_close"]),
                             (8, 9, 0), system)
            self.assertGreaterEqual(life["settle_wait_s"], 0.1)
            self.assertLess(life["settle_wait_s"], 0.5)

    async def test_settle_is_immediate_in_the_normal_case(self):
        """Every payment admitted and Close above them: latest is already Close + 1, no wait."""
        for system in ("fakesys", "arbitrum"):
            code, life, _ = await self.run_main(system=system, honor_min_nonce=True, pending_script=[9])
            self.assertEqual((life["close_nonce"], life["final_nonce"], life["executed_after_close"]),
                             (9, 10, 0), system)
            self.assertLess(life["settle_wait_s"], 0.1)

    async def test_failed_final_nonce_read_is_not_complete(self):
        code, life, _ = await self.run_main(system="fakesys", fail_reads={("latest", True): 99})
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertEqual((life["close_nonce"], life["executed_onchain"]), (8, 1))
        self.assertIsNone(life["final_nonce"])
        self.assertIsNone(life["executed_after_close"])
        self.assertIn("final_nonce", life["nonce_read_error"])

    async def test_sigterm_during_the_admission_wait_writes_an_incomplete_lifecycle(self):
        """Review item 4: SIGTERM inside the (up to 30 s) admission wait ends as a clean failure."""
        from unittest import mock
        with mock.patch.object(payclient, "ADMISSION_WAIT_S", 30.0):
            code, life, out = await self.run_main(system="fakesys", interrupt="wait_for_admission")
        self.assertFalse(life["complete"])
        self.assertIn("CancelledError", life["error"])
        self.assertNotIn("close", life)
        self.assertTrue(os.path.exists(os.path.join(out, "ledger.csv")))

    async def test_sigterm_during_the_post_close_settle_writes_an_incomplete_lifecycle(self):
        code, life, out = await self.run_main(system="fakesys", submitted=12, interrupt="settle_latest")
        self.assertFalse(life["complete"])
        self.assertIn("CancelledError", life["error"])
        self.assertEqual(life["close"]["status"], 1)
        self.assertTrue(os.path.exists(os.path.join(out, "ledger.csv")))

    async def test_failed_admission_read_lets_close_read_its_own_nonce(self):
        """If the pending nonce cannot be read at all, Close is still attempted without a bound
        (close_withdraw reads the node's pending nonce itself), not_admitted is unknown and the
        missing fact makes the run incomplete."""
        code, life, _ = await self.run_main(system="fakesys", fail_reads={("pending", False): 99})
        self.assertNotEqual(code, 0)
        self.assertEqual(self.close_kw, {"timeout": 77})
        self.assertIsNone(life["not_admitted"])
        self.assertIn("not_admitted", life["nonce_read_error"])
        self.assertEqual((life["close_nonce"], life["executed_onchain"]), (8, 1))

    async def test_arbitrum_close_nonce_from_latest_after_close(self):
        code, life, _ = await self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual((life["start_nonce"], life["pending_before_close"], life["close_nonce"],
                          life["executed_onchain"]), (7, 0, 8, 1))
        self.assertEqual(life["submitted_nonce"], 9)
        self.assertEqual(self.close_kw, {"min_nonce": 8})  # Arbitrum gets the same rule: the node's pending
        self.assertEqual(life["not_admitted"], 1)
        self.assertEqual(FakeSub.last_kw, {"receipt_timeout": 1200})

    async def test_transient_nonce_read_failures_around_close_are_retried(self):
        """Final review Minor 1: a failed read is retried; two failures each still give a complete run."""
        for system in ("arbitrum", "fakesys"):
            fails = {("pending", False): 2, ("latest", False): 2, ("latest", True): 2}
            code, life, _ = await self.run_main(system=system, fail_reads=fails)
            self.assertEqual(code, 0, system)
            self.assertTrue(life["complete"], system)
            self.assertNotIn("nonce_read_error", life)
            self.assertEqual((life["pending_before_close"], life["close_nonce"], life["executed_onchain"]),
                             (0, 8, 1), system)

    async def test_failed_read_before_close_still_closes_but_is_not_complete(self):
        """Pre-Close reads are diagnostics only (close_withdraw reads its own nonce): Close is still
        attempted, so the account is closed and close_nonce/executed_onchain are recorded, but the
        missing pending_before_close fact makes the run incomplete."""
        for system in ("arbitrum", "fakesys"):
            for tag in ("pending", "latest"):
                code, life, _ = await self.run_main(system=system, fail_reads={(tag, False): 99})
                self.assertNotEqual(code, 0, (system, tag))
                self.assertFalse(life["complete"], (system, tag))
                self.assertIsNotNone(self.close_kw, (system, tag))               # Close was attempted
                self.assertIn("close", life)
                self.assertIsNone(life["pending_before_close"])
                self.assertIn("pending_before_close", life["nonce_read_error"])
                self.assertEqual((life["close_nonce"], life["executed_onchain"]), (8, 1), (system, tag))
                self.assertNotIn("error", life)                                  # a clean failure, no crash

    async def test_failed_arbitrum_read_after_close_is_not_complete(self):
        code, life, _ = await self.run_main(fail_reads={("latest", True): 99})
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertEqual(life["close"]["status"], 1)
        self.assertIsNone(life["close_nonce"])
        self.assertIsNone(life["executed_onchain"])
        self.assertIn("close_nonce", life["nonce_read_error"])
        self.assertNotIn("error", life)

    def points_args(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "rec.json")
        with open(p, "w") as f:
            json.dump({"arena": "0x" + "11" * 20, **{f"p{k}": "0x" + f"{k + 2:02x}" * 20 for k in range(6)}}, f)
        return {"asset": "points", "token": "0x" + "ee" * 20, "recipients": p, "players": 6}

    async def test_points_mode_sends_token_transfers(self):
        e = PaymentEvent("uc2/r/p1/0", "p3", 5, time.time(), time.time())
        code, life, _ = await self.run_main(extra=self.points_args(), event=e)
        self.assertEqual(code, 0)
        self.assertEqual(len(FakeSub.txs), 1)
        tx = FakeSub.txs[0]
        self.assertEqual(tx["to"], "0x" + "ee" * 20)
        self.assertEqual(tx["value"], 0)
        self.assertIn("05" * 20, tx["data"])
        self.assertTrue(tx["data"].startswith("0xa9059cbb") and tx["data"].endswith("5"))
        self.assertEqual(life["payment_gas_limit"], 75000)       # 1.5 x the fake estimate

    async def test_points_mode_records_balance_before_close(self):
        code, life, _ = await self.run_main(extra=self.points_args(), event=PaymentEvent(
            "uc2/r/p1/0", "arena", 1, time.time(), time.time()))
        self.assertEqual((code, life["points_before_close"], life["complete"]), (0, 1234, True))
        FakeEth.points_balance = 1234

    async def test_points_mode_failed_balance_read_is_not_complete(self):
        FakeEth.points_balance = ConnectionError("rpc down")
        try:
            code, life, _ = await self.run_main(extra=self.points_args(), event=PaymentEvent(
                "uc2/r/p1/0", "arena", 1, time.time(), time.time()))
        finally:
            FakeEth.points_balance = 1234
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIsNone(life["points_before_close"])
        self.assertIn("points_before_close", life["nonce_read_error"])

    async def test_eth_mode_unchanged(self):
        e = PaymentEvent("uc1/r/c1/w00", "0x" + "33" * 20, 5, time.time(), time.time())
        code, life, _ = await self.run_main(event=e)
        self.assertEqual(code, 0)
        self.assertEqual(FakeSub.txs, [{"to": "0x" + "33" * 20, "value": 5, "gas": 21000,
                                        "maxFeePerGas": 10 * 10**8 + 1, "maxPriorityFeePerGas": 0}])
        self.assertNotIn("points_before_close", life)
        a = payclient.parse_args(["--system", "arbitrum", "--l1", "x", "--l2", "y", "--account", "1",
                                  "--pay-to", "0x1", "--deposit-wei", "1", "--out", "o"])
        self.assertEqual(a.asset, "eth")
        with self.assertRaises(SystemExit):                      # eth still needs --pay-to
            payclient.parse_args(["--system", "arbitrum", "--l1", "x", "--l2", "y", "--account", "1",
                                  "--deposit-wei", "1", "--out", "o"])

    async def test_nonzero_balance_before_open_warns(self):
        code, life, _ = await self.run_main(balance=5)
        self.assertEqual(code, 0)
        self.assertIn("open_warning", life)


@unittest.skipUnless(os.environ.get("ZK_L1") and os.environ.get("ZK_L2"), "needs the workstation zkSync stack")
class TestAgainstZkSync(unittest.IsolatedAsyncioTestCase):
    """End to end on the workstation zkSync: the client process for account 192 (outside
    the run range), 100 events at 2/s over the socket, then the end marker."""

    async def test_100_payments_all_executed(self):
        import subprocess
        import sys
        from web3 import Web3
        from measure import keys, zksync
        payer = keys.account(192)
        l1 = Web3(Web3.HTTPProvider(os.environ["ZK_L1"]))
        await asyncio.to_thread(zksync.fund_on_l1, l1, zksync.funder(), payer.address, 2 * 10**18)
        d = tempfile.mkdtemp()
        out, sock = os.path.join(d, "out"), os.path.join(d, "p.sock")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "measure.payclient", "--system", "zksync", "--l1", os.environ["ZK_L1"],
            "--l2", os.environ["ZK_L2"], "--account", "192", "--pay-to", keys.account(193).address,
            "--deposit-wei", str(5 * 10**17), "--max-in-flight", "20", "--receipt-timeout", "1200",
            "--drain", "120", "--socket", sock, "--out", out)
        try:
            t0 = time.monotonic()
            while not os.path.exists(os.path.join(out, "ready")):
                self.assertIsNone(proc.returncode, "client exited before ready")
                self.assertLess(time.monotonic() - t0, 600, "no ready within 600 s")
                await asyncio.sleep(0.1)
            _, w = await asyncio.open_unix_connection(sock)
            run = f"e2e{int(time.time())}"
            for k in range(100):
                now = time.time()
                w.write(PaymentEvent(f"uc1/{run}/c1/w{k:03d}", keys.account(193).address, 1000 + k,
                                     now, now).to_line())
                await w.drain()
                await asyncio.sleep(0.5)
            w.write(b'{"end":true}\n')
            await w.drain()
            w.close()
            code = await asyncio.wait_for(proc.wait(), 600)
        finally:
            if proc.returncode is None:
                proc.kill()
        with open(os.path.join(out, "lifecycle.json")) as f:
            life = json.load(f)
        print("LIFECYCLE", json.dumps({k: v for k, v in life.items() if k != "open"}))
        self.assertEqual(code, 0, life)
        self.assertTrue(life["complete"])
        self.assertEqual(life["reconciliation"]["counts"]["succeeded"], 100, life["reconciliation"])
        self.assertEqual(life["executed_onchain"], 100)
        self.assertEqual(life["pending_before_close"], 0)
