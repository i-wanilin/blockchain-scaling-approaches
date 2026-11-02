"""The submitter keeps nonces in order, pipelines, and survives nonce gaps,
permanent rejections, dropped connections and missing receipts."""
import asyncio
import json
import os
import time
import unittest
import urllib.request
from collections import Counter

import rlp
import websockets
from eth_hash.auto import keccak

from measure import keys, zksync
from measure.submitter import OrderedSubmitter, SubmitResult, _Payment, _Send

TX = {"to": keys.account(100).address, "value": 1, "gas": 21000,
      "maxFeePerGas": 2 * 10**8, "maxPriorityFeePerGas": 0}


class FakeSequencer:
    """Accepts a raw tx only if its nonce is the next one; replies after a delay,
    like Arbitrum answering once a transfer is sequenced. Supports newHeads and
    eth_getBlockReceipts (one tx per block, block = nonce + 1).
    gap_every:      every n-th nonce arrives 'too early' once (nonce too high)
    swallow_reply:  nonces accepted but not answered the first time
    known_msg:      error text for a nonce that is already behind the chain
    reject_nonces:  the first tx at such a nonce is rejected permanently
    drop_after:     close the socket once, at the n-th eth_sendRawTransaction (that tx is lost)
    never_receipt:  no receipts and no heads
    head_first:     send the newHeads notification before the send reply
    no_subscribe:   eth_subscribe returns an error
    hold_s:         a tx whose nonce is ahead is held up to this long, executed if the gap fills
                    in time, else answered "nonce too high" only when the hold expires
    slow_nonces:    {nonce: seconds} extra processing time, first arrival only
    reject_values:  the first tx with such a value is rejected permanently, whatever its nonce
    drop_second:    {nonce: seconds}: close the socket that long after that nonce arrives the 2nd time
                    (its held resend is then lost with the connection, but stays held in the fake)
    subscribe_delay: eth_subscribe on a reconnection answers this late (a wide reconnect window)
    drop_after_exec: close the socket right after executing such a nonce (executed, reply lost)
    receipt_delay:  receipts (and heads) appear only this long after execution
    exec_counts:    executions per tx value (tests give each payment a unique value)
    foreign_per_block: every block also holds this many other accounts' txs (receipts exist for them)
    no_block_by_number: eth_getBlockByNumber answers a method-not-found error
    block_by_number_errors: the first n eth_getBlockByNumber calls answer a transient
                    -32000 "block not found" error (a false "method missing" text match)"""

    def __init__(self, delay=0.05, gap_every=0, swallow_reply=(), known_msg="nonce too low",
                 reject_nonces=(), drop_after=0, never_receipt=False, head_first=False, no_subscribe=False,
                 hold_s=0.0, slow_nonces=None, reject_values=(), drop_second=None, receipt_delay=0.0, subscribe_delay=0.0, drop_after_exec=(),
                 foreign_per_block=0, no_block_by_number=False, block_by_number_errors=0):
        self.delay, self.gap_every, self.swallow_reply = delay, gap_every, set(swallow_reply)
        self.known_msg, self.reject_nonces = known_msg, set(reject_nonces)
        self.drop_after, self.never_receipt = drop_after, never_receipt
        self.head_first, self.no_subscribe = head_first, no_subscribe
        self.hold_s, self.slow_nonces, self.reject_values = hold_s, dict(slow_nonces or {}), set(reject_values)
        self.drop_second, self.receipt_delay = dict(drop_second or {}), receipt_delay
        self.exec_time = {}
        self.drop_after_exec, self._dropped_exec = set(drop_after_exec), set()
        self.subscribe_delay, self._subscribes = subscribe_delay, 0
        self.exec_counts = Counter()
        self.calls = Counter()             # method name -> number of calls seen in _answer
        self.receipt_asks = Counter()      # tx hash -> eth_getTransactionReceipt calls for it
        self.foreign_per_block, self.no_block_by_number = foreign_per_block, no_block_by_number
        self.foreign = set()               # hashes of other accounts' txs
        self.block_by_number_errors = block_by_number_errors
        self._progress = asyncio.Event()
        self.next_nonce = 0
        self.accepted = []
        self.receipts, self.blocks, self.raws = {}, {}, {}
        self.max_concurrent = 0
        self.sent_count = 0
        self._open = 0
        self._seen, self._rejected, self._swallowed = set(), set(), set()
        self._dropped = False
        self._subs = []

    async def handler(self, ws):
        async for raw in ws:
            asyncio.create_task(self._answer(ws, json.loads(raw)))

    async def _send(self, ws, obj):
        try:
            await ws.send(json.dumps(obj))
        except websockets.ConnectionClosed:
            pass

    async def _reply(self, ws, rid, **kw):
        await self._send(ws, {"jsonrpc": "2.0", "id": rid, **kw})

    def _visible(self, h):
        t = self.exec_time.get(h)
        return self.receipts.get(h) if t is not None and time.monotonic() - t >= self.receipt_delay else None

    async def _late_head(self, block):
        await asyncio.sleep(self.receipt_delay)
        await self._head(block)

    async def _drop_later(self, ws, secs):
        await asyncio.sleep(secs)
        await ws.close()

    async def _head(self, block):
        for w in list(self._subs):
            await self._send(w, {"jsonrpc": "2.0", "method": "eth_subscription",
                                 "params": {"subscription": "0x1", "result": {"number": hex(block)}}})

    async def _answer(self, ws, msg):
        m, p, rid = msg["method"], msg.get("params", []), msg["id"]
        self.calls[m] += 1
        if m == "eth_getTransactionCount":
            return await self._reply(ws, rid, result=hex(self.next_nonce))
        if m == "eth_subscribe":
            if self.no_subscribe:
                return await self._reply(ws, rid, error={"code": -32601, "message": "no subscriptions"})
            self._subs.append(ws)
            self._subscribes += 1
            if self._subscribes > 1:                               # slow resubscribe after a reconnect
                await asyncio.sleep(self.subscribe_delay)
            return await self._reply(ws, rid, result="0x1")
        if m == "eth_getTransactionReceipt":
            self.receipt_asks[p[0]] += 1
            return await self._reply(ws, rid, result=None if self.never_receipt else self._visible(p[0]))
        if m == "eth_getBlockReceipts":
            return await self._reply(ws, rid, result=[] if self.never_receipt else
                                    [r for r in self.blocks.get(int(p[0], 16), []) if self._visible(r["transactionHash"])])
        if m == "eth_getBlockByNumber":
            if self.no_block_by_number:
                return await self._reply(ws, rid, error={"code": -32601, "message": "the method does not exist"})
            if self.block_by_number_errors > 0:
                self.block_by_number_errors -= 1
                return await self._reply(ws, rid, error={"code": -32000, "message": "block not found"})
            b = int(p[0], 16)
            if b not in self.blocks:
                return await self._reply(ws, rid, result=None)
            hashes = [] if self.never_receipt else \
                [r["transactionHash"] for r in self.blocks[b] if self._visible(r["transactionHash"])]
            return await self._reply(ws, rid, result={"number": hex(b), "transactions": hashes})
        if m == "eth_sendRawTransaction":
            raw = bytes.fromhex(p[0][2:])
            fields = rlp.decode(raw[1:])                            # EIP-1559: 0x02 || rlp([chainId, nonce, ..., value, ...])
            nonce, value = int.from_bytes(fields[1], "big"), int.from_bytes(fields[6], "big")
            self.raws.setdefault(nonce, []).append(raw)
            self.sent_count += 1
            if nonce in self.drop_second and len(self.raws[nonce]) == 2:
                asyncio.create_task(self._drop_later(ws, self.drop_second[nonce]))
            if self.drop_after and not self._dropped and self.sent_count == self.drop_after:
                self._dropped = True
                return await ws.close()                            # this tx is lost with the connection
            extra = self.slow_nonces.pop(nonce, 0)
            self._open += 1
            self.max_concurrent = max(self.max_concurrent, self._open)
            await asyncio.sleep(self.delay + extra)
            self._open -= 1
            h = "0x" + keccak(raw).hex()
            first_time = nonce not in self._seen
            self._seen.add(nonce)
            if value in self.reject_values and value not in self._rejected:
                self._rejected.add(value)
                return await self._reply(ws, rid, error={"code": -32000, "message": "invalid transaction: rejected"})
            t_end = asyncio.get_running_loop().time() + self.hold_s
            while nonce > self.next_nonce:                          # held until the gap fills or the hold expires
                left = t_end - asyncio.get_running_loop().time()
                if left <= 0:
                    break
                try:
                    await asyncio.wait_for(self._progress.wait(), left)
                except asyncio.TimeoutError:
                    break
            if nonce in self.reject_nonces and nonce not in self._rejected:
                self._rejected.add(nonce)
                return await self._reply(ws, rid, error={"code": -32000, "message": "invalid transaction: rejected"})
            if nonce < self.next_nonce:
                return await self._reply(ws, rid, error={"code": -32000, "message": self.known_msg})
            if nonce > self.next_nonce or (self.gap_every and nonce % self.gap_every == 0 and first_time and nonce):
                return await self._reply(ws, rid, error={"code": -32000, "message": "nonce too high"})
            rec = {"status": "0x1", "blockNumber": hex(nonce + 1), "gasUsed": "0x5208",
                   "effectiveGasPrice": "0x5f5e100", "transactionHash": h}
            self.receipts[h] = rec
            self.exec_time[h] = time.monotonic()
            self.blocks[nonce + 1] = [rec]
            for k in range(self.foreign_per_block):                 # other accounts' txs in the same block
                fh = "0x" + keccak(raw + bytes([k])).hex()
                frec = dict(rec, transactionHash=fh, gasUsed="0x7a120")
                self.receipts[fh], self.exec_time[fh] = frec, self.exec_time[h]
                self.foreign.add(fh)
                self.blocks[nonce + 1].append(frec)
            self.next_nonce += 1
            self.accepted.append(nonce)
            self.exec_counts[value] += 1
            old, self._progress = self._progress, asyncio.Event()
            old.set()
            if nonce in self.drop_after_exec and nonce not in self._dropped_exec:
                self._dropped_exec.add(nonce)
                return await ws.close()
            swallow = nonce in self.swallow_reply and nonce not in self._swallowed
            self._swallowed.add(nonce)
            if self.receipt_delay:
                asyncio.create_task(self._late_head(nonce + 1))
            elif self.head_first and not self.never_receipt:
                await self._head(nonce + 1)
            if not swallow:
                await self._reply(ws, rid, result=h)
            if not self.head_first and not self.never_receipt and not self.receipt_delay:
                await self._head(nonce + 1)


