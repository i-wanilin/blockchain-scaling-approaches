# measure/tests/test_perun.py
"""The Perun adapter keeps the ledger semantics: one outcome per payment,
drain and late replies, sidecar death -> unresolved, never a hang.

The sidecar is a small Python script speaking the Go sidecar's protocol
(fixtures/fake_perun_sidecar.py); pay_loop, accept, EventQueue and
PaymentLedger are the real, unchanged ones."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from measure import payclient, perun
from measure.events import PaymentEvent
from measure.ledger import PaymentLedger

FAKE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_perun_sidecar.py")
AMOUNT = 10**12


def ev(w):
    return PaymentEvent(f"uc1/r/c1/w{w:03d}", "0x" + "33" * 20, AMOUNT, time.time(), time.time())


def trace_lines(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.trace = os.path.join(self.dir, "trace.jsonl")
        self.sidecars, self.ledgers = [], []

    async def asyncTearDown(self):
        for s in self.sidecars:
            await asyncio.wait_for(s.stop(), 20)
        for led in self.ledgers:
            led.close()

    async def sidecar(self, *args):
        s = perun.PerunSidecar([sys.executable, FAKE, "--trace", self.trace, *args],
                               os.path.join(self.dir, "sidecar.log"))
        self.sidecars.append(s)
        await s.start()
        return s

    async def run_loop(self, sub, n, drain, max_queue=1000):
        led = PaymentLedger(os.path.join(self.dir, "ledger.log.jsonl"), max_queue=max_queue)
        self.ledgers.append(led)
        q = payclient.EventQueue()
        for w in range(n):
            payclient.accept(led, q, ev(w))
        q.put_nowait(None)
        await asyncio.wait_for(payclient.pay_loop(led, sub, perun.make_tx, q, drain), drain + 20)
        return led

    def outcomes(self, led):
        return {r["id"]: r for r in led.rows()}


class TestPerunSubmitter(Base):
    async def test_all_paid_in_order_with_go_timestamps(self):
        s = await self.sidecar("--pay-delay", "0.005")
        led = await self.run_loop(perun.PerunSubmitter(s, max_in_flight=64), 200, drain=30)
        rows = sorted(led.rows(), key=lambda r: r["id"])
        self.assertEqual([r["outcome"] for r in rows], ["succeeded"] * 200)
        self.assertEqual([r["block"] for r in rows], list(range(1, 201)))   # version, in request order
        replies = {t["reply"]["id"]: t["reply"] for t in trace_lines(self.trace)
                   if t.get("reply", {}).get("op") == "pay"}
        for r in rows:
            self.assertEqual(r["t_confirmed"], replies[r["id"]]["t_confirmed"])   # Go's clock, not Python's
            self.assertEqual(r["t_submitted"], replies[r["id"]]["t_submitted"])
            self.assertEqual((r["tx_hash"], r["gas_used"], r["effective_gas_price"], r["attempts"]),
                             (None, 0, 0, 1))
        self.assertEqual(led.reconcile(0)["succeeded_wei"], 200 * AMOUNT)

    async def test_rejection_is_failed_timeout_is_unresolved_then_broken(self):
        s = await self.sidecar("--reject", "uc1/r/c1/w003", "--timeout-id", "uc1/r/c1/w005")
        rows = self.outcomes(await self.run_loop(perun.PerunSubmitter(s, max_in_flight=64), 10, drain=10))
        out = {k[-3:]: (r["outcome"], r["error"]) for k, r in rows.items()}
        self.assertEqual(out["003"], ("failed", "rejected: test"))
        self.assertEqual(out["005"], ("unresolved", "unresolved: update timeout"))
        for w in ("006", "007", "008", "009"):
            self.assertEqual(out[w][0], "failed")
            self.assertTrue(out[w][1].startswith("channel broken:"), out[w])
            self.assertIsNone(rows[f"uc1/r/c1/w{w}"]["t_confirmed"])
        for w in ("000", "001", "002", "004"):
            self.assertEqual(out[w][0], "succeeded")
        st = await s.request({"op": "state"}, timeout=5)
        self.assertTrue(st["broken"])

    def test_error_classification_is_by_prefix(self):
        f = perun.result_from_reply
        self.assertEqual(f({"ok": False, "error": "rejected: bad request: x"}).error, "rejected: bad request: x")
        self.assertEqual(f({"ok": False, "error": "channel empty"}).status, 0)
        self.assertTrue(f({"ok": False, "error": "unresolved: something new"}).error.startswith("unresolved"))
        # An error we cannot place: the sensor may hold the state, so never "failed".
        self.assertTrue(f({"ok": False, "error": "weird"}).error.startswith("unresolved"))
        self.assertTrue(f({"ok": False}).error.startswith("unresolved"))
        ok = f({"ok": True, "t_submitted": 1.5, "t_confirmed": 1.75, "version": 7, "error": ""})
        self.assertEqual((ok.status, ok.block, ok.t_submitted, ok.t_confirmed), (1, 7, 1.5, 1.75))

    async def test_in_flight_bound_blocks_submit(self):
        s = await self.sidecar("--pay-delay", "0.2")
        sub = perun.PerunSubmitter(s, max_in_flight=3)
        futs = [await sub.submit({"id": f"p{k}", "amount_wei": AMOUNT}) for k in range(3)]
        fourth = asyncio.create_task(sub.submit({"id": "p3", "amount_wei": AMOUNT}))
        await asyncio.sleep(0.1)
        self.assertFalse(fourth.done())                     # 3 outstanding: blocked
        await asyncio.wait_for(futs[0], 5)
        f4 = await asyncio.wait_for(fourth, 5)
        res = await asyncio.wait_for(asyncio.gather(*futs[1:], f4), 5)
        self.assertEqual([r.status for r in res], [1, 1, 1])
        await sub.close()

    async def test_drain_deadline_then_quiesce_late_reply_does_not_change_outcome(self):
        s = await self.sidecar("--pay-delay", "0.1")
        led = await self.run_loop(perun.PerunSubmitter(s, max_in_flight=64), 20, drain=0.25)
        first = {k: r["outcome"] for k, r in self.outcomes(led).items()}
        self.assertIn("unresolved", first.values())
        self.assertIn("succeeded", first.values())
        q = await s.request({"op": "quiesce"}, timeout=5)
        t_quiesced = time.time()
        self.assertTrue(q["ok"])
        self.assertGreater(q["dropped"], 0)
        await asyncio.sleep(0.3)                            # late replies (in flight + dropped) arrive
        self.assertEqual(s.outstanding, 0)
        self.assertEqual({k: r["outcome"] for k, r in self.outcomes(led).items()}, first)
        finals = {}
        for line in trace_lines(led._log.name):
            if line.get("outcome") in ("succeeded", "failed", "unresolved"):
                finals[line["id"]] = finals.get(line["id"], 0) + 1
        self.assertEqual(set(finals.values()), {1})         # one outcome per row, ever
        starts = [t["t"] for t in trace_lines(self.trace) if "start" in t]
        self.assertLessEqual(max(starts), t_quiesced)       # nothing started after quiesce
        self.assertEqual(len(starts) + q["dropped"], 20)

    async def test_sidecar_death_resolves_everything_unresolved(self):
        s = await self.sidecar("--pay-delay", "0.002", "--die-after", "50")
        led = await self.run_loop(perun.PerunSubmitter(s, max_in_flight=64), 200, drain=10)
        rows = sorted(led.rows(), key=lambda r: r["id"])
        self.assertEqual([r["outcome"] for r in rows[:50]], ["succeeded"] * 50)
        for r in rows[50:]:
            self.assertIn(r["outcome"], ("unresolved", "failed"))
            if r["outcome"] == "unresolved":
                self.assertEqual(r["error"], "unresolved: sidecar exited (rc 3)")
            else:                                           # submitted after the exit was known: never sent
                self.assertTrue(r["error"].startswith("not sent: sidecar exited"), r)
                self.assertIsNone(r["t_submitted"])
        self.assertTrue(any(r["outcome"] == "unresolved" for r in rows[50:]))
        self.assertTrue(s.exited)
        self.assertEqual(s.returncode, 3)
        rep = await s.request({"op": "close"}, timeout=5)   # requests after death answer at once
        self.assertFalse(rep["ok"])
        self.assertIn("sidecar exited (rc 3)", rep["error"])

    async def test_stdout_noise_is_an_error_not_a_hang(self):
        s = await self.sidecar("--pay-delay", "0.002", "--noise-after", "10")
        with self.assertLogs("perun", level="ERROR") as logs:
            led = await self.run_loop(perun.PerunSubmitter(s, max_in_flight=64), 50, drain=10)
        self.assertTrue(any("rootSeed" in m for m in logs.output))
        rows = sorted(led.rows(), key=lambda r: r["id"])
        self.assertEqual([r["outcome"] for r in rows[:10]], ["succeeded"] * 10)
        self.assertNotIn("succeeded", {r["outcome"] for r in rows[10:]})
        self.assertTrue(any(r["error"].startswith("unresolved: sidecar protocol broken") for r in rows[10:]))
        self.assertTrue(s.exited)

    async def test_request_timeout_kills_the_sidecar_and_raises(self):
        s = await self.sidecar("--hang-op", "close")
        self.assertTrue((await s.request({"op": "open", "deposit_wei": "1"}, timeout=5))["ok"])
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            await s.request({"op": "close"}, timeout=0.3)
        self.assertLess(time.monotonic() - t0, 3)
        await asyncio.wait_for(s.wait_exited(), 5)
        self.assertTrue(s.exited)

    async def test_lifecycle_requests_in_order(self):
        s = await self.sidecar()
        self.assertTrue((await s.request({"op": "init", "key": "x"}, timeout=5))["ok"])
        o = await s.request({"op": "open", "deposit_wei": str(10**18)}, timeout=5)
        self.assertEqual(o["txs"], 1)
        futs = [await s.pay(f"p{k}", AMOUNT) for k in range(5)]
        cl = await s.request({"op": "close"}, timeout=5)    # through the FIFO: after the five pays
        self.assertEqual([(await f)["version"] for f in futs], [1, 2, 3, 4, 5])
        self.assertEqual(cl["final_version"], 6)
        await s.stop()
        self.assertTrue(s.exited)
        self.assertEqual(s.returncode, 0)

    def test_python_and_go_clocks_agree(self):
        """Review Focus 5: t_intended is Python's time.time(), t_submitted and
        t_confirmed are Go's time.Now(), on the same board."""
        binary = os.environ.get("PERUN_BIN")
        if not binary:
            self.skipTest("PERUN_BIN not set")
        p = subprocess.Popen([binary, "-mode", "uc1-customer", "-clock-check"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        try:
            p.stdin.write(b"warm\n")
            p.stdout.readline()
            worst = 0.0
            for _ in range(200):
                before = time.time()
                p.stdin.write(b"x\n")
                now = json.loads(p.stdout.readline())["now"]
                after = time.time()
                worst = max(worst, before - now, now - after)   # > 0: Go's clock outside the bracket
            self.assertLess(worst, 0.001, f"Go's clock is {worst * 1e3:.3f} ms outside Python's bracket")
        finally:
            p.stdin.close()
            p.wait(10)


class TestPerunLifecycle(unittest.IsolatedAsyncioTestCase):
    """perun.main_async (payclient --system perun) against the fake sidecar:
    Open, pays, drain, quiesce, state, Close, and the finally guarantees."""

    def args(self, **kw):
        import argparse
        d = tempfile.mkdtemp()
        a = argparse.Namespace(system="perun", chain="ws://127.0.0.1:8545", chain_id=31337,
                               sensor_control="http://127.0.0.1:8090", sidecar="/nonexistent/perun-node",
                               wire_bind="127.0.0.1:9101", wire_endpoint="127.0.0.1:9101", account=101,
                               pay_to="0x" + "33" * 20, deposit_wei=10**18, max_in_flight=8, max_queue=1000,
                               drain=3, update_timeout=1, persist=1, socket=os.path.join(d, "p.sock"),
                               out=os.path.join(d, "out"))
        for k, v in kw.items():
            setattr(a, k, v)
        self.trace = os.path.join(d, "trace.jsonl")
        os.makedirs(os.path.join(a.out, "state"))          # stands in for the sidecar's LevelDB
        open(os.path.join(a.out, "state", "CURRENT"), "w").close()
        return a

    async def run_main(self, a, fake=(), n=20, balance=2 * 10**18, sigterm=False, timeouts=None):
        from unittest import mock
        argv = [sys.executable, FAKE, "--trace", self.trace, "--pay-delay", "0.002", *fake]
        patches = [mock.patch.object(perun, "sidecar_argv", lambda _a: argv),
                   mock.patch.object(perun, "l1_balance", lambda url, addr: balance)]
        for k, v in (timeouts or {}).items():
            patches.append(mock.patch.object(perun, k, v))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        async def client():
            while not os.path.exists(os.path.join(a.out, "ready")):
                await asyncio.sleep(0.02)
            _, w = await asyncio.open_unix_connection(a.socket)
            w.write(b"".join(ev(k).to_line() for k in range(n)))
            if not sigterm:
                w.write(b'{"end":true}\n')
            await w.drain()
            w.close()
        c = asyncio.create_task(client())
        try:
            if sigterm:
                import signal
                t = asyncio.create_task(payclient.run(a))
                while not os.path.exists(os.path.join(a.out, "ready")):
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.3)
                os.kill(os.getpid(), signal.SIGTERM)
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(t, 30)
                code = None
            else:
                code = await asyncio.wait_for(payclient.run(a), 30)
        finally:
            c.cancel()
            await asyncio.gather(c, return_exceptions=True)
        with open(os.path.join(a.out, "lifecycle.json")) as f:
            return code, json.load(f)

    def csv_rows(self, a):
        import csv
        with open(os.path.join(a.out, "ledger.csv")) as f:
            return list(csv.DictReader(f))

    def reply_ops(self):
        return [t["reply"]["op"] for t in trace_lines(self.trace) if "reply" in t and t["reply"]["op"] != "pay"]

    async def test_perun_success_is_complete(self):
        a = self.args()
        code, life = await self.run_main(a)
        self.assertEqual(code, 0)
        self.assertTrue(life["complete"])
        self.assertEqual((life["system"], life["account"], life["max_in_flight"], life["max_queue"],
                          life["drain_s"], life["duplicates"], life["late"]), ("perun", 101, 8, 1000, 3, 0, 0))
        self.assertEqual(life["open"]["txs"], 1)
        self.assertEqual(life["open"]["channel_id"], "0x" + "22" * 32)
        self.assertEqual(life["quiesce"], {"dropped": 0, "version": 20})
        self.assertEqual(life["channel_before_close"],
                         {"version": 20, "balances_wei": [str(10**18 - 20 * AMOUNT), str(20 * AMOUNT)],
                          "broken": False})
        self.assertEqual(life["reconciliation"]["difference_wei"], 0)
        self.assertEqual(life["reconciliation"]["observed_source"], "channel")
        self.assertTrue(life["close"]["ok"])
        self.assertEqual(life["close"]["final_version"], 21)
        self.assertNotIn("open_error", life)
        self.assertNotIn("close_error", life)
        self.assertIn("ready_at", life)
        self.assertIn("ended_at", life)
        rows = self.csv_rows(a)
        self.assertEqual(sorted(int(r["block"]) for r in rows), list(range(1, 21)))
        self.assertEqual({r["outcome"] for r in rows}, {"succeeded"})
        self.assertTrue(os.path.exists(os.path.join(a.out, "sidecar.log")))
        self.assertFalse(os.path.exists(os.path.join(a.out, "state")))   # removed after a complete run
        # quiesce right after the drain, state only after it, then close and exit
        self.assertEqual(self.reply_ops(), ["init", "open", "quiesce", "state", "close", "exit"])

    async def test_perun_init_carries_the_run_settings(self):
        a = self.args()
        from unittest import mock
        seen = {}
        orig = perun.PerunSidecar.request

        async def spy(self_, obj, timeout):
            seen.setdefault(obj["op"], (obj, timeout))
            return await orig(self_, obj, timeout)
        waited = {}
        orig_wait = perun.PerunSidecar.wait_reply

        async def wait_spy(self_, op, fut, timeout):           # quiesce is sent by the deadline hook
            waited.setdefault(op, timeout)
            return await orig_wait(self_, op, fut, timeout)
        with mock.patch.object(perun.PerunSidecar, "request", spy), \
                mock.patch.object(perun.PerunSidecar, "wait_reply", wait_spy):
            code, life = await self.run_main(a, n=2)
        self.assertEqual(code, 0)
        init, t_init = seen["init"]
        self.assertEqual((init["chain"], init["chain_id"], init["sensor_control"], init["bind"],
                          init["endpoint"], init["update_timeout_s"], init["persist"],
                          init["state_dir"], init["startup_timeout_s"]),
                         (a.chain, 31337, a.sensor_control, a.wire_bind, a.wire_endpoint, 1, True,
                          os.path.join(a.out, "state"), 120))
        from measure import keys
        self.assertEqual(init["key"].removeprefix("0x"), keys.account(101).key.hex().removeprefix("0x"))
        self.assertEqual(t_init, 130)
        self.assertEqual(seen["open"][0]["deposit_wei"], str(10**18))
        self.assertEqual(seen["open"][1], 300)
        self.assertEqual(waited["quiesce"], 11)                     # update timeout + 10
        self.assertGreater(seen["close"][1], 300)                   # above Go's own 300 s close timeout

    async def test_perun_failed_open_is_recorded_and_no_socket(self):
        a = self.args()
        code, life = await self.run_main(a, balance=10**18)     # not more than the deposit
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIn("not funded", life["open_error"])
        self.assertNotIn("open", life)
        self.assertFalse(os.path.exists(os.path.join(a.out, "ready")))
        self.assertFalse(os.path.exists(a.socket))
        self.assertTrue(os.path.exists(os.path.join(a.out, "ledger.csv")))
        self.assertTrue(os.path.exists(os.path.join(a.out, "state")))       # kept for diagnosis

    async def test_perun_open_timeout_is_an_open_error(self):
        a = self.args()
        code, life = await self.run_main(a, fake=("--hang-op", "open"), timeouts={"OPEN_TIMEOUT_S": 0.5})
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIn("TimeoutError", life["open_error"])
        self.assertFalse(os.path.exists(os.path.join(a.out, "ready")))

    async def test_perun_failed_init_is_an_open_error(self):
        a = self.args()
        code, life = await self.run_main(a, fake=("--fail-op", "init"))
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIn("init", life["open_error"])
        self.assertIn("test failure", life["open_error"])
        self.assertFalse(os.path.exists(os.path.join(a.out, "ready")))

    async def test_perun_broken_channel_closes_but_is_not_complete(self):
        a = self.args()
        code, life = await self.run_main(a, fake=("--timeout-id", "uc1/r/c1/w005"))
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertTrue(life["channel_before_close"]["broken"])
        self.assertIn("close", self.reply_ops())                  # Close is still attempted
        # The Go sidecar (and the fake) refuse to close a broken channel (dispute path out of scope).
        self.assertIn("channel broken", life["close_error"])
        self.assertTrue(os.path.exists(os.path.join(a.out, "state")))       # kept for diagnosis
        out = {r["id"][-3:]: r["outcome"] for r in self.csv_rows(a)}
        self.assertEqual(out["005"], "unresolved")

    async def test_perun_broken_channel_is_not_complete_even_if_close_succeeds(self):
        a = self.args()
        code, life = await self.run_main(a, fake=("--timeout-id", "uc1/r/c1/w005", "--close-broken"))
        self.assertTrue(life["close"]["ok"])
        self.assertNotIn("close_error", life)
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIn("broken", life["error"])

    async def test_perun_close_error_is_not_complete(self):
        a = self.args()
        code, life = await self.run_main(a, fake=("--hang-op", "close"), timeouts={"CLOSE_TIMEOUT_S": 0.5})
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIn("TimeoutError", life["close_error"])
        self.assertNotIn("close", life)
        self.assertEqual(life["reconciliation"]["difference_wei"], 0)
        self.assertTrue(os.path.exists(os.path.join(a.out, "ledger.csv")))
        self.assertTrue(os.path.exists(os.path.join(a.out, "state")))

    async def test_perun_failed_state_is_a_missing_fact(self):
        a = self.args()
        code, life = await self.run_main(a, fake=("--fail-op", "state"))
        self.assertNotEqual(code, 0)
        self.assertFalse(life["complete"])
        self.assertIn("test failure", life["state_error"])
        self.assertNotIn("reconciliation", life)
        self.assertTrue(life["close"]["ok"])                       # the funds still come back

    async def test_perun_sigterm_still_writes_lifecycle_and_csv(self):
        a = self.args()
        code, life = await self.run_main(a, sigterm=True, fake=("--pay-delay", "0.05"))
        self.assertFalse(life["complete"])
        self.assertIn("CancelledError", life["error"])
        rows = self.csv_rows(a)
        self.assertEqual(len(rows), 20)
        self.assertTrue(os.path.exists(os.path.join(a.out, "state")))
        ops = self.reply_ops()
        self.assertEqual(ops[-1], "exit")                         # the sidecar was stopped, not orphaned
        self.assertNotIn("close", ops)

    async def test_perun_worker_crash_is_never_complete(self):
        a = self.args()
        from unittest import mock

        orig = PaymentLedger.submitted

        def bad_submitted(self_, id_, t):
            if id_.endswith("w003"):
                raise OSError("worker crash: disk full")    # ledger I/O outside the submit try
            return orig(self_, id_, t)
        with mock.patch.object(PaymentLedger, "submitted", bad_submitted):
            with self.assertRaises(OSError):
                await self.run_main(a)
        with open(os.path.join(a.out, "lifecycle.json")) as f:
            life = json.load(f)
        self.assertFalse(life["complete"])
        self.assertIn("worker crash", life["error"])
        self.assertTrue(os.path.exists(os.path.join(a.out, "ledger.csv")))

    async def test_perun_quiesce_goes_out_at_the_deadline_before_unresolve(self):
        """I1: quiesce reaches the sidecar at the drain deadline, before Python
        marks the pending rows unresolved (on a Pi that takes seconds at w10),
        so the sidecar does not keep starting its queued pays meanwhile."""
        a = self.args(drain=0.5, max_in_flight=16)
        from unittest import mock
        order, t_unresolve = [], []
        orig_unresolve = PaymentLedger.unresolve_pending
        orig_send = perun.PerunSidecar.send_request

        def slow_unresolve(self_):
            order.append("unresolve")
            t_unresolve.append(time.time())
            time.sleep(0.5)                                 # ~8 000 line-buffered writes on a Pi 3
            return orig_unresolve(self_)

        def send(self_, obj):
            order.append(obj["op"])
            return orig_send(self_, obj)
        with mock.patch.object(PaymentLedger, "unresolve_pending", slow_unresolve), \
                mock.patch.object(perun.PerunSidecar, "send_request", send):
            code, life = await self.run_main(a, n=200, fake=("--pay-delay", "0.05"))
        self.assertEqual(code, 0)
        self.assertTrue(life["complete"])
        self.assertLess(order.index("quiesce"), order.index("unresolve"))
        self.assertGreater(life["quiesce"]["dropped"], 0)
        starts = [t["t"] for t in trace_lines(self.trace) if "start" in t]
        self.assertLessEqual(sum(t > t_unresolve[0] + 0.05 for t in starts), 0)   # none ran during the unresolve
        self.assertIn("quiesce_sent_at", life)
        rows = self.csv_rows(a)
        self.assertEqual(len(rows), 200)
        self.assertLessEqual(life["reconciliation"]["difference_wei"], AMOUNT)   # at most the update in flight

    async def test_perun_reconciliation_uses_the_channel_state(self):
        """A timed-out update the sensor did sign: the ledger says unresolved, the
        channel says paid. observed_wei comes from the channel, so the difference shows it."""
        a = self.args()
        code, life = await self.run_main(a, fake=("--timeout-id", "uc1/r/c1/w005", "--timeout-signed"))
        rec = life["reconciliation"]
        self.assertEqual(rec["observed_source"], "channel")
        self.assertEqual(rec["succeeded_wei"], 5 * AMOUNT)         # w000..w004
        self.assertEqual(rec["observed_wei"], 6 * AMOUNT)          # deposit - own balance on the channel
        self.assertEqual(rec["difference_wei"], AMOUNT)
        self.assertEqual(rec["counts"]["unresolved"], 1)
        self.assertFalse(life["complete"])


class TestPayclientArguments(unittest.IsolatedAsyncioTestCase):
    ARB = ["--system", "arbitrum", "--l1", "http://192.168.178.210:8545", "--l2", "ws://192.168.178.210:8548",
           "--account", "101", "--pay-to", "0xabc", "--deposit-wei", "1000000000000000000", "--out", "/tmp/x"]
    ZK = ["--system", "zksync", "--l1", "http://192.168.178.210:8545", "--l2", "ws://192.168.178.210:3051",
          "--account", "102", "--pay-to", "0xabc", "--deposit-wei", "5", "--max-in-flight", "20",
          "--receipt-timeout", "1200", "--close-timeout", "600", "--out", "/tmp/y"]
    PERUN = ["--system", "perun", "--chain", "ws://192.168.178.210:8545", "--chain-id", "31337",
             "--sensor-control", "http://192.168.178.211:8090", "--sidecar", "/home/x/perun-node",
             "--wire-bind", "0.0.0.0:9000", "--wire-endpoint", "192.168.178.212:9000", "--account", "101",
             "--pay-to", "0xabc", "--deposit-wei", "1000000000000000000", "--max-in-flight", "64",
             "--max-queue", "10000", "--drain", "60", "--update-timeout", "10", "--persist", "1",
             "--socket", "/tmp/measure-pay.sock", "--out", "/tmp/z"]

    async def test_arbitrum_arguments_unchanged(self):
        from unittest import mock
        a = payclient.parse_args(self.ARB)
        self.assertEqual((a.system, a.l1, a.l2, a.account, a.pay_to, a.deposit_wei, a.max_in_flight, a.max_queue,
                          a.drain, a.receipt_timeout, a.close_timeout, a.socket, a.out),
                         ("arbitrum", "http://192.168.178.210:8545", "ws://192.168.178.210:8548", 101, "0xabc",
                          10**18, 64, 10000, 60, 120, 300, "/tmp/measure-pay.sock", "/tmp/x"))
        z = payclient.parse_args(self.ZK)
        self.assertEqual((z.system, z.l1, z.l2, z.account, z.deposit_wei, z.max_in_flight, z.receipt_timeout,
                          z.close_timeout, z.out, z.max_queue, z.drain, z.socket),
                         ("zksync", "http://192.168.178.210:8545", "ws://192.168.178.210:3051", 102, 5, 20,
                          1200, 600, "/tmp/y", 10000, 60, "/tmp/measure-pay.sock"))
        for argv in (self.ARB, self.ZK):                          # --l1/--l2 still required for the rollups
            for flag in ("--l1", "--l2"):
                i = argv.index(flag)
                with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                    payclient.parse_args(argv[:i] + argv[i + 2:])
        # dispatch: the rollups go to payclient.main_async, perun to perun.main_async
        for argv, rollup in ((self.ARB, True), (self.ZK, True), (self.PERUN, False)):
            with mock.patch.object(payclient, "main_async", mock.AsyncMock(return_value=0)) as m_roll, \
                    mock.patch.object(perun, "main_async", mock.AsyncMock(return_value=0)) as m_perun:
                self.assertEqual(await payclient.run(payclient.parse_args(argv)), 0)
            self.assertEqual((m_roll.called, m_perun.called), (rollup, not rollup))

    def test_perun_arguments(self):
        from unittest import mock
        a = payclient.parse_args(self.PERUN)
        self.assertEqual((a.system, a.chain, a.chain_id, a.sensor_control, a.sidecar, a.wire_bind,
                          a.wire_endpoint, a.account, a.deposit_wei, a.max_in_flight, a.max_queue, a.drain,
                          a.update_timeout, a.persist, a.socket, a.out, a.l1, a.l2),
                         ("perun", "ws://192.168.178.210:8545", 31337, "http://192.168.178.211:8090",
                          "/home/x/perun-node", "0.0.0.0:9000", "192.168.178.212:9000", 101, 10**18, 64, 10000,
                          60, 10, 1, "/tmp/measure-pay.sock", "/tmp/z", None, None))
        i = self.PERUN.index("--sensor-control")
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            payclient.parse_args(self.PERUN[:i] + self.PERUN[i + 2:])


class TestPerunEndToEnd(unittest.IsolatedAsyncioTestCase):
    """One customer against a real Anvil and a real uc1-sensor: set PERUN_ANVIL
    (e.g. http://127.0.0.1:8545, a fresh `anvil --chain-id 31337 --block-time 1`)
    and PERUN_BIN (perun-node built from perun/hub-demo)."""

    async def test_200_payments_open_close_against_anvil(self):
        url, binary = os.environ.get("PERUN_ANVIL"), os.environ.get("PERUN_BIN")
        if not url or not binary:
            self.skipTest("PERUN_ANVIL or PERUN_BIN not set")
        import argparse
        from web3 import Web3
        from measure import arbitrum, keys
        d = tempfile.mkdtemp()
        ws = url.replace("http://", "ws://", 1)
        subprocess.run([binary, "-mode", "uc1-deploy", "-url", ws, "-chainid", "31337",
                        "-out", os.path.join(d, "contracts.json")], check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=open(os.path.join(d, "deploy.log"), "w"))
        l1 = Web3(Web3.HTTPProvider(url))
        for i in (100, 101):
            arbitrum.fund_on_l1(l1, keys.account(0), keys.account(i).address, 2 * 10**18)
        key = os.path.join(d, "sensor.key")
        with open(os.open(key, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
            f.write(keys.account(100).key.hex())
        sensor = subprocess.Popen(
            [binary, "-mode", "uc1-sensor", "-url", ws, "-chainid", "31337",
             "-contracts", os.path.join(d, "contracts.json"), "-key-file", key, "-bind", "127.0.0.1:9180",
             "-endpoint", "127.0.0.1:9180", "-control", "127.0.0.1:8190", "-customers", "1",
             "-allow", keys.account(101).address, "-deposit-wei", str(10**18),
             "-state", os.path.join(d, "sensor-state"), "-out", os.path.join(d, "sensor")],
            stdout=subprocess.DEVNULL, stderr=open(os.path.join(d, "sensor.log"), "w"))
        try:
            a = argparse.Namespace(system="perun", chain=ws, chain_id=31337,
                                   sensor_control="http://127.0.0.1:8190", sidecar=binary,
                                   wire_bind="127.0.0.1:9181", wire_endpoint="127.0.0.1:9181", account=101,
                                   pay_to=keys.account(100).address, deposit_wei=10**18, max_in_flight=64,
                                   max_queue=10000, drain=60, update_timeout=10, persist=1,
                                   socket=os.path.join(d, "p.sock"), out=os.path.join(d, "c1"))

            async def client():
                while not os.path.exists(os.path.join(a.out, "ready")):
                    await asyncio.sleep(0.05)
                _, w = await asyncio.open_unix_connection(a.socket)
                for k in range(200):
                    w.write(ev(k).to_line())
                    await w.drain()
                    await asyncio.sleep(0.01)
                w.write(b'{"end":true}\n')
                await w.drain()
            c = asyncio.create_task(client())
            code = await asyncio.wait_for(perun.main_async(a), 600)
            c.cancel()
            with open(os.path.join(a.out, "lifecycle.json")) as f:
                life = json.load(f)
            self.assertEqual(code, 0, life)
            self.assertTrue(life["complete"])
            self.assertEqual(life["open"]["txs"], 1)
            self.assertEqual(life["reconciliation"]["difference_wei"], 0)
            self.assertEqual(life["reconciliation"]["counts"]["succeeded"], 200)
            self.assertEqual(sensor.wait(120), 0)
            with open(os.path.join(d, "sensor", "sensor-result.json")) as f:
                res = json.load(f)
            self.assertTrue(res.get("complete"), res)
        finally:
            if sensor.poll() is None:
                sensor.kill()
                sensor.wait(10)


if __name__ == "__main__":
    unittest.main()
