"""In-order, pipelined transaction submission over one WebSocket per account.

Why: Arbitrum answers eth_sendRawTransaction only once the transaction is
sequenced, so waiting for each reply caps an account at 1/latency; and it
rejects a nonce that is ahead of the account ("nonce too high"), so parallel
threads that race each other lose transactions (2 803 of 3 000 on 2026-09-29).
Here one submitter sends signed transactions strictly in nonce order over a
single connection and does not wait for replies; up to `max_in_flight` are open
at once. Cluster spec §7.3. The Arbitrum WebSocket of the workstation stack is
ws://127.0.0.1:8548 (8547 is HTTP).

Failure handling, all of it ending in a resolved future, never a hang:
- "nonce too high" (transient): the *same* signed bytes are resent after a
  short delay, so a payment can never be executed twice.
- Any other error is a permanent rejection ("resync and continue"), and so is
  "nonce too high" after the retries ran out (a symptom of an earlier gap).
  The sequencer holds a nonce-gapped transaction for about a second and may
  still execute it when the gap fills, so a resync first QUIESCES: it takes
  the send lock (no new sends) and waits until every open payment's sends have
  a final node answer. Only then it reads the pending nonce, keeps whatever
  was accepted, fails permanently rejected payments, and re-signs the payments
  whose current bytes are dead with consecutive nonces from the pending nonce,
  resending them in original order. "nonce too low" proves acceptance only if
  the receipt of those exact bytes exists; without one it stays "unproven" for
  `receipt_lag` seconds (indexing may lag) before the version counts as dead.
  A payment whose most recent send was lost with a connection is never judged
  or re-signed until a later send of the same bytes has a final answer. The
  quiesce waits at most `reply_timeout` (`quiesce_timeouts` counts the misses)
  and then releases the lock; every reconnect wait is bounded by the payment's
  own deadline ("unresolved: connection lost"). Futures stay the same;
  `SubmitResult.resigned` counts re-signings, `resyncs` the events.
  Invariant: a payment has at most one signed version the node may still
  execute; a new version is signed only after the old one is proven dead.
- A dropped connection fails all waiting requests; the submitter keeps
  reconnecting with capped backoff (`reconnect_delays`, last value repeated)
  until close(), resubscribes and resends every unaccepted payment (same
  bytes). `reconnects` and `outage_seconds` are recorded.
- No reply within `reply_timeout` resends the same bytes on the same connection.
- Anything still unknown after `receipt_timeout` ends as "unresolved: ...".

Receipts: one shared mechanism per submitter. It subscribes to newHeads and, for
every new head (all heads that arrived since the last check), learns the block's
receipts in one of two ways (`receipt_mode`):
- "block" (default, Arbitrum): eth_getBlockReceipts, every receipt of the block.
- "own" (zkSync, task 10c): eth_getBlockByNumber(n, false) gives the block's tx
  hashes only; those that belong to this submitter (any signed version of an
  unfinished payment) are fetched with eth_getTransactionReceipt, concurrently.
  Other accounts' receipts are never downloaded: on the Pi cluster six clients
  pulling whole zkSync catch-up blocks at once flooded the shared Wi-Fi.
Either way the matching open hashes are resolved right after the head's fetches
return; receipts seen before a hash was registered are cached; if the per-head
method does not exist the submitter latches to fast eth_getTransactionReceipt
sweeps; a slow sweep (`receipt_sweep_s`) covers missed heads. `t_confirmed` is the
time the receipt became known to this process, not the block timestamp (in "own"
mode that is one extra round trip after the hash list). `t_submitted` is taken
when the send is handed to the socket.

zkSync: the matched error texts and the receipt-sweep interval are per-system
parameters (`accepted_texts`, `retry_texts`, `low_texts`, `receipt_sweep_s`);
the logic above is unchanged. zkSync's sequencer answers eth_sendRawTransaction
once the transaction is admitted to the mempool, before it is sequenced (F10),
so its duplicate/nonce-too-high/nonce-too-low texts differ in wording from
Arbitrum's; `measure/zksync.py` holds zkSync's texts.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

import websockets

from measure.txsign import Eip1559Signer

log = logging.getLogger(__name__)


@dataclass
class SubmitResult:
    tx_hash: str | None = None
    t_submitted: float | None = None
    t_confirmed: float | None = None
    status: int | None = None
    block: int | None = None
    gas_used: int | None = None
    effective_gas_price: int | None = None
    attempts: int = 0
    error: str = ""
    resigned: int = 0


class RpcError(RuntimeError):
    """The node answered a request with a JSON-RPC error."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class _Send:
    """One eth_sendRawTransaction of one signed version; its answer may come late."""
    __slots__ = ("gen", "raw", "tx_hash", "fut", "low_seen")

    def __init__(self, gen, raw, tx_hash, fut):
        self.gen, self.raw, self.tx_hash, self.fut = gen, raw, tx_hash, fut
        self.low_seen = None           # when "nonce too low" without a receipt was first seen