async def run_fake(fake, n, max_in_flight=32, sub_holder=None, **kw):
    """Serve `fake` over a local WebSocket, run n payments through a fresh
    OrderedSubmitter and return the results. If `sub_holder` is given, the
    submitter instance is appended to it before start()."""
    kw.setdefault("retry_delays", (0.1, 0.2, 0.4, 0.8, 1.6))
    async with websockets.serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        sub = OrderedSubmitter(f"ws://127.0.0.1:{port}", keys.account(101), 412346,
                               max_in_flight=max_in_flight, **kw)
        if sub_holder is not None:
            sub_holder.append(sub)
        await sub.start()
        futs = [await sub.submit(dict(TX, value=i + 1)) for i in range(n)]
        results = await asyncio.gather(*futs)
        await sub.close()
        return results


class TestOrderedSubmitter(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, fake, n, max_in_flight=32, **kw):
        holder = []
        results = await run_fake(fake, n, max_in_flight, sub_holder=holder, **kw)
        self.sub = holder[0]
        return results

    async def test_all_succeed_in_order_and_pipelined(self):
        fake = FakeSequencer(delay=0.05)
        t0 = time.monotonic()
        results = await self.run_fake(fake, 100)
        self.assertTrue(all(r.status == 1 for r in results))
        self.assertEqual(fake.next_nonce, 100)
        self.assertEqual(fake.accepted, list(range(100)))
        self.assertGreater(fake.max_concurrent, 1)          # pipelined, not one at a time
        self.assertLess(time.monotonic() - t0, 100 * 0.05)  # faster than one-at-a-time

    async def test_next_nonce_is_the_authoritative_issued_bound(self):
        """Task 10d: next_nonce is every nonce submit() has assigned so far, with no RPC
        read and no lag -- the bound payclient hands to Close as min_nonce."""
        fake = FakeSequencer(delay=0.01)
        self.assertEqual((await self.run_fake(fake, 10))[0].status, 1)
        self.assertEqual(self.sub.next_nonce, self.sub.start_nonce + 10)

    async def test_nonce_too_high_is_retried_with_the_same_transaction(self):
        fake = FakeSequencer(delay=0.01, gap_every=7)
        results = await self.run_fake(fake, 50)
        self.assertTrue(all(r.status == 1 for r in results), [r.error for r in results if r.status != 1])
        self.assertTrue(any(r.attempts > 1 for r in results))
        self.assertEqual(len({r.tx_hash for r in results}), 50)  # no duplicates
        resent = [raws for raws in fake.raws.values() if len(raws) > 1]
        self.assertTrue(resent)
        for raws in resent:                                  # a retry resends identical bytes
            self.assertEqual(len(set(raws)), 1)

    async def test_max_in_flight_is_never_exceeded(self):
        fake = FakeSequencer(delay=0.05)
        results = await self.run_fake(fake, 40, max_in_flight=4)
        self.assertTrue(all(r.status == 1 for r in results))
        self.assertLessEqual(fake.max_concurrent, 4)
        self.assertGreater(fake.max_concurrent, 1)

    async def test_already_known_and_nonce_too_low_count_as_accepted(self):
        for msg in ("already known", "nonce too low"):
            with self.subTest(msg=msg):
                fake = FakeSequencer(delay=0.01, swallow_reply={3, 4}, known_msg=msg)
                results = await asyncio.wait_for(self.run_fake(fake, 8, reply_timeout=0.3), 20)
                self.assertTrue(all(r.status == 1 for r in results), [r.error for r in results])
                self.assertGreater(results[3].attempts, 1)
                self.assertEqual(results[3].tx_hash, "0x" + keccak(fake.raws[3][0]).hex())
                self.assertEqual(results[3].block, 4)
                self.assertEqual(fake.accepted, list(range(8)))

    async def test_permanent_rejection_fails_one_and_resyncs_the_rest(self):
        fake = FakeSequencer(delay=0.02, reject_nonces={5})
        results = await asyncio.wait_for(self.run_fake(fake, 20), 30)
        self.assertIsNone(results[5].status)
        self.assertIn("rejected", results[5].error)
        good = [r for i, r in enumerate(results) if i != 5]
        self.assertTrue(all(r.status == 1 for r in good), [r.error for r in good if r.status != 1])
        self.assertEqual(fake.accepted, list(range(19)))     # consecutive, no duplicate nonces
        self.assertEqual(len({r.tx_hash for r in good}), 19)
        self.assertTrue(all(r.resigned == 0 for r in results[:6]))
        self.assertTrue(any(r.resigned >= 1 for r in results[6:]))
        self.assertGreaterEqual(self.sub.resyncs, 1)

    async def test_connection_drop_never_hangs(self):
        fake = FakeSequencer(delay=0.02, drop_after=10)
        results = await asyncio.wait_for(
            self.run_fake(fake, 40, reconnect_delays=(0.05, 0.1), reply_timeout=2.0), 30)
        self.assertEqual(len(results), 40)
        self.assertTrue(all(r.status == 1 for r in results), [r.error for r in results if r.status != 1][:3])
        self.assertEqual(fake.accepted, list(range(40)))

    async def test_receipt_timeout_is_unresolved(self):
        fake = FakeSequencer(delay=0.01, never_receipt=True)
        results = await asyncio.wait_for(self.run_fake(fake, 3, receipt_timeout=0.5), 20)
        for r in results:
            self.assertIsNone(r.status)
            self.assertTrue(r.error.startswith("unresolved"), r.error)
            self.assertIsNotNone(r.tx_hash)

    async def test_head_before_reply_uses_the_receipt_cache(self):
        fake = FakeSequencer(delay=0.01, head_first=True)
        results = await asyncio.wait_for(self.run_fake(fake, 30), 20)
        self.assertTrue(all(r.status == 1 for r in results), [r.error for r in results if r.status != 1])

    async def test_without_subscriptions_receipts_are_polled(self):
        fake = FakeSequencer(delay=0.01, no_subscribe=True)
        results = await asyncio.wait_for(self.run_fake(fake, 20), 20)
        self.assertTrue(all(r.status == 1 for r in results), [r.error for r in results if r.status != 1])

    async def test_rejection_while_later_txs_are_held_pays_each_payment_once(self):
        fake = FakeSequencer(delay=0.02, hold_s=0.3, reject_values={6})     # payment index 5
        results = await asyncio.wait_for(self.run_fake(fake, 20), 40)
        self.assertIsNone(results[5].status)
        self.assertIn("rejected", results[5].error)
        good = [r for i, r in enumerate(results) if i != 5]
        self.assertTrue(all(r.status == 1 and not r.error for r in good), [r.error for r in good if r.status != 1])
        self.assertFalse(any("unresolved" in r.error for r in results))
        self.assertEqual(fake.exec_counts, Counter({v: 1 for v in range(1, 21) if v != 6}))
        self.assertEqual(fake.accepted, list(range(19)))

    async def test_rejection_while_an_earlier_payment_is_still_unanswered(self):
        # payment 3 is slow (0.8 s); payment 6 is rejected at once; 4, 5 are held and run after 3
        fake = FakeSequencer(delay=0.02, hold_s=1.5, slow_nonces={3: 0.8}, reject_values={7})
        results = await asyncio.wait_for(self.run_fake(fake, 10), 40)
        self.assertIsNone(results[6].status)
        self.assertIn("rejected", results[6].error)
        good = [r for i, r in enumerate(results) if i != 6]
        self.assertTrue(all(r.status == 1 and not r.error for r in good), [r.error for r in good if r.status != 1])
        self.assertEqual(fake.exec_counts, Counter({v: 1 for v in range(1, 11) if v != 7}))
        self.assertTrue(all(r.resigned == 0 for r in results[:6]))       # 3, 4, 5 are kept, not misclassified
        self.assertTrue(all(r.resigned == 1 for r in results[7:]))
        self.assertEqual(fake.accepted, list(range(9)))

    async def test_three_second_outage_completes_without_double_execution(self):
        fake = FakeSequencer(delay=0.05, hold_s=0.3)
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        sub = OrderedSubmitter(f"ws://127.0.0.1:{port}", keys.account(101), 412346, max_in_flight=32,
                               receipt_timeout=40, reply_timeout=5)
        await sub.start()

        async def outage():
            nonlocal server
            await asyncio.sleep(1.0)
            server.close(close_connections=True)
            await server.wait_closed()
            await asyncio.sleep(3.0)
            server = await websockets.serve(fake.handler, "127.0.0.1", port)

        task = asyncio.create_task(outage())
        futs = []
        for i in range(60):
            futs.append(await sub.submit(dict(TX, value=i + 1)))
            await asyncio.sleep(0.05)
        results = await asyncio.wait_for(asyncio.gather(*futs), 60)
        await task
        await sub.close()
        server.close()
        self.assertTrue(all(r.status == 1 for r in results), [r.error for r in results if r.status != 1][:3])
        self.assertEqual(fake.exec_counts, Counter({v: 1 for v in range(1, 61)}))
        self.assertGreaterEqual(sub.reconnects, 1)
        self.assertGreater(sub.outage_seconds, 2.0)

    async def test_lost_resend_that_is_still_held_is_not_paid_twice(self):
        # (behavioural check of the whole flow; the state-level check is test_latest_send_lost_...)
        # payment 1 (nonce 1) gets "nonce too high" while payment 0 is slow; its retry L is held and
        # the connection drops before L is answered; L then runs when nonce 0 executes. During the
        # slow reconnect (new socket up, resends not yet done) payment 2 is rejected permanently and
        # triggers a resync: it must not re-sign payment 1, whose latest send was lost.
        fake = FakeSequencer(delay=0.02, hold_s=0.3, slow_nonces={0: 0.55}, drop_second={1: 0.05},
                             reject_values={3}, subscribe_delay=0.5)
        async with websockets.serve(fake.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            sub = OrderedSubmitter(f"ws://127.0.0.1:{port}", keys.account(101), 412346, max_in_flight=8,
                                   retry_delays=(0.05,), reconnect_delays=(0.2,), reply_timeout=5.0)
            await sub.start()
            futs = [await sub.submit(dict(TX, value=1)), await sub.submit(dict(TX, value=2))]
            await asyncio.sleep(0.8)                          # inside the reconnect window
            futs.append(await sub.submit(dict(TX, value=3)))
            results = await asyncio.wait_for(asyncio.gather(*futs), 30)
            await sub.close()
        self.assertEqual([r.status for r in results], [1, 1, None], [r.error for r in results])
        self.assertIn("rejected", results[2].error)
        self.assertEqual(fake.exec_counts, Counter({1: 1, 2: 1}))
        self.assertEqual(fake.accepted, [0, 1])
        self.assertEqual([r.resigned for r in results], [0, 0, 0])
    async def test_latest_send_lost_is_never_judged_dead(self):
        # the exact state of the residual double-pay: H answered "nonce too high", the retry L of the
        # same bytes was lost with the connection. Not "high" (which would get re-signed): "lost".
        sub = OrderedSubmitter("ws://unused", keys.account(101), 412346, max_in_flight=1)
        loop = asyncio.get_running_loop()
        p = _Payment(0, TX, 1, "0x00", "0x11", SubmitResult(), loop.create_future())
        h, lost = loop.create_future(), loop.create_future()
        h.set_result({"error": {"code": -32000, "message": "nonce too high"}})
        lost.set_exception(ConnectionError("connection lost"))
        p.sends = [_Send(0, "0x00", "0x11", h), _Send(0, "0x00", "0x11", lost)]
        sub._open_payments[0] = p
        self.assertEqual((await sub._judge(p))[0], "lost")
        with self.assertRaises(ConnectionError):                 # quiesce refuses to go on
            await sub._quiesce(time.monotonic() + 5)
        pending = loop.create_future()                            # a send still unanswered: waiting, and
        p.sends.append(_Send(0, "0x00", "0x11", pending))         # a permanent error elsewhere does not fail it
        p.sends.append(_Send(0, "0x00", "0x11", loop.create_future()))
        p.sends[0].fut = loop.create_future()
        p.sends[0].fut.set_result({"error": {"code": -32000, "message": "invalid transaction: bad"}})
        self.assertEqual((await sub._judge(p))[0], "waiting")
        for s in p.sends[2:]:
            s.fut.cancel()


    async def test_unreachable_sequencer_never_hangs(self):
        fake = FakeSequencer(delay=0.05, hold_s=0.3)
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        sub = OrderedSubmitter(f"ws://127.0.0.1:{port}", keys.account(101), 412346, max_in_flight=32,
                               receipt_timeout=2.0, reply_timeout=1.0, reconnect_delays=(0.1, 0.2))

        async def kill():
            await asyncio.sleep(0.5)
            server.close(close_connections=True)          # and never comes back

        await sub.start()
        task = asyncio.create_task(kill())
        futs = []
        for i in range(30):
            futs.append(await sub.submit(dict(TX, value=i + 1)))
            await asyncio.sleep(0.05)
        results = await asyncio.wait_for(asyncio.gather(*futs), 10)
        await task
        await asyncio.wait_for(sub.close(), 5)
        self.assertEqual(len(results), 30)
        for r in results:
            self.assertTrue(r.status == 1 or r.error.startswith("unresolved"), (r.status, r.error))
        self.assertTrue(any(r.error.startswith("unresolved") for r in results))
        self.assertEqual(max(fake.exec_counts.values()), 1)

    async def test_lagging_receipt_index_does_not_cause_a_resign(self):
        fake = FakeSequencer(delay=0.01, drop_after_exec={3}, receipt_delay=1.5)   # executed, reply lost
        results = await asyncio.wait_for(self.run_fake(fake, 6, reconnect_delays=(0.1,)), 30)
        self.assertTrue(all(r.status == 1 and not r.error for r in results), [r.error for r in results])
        self.assertEqual(fake.exec_counts, Counter({v: 1 for v in range(1, 7)}))
        self.assertTrue(all(r.resigned == 0 for r in results))

    async def test_failed_signing_frees_the_slot_and_keeps_the_nonce(self):
        fake = FakeSequencer(delay=0.01)
        async with websockets.serve(fake.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            sub = OrderedSubmitter(f"ws://127.0.0.1:{port}", keys.account(101), 412346, max_in_flight=1)
            await sub.start()
            bad = await asyncio.wait_for(sub.submit({"to": TX["to"], "value": 1}), 5)   # no gas/fees
            self.assertTrue((await bad).error)
            good = await asyncio.wait_for(sub.submit(dict(TX)), 5)
            r = await asyncio.wait_for(good, 10)
            await sub.close()
        self.assertEqual(r.status, 1)
        self.assertEqual(fake.accepted, [0])


ZK_DUP = "known transaction"                                                    # F12 stable substring
ZK_LOW = "nonce too low. allowed nonce range: 8 - 2508, actual: 5"              # F12 fixture text


class TestZkSyncTexts(unittest.IsolatedAsyncioTestCase):
    async def test_zksync_duplicate_text_counts_as_accepted(self):
        fake = FakeSequencer(delay=0.01, known_msg=ZK_DUP, swallow_reply={3})   # reply to nonce 3 lost -> resent
        results = await run_fake(fake, 10, accepted_texts=("already known", ZK_DUP), reply_timeout=0.3)
        self.assertTrue(all(r.status == 1 for r in results), [r.error for r in results])
        self.assertEqual(fake.exec_counts.most_common(1)[0][1], 1)            # nothing paid twice

    async def test_default_texts_are_unchanged(self):
        s = OrderedSubmitter("ws://x", keys.account(101), 1, 1)
        self.assertEqual((s.accepted_texts, s.retry_texts, s.low_texts, s.receipt_sweep_s),
                         (("already known",), ("nonce too high",), ("nonce too low",), 1.0))

    async def test_receipt_sweep_interval_bounds_polling(self):
        fake = FakeSequencer(delay=0.01, receipt_delay=2.0)                  # heads and receipts arrive late
        await run_fake(fake, 20, receipt_sweep_s=10.0)
        self.assertLessEqual(fake.calls["eth_getTransactionReceipt"], 20)    # at most one sweep per hash


class TestOwnReceipts(unittest.IsolatedAsyncioTestCase):
    """receipt_mode="own" (task 10c): on each new head the submitter fetches the block's tx hashes
    and only its own receipts, never the whole block's receipts (Wi-Fi bursts on the Pi cluster)."""

    def check_fields(self, fake, results):
        for i, r in enumerate(results):
            self.assertEqual((r.status, r.block, r.gas_used, r.effective_gas_price, r.error),
                             (1, i + 1, 21000, 10**8, ""), i)
            self.assertEqual(r.tx_hash, "0x" + keccak(fake.raws[i][-1]).hex())
            self.assertGreaterEqual(r.t_confirmed, r.t_submitted)

    async def test_own_mode_fetches_hash_lists_and_own_receipts_only(self):
        fake = FakeSequencer(delay=0.01, foreign_per_block=3)
        results = await asyncio.wait_for(run_fake(fake, 30, receipt_mode="own", receipt_sweep_s=10.0), 30)
        self.check_fields(fake, results)
        self.assertEqual(fake.calls["eth_getBlockReceipts"], 0)
        self.assertGreater(fake.calls["eth_getBlockByNumber"], 0)
        own = {r.tx_hash for r in results}
        self.assertEqual(set(fake.receipt_asks), own)                  # asked for each own hash ...
        self.assertLessEqual(max(fake.receipt_asks.values()), 2)        # ... about once (no sweeping)

    async def test_other_accounts_txs_trigger_no_receipt_fetch(self):
        fake = FakeSequencer(delay=0.01, foreign_per_block=5)
        results = await asyncio.wait_for(run_fake(fake, 20, receipt_mode="own"), 30)
        self.assertTrue(all(r.status == 1 for r in results), [r.error for r in results])
        self.assertEqual(len(fake.foreign), 100)
        self.assertFalse(fake.foreign & set(fake.receipt_asks))

    async def test_default_mode_is_block_and_unchanged(self):
        s = OrderedSubmitter("ws://x", keys.account(101), 1, 1)
        self.assertEqual(s.receipt_mode, "block")
        fake = FakeSequencer(delay=0.01, foreign_per_block=3)
        results = await asyncio.wait_for(run_fake(fake, 20, receipt_sweep_s=10.0), 30)
        self.check_fields(fake, results)
        self.assertGreater(fake.calls["eth_getBlockReceipts"], 0)
        self.assertEqual(fake.calls["eth_getBlockByNumber"], 0)

    async def test_both_modes_give_the_same_results(self):
        out = {}
        for mode in ("block", "own"):
            fake = FakeSequencer(delay=0.01, foreign_per_block=2, gap_every=5)
            res = await asyncio.wait_for(run_fake(fake, 25, receipt_mode=mode), 30)
            out[mode] = [(r.tx_hash, r.status, r.block, r.gas_used, r.effective_gas_price, r.error) for r in res]
        self.assertEqual(out["block"], out["own"])

    async def test_head_before_reply_uses_the_receipt_cache_in_own_mode(self):
        fake = FakeSequencer(delay=0.01, head_first=True, foreign_per_block=2)
        results = await asyncio.wait_for(run_fake(fake, 30, receipt_mode="own", receipt_sweep_s=10.0), 30)
        self.check_fields(fake, results)
        self.assertFalse(fake.foreign & set(fake.receipt_asks))
        self.assertLessEqual(max(fake.receipt_asks.values()), 2)        # resolved from the head, not by sweeps

    async def test_missing_block_by_number_falls_back_to_sweeps(self):
        fake = FakeSequencer(delay=0.01, no_block_by_number=True, foreign_per_block=2)
        holder = []
        results = await asyncio.wait_for(run_fake(fake, 20, sub_holder=holder, receipt_mode="own",
                                                  receipt_sweep_s=10.0), 30)
        self.check_fields(fake, results)
        self.assertEqual(fake.calls["eth_getBlockReceipts"], 0)
        self.assertFalse(holder[0]._block_receipts_ok)                  # latched: fast sweeps instead
        self.assertFalse(fake.foreign & set(fake.receipt_asks))
        self.assertTrue(holder[0].receipt_fallback)                     # ... and the latch is recorded
        self.assertIn("-32601", holder[0].receipt_fallback_error)

    async def test_transient_not_found_text_does_not_latch_in_own_mode(self):
        """Final review Important 1: in own mode only code -32601 (method not found) may switch
        the submitter to 0.05 s polling; a transient "block not found" error must not."""
        fake = FakeSequencer(delay=0.01, block_by_number_errors=5)
        holder = []
        results = await asyncio.wait_for(run_fake(fake, 20, sub_holder=holder, receipt_mode="own",
                                                  receipt_sweep_s=1.0), 30)
        self.check_fields(fake, results)
        self.assertEqual(fake.block_by_number_errors, 0)                # the errors were served
        self.assertTrue(holder[0]._block_receipts_ok)
        self.assertFalse(holder[0].receipt_fallback)
        self.assertIsNone(holder[0].receipt_fallback_error)

    def test_latch_rule_per_mode(self):
        """Own mode: code -32601 only. Block mode (Arbitrum): unchanged, code or text."""
        from measure.submitter import RpcError
        own = OrderedSubmitter("ws://x", keys.account(101), 1, 1, receipt_mode="own")
        blk = OrderedSubmitter("ws://x", keys.account(101), 1, 1)
        text_only = RpcError("block not found", code=-32000)
        missing = RpcError("the method eth_x does not exist/is not available", code=-32601)
        self.assertFalse(own._latches(text_only))
        self.assertTrue(own._latches(missing))
        self.assertTrue(blk._latches(text_only))
        self.assertTrue(blk._latches(missing))
        self.assertFalse(blk._latches(RpcError("execution reverted", code=3)))
        self.assertEqual((own.receipt_fallback, own.receipt_fallback_error), (False, None))

    async def test_late_heads_and_receipts_in_own_mode(self):
        fake = FakeSequencer(delay=0.01, receipt_delay=1.0, foreign_per_block=2)
        results = await asyncio.wait_for(run_fake(fake, 10, receipt_mode="own"), 30)
        self.check_fields(fake, results)

    async def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            OrderedSubmitter("ws://x", keys.account(101), 1, 1, receipt_mode="all")

    def test_zksync_uses_own_mode(self):
        self.assertEqual(zksync.SUBMITTER_KW["receipt_mode"], "own")


class TestHasReceipt(unittest.IsolatedAsyncioTestCase):
    """An RPC timeout or error means "cannot tell" (None), never "no receipt" (False),
    because False can lead to a re-sign of the same nonce (final review T1)."""

    async def _ask(self, exc=None, result=None):
        sub = OrderedSubmitter("ws://127.0.0.1:9", keys.account(101), 412346, max_in_flight=1)

        async def rpc(method, params, timeout=None):
            if exc is not None:
                raise exc
            return result
        sub._rpc = rpc
        return await sub._has_receipt("0x" + "ab" * 32)

    async def test_timeout_error_and_dropped_connection_cannot_tell(self):
        from measure.submitter import RpcError
        self.assertIsNone(await self._ask(TimeoutError("no reply to eth_getTransactionReceipt")))
        self.assertIsNone(await self._ask(RpcError("header not found", -32000)))
        self.assertIsNone(await self._ask(ConnectionError("closed")))

    async def test_a_real_answer_decides(self):
        self.assertIs(await self._ask(result=None), False)
        self.assertIs(await self._ask(result={"status": "0x1"}), True)


@unittest.skipUnless(os.environ.get("ARB_L2"), "needs the workstation Arbitrum stack (ARB_L2, ws://127.0.0.1:8548)")
class TestAgainstArbitrum(unittest.IsolatedAsyncioTestCase):
    async def test_50_per_second_one_account_no_losses(self):
        sub = OrderedSubmitter(os.environ["ARB_L2"], keys.account(1), 412346, max_in_flight=64)
        await sub.start()
        futs = []
        t0 = time.monotonic()
        for k in range(500):                                  # 10 s at 50/s
            await asyncio.sleep(max(0, t0 + k / 50 - time.monotonic()))
            futs.append(await sub.submit({"to": keys.account(1).address, "value": 1, "gas": 100000,
                                          "maxFeePerGas": 2 * 10**8, "maxPriorityFeePerGas": 0}))
        results = await asyncio.gather(*futs)
        await sub.close()
        self.assertEqual(sum(r.status == 1 for r in results), 500, [r.error for r in results if r.status != 1][:3])


@unittest.skipUnless(os.environ.get("ZK_L2"), "needs the workstation zkSync stack (ZK_L2, ws://127.0.0.1:3051)")
class TestAgainstZkSync(unittest.IsolatedAsyncioTestCase):
    async def test_200_at_5_per_second_one_account_bounded_in_flight(self):
        ZK_CHAIN_ID = 271
        acct = keys.account(195)
        http = os.environ["ZK_L2"].replace("wss://", "https://", 1).replace("ws://", "http://", 1).replace(":3051", ":3050", 1)

        def rpc(method, params):
            req = urllib.request.Request(http, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                                 "params": params}).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req).read())["result"]

        pending_nonce_before = int(rpc("eth_getTransactionCount", [acct.address, "pending"]), 16)

        sub = OrderedSubmitter(os.environ["ZK_L2"], acct, ZK_CHAIN_ID, max_in_flight=20,
                               **zksync.SUBMITTER_KW)
        await sub.start()
        self.assertEqual(sub.start_nonce, pending_nonce_before)

        max_in_flight_seen = 0
        futs = []
        t0 = time.monotonic()
        for k in range(200):                                  # 40 s at 5/s
            await asyncio.sleep(max(0, t0 + k / 5 - time.monotonic()))
            futs.append(await sub.submit({"to": acct.address, "value": 1, "gas": 500000,
                                          "maxFeePerGas": 2 * 10**8, "maxPriorityFeePerGas": 0}))
            in_flight = sum(not f.done() for f in futs)
            max_in_flight_seen = max(max_in_flight_seen, in_flight)
        results = await asyncio.gather(*futs)
        await sub.close()
        self.assertEqual(sum(r.status == 1 for r in results), 200, [r.error for r in results if r.status != 1][:3])
        self.assertLessEqual(max_in_flight_seen, 20)
