# measure/payclient.py
"""Local payment client: the only code on a board that talks to the system
under test. It receives payment events (JSON lines, Unix socket) from the
use-case code, records each in the payment ledger and executes it. The same
interface serves UC1 and UC2; this file implements the rollup side for
Arbitrum and zkSync Era (cluster spec §7.3), selected with --system (the
system module is measure/arbitrum.py or measure/zksync.py). --system perun
has its own lifecycle (a Go sidecar, direct channels): run() hands it to
measure.perun.main_async, which reuses serve_socket/accept/pay_loop.

Lifecycle for one run: Open (timed, the account is idle) -> only then create
the socket and write DIR/ready -> pay events as they arrive -> on
{"end": true} drain for at most --drain seconds -> Close (timed) ->
lifecycle.json. lifecycle.json also records the nonce bookkeeping:
start_nonce (pending nonce when the submitter starts), submitted_nonce (the
submitter's own next_nonce, captured before it is torn down: every nonce
below this was signed and sent over its WebSocket, with no RPC lag),
pending_before_close (pending - latest nonce just before Close; 0 = every
sent payment executed), close_nonce, and executed_onchain = close_nonce -
start_nonce. Before Close, payclient waits (at most ADMISSION_WAIT_S, polling
every ADMISSION_POLL_S) for the node's pending nonce to reach submitted_nonce:
the node may still be admitting payments the submitter just sent (task 10d;
cluster spec §8 step 3, "Close-nonce collision"),
and a Close below that nonce would collide with, and orphan, one of them. If
it gets there, Close's nonce is >= submitted_nonce. If it does not, Close
takes the node's pending nonce (a nonce above it would leave a gap and the
node rejects Close with "nonce too high", as in run
20261003-024642-arbitrum-uc1-arbitrum5-w10-rep3), and lifecycle.json records
not_admitted = submitted_nonce - pending (0 when it got there) and
admission_wait_s. The node's value is handed to close_withdraw as min_nonce
(its own pending read can only be later, so max() is a guard, never a gap).
not_admitted payments were not admitted before Close; that does not prove
they never execute: zkSync's pending nonce stops at the first missing nonce,
so later payments may sit queued behind it and mine right after Close fills
the gap. So after the Close receipt payclient follows the latest nonce (at
most SETTLE_MAX_S, stopping early once it reaches max(submitted_nonce,
close_nonce + 1) or has not moved for SETTLE_QUIET_S) and records
final_nonce, settle_wait_s and executed_after_close = final_nonce -
(close_nonce + 1): the payments that executed after Close. executed_onchain
keeps its meaning (nonces below Close); executed_onchain +
executed_after_close is every payment that executed (analyze_uc1 counts
both). On Arbitrum executed_after_close reads 0 (its sequencer does not hold
nonces behind a gap).

zkSync: --receipt-timeout must exceed the whole run (the env files set
RECEIPT_TIMEOUT_S=1200). zkSync allows at most 20 transactions in flight per
account (spec §7.3) and --max-in-flight is set to that bound; a per-payment
receipt timeout shorter than the run would free an in-flight slot while the
payment is still in the mempool, so a 21st transaction would be sent and the
bound broken. --close-timeout bounds the wait for the Close receipt (zkSync
only; Arbitrum's Close keeps its fixed 300 s).

UC2 on the rollups (--asset points, default eth = UC1 unchanged): each event
is an ERC-20 PointsToken.transfer (measure/points.py) to the address that
--recipients maps its logical `to` ("arena", "p<k>") to; amount_wei carries
points. --pay-to is not needed. lifecycle.json gains points_before_close (the
account's balanceOf just before Close, read with read_retry; a failed read is
a missing fact, so the run is not complete). The reconciliation stays
client-side; the cross-board one is the analysis's job.

--receipt-mode overrides OrderedSubmitter's receipt_mode ("block": per new
head, eth_getBlockReceipts for the whole block; "own": the block's tx-hash
list, then only this account's receipts -- same detection moment, far less
traffic). Default (None) leaves the system module's own SUBMITTER_KW choice
unchanged (arbitrum: block, zksync: own already). Not applicable to --system
perun (no OrderedSubmitter there); rejected by parse_args.

Crash safety: the ledger appends to ledger.log.jsonl during the run and
ledger.csv is built at the end (from whatever is known, also after a failure);
lifecycle.json has complete=false until the very end, and stays false, with
"open_error" / "close_error", if Open or Close failed. The exit code is then
non-zero. The RPC reads around Close (balance and pending/latest nonce before
Close, Arbitrum's latest nonce after it) are retried RPC_RETRY_DELAYS; a nonce
read that still fails is recorded as "nonce_read_error" and the run is not
complete. A failed pre-Close nonce read does not skip Close: those reads are
diagnostics (pending_before_close), close_withdraw reads its own nonce, and
Close still returns the funds and pins close_nonce/executed_onchain. The same
holds for a pending read that fails during the admission wait (not_admitted
null): Close then runs with no lower bound from payclient, taking the node's
pending nonce as read by close_withdraw, a trade-off of a possible collision
with a payment still being admitted against the certain gap a guessed bound
could leave; the run is incomplete either way. A failed final_nonce read
(post-Close) likewise leaves executed_after_close null and the run
incomplete.

Example (--l2 is the sequencer WebSocket, the HTTP endpoint is derived):
  python3 -m measure.payclient --system arbitrum --l1 http://192.168.178.210:8545 \
    --l2 ws://192.168.178.210:8548 --account 101 --pay-to 0x.. \
    --deposit-wei 1000000000000000000 --out DIR
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time

from web3 import Web3

from measure import arbitrum, keys, points, zksync
from measure.events import PaymentEvent
from measure.ledger import PaymentLedger
from measure.submitter import OrderedSubmitter, SubmitResult

log = logging.getLogger("payclient")

SYSTEMS = {"arbitrum": arbitrum, "zksync": zksync}
RPC_RETRY_DELAYS = (0.5, 1.0, 2.0)          # backoff between attempts of one RPC read around Close
ADMISSION_WAIT_S = 30.0                     # max wait before Close for the node to admit every sent payment
ADMISSION_POLL_S = 0.5
SETTLE_MAX_S = 30.0                         # max wait after Close for payments queued behind it to mine
SETTLE_QUIET_S = 3.0                        # ... stopping early once the latest nonce has not moved this long
SETTLE_POLL_S = 0.5


async def read_retry(what, fn, *args):
    """fn(*args) in a thread, retried after each of RPC_RETRY_DELAYS; the last failure is raised."""
    for delay in (*RPC_RETRY_DELAYS, None):
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as e:
            if delay is None:
                raise
            log.warning("%s failed (%s: %s); retrying in %.1f s", what, type(e).__name__, e, delay)
            await asyncio.sleep(delay)


async def wait_for_admission(read_pending, submitted, wait_s=None, poll_s=None):
    """Poll read_pending() (the node's pending nonce, each read retried as read_retry)
    until it is >= submitted, for at most wait_s seconds. Returns (the last pending
    nonce read, seconds waited). A value below `submitted` means the node had not
    admitted the rest by then (dropped, or on zkSync possibly queued behind a missing
    nonce: see settle_latest). The bound is checked between reads, so a read that
    needs its retries can overrun it by those. A read that still fails after its
    retries is raised."""
    wait_s = ADMISSION_WAIT_S if wait_s is None else wait_s
    poll_s = ADMISSION_POLL_S if poll_s is None else poll_s
    t0 = time.monotonic()
    while True:
        pending = await read_retry("pending nonce (admission wait)", read_pending)
        waited = time.monotonic() - t0
        if pending >= submitted or waited >= wait_s:
            return pending, waited
        await asyncio.sleep(min(poll_s, max(wait_s - waited, 0)))


async def settle_latest(read_latest, target, quiet_s=None, max_s=None, poll_s=None):
    """After Close: poll read_latest() (the latest nonce, each read retried as read_retry)
    until it reaches target (every nonce that can still execute), has not changed for
    quiet_s seconds, or max_s seconds have passed (each bound checked between reads).
    Returns (the last latest nonce read, seconds waited). A failed read is raised."""
    quiet_s = SETTLE_QUIET_S if quiet_s is None else quiet_s
    max_s = SETTLE_MAX_S if max_s is None else max_s
    poll_s = SETTLE_POLL_S if poll_s is None else poll_s
    t0 = time.monotonic()
    last, since = None, t0
    while True:
        latest = await read_retry("latest nonce (post-Close settle)", read_latest)
        now = time.monotonic()
        if latest != last:
            last, since = latest, now
        if latest >= target or now - since >= quiet_s or now - t0 >= max_s:
            return latest, now - t0
        await asyncio.sleep(poll_s)


async def serve_socket(path, on_event, on_end, listening: asyncio.Event | None = None):
    """Serve JSON lines on a Unix socket until cancelled. A malformed line is
    logged and skipped; the end marker only closes that connection. `listening`
    is set once the socket is bound (any stale file was unlinked first)."""
    if os.path.exists(path):
        os.unlink(path)

    async def handle(reader, writer):
        try:
            while line := await reader.readline():
                try:
                    obj = json.loads(line)
                    if obj.get("end"):
                        on_end()
                        break
                    ev = PaymentEvent(**obj)
                except (ValueError, TypeError, AttributeError) as e:
                    log.warning("skipping malformed line %r: %s", line[:200], e)
                    continue
                await on_event(ev)
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, path)
    if listening is not None:
        listening.set()
    async with server:
        await server.serve_forever()


class EventQueue(asyncio.Queue):
    """Queue of accepted events; the None end marker also stamps `ended_at`
    (monotonic) and sets `ended`, so the drain clock starts at the marker even
    while pay_loop is blocked in submit()."""

    def __init__(self):
        super().__init__()
        self.ended = asyncio.Event()
        self.ended_at = None

    def put_nowait(self, item):
        if item is None:
            self.ended_at = time.monotonic()
            self.ended.set()
        super().put_nowait(item)


def accept(ledger, queue, ev) -> str:
    """Record an event in the ledger on arrival (t_queued = arrival) and
    enqueue it. The ledger's max_queue bounds queued + in-flight rows.
    Returns 'queued', 'full', 'duplicate' or 'late' (after the end marker)."""
    late = getattr(queue, "ended", None) is not None and queue.ended.is_set()
    if not ledger.add(ev):
        return "duplicate"                                    # never pay twice
    if late:                                                  # recorded, never paid
        ledger.finish(ev.id, SubmitResult(status=0, error="after end"))
        return "late"
    if ledger.outcome(ev.id) == "failed":                     # queue full
        return "full"
    ledger.queued(ev.id)
    queue.put_nowait(ev)
    return "queued"


async def pay_loop(ledger, submitter, make_tx, events: asyncio.Queue, drain_s, at_deadline=None):
    """Submit accepted events (already in the ledger). The drain clock starts
    at the end marker; at the deadline stop submitting, cancel the trackers and
    mark every row still pending unresolved.

    at_deadline: an optional plain (not async) callable run once at the
    deadline, before the pending rows are marked unresolved (Perun sends
    quiesce there, so the sidecar stops starting queued pays at once). It must
    not yield to the event loop, so no tracker can record an outcome between
    the deadline and unresolve_pending; if it raises, the error is logged and
    the drain goes on unchanged. None (Arbitrum, zkSync) changes nothing."""
    inflight: set = set()

    async def track(ev_id, fut):
        res = await fut
        ledger.finish(ev_id, res)

    async def submit_all():
        while True:
            ev = await events.get()
            if ev is None:
                return
            try:
                fut = await submitter.submit(make_tx(ev))
            except Exception as e:                            # failed before sending
                log.exception("submit failed for %s", ev.id)
                ledger.finish(ev.id, SubmitResult(t_confirmed=time.time(), status=0,
                                                  error=f"submit failed: {type(e).__name__}: {e}"))
                continue
            if fut.done():                                    # resolved before sending: pre-send failure
                ledger.finish(ev.id, fut.result())
                continue
            ledger.submitted(ev.id, time.time())
            t = asyncio.create_task(track(ev.id, fut))
            inflight.add(t)
            t.add_done_callback(inflight.discard)

    worker = asyncio.create_task(submit_all())
    try:
        ended = getattr(events, "ended", None)
        if ended is not None:
            waiter = asyncio.create_task(ended.wait())
            await asyncio.wait({worker, waiter}, return_when=asyncio.FIRST_COMPLETED)
            waiter.cancel()
        if ended is not None and ended.is_set():
            deadline = events.ended_at + drain_s
        else:
            await asyncio.wait({worker})                      # plain queue: clock starts at the marker
            deadline = time.monotonic() + drain_s
        if not worker.done():
            await asyncio.wait({worker}, timeout=max(0.0, deadline - time.monotonic()))
        if inflight:
            await asyncio.wait(set(inflight), timeout=max(0.0, deadline - time.monotonic()))
        if at_deadline is not None:
            try:
                at_deadline()
            except Exception:
                log.exception("at_deadline hook failed")
        ledger.unresolve_pending()
        if worker.done() and not worker.cancelled() and worker.exception() is not None:
            raise worker.exception()                          # never report a clean run after a worker crash
    finally:
        # A late result must not give an already-unresolved row a second outcome.
        rest = [worker, *inflight]
        for t in rest:
            t.cancel()
        await asyncio.gather(*rest, return_exceptions=True)


def write_json(path, obj):
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(path + ".tmp", path)


async def main_async(a) -> int:
    """Run the lifecycle; returns the process exit code (0 only if complete)."""
    os.makedirs(a.out, exist_ok=True)
    life_path = os.path.join(a.out, "lifecycle.json")
    ready_path = os.path.join(a.out, "ready")
    if os.path.exists(ready_path):
        os.unlink(ready_path)                                 # stale from an earlier run
    life = {"system": a.system, "account": a.account, "complete": False,
            "max_in_flight": a.max_in_flight, "max_queue": a.max_queue, "drain_s": a.drain,
            "duplicates": 0, "late": 0, "receipt_timeout": a.receipt_timeout}
    write_json(life_path, life)
    ledger = PaymentLedger(os.path.join(a.out, "ledger.log.jsonl"), max_queue=a.max_queue)
    sub = None
    sock = None

    async def close_sub():
        nonlocal sub
        if sub is None:
            return
        s_, sub = sub, None
        try:
            await s_.close()
        except Exception:
            log.exception("submitter close failed")
        for k in ("resyncs", "reconnects", "outage_seconds", "quiesce_timeouts",
                  "receipt_mode", "receipt_fallback", "receipt_fallback_error"):   # receipt path actually used
            life[k] = getattr(s_, k, None)

    try:
        mod = SYSTEMS[a.system]
        acct = keys.account(a.account)
        l1 = Web3(Web3.HTTPProvider(a.l1))
        l2http = Web3(Web3.HTTPProvider(mod.http_url(a.l2)))
        # Open credits are detected as a balance delta: the account must be idle.
        points_mode = getattr(a, "asset", "eth") == "points"
        recipients = points.load_recipients(a.recipients, a.players) if points_mode else None   # fail before Open
        before = await asyncio.to_thread(l2http.eth.get_balance, acct.address)
        if before != 0:
            life["open_warning"] = f"L2 balance before Open was {before} wei, not 0"
            log.warning(life["open_warning"])
        try:
            life["open"] = await asyncio.to_thread(mod.open_deposit, l1, l2http, acct, a.deposit_wei)
        except Exception as e:
            life["open_error"] = f"{type(e).__name__}: {e}"
            log.exception("Open failed")
            return 1
        finally:
            write_json(life_path, life)
        if points_mode:
            life["asset"], life["token"] = "points", a.token
            gas = await asyncio.to_thread(points.token_gas, mod, l2http, acct.address, a.token,
                                          recipients["arena"])
        else:
            gas = await asyncio.to_thread(mod.estimate_payment_gas, l2http, acct.address, a.pay_to)
        # FEE_CAP_MULTIPLIER (measure/arbitrum.py): admission ceiling only, taken once for the whole run,
        # same constant zksync.py imports -- the charged price is always the base fee, never this cap.
        max_fee = arbitrum.FEE_CAP_MULTIPLIER * (await asyncio.to_thread(lambda: l2http.eth.gas_price)) + 1
        life["fee_cap_wei"] = max_fee
        submitter_kw = dict(getattr(mod, "SUBMITTER_KW", {}))
        if getattr(a, "receipt_mode", None) is not None:          # --receipt-mode overrides the module's choice
            submitter_kw["receipt_mode"] = a.receipt_mode
        sub = OrderedSubmitter(a.l2, acct, mod.L2_CHAIN_ID, a.max_in_flight,
                               receipt_timeout=a.receipt_timeout, **submitter_kw)
        await sub.start()
        life["start_nonce"] = sub.start_nonce
        q = EventQueue()

        async def on_event(ev):
            r = accept(ledger, q, ev)
            if r == "duplicate":
                life["duplicates"] += 1
                log.warning("duplicate event id %s ignored", ev.id)
            elif r == "late":
                life["late"] += 1
                log.warning("event %s after the end marker recorded as failed", ev.id)

        listening = asyncio.Event()
        sock = asyncio.create_task(serve_socket(a.socket, on_event, lambda: q.put_nowait(None), listening))
        waiter = asyncio.create_task(listening.wait())
        await asyncio.wait({sock, waiter}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        if sock.done():
            sock.result()
        open(ready_path, "w").close()
        life["ready_at"] = time.time()
        write_json(life_path, life)
        if points_mode:
            def make_tx(e):
                return points.token_tx(a.token, recipients, e, gas, max_fee)
        else:
            def make_tx(e):
                return mod.payment_tx(e.to, e.amount_wei, gas, max_fee)
        await pay_loop(ledger, sub, make_tx, q, a.drain)
        life["ended_at"] = time.time()
        sock.cancel()
        await asyncio.gather(sock, return_exceptions=True)
        sock = None
        life["submitted_nonce"] = sub.next_nonce              # authoritative bound: capture before teardown
        await close_sub()                                     # no _follow/resync may race Close's nonce
        ledger.write_csv(os.path.join(a.out, "ledger.csv"))
        spent = sum(int(r["amount_wei"]) for r in ledger.rows() if r["outcome"] == "succeeded")
        life["reconciliation"] = dict(ledger.reconcile(observed_wei=spent), observed_source="client")
        life["payment_gas_limit"] = gas
        nonce_errors = []

        def nonce_error(what, e):
            nonce_errors.append(f"{what}: {type(e).__name__}: {e}")
            life["nonce_read_error"] = "; ".join(nonce_errors)
            log.error("%s not read: %s: %s", what, type(e).__name__, e)

        close_kw = {"timeout": a.close_timeout} if a.system != "arbitrum" else {}
        try:                                                  # never Close above the node's own pending nonce
            admitted, life["admission_wait_s"] = await wait_for_admission(
                lambda: l2http.eth.get_transaction_count(acct.address, "pending"), life["submitted_nonce"])
            life["not_admitted"] = max(life["submitted_nonce"] - admitted, 0)
            close_kw["min_nonce"] = admitted
            if life["not_admitted"]:
                log.warning("%d sent payment(s) not admitted by the node after %.1f s: Close uses nonce %d",
                            life["not_admitted"], life["admission_wait_s"], admitted)
        except Exception as e:                                # Close still reads the node's pending nonce itself
            life["not_admitted"] = None
            nonce_error("not_admitted", e)

        try:
            left = await read_retry("balance before Close", l2http.eth.get_balance, acct.address)
        except Exception as e:                                # Close needs the amount: cannot proceed
            life["close_error"] = f"balance before Close not read: {type(e).__name__}: {e}"
            log.error(life["close_error"])
            return 1
        if points_mode:                                       # the account's points, a fact the run needs
            try:
                raw = await read_retry("points before Close", l2http.eth.call,
                                       {"to": a.token, "data": points.balance_of_data(acct.address)})
                life["points_before_close"] = int.from_bytes(raw, "big")
            except Exception as e:
                life["points_before_close"] = None
                nonce_error("points_before_close", e)
        try:                                                  # diagnostics only: Close reads its own nonce
            pending = await read_retry("pending nonce before Close", l2http.eth.get_transaction_count,
                                       acct.address, "pending")
            latest = await read_retry("latest nonce before Close", l2http.eth.get_transaction_count,
                                      acct.address, "latest")
            life["pending_before_close"] = pending - latest
        except Exception as e:
            life["pending_before_close"] = None
            nonce_error("pending_before_close", e)
        try:
            life["close"] = await asyncio.to_thread(mod.close_withdraw, l2http, acct, left // 2, **close_kw)
        except Exception as e:
            life["close_error"] = f"{type(e).__name__}: {e}"
            log.exception("Close failed")
            return 1
        close_nonce = life["close"].get("nonce")
        if close_nonce is None:                               # Arbitrum: Close is the last mined tx
            try:
                close_nonce = await read_retry("latest nonce after Close", l2http.eth.get_transaction_count,
                                               acct.address, "latest") - 1
            except Exception as e:
                nonce_error("close_nonce", e)
        life["close_nonce"] = close_nonce
        life["executed_onchain"] = close_nonce - life["start_nonce"] if close_nonce is not None else None
        life["final_nonce"] = life["executed_after_close"] = None
        if close_nonce is not None:                           # payments queued behind Close may mine after it
            try:
                life["final_nonce"], life["settle_wait_s"] = await settle_latest(
                    lambda: l2http.eth.get_transaction_count(acct.address, "latest"),
                    max(life["submitted_nonce"], close_nonce + 1))
                life["executed_after_close"] = life["final_nonce"] - (close_nonce + 1)
                if life["executed_after_close"]:
                    log.warning("%d payment(s) executed after Close (nonce %d)",
                                life["executed_after_close"], close_nonce)
            except Exception as e:
                nonce_error("final_nonce", e)
        if life["close"].get("status") != 1:
            life["close_error"] = f"withdrawal reverted (status {life['close'].get('status')})"
            return 1
        if nonce_errors:                                      # a missing fact is a failure, never complete
            return 1
        life["complete"] = True
        return 0
    except BaseException as e:
        life.setdefault("error", f"{type(e).__name__}: {e}")
        raise
    finally:
        try:
            asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)   # cleanup must finish
        except (NotImplementedError, RuntimeError, ValueError):
            pass
        if sock is not None:
            sock.cancel()
        await close_sub()
        try:
            ledger.write_csv(os.path.join(a.out, "ledger.csv"))
        except Exception:
            log.exception("ledger.csv not written")
        ledger.close()
        write_json(life_path, life)


async def run(a) -> int:
    """main_async with SIGTERM turned into cancellation, so the finally
    blocks still write lifecycle.json and ledger.csv. --system perun goes to
    measure.perun.main_async (its own lifecycle: a sidecar, not a SYSTEMS module)."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        if a.system == "perun":
            if getattr(a, "use_case", "uc1") == "uc2":
                from measure import perun_uc2             # imports perun, which imports payclient: lazily
                return await perun_uc2.main_async(a)
            from measure import perun                     # perun imports payclient: import here
            return await perun.main_async(a)
        return await main_async(a)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=sorted(SYSTEMS) + ["perun"], required=True)
    ap.add_argument("--l1", help="L1 HTTP endpoint (arbitrum, zksync: required)")
    ap.add_argument("--l2", help="sequencer WebSocket, e.g. ws://192.168.178.210:8548 (arbitrum, zksync: required)")
    ap.add_argument("--chain", help="perun: the chain's WebSocket, e.g. ws://192.168.178.210:8545")
    ap.add_argument("--chain-id", type=int, help="perun: chain id")
    ap.add_argument("--sensor-control", help="perun: the uc1-sensor's control URL, e.g. http://192.168.178.211:8090")
    ap.add_argument("--sidecar", help="perun: path of the perun-node binary")
    ap.add_argument("--wire-bind", help="perun: the sidecar's Perun wire listen address")
    ap.add_argument("--wire-endpoint", help="perun: the address the sensor dials back")
    ap.add_argument("--update-timeout", type=float, default=10, help="perun: channel update timeout, s")
    ap.add_argument("--persist", type=int, choices=(0, 1), default=1, help="perun: persist channel state")
    ap.add_argument("--use-case", choices=("uc1", "uc2"), default="uc1",
                    help="perun: uc2 drives the uc2-player sidecar (needs --perun-config)")
    ap.add_argument("--perun-config", help="perun uc2: the cluster config JSON of the run")
    ap.add_argument("--open-at", type=float, default=None,
                    help="perun uc2: begin the Open at this Unix time (all players at once), not at start")
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--asset", choices=("eth", "points"), default="eth",
                    help="eth: native payments to --pay-to (UC1). points: PointsToken.transfer to the "
                         "logical recipients of --recipients (UC2)")
    ap.add_argument("--token", help="points: the PointsToken address")
    ap.add_argument("--recipients", help="points: JSON file, logical name (arena, p0..) -> address")
    ap.add_argument("--players", type=int, default=6, help="points: players that must be in --recipients")
    ap.add_argument("--pay-to", help="eth: recipient address (required)")
    ap.add_argument("--deposit-wei", type=int, required=True)
    ap.add_argument("--max-in-flight", type=int, default=64)
    ap.add_argument("--max-queue", type=int, default=10000)
    ap.add_argument("--drain", type=float, default=60)
    ap.add_argument("--receipt-timeout", type=float, default=120,
                    help="per-payment receipt timeout, s (zkSync: longer than the run, see above)")
    ap.add_argument("--close-timeout", type=float, default=300, help="Close receipt timeout, s (zkSync)")
    ap.add_argument("--receipt-mode", choices=("block", "own"), default=None,
                    help="override the system module's OrderedSubmitter receipt_mode (own = per-account "
                         "receipts, far less traffic than block). Default: the module's own choice "
                         "(arbitrum: block, zksync: own). Not applicable to --system perun.")
    ap.add_argument("--socket", default="/tmp/measure-pay.sock")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.use_case == "uc2" and a.system != "perun":
        ap.error("--use-case uc2 is only for --system perun (the rollups use --asset points)")
    if a.system == "perun" and a.use_case == "uc2":
        need = ("sidecar", "perun_config")          # the cluster config carries the chain and the wire
        if a.receipt_mode is not None:
            ap.error("--receipt-mode is not applicable to --system perun")
    elif a.system == "perun":
        need = ("chain", "chain_id", "sensor_control", "sidecar", "wire_bind", "wire_endpoint")
        if a.receipt_mode is not None:
            ap.error("--receipt-mode is not applicable to --system perun (it has its own lifecycle, no "
                     "OrderedSubmitter)")
    else:
        need = ("l1", "l2")
    if a.system != "perun":
        need = need + (("token", "recipients") if a.asset == "points" else ("pay_to",))
    elif a.use_case == "uc1" and a.pay_to is None:
        ap.error("--system perun requires --pay-to")
    missing = ["--" + k.replace("_", "-") for k in need if getattr(a, k) is None]
    if missing:
        ap.error(f"--system {a.system} requires " + ", ".join(missing))
    return a


def main():
    a = parse_args()
    os.makedirs(a.out, exist_ok=True)
    logging.basicConfig(filename=os.path.join(a.out, "payclient.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        code = asyncio.run(run(a))
    except asyncio.CancelledError:
        code = 143
    sys.exit(code)


if __name__ == "__main__":
    main()