class _Payment:
    def __init__(self, seq, tx, nonce, raw, tx_hash, result, done):
        self.tx_fields = dict(tx)
        self.seq, self.nonce, self.raw, self.tx_hash = seq, nonce, raw, tx_hash
        self.result, self.done = result, done
        self.state = "open"            # open (not accepted) -> accepted, or failed
        self.gen = 0                   # bumped whenever the payment is re-signed
        self.sends: list[_Send] = []   # sends not yet retired; never overwritten while unanswered
        self.receipt_fut: asyncio.Future | None = None
        self.receipt_deadline = 0.0
        self.last_check = 0.0
        self.sent = False


def _swallow(fut):
    if not fut.cancelled():
        fut.exception()               # mark retrieved: nobody may be waiting any more


class OrderedSubmitter:
    def __init__(self, ws_url, account, chain_id, max_in_flight, retry_delays=(0.25, 0.5, 1.0),
                 receipt_timeout=120.0, reply_timeout=30.0, reconnect_delays=(0.2, 0.5, 1.0, 2.0), receipt_lag=5.0,
                 accepted_texts=("already known",), retry_texts=("nonce too high",),
                 low_texts=("nonce too low",), receipt_sweep_s=1.0, receipt_mode="block"):
        if receipt_mode not in ("block", "own"):
            raise ValueError(f"receipt_mode must be 'block' or 'own', not {receipt_mode!r}")
        self.receipt_mode = receipt_mode
        self.ws_url, self.account, self.chain_id = ws_url, account, chain_id
        self.retry_delays, self.receipt_timeout = retry_delays, receipt_timeout
        self.reply_timeout, self.reconnect_delays = reply_timeout, reconnect_delays
        self.receipt_lag = receipt_lag
        self.accepted_texts, self.retry_texts = accepted_texts, retry_texts
        self.low_texts, self.receipt_sweep_s = low_texts, receipt_sweep_s
        self.start_nonce = None
        self.resyncs = 0
        self.quiesce_timeouts = 0
        self.reconnects = 0
        self.outage_seconds = 0.0
        self._signer = Eip1559Signer(account.key)
        self._slots = asyncio.Semaphore(max_in_flight)
        self._ids = itertools.count(1)
        self._replies: dict[int, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()
        self._conn_lock = asyncio.Lock()
        self._conn_gen = 0
        self._nonce = None
        self._ws = None
        self._reader = None
        self._manager = None
        self._closing = False
        self._seq = itertools.count()
        self._open_payments: dict[int, _Payment] = {}    # not finished, in seq (= send) order
        self._tasks: set[asyncio.Task] = set()
        self._bg: set[asyncio.Task] = set()                # bounded reconnect waits
        self._open_hashes: dict[str, _Payment] = {}      # accepted, waiting for a receipt
        self._cache: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._heads: set[int] = set()
        self._wake = asyncio.Event()
        self._subscribed = False
        self._block_receipts_ok = True                    # the per-head method (either mode) works
        self.receipt_fallback = False                     # True once latched to per-hash polling
        self.receipt_fallback_error = None                # the RPC error that latched it (code: message)

    # ---- connection -------------------------------------------------------------------------

    async def start(self):
        await self._connect()
        self._conn_gen += 1
        self._nonce = int(await self._rpc("eth_getTransactionCount", [self.account.address, "pending"]), 16)
        self.start_nonce = self._nonce
        self._manager = asyncio.create_task(self._receipt_loop())

    @property
    def next_nonce(self):
        """The nonce submit() will assign next: every nonce below this was signed and
        sent at least once (not necessarily confirmed, or even accepted). Authoritative
        for "every payment nonce issued so far" -- unlike a fresh RPC pending-nonce read,
        it cannot lag behind what this submitter already put on the wire."""
        return self._nonce

    async def _connect(self):
        ws = await websockets.connect(self.ws_url, max_size=None, ping_interval=None)
        self._ws = ws
        self._reader = asyncio.create_task(self._read_loop(ws))
        try:
            await self._rpc("eth_subscribe", ["newHeads"])
            self._subscribed = True
        except (RpcError, TimeoutError):
            self._subscribed = False          # receipts fall back to faster sweeps
        except BaseException:
            await asyncio.gather(ws.close(), return_exceptions=True)
            raise

    async def close(self):
        self._closing = True
        tasks = [t for t in [self._manager, *self._tasks, *self._bg] if t]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._reader:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self._ws:
            await asyncio.gather(self._ws.close(), return_exceptions=True)

    async def _read_loop(self, ws):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                for m in msg if isinstance(msg, list) else [msg]:
                    if m.get("method") == "eth_subscription":
                        self._heads.add(int(m["params"]["result"]["number"], 16))
                        self._wake.set()
                        continue
                    fut = self._replies.pop(m.get("id"), None)
                    if fut and not fut.done():
                        fut.set_result(m)
        except Exception:
            pass
        finally:
            if self._ws is ws:                # connection is gone: nobody may wait forever
                replies, self._replies = self._replies, {}
                for fut in replies.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("connection lost"))

    async def _ensure_connected(self, seen_gen):
        """Reconnect (once, however many waiters ask; retrying with capped backoff until
        close()) and resend every unaccepted payment. Never call with the send lock held."""
        async with self._conn_lock:
            if self._conn_gen != seen_gen:
                return                        # somebody else already reconnected
            t0 = time.monotonic()
            old, reader = self._ws, self._reader
            await asyncio.gather(old.close(), return_exceptions=True)
            await asyncio.gather(reader, return_exceptions=True)
            attempt = 0
            while True:
                if self._closing:
                    raise ConnectionError("closed")
                await asyncio.sleep(self.reconnect_delays[min(attempt, len(self.reconnect_delays) - 1)])
                attempt += 1
                try:
                    await self._connect()
                    break
                except Exception:
                    continue
            self.reconnects += 1
            self.outage_seconds += time.monotonic() - t0
            async with self._send_lock:
                for q in list(self._open_payments.values()):
                    if q.state == "open":
                        await self._send_raw(q)   # same bytes: idempotent
            self._conn_gen += 1               # only now: a resync in between must wait for the resends
            self._wake.set()

    async def _reconnected(self, seen_gen, timeout):
        """_ensure_connected, but never for longer than `timeout` (TimeoutError)."""
        task = asyncio.ensure_future(self._ensure_connected(seen_gen))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        done, _ = await asyncio.wait({task}, timeout=max(timeout, 0))
        if not done:
            raise TimeoutError("reconnect")
        task.result()                         # ConnectionError if closing

    async def _send(self, method, params) -> asyncio.Future:
        """Hand a request to the socket; never raises, a failed send is a failed future."""
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        fut.add_done_callback(_swallow)
        self._replies[rid] = fut
        try:
            await self._ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        except Exception as e:
            self._replies.pop(rid, None)
            fut.set_exception(ConnectionError(f"send failed: {e}"))
        return fut

    async def _rpc(self, method, params, timeout=None):
        fut = await self._send(method, params)
        done, _ = await asyncio.wait({fut}, timeout=timeout or self.reply_timeout)
        if not done:
            raise TimeoutError(f"no reply to {method}")
        msg = fut.result()                    # ConnectionError if the connection dropped
        if "error" in msg:
            raise RpcError(msg["error"].get("message", str(msg["error"])), msg["error"].get("code"))
        return msg["result"]

    async def _send_raw(self, p: _Payment):
        """Send p's current signed bytes. Caller holds the send lock. Earlier sends stay
        recorded until answered: an unanswered send may still be executed by the node."""
        if p.sent:
            p.result.attempts += 1
        p.sent = True
        p.sends = [s for s in p.sends if not (s.fut.done() and s.fut.exception() is not None)]
        fut = await self._send("eth_sendRawTransaction", [p.raw])
        p.sends.append(_Send(p.gen, p.raw, p.tx_hash, fut))
        if p.result.t_submitted is None:
            p.result.t_submitted = time.time()

    async def _resend(self, p: _Payment, gen: int, only_if_idle=False):
        async with self._send_lock:
            if p.gen != gen or p.state != "open":
                return
            if only_if_idle and any(s.gen == gen and not s.fut.done() for s in p.sends):
                return
            await self._send_raw(p)

    # ---- submitting ---------------------------------------------------------------------------

    async def submit(self, tx: dict) -> asyncio.Future:
        """Assign the next nonce, sign and send now (in order); the returned
        future resolves to a SubmitResult once the outcome is known."""
        await self._slots.acquire()
        loop = asyncio.get_running_loop()
        done, result = loop.create_future(), SubmitResult(attempts=1)
        try:
            if self._closing:
                raise ConnectionError("closed")
            async with self._send_lock:                   # strict nonce order on the wire
                raw, tx_hash = self._signer.sign(dict(tx, nonce=self._nonce, chainId=self.chain_id))
                p = _Payment(next(self._seq), tx, self._nonce, raw, tx_hash, result, done)
                self._nonce += 1                          # only after signing worked
                self._open_payments[p.seq] = p
                await self._send_raw(p)
        except Exception as e:                            # nothing was committed: free the slot
            self._slots.release()
            result.error = f"{type(e).__name__}: {e}"[:200]
            done.set_result(result)
            return done
        task = asyncio.create_task(self._follow(p))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return done

    async def _follow(self, p: _Payment):
        res = p.result
        try:
            if await self._accept_phase(p):
                await self._receipt_phase(p)
        except asyncio.CancelledError:
            res.error = res.error or "unresolved: closed"
            raise
        except ConnectionError as e:
            res.error = res.error or f"unresolved: {e}"[:200]
        except Exception as e:                            # the ledger records it; never raise into the run
            res.error = res.error or f"{type(e).__name__}: {e}"[:200]
        finally:
            self._open_payments.pop(p.seq, None)
            self._open_hashes.pop(p.tx_hash, None)
            self._slots.release()
            if not p.done.done():
                p.done.set_result(res)

    async def _has_receipt(self, tx_hash):
        """True/False: is there a receipt for exactly these bytes now; None: cannot tell.
        Only a real answer (a receipt, or null) decides. A dropped connection, an
        unanswered request or a JSON-RPC error says nothing about the chain, so it
        is None, never False: False can end in a re-sign of the same nonce."""
        if tx_hash in self._cache:
            return True
        try:
            return bool(await self._rpc("eth_getTransactionReceipt", [tx_hash]))
        except (ConnectionError, TimeoutError, RpcError):
            return None

    async def _judge(self, p: _Payment):
        """What the node's answers to p's CURRENT bytes say. Results: accepted; waiting (a send
        is unanswered); lost (the most recent send died with the connection: only a later send
        of the same bytes can settle it); rejected (every send has a final answer, none
        succeeded, at least one was a permanent error); unproven ("nonce too low" but no receipt
        yet); stale; high ("nonce too high" everywhere).
        A version is dead only if every send of it has a final answer, the chain's nonce has
        moved past it ("nonce too low") and its exact hash has no receipt after `receipt_lag`
        seconds (indexing may lag), or the node refused it outright."""
        cur = [s for s in p.sends if s.gen == p.gen]
        rejected, waiting, unproven, stale, high = None, False, False, False, False
        for s in cur:
            if not s.fut.done():
                waiting = True
                continue
            if s.fut.exception() is not None:
                continue                                  # lost with the connection
            msg = s.fut.result()
            if "error" not in msg:
                return "accepted", msg["result"]
            text = msg["error"].get("message", "")
            if any(t in text for t in self.accepted_texts):
                return "accepted", s.tx_hash
            if any(t in text for t in self.low_texts):
                found = await self._has_receipt(s.tx_hash)
                if found:
                    return "accepted", s.tx_hash
                if s.low_seen is None:
                    s.low_seen = time.monotonic()
                if found is None or time.monotonic() - s.low_seen < self.receipt_lag:
                    unproven = True
                else:
                    stale = True
            elif any(t in text for t in self.retry_texts):
                high = True
            else:
                rejected = rejected or text
        if waiting:
            return "waiting", None
        if not cur or cur[-1].fut.exception() is not None:
            return "lost", None                           # the latest send has no real answer
        if rejected:
            return "rejected", rejected
        if unproven:
            return "unproven", None
        if stale:
            return "stale", None
        return ("high", None) if high else ("lost", None)

    def _apply(self, p: _Payment, kind, info):
        if kind == "accepted":
            p.result.tx_hash, p.state = info, "accepted"
        elif kind == "rejected":
            p.result.error, p.state = info[:200], "failed"

    async def _accept_phase(self, p: _Payment) -> bool:
        """Wait until the node accepted the payment. True: hash known, wait for the receipt."""
        res = p.result
        deadline = time.monotonic() + self.receipt_timeout
        idx, last_gen = 0, p.gen
        while True:
            if p.state != "open":
                return p.state == "accepted"
            gen, conn_gen = p.gen, self._conn_gen
            if gen != last_gen:
                idx, last_gen = 0, gen
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                res.error, p.state = "unresolved: no reply from node", "failed"
                return False
            pending = [s.fut for s in p.sends if s.gen == gen and not s.fut.done()]
            if pending:
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED,
                                             timeout=min(self.reply_timeout, remaining))
                if not done:
                    await self._resend(p, gen)            # no answer: same bytes again
                    continue
            kind, info = await self._judge(p)
            if p.gen != gen or p.state != "open":
                continue                                  # changed meanwhile (resync)
            if kind == "accepted":
                self._apply(p, kind, info)
            elif kind == "waiting":
                continue
            elif kind == "rejected":
                self._apply(p, kind, info)
                await self._resync(p, None, deadline)
                return False
            elif kind == "high" and idx < len(self.retry_delays):
                await asyncio.sleep(self.retry_delays[idx])
                idx += 1
                await self._resend(p, gen)                # the same signed bytes
            elif kind in ("high", "stale"):               # symptom of an earlier gap, or nonce taken
                await self._resync(p, gen, deadline)
                if p.gen == gen and p.state == "open":
                    await asyncio.sleep(0.2)              # resync could not act; do not spin
            elif kind == "unproven":
                await asyncio.sleep(0.1)                  # wait for the receipt to be indexed
            else:                                         # lost
                try:
                    await self._reconnected(conn_gen, deadline - time.monotonic())
                except TimeoutError:
                    res.error, p.state = "unresolved: connection lost", "failed"
                    return False
                await self._resend(p, gen, only_if_idle=True)

    async def _quiesce(self, deadline):
        """Wait until every open payment's sends have a final node answer (no send pending, so
        nothing can still be held in the sequencer). Caller holds the send lock: no new sends.
        Waits at most reply_timeout (counted in `quiesce_timeouts`): the lock must not be held
        for minutes, and an unproven payment is never re-signed."""
        limit = min(deadline, time.monotonic() + self.reply_timeout)
        while True:
            pending = [s.fut for q in self._open_payments.values() if q.state == "open"
                       for s in q.sends if not s.fut.done()]
            if not pending:
                break
            remaining = limit - time.monotonic()
            if remaining <= 0:
                self.quiesce_timeouts += 1
                raise TimeoutError("quiesce")
            await asyncio.wait(pending, timeout=remaining)
        for q in self._open_payments.values():            # the MOST RECENT send needs a real answer
            cur = [s for s in q.sends if s.gen == q.gen]
            if q.state == "open" and (not cur or cur[-1].fut.exception() is not None):
                raise ConnectionError("send lost with the connection")

    async def _resync(self, trigger: _Payment, gen, deadline):
        """Quiesce, read the pending nonce, then re-sign every open payment whose current bytes
        are dead, in original order, from that nonce. Invariant: a payment has at most one
        signed version the node may still execute; a new version is signed only after the old
        one is proven dead (see _judge). Retried until done or the payment deadline."""
        while time.monotonic() < deadline:
            conn_gen = self._conn_gen
            try:
                async with self._send_lock:
                    if gen is not None and (trigger.gen != gen or trigger.state != "open"):
                        return                            # an earlier resync already handled it
                    await self._quiesce(deadline)
                    n = int(await self._rpc("eth_getTransactionCount", [self.account.address, "pending"]), 16)
                    plan, unsettled = [], False
                    for q in list(self._open_payments.values()):
                        if q.state != "open":
                            continue
                        kind, info = await self._judge(q)
                        if kind in ("accepted", "rejected"):
                            self._apply(q, kind, info)
                        elif kind in ("stale", "high"):
                            plan.append(q)
                        elif kind == "lost":
                            raise ConnectionError("no usable answer")
                        else:                             # waiting / unproven: not proven dead
                            unsettled = True
                    if not unsettled:
                        self.resyncs += 1
                        for q in plan:
                            q.raw, q.tx_hash = self._signer.sign(dict(q.tx_fields, nonce=n, chainId=self.chain_id))
                            q.nonce, q.gen, q.sends = n, q.gen + 1, []     # every old send is answered
                            q.result.resigned += 1
                            n += 1
                            await self._send_raw(q)
                        self._nonce = n
                        return
                await asyncio.sleep(0.1)                  # something is still unproven: retry later
            except ConnectionError:
                try:
                    await self._reconnected(conn_gen, deadline - time.monotonic())
                except (ConnectionError, TimeoutError):
                    return
            except TimeoutError:
                await asyncio.sleep(0.2)                  # never re-sign an unproven payment
            except RpcError:
                await asyncio.sleep(0.2)

    # ---- receipts -----------------------------------------------------------------------------

    async def _receipt_phase(self, p: _Payment):
        p.receipt_deadline = time.monotonic() + self.receipt_timeout
        p.last_check = time.monotonic()
        p.receipt_fut = asyncio.get_running_loop().create_future()
        hit = self._cache.get(p.result.tx_hash)
        if hit:
            self._resolve(p, *hit)
        else:
            self._open_hashes[p.result.tx_hash] = p
        try:
            await asyncio.wait_for(asyncio.shield(p.receipt_fut), self.receipt_timeout + 1.0)
        except asyncio.TimeoutError:
            p.result.error = p.result.error or "unresolved: no receipt"

    def _resolve(self, p: _Payment, r: dict, t: float):
        res = p.result
        res.t_confirmed = t
        res.status = int(r["status"], 16)
        res.block = int(r["blockNumber"], 16)
        res.gas_used = int(r["gasUsed"], 16)
        res.effective_gas_price = int(r.get("effectiveGasPrice", "0x0"), 16)
        self._open_hashes.pop(res.tx_hash, None)
        if not p.receipt_fut.done():
            p.receipt_fut.set_result(None)

    async def _receipt_loop(self):
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), 0.1)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            conn_gen = self._conn_gen
            try:
                await self._check_receipts()
            except ConnectionError:
                try:
                    await self._ensure_connected(conn_gen)
                except ConnectionError:
                    return
            except Exception:
                pass                                      # transient: try again, the deadline decides
            now = time.monotonic()
            for h, p in list(self._open_hashes.items()):
                if now > p.receipt_deadline:
                    p.result.error = "unresolved: no receipt"
                    self._open_hashes.pop(h, None)
                    if not p.receipt_fut.done():
                        p.receipt_fut.set_result(None)

    def _take_receipt(self, r, t):
        h = r["transactionHash"]
        self._cache[h] = (r, t)
        if len(self._cache) > 4096:
            self._cache.popitem(last=False)
        p = self._open_hashes.get(h)
        if p:
            self._resolve(p, r, t)

    def _latches(self, e: RpcError):
        """Whether a per-head RPC error means the node lacks the method (latch to per-hash polling).
        Block mode (Arbitrum, eth_getBlockReceipts): code -32601 or a "missing method" text,
        unchanged. Own mode (eth_getBlockByNumber, which every node has): code -32601 only --
        a transient "block not found" must never switch to 0.05 s polling (final review, Important 1)."""
        if e.code == -32601:
            return True
        if self.receipt_mode == "own":
            return False
        return any(w in str(e).lower() for w in ("not found", "not supported", "does not exist"))

    def _latch(self, e: RpcError):
        """Switch to per-hash polling for the rest of the run, and record why (lifecycle.json)."""
        if self._block_receipts_ok:
            self._block_receipts_ok = False
            self.receipt_fallback = True
            self.receipt_fallback_error = f"{e.code}: {e}"
            log.warning("per-head receipt method unavailable (%s); polling receipts per hash", self.receipt_fallback_error)

    def _own_hashes(self) -> set:
        """Every hash a block could hold for this submitter: each signed version of each
        unfinished payment (a payment is registered before its first send) and every
        accepted hash still waiting for a receipt."""
        own = set(self._open_hashes)
        for p in self._open_payments.values():
            own.add(p.tx_hash)
            own.update(s.tx_hash for s in p.sends)
            if p.result.tx_hash:
                own.add(p.result.tx_hash)
        return own

    async def _own_receipts(self, heads):
        """receipt_mode "own": the heads' tx hash lists, then only this submitter's receipts."""
        blocks = await asyncio.gather(*[self._rpc("eth_getBlockByNumber", [hex(b), False]) for b in heads],
                                      return_exceptions=True)
        own, wanted = self._own_hashes(), []
        for blk in blocks:
            if isinstance(blk, ConnectionError):
                raise blk
            if isinstance(blk, RpcError):
                if self._latches(blk):
                    self._latch(blk)                      # latch only if the node lacks the method
            elif isinstance(blk, dict):
                for h in blk.get("transactions") or []:
                    h = h if isinstance(h, str) else h.get("hash")
                    if h in own and h not in self._cache and h not in wanted:
                        wanted.append(h)
        if not wanted:
            return
        results = await asyncio.gather(*[self._rpc("eth_getTransactionReceipt", [h]) for h in wanted],
                                       return_exceptions=True)
        t = time.time()
        for r in results:
            if isinstance(r, ConnectionError):
                raise r
            if isinstance(r, dict):
                self._take_receipt(r, t)

    async def _check_receipts(self):
        heads, self._heads = sorted(self._heads), set()
        if heads and self._block_receipts_ok and self.receipt_mode == "own":
            await self._own_receipts(heads)
        elif heads and self._block_receipts_ok:
            results = await asyncio.gather(*[self._rpc("eth_getBlockReceipts", [hex(b)]) for b in heads],
                                           return_exceptions=True)
            for r in results:
                if isinstance(r, ConnectionError):
                    raise r
                if isinstance(r, RpcError):
                    if self._latches(r):
                        self._latch(r)                    # latch only if the node lacks the method
                elif isinstance(r, list):
                    t = time.time()
                    for rec in r:
                        self._take_receipt(rec, t)
        interval = self.receipt_sweep_s if (self._subscribed and self._block_receipts_ok) else 0.05
        now = time.monotonic()
        due = [p for p in self._open_hashes.values() if now - p.last_check >= interval]
        if due:
            for p in due:
                p.last_check = now
            results = await asyncio.gather(*[self._rpc("eth_getTransactionReceipt", [p.result.tx_hash])
                                             for p in due], return_exceptions=True)
            for p, r in zip(due, results):
                if isinstance(r, ConnectionError):
                    raise r
                if isinstance(r, dict) and p.result.tx_hash in self._open_hashes:
                    self._resolve(p, r, time.time())
