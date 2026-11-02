# measure/tests/test_perun_uc2.py
"""The Perun UC2 lifecycle (measure/perun_uc2.py) against fixtures/fake_uc2_sidecar.py;
pay_loop, accept, EventQueue and PaymentLedger are the real ones."""
import argparse
import asyncio
import csv
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from measure import payclient, perun, perun_uc2
from measure.events import PaymentEvent

FAKE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_uc2_sidecar.py")


def ev(w, to):
    return PaymentEvent(f"uc2/r/p2/w{w:03d}", to, 5, time.time(), time.time())


def trace_lines(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


class PerunUC2Test(unittest.IsolatedAsyncioTestCase):
    def test_arena_maps_to_hub_and_players_pass_through(self):
        self.assertEqual(perun_uc2.make_tx(ev(1, "arena")), {"id": "uc2/r/p2/w001", "to": "hub", "amount_wei": 5})
        self.assertEqual(perun_uc2.make_tx(ev(2, "p4"))["to"], "p4")
        self.assertEqual(perun.make_tx(ev(3, "p4")), {"id": "uc2/r/p2/w003", "amount_wei": 5})   # UC1 shape

    def args(self, **kw):
        d = tempfile.mkdtemp()
        a = argparse.Namespace(system="perun", use_case="uc2", perun_config="/x/cluster.json",
                               sidecar="/nonexistent/perun-node", account=113, deposit_wei=0, max_in_flight=8,
                               max_queue=1000, drain=3, update_timeout=1, persist=1,
                               socket=os.path.join(d, "p.sock"), out=os.path.join(d, "out"))
        for k, v in kw.items():
            setattr(a, k, v)
        self.trace = os.path.join(d, "trace.jsonl")
        os.makedirs(os.path.join(a.out, "state"))
        return a

    async def run_main(self, a, events, fake=()):
        argv = [sys.executable, FAKE, "--trace", self.trace, *fake]
        p = mock.patch.object(perun_uc2, "sidecar_argv", lambda _a: argv)
        p.start()
        self.addCleanup(p.stop)

        async def client():
            while not os.path.exists(os.path.join(a.out, "ready")):
                await asyncio.sleep(0.02)
            _, w = await asyncio.open_unix_connection(a.socket)
            w.write(b"".join(e.to_line() for e in events) + b'{"end":true}\n')
            await w.drain()
            w.close()
        c = asyncio.create_task(client())
        try:
            code = await asyncio.wait_for(payclient.run(a), 30)
        finally:
            c.cancel()
            await asyncio.gather(c, return_exceptions=True)
        with open(os.path.join(a.out, "lifecycle.json")) as f:
            return code, json.load(f)

    def csv_rows(self, a):
        with open(os.path.join(a.out, "ledger.csv")) as f:
            return list(csv.DictReader(f))

    async def test_lifecycle_complete_with_fake_sidecar(self):
        a = self.args()
        events = [ev(0, "arena"), ev(1, "p0"), ev(2, "arena"), ev(3, "p4"), ev(4, "p0")]
        code, life = await self.run_main(a, events)
        self.assertEqual(code, 0)
        self.assertTrue(life["complete"])
        self.assertEqual((life["use_case"], life["player"], life["init"]["index"]), ("uc2", "p2", 2))
        self.assertEqual(life["open"]["txs"], 2)
        self.assertEqual(sorted(life["channels_before_close"]), ["hub", "p0", "p1", "p3", "p4", "p5"])
        self.assertEqual(life["close"]["ok"], True)
        self.assertEqual([r["outcome"] for r in self.csv_rows(a)], ["succeeded"] * 5)
        rec = life["reconciliation"]
        self.assertEqual((rec["hub"]["out_succeeded_wei"], rec["hub"]["free_balance_wei"]), (10, 990))
        self.assertEqual((rec["p0"]["out_succeeded_count"], rec["p0"]["free_balance_wei"]), (2, 990))
        self.assertEqual(rec["p1"]["out_succeeded_wei"], 0)
        self.assertEqual(life["broken_channels"], [])
        ops = [t["op"] for t in trace_lines(self.trace)]
        self.assertEqual([o for o in ops if o != "pay"], ["init", "open", "quiesce", "state", "close", "exit"])
        init = trace_lines(self.trace)[0]
        self.assertEqual((init["name"], init["config"], init["persist"]), ("p2", "/x/cluster.json", True))
        pays = {t["id"]: t["to"] for t in trace_lines(self.trace) if t["op"] == "pay"}
        self.assertEqual(pays["uc2/r/p2/w000"], "hub")
        self.assertEqual(pays["uc2/r/p2/w003"], "p4")
        self.assertFalse(os.path.exists(os.path.join(a.out, "state")))      # removed when complete

    async def test_broken_channel_means_incomplete(self):
        a = self.args()
        events = [ev(0, "p0"), ev(1, "p4"), ev(2, "p4"), ev(3, "arena")]
        code, life = await self.run_main(a, events, ["--timeout-id", "uc2/r/p2/w001"])
        self.assertEqual(code, 1)
        self.assertFalse(life["complete"])
        self.assertEqual(life["broken_channels"], ["p4"])
        self.assertIn("close_error", life)                 # the sidecar refuses close while broken
        out = {r["id"][-3:]: (r["outcome"], r["error"]) for r in self.csv_rows(a)}
        self.assertEqual(out["001"][0], "unresolved")
        self.assertTrue(out["002"][1].startswith("channel broken:"))
        self.assertEqual([out["000"][0], out["003"][0]], ["succeeded"] * 2)
        self.assertTrue(os.path.exists(os.path.join(a.out, "state")))       # kept for diagnosis

    async def test_sidecar_death_resolves_everything_unresolved(self):
        a = self.args()
        events = [ev(w, "p0") for w in range(6)]
        code, life = await self.run_main(a, events, ["--die-after", "2"])
        self.assertEqual(code, 1)
        self.assertFalse(life["complete"])
        rows = self.csv_rows(a)
        self.assertEqual(len(rows), 6)
        self.assertEqual([r["outcome"] for r in rows[:2]], ["succeeded"] * 2)
        for r in rows[2:]:
            self.assertIn(r["outcome"], ("unresolved", "failed"))
            self.assertTrue(r["error"].startswith(("unresolved: sidecar exited (rc 3)", "not sent:")), r)
        self.assertIn("quiesce_error", life)
        self.assertEqual(life["sidecar_rc"], 3)

    async def test_failed_open_is_fatal_and_not_retried(self):
        a = self.args()
        code, life = await self.run_main(a, [], ["--fail-op", "open"])
        self.assertEqual(code, 1)
        self.assertFalse(life["complete"])
        self.assertIn("open: test failure", life["open_error"])
        self.assertEqual([t["op"] for t in trace_lines(self.trace)].count("open"), 1)

    def test_broken_channel_named_even_with_unusable_balances(self):
        lc = perun_uc2.UC2Lifecycle(self.args())
        life = {"channels_before_close": {"hub": {"balances_wei": ["1", "2"], "broken": False},
                                          "p3": {"balances_wei": None, "broken": True}}}
        missing = []
        lc.reconcile(life, mock.Mock(rows=lambda: []), missing)
        self.assertEqual(life["broken_channels"], ["p3"])
        self.assertEqual(missing, ["reconciliation"])
        self.assertIn("unusable", life["state_error"])
        self.assertIn("p3", lc.broken_error(life))

    async def _run_with_state(self, mutate):
        orig = perun_uc2.UC2Lifecycle.record_state

        def rs(lc, life, rep):
            orig(lc, life, mutate(dict(rep)))
        p = mock.patch.object(perun_uc2.UC2Lifecycle, "record_state", rs)
        p.start()
        self.addCleanup(p.stop)
        return await self.run_main(self.args(), [ev(0, "p0")])

    async def test_channels_none_means_incomplete(self):
        code, life = await self._run_with_state(lambda r: dict(r, channels=None))
        self.assertEqual(code, 1)
        self.assertFalse(life["complete"])
        self.assertIn("channels", life["error"])
        self.assertIn("state_error", life)

    async def test_missing_channel_means_incomplete(self):
        code, life = await self._run_with_state(lambda r: dict(r, channels={k: v for k, v in r["channels"].items() if k != "p5"}))
        self.assertEqual(code, 1)
        self.assertFalse(life["complete"])
        self.assertIn("missing: channels", life["error"])
        self.assertIn("p5", life["state_error"])

    def test_uc1_path_unchanged(self):
        argv = ["--system", "perun", "--chain", "ws://h:8545", "--chain-id", "31337", "--sensor-control", "http://h:1",
                "--sidecar", "/x/perun-node", "--wire-bind", "0.0.0.0:9000", "--wire-endpoint", "h:9000",
                "--pay-to", "0x" + "33" * 20, "--account", "101", "--deposit-wei", "5", "--out", "/tmp/z"]
        a = payclient.parse_args(argv)
        self.assertEqual(a.use_case, "uc1")

        async def go(args):
            with mock.patch.object(perun, "main_async", mock.AsyncMock(return_value=0)) as m1, \
                    mock.patch.object(perun_uc2, "main_async", mock.AsyncMock(return_value=0)) as m2:
                await payclient.run(args)
            return m1.called, m2.called
        self.assertEqual(asyncio.run(go(a)), (True, False))
        u2 = payclient.parse_args(["--system", "perun", "--use-case", "uc2", "--perun-config", "/c.json",
                                   "--sidecar", "/x/perun-node", "--account", "113", "--deposit-wei", "0",
                                   "--out", "/tmp/z"])
        self.assertEqual(asyncio.run(go(u2)), (False, True))
