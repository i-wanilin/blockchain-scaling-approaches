"""The Perun side of the payment client (UC1): an adapter that drives the Go
sidecar `perun-node -mode uc1-customer` (perun/hub-demo/uc1_customer.go) and
looks to payclient's unchanged pay_loop/accept/EventQueue and PaymentLedger
like any other submitter.

The sidecar speaks JSON lines: one request per stdin line, one reply per
request on stdout, in request order. `PerunSidecar` keeps one reader task
that dispatches the replies: a `pay` reply by its id, every other reply to the
oldest open request (FIFO). Pays are pipelined (the sidecar runs them one at
a time through its own FIFO); `init`/`open`/`state`/`quiesce`/`close`/`exit`
go through `request`, which always has a timeout: a request that is not
answered in time kills the sidecar and raises, so a wedged sidecar can never
hang the run (the lifecycle records open_error/close_error).

A pay never times out on its own: pay_loop's drain deadline bounds it (the
row is unresolved then, and the late reply is ignored because the tracker is
cancelled), and `quiesce` makes the sidecar drop every queued pay so none of
them starts after the deadline. The sidecar's `state` reply does not go
through its pay queue, so it can be stale while pays are pipelined: ask for
it only after quiesce.

Outcomes are classified by the error's prefix, never by its full text (the
sidecar appends reasons): "unresolved:" -> unresolved (an update that timed
out may be signed by the sensor, Review Focus 1); "rejected:", "channel
broken:", "channel empty" -> failed; anything else is unresolved as well,
because a reply we cannot place may hide a payment the sensor holds. If the
sidecar exits (or breaks the protocol: a non-JSON line on stdout, a reply
nobody asked for), every open pay and request resolves
"unresolved: sidecar exited (rc N)" (or "unresolved: sidecar protocol broken:
..."); a pay asked for after that is "not sent: ..." and so failed, because
it never reached a process that could execute it.

Timestamps: t_submitted and t_confirmed come from the sidecar (Go's
time.Now(), as Unix seconds), t_intended from Python's time.time(), on the
same board; test_perun checks that the two clocks agree within 1 ms.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import shutil
import signal
import time

from web3 import Web3

from measure import keys
from measure.ledger import PaymentLedger
from measure.submitter import SubmitResult

log = logging.getLogger("perun")

LINE_LIMIT = 1 << 20                     # a close reply with by_contract fits easily
FAILED_PREFIXES = ("rejected:", "channel broken:", "channel empty", "not sent:")
STOP_TIMEOUT_S = 15.0                    # > the sidecar's quiesce wait (update timeout 10 s + 1 s)

# Lifecycle request timeouts (a request timeout kills the sidecar, see PerunSidecar.request).
STARTUP_TIMEOUT_S = 120                  # the sidecar's own bound on reaching the sensor (init)
INIT_TIMEOUT_S = STARTUP_TIMEOUT_S + 10
OPEN_TIMEOUT_S = 300
STATE_TIMEOUT_S = 30
CLOSE_TIMEOUT_S = 360                    # > Go's own 300 s settle timeout (uc1ChannelBackend.closeTO) + finalize
QUIESCE_EXTRA_S = 10                     # quiesce timeout = update timeout + this (Go waits update timeout + 1 s)


class _ProtocolError(Exception):
    pass


def _t(x):
    """The sidecar encodes a missing time as 0."""
    return float(x) if x else None


def result_from_reply(rep: dict) -> SubmitResult:
    """Map one pay reply to the ledger's SubmitResult. For Perun, block is the
    channel version, gas_used and effective_gas_price are 0 (an off-chain
    update) and tx_hash is None; t_confirmed is set only for a succeeded pay,
    as the ledger keeps no time for outcomes without a confirmation."""
    if rep.get("ok"):
        return SubmitResult(tx_hash=None, t_submitted=_t(rep.get("t_submitted")),
                            t_confirmed=_t(rep.get("t_confirmed")), status=1, block=rep.get("version"),
                            gas_used=0, effective_gas_price=0, attempts=1, error="")
    err = rep.get("error") or ""
    if not err.startswith(("unresolved",) + FAILED_PREFIXES):
        err = "unresolved: unclassified sidecar reply: " + (err or "no error text")
    return SubmitResult(t_submitted=_t(rep.get("t_submitted")), status=0, attempts=1, error=err)


def make_tx(ev) -> dict:
    return {"id": ev.id, "amount_wei": ev.amount_wei}


class PerunSidecar:
    """One `perun-node -mode uc1-customer` process. Its stderr goes to log_path."""

    def __init__(self, argv: list[str], log_path: str):
        self.argv, self.log_path = list(argv), log_path
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._wlock = asyncio.Lock()
        self._pays: dict[str, asyncio.Future] = {}
        self._requests: collections.deque[tuple[str, asyncio.Future]] = collections.deque()
        self._broken: str | None = None          # protocol broken: the reason; the process is being killed
        self._end_reason: str | None = None      # set once the reader has finished: the sidecar is gone

    # ---- lifecycle ----------------------------------------------------------------------------

    async def start(self):
        with open(self.log_path, "ab") as logf:  # the child keeps its own descriptor
            self._proc = await asyncio.create_subprocess_exec(
                *self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=logf, limit=LINE_LIMIT)
        self._reader = asyncio.create_task(self._read())

    @property
    def exited(self) -> bool:
        return self._end_reason is not None

    @property
    def returncode(self):
        return None if self._proc is None else self._proc.returncode

    @property
    def outstanding(self) -> int:
        """Pays sent and not answered yet."""
        return len(self._pays)

    async def wait_exited(self):
        if self._reader is not None:
            await asyncio.shield(self._reader)

    async def stop(self, timeout: float = STOP_TIMEOUT_S):
        """Leave cleanly if possible: quiesce (so no queued pay runs unrecorded:
        the sidecar still executes its queue on exit or EOF), exit, then
        terminate/kill whatever is left. Idempotent."""
        if self._proc is None:
            return
        if not self.exited and not self._broken and self._proc.returncode is None:
            try:
                if self._pays:
                    await self.request({"op": "quiesce"}, timeout)
                await self.request({"op": "exit"}, timeout)
            except Exception as e:                # request() killed it on a timeout
                log.warning("sidecar did not stop cleanly: %s: %s", type(e).__name__, e)
        if self._proc.stdin is not None and not self._proc.stdin.is_closing():
            self._proc.stdin.close()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout)
        except TimeoutError:
            self._kill()
            await self._proc.wait()
        if self._reader is not None:
            await asyncio.gather(self._reader, return_exceptions=True)

    def _kill(self):
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass

    # ---- requests -----------------------------------------------------------------------------

    def _gone(self):
        return self._end_reason or self._broken

    async def _send(self, obj: dict):
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        async with self._wlock:                   # one line at a time, never interleaved
            if self._gone():
                return
            try:
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
                log.warning("write to the sidecar failed (%s: %s); killing it", type(e).__name__, e)
                self._kill()                      # the reader then resolves everything at EOF

    async def pay(self, id_: str, amount_wei: int, to: str | None = None) -> asyncio.Future:
        """Send one pay; the future resolves to the sidecar's reply dict (never
        raises). Not bounded in time here: see the module docstring."""
        fut = asyncio.get_running_loop().create_future()
        if self._gone():
            fut.set_result({"op": "pay", "id": id_, "ok": False, "error": "not sent: " + self._gone()})
            return fut
        if id_ in self._pays:
            raise ValueError(f"pay {id_!r} is already outstanding")
        self._pays[id_] = fut
        req = {"op": "pay", "id": id_, "amount_wei": str(int(amount_wei))}
        if to is not None:                        # UC2: the channel (uc2-player); UC1's line has no "to"
            req["to"] = to
        await self._send(req)
        return fut

    def send_request(self, obj: dict) -> asyncio.Future:
        """Queue a non-pay request and write its line at once, without yielding
        to the event loop (pay_loop's at_deadline hook relies on that). Returns
        the future of its reply; wait for it with wait_reply. Append and write
        happen together, so the FIFO of open requests matches the write order."""
        op = obj["op"]
        if op == "pay":
            raise ValueError("use pay() for pays")
        fut = asyncio.get_running_loop().create_future()
        if self._gone():
            fut.set_result({"op": op, "ok": False, "error": "not sent: " + self._gone()})
            return fut
        self._requests.append((op, fut))          # stays queued even if we stop waiting: keeps FIFO aligned
        try:                                      # one whole line per write: never interleaved with a pay
            self._proc.stdin.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
            log.warning("write to the sidecar failed (%s: %s); killing it", type(e).__name__, e)
            self._kill()                          # the reader then resolves everything at EOF
        return fut

    async def wait_reply(self, op: str, fut: asyncio.Future, timeout: float) -> dict:
        """Wait for the reply to a request sent with send_request. On timeout
        the sidecar is killed and TimeoutError raised."""
        async def roundtrip():
            async with self._wlock:               # flush our line (one drain at a time)
                if not self._gone():
                    try:
                        await self._proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
                        log.warning("write to the sidecar failed (%s: %s); killing it", type(e).__name__, e)
                        self._kill()
            return await asyncio.shield(fut)

        try:
            return await asyncio.wait_for(roundtrip(), timeout)
        except TimeoutError:
            log.error("sidecar did not answer %r within %.1f s; killing it", op, timeout)
            self._kill()
            raise TimeoutError(f"sidecar did not answer {op!r} within {timeout} s (killed)") from None

    async def request(self, obj: dict, timeout: float) -> dict:
        """Send a non-pay request and return its reply. On timeout the sidecar
        is killed and TimeoutError raised."""
        fut = self.send_request(obj)
        if fut.done():                            # not sent: the sidecar is gone
            return fut.result()
        return await self.wait_reply(obj["op"], fut, timeout)

    # ---- the reader ---------------------------------------------------------------------------

    def _dispatch(self, line: bytes):
        try:
            rep = json.loads(line)
        except ValueError:
            raise _ProtocolError(f"non-JSON line on stdout: {line[:200]!r}") from None
        if not isinstance(rep, dict):
            raise _ProtocolError(f"not a JSON object: {line[:200]!r}")
        if rep.get("op") == "pay" and "id" in rep:
            fut = self._pays.pop(rep["id"], None)
            if fut is None:
                raise _ProtocolError(f"pay reply for an unknown id: {line[:200]!r}")
        else:
            if not self._requests:
                raise _ProtocolError(f"reply to no request: {line[:200]!r}")
            op, fut = self._requests.popleft()
            if rep.get("op") not in (op, ""):     # "" = the sidecar could not parse our request
                raise _ProtocolError(f"reply {rep.get('op')!r} to a {op!r} request")
        if not fut.done():                        # a cancelled waiter: the reply is ignored
            fut.set_result(rep)

    async def _read(self):
        try:
            while True:
                try:
                    line = await self._proc.stdout.readline()
                    if not line:
                        break
                    if line.strip():
                        self._dispatch(line)
                except (_ProtocolError, ValueError) as e:   # ValueError: a line over LINE_LIMIT
                    self._broken = f"sidecar protocol broken: {e}"
                    log.error("%s; killing the sidecar", self._broken)
                    self._kill()
                    break
            if self._broken:
                try:
                    await self._proc.stdout.read()          # to EOF; nothing more is trusted
                except Exception:
                    pass
            rc = await self._proc.wait()
            self._end_reason = f"{self._broken} (rc {rc})" if self._broken else f"sidecar exited (rc {rc})"
        finally:
            if self._end_reason is None:                    # the reader itself failed or was cancelled
                self._end_reason = self._broken or "sidecar reader stopped"
                self._kill()
            self._resolve_all()

    def _resolve_all(self):
        err = "unresolved: " + self._end_reason
        pays, self._pays = self._pays, {}
        for id_, fut in pays.items():
            if not fut.done():
                fut.set_result({"op": "pay", "id": id_, "ok": False, "error": err})
        reqs, self._requests = self._requests, collections.deque()
        for op, fut in reqs:
            if not fut.done():
                fut.set_result({"op": op, "ok": False, "error": err})


class PerunSubmitter:
    """pay_loop's submitter for Perun: at most max_in_flight pays outstanding in
    the sidecar (submit blocks, as OrderedSubmitter's does); a slot is freed
    only when the sidecar has answered that pay (or is gone), so a pay whose
    row went unresolved at the drain deadline still holds its slot."""

    def __init__(self, sidecar: PerunSidecar, max_in_flight: int):
        self.sidecar = sidecar
        self._slots = asyncio.Semaphore(max_in_flight)

    async def submit(self, tx: dict) -> asyncio.Future:
        await self._slots.acquire()
        try:
            raw = await self.sidecar.pay(tx["id"], tx["amount_wei"], tx.get("to"))
        except BaseException:
            self._slots.release()
            raise
        done = asyncio.get_running_loop().create_future()

        def resolve(f):
            self._slots.release()
            if done.done():                       # the tracker was cancelled (drain deadline): ignore
                return
            if f.cancelled():
                done.set_result(SubmitResult(attempts=1, status=0, error="unresolved: sidecar reply lost"))
            else:
                done.set_result(result_from_reply(f.result()))

        if raw.done():                            # never sent: pay_loop records it without t_submitted
            resolve(raw)
        else:
            raw.add_done_callback(resolve)
        return done

    async def close(self):
        """Nothing to do: the lifecycle stops the sidecar."""


# ---- the lifecycle: payclient --system perun -----------------------------------------------------

def sidecar_argv(a) -> list[str]:
    """The sidecar's command line; everything else goes in the init request."""
    return [a.sidecar, "-mode", "uc1-customer"]


def l1_balance(chain_url: str, address: str) -> int:
    """The account's balance on the chain (Anvil serves HTTP and WebSocket on one port)."""
    url = chain_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    return Web3(Web3.HTTPProvider(url)).eth.get_balance(address)


async def call(sidecar: PerunSidecar, req: dict, timeout: float, sent: asyncio.Future | None = None):
    """One lifecycle request. Returns (reply, None) or (None, error text): both a
    TimeoutError (the sidecar was killed) and an {"ok": false} reply are errors.
    sent: the reply future of a request already sent with send_request."""
    try:
        rep = await (sidecar.request(req, timeout) if sent is None else sidecar.wait_reply(req["op"], sent, timeout))
    except TimeoutError as e:
        return None, f"TimeoutError: {e}"
    if not rep.get("ok"):
        return None, f"{req['op']}: {rep.get('error') or 'failed without an error text'}"
    return rep, None


class Lifecycle:
    """What differs between the use cases in run_lifecycle: the sidecar's init
    and open requests, how the state reply becomes recorded facts and a
    reconciliation, and the verdict on a closed run. This base is UC1's
    (one customer, one channel to the sensor); perun_uc2 overrides the hooks."""

    close_timeout_s: float | None = None          # None: read CLOSE_TIMEOUT_S at call time

    def __init__(self, a):
        self.a = a

    def make_tx(self, ev) -> dict:
        return make_tx(ev)

    def initial_life(self) -> dict:
        a = self.a
        return {"system": "perun", "account": a.account, "complete": False,
                "max_in_flight": a.max_in_flight, "max_queue": a.max_queue, "drain_s": a.drain,
                "duplicates": 0, "late": 0}

    def argv(self) -> list[str]:
        return sidecar_argv(self.a)

    def init_request(self, acct, state_dir: str) -> dict:
        a = self.a
        return {"op": "init", "key": acct.key.hex(), "name": f"customer-{a.account}", "chain": a.chain,
                "chain_id": a.chain_id, "sensor_control": a.sensor_control, "bind": a.wire_bind,
                "endpoint": a.wire_endpoint, "state_dir": state_dir, "update_timeout_s": a.update_timeout,
                "persist": bool(a.persist), "startup_timeout_s": STARTUP_TIMEOUT_S}

    def init_timeout(self) -> float:
        return INIT_TIMEOUT_S

    def record_init(self, life: dict, rep: dict):
        """Facts from the init reply (UC1 records none)."""

    async def pre_open_error(self, acct) -> str | None:
        """A reason not to open (UC1: the L1 balance does not cover the deposit)."""
        a = self.a
        balance = await asyncio.to_thread(l1_balance, a.chain, acct.address)
        if balance <= a.deposit_wei:
            return f"not funded: L1 balance {balance} wei <= deposit {a.deposit_wei} wei"
        return None

    def open_request(self) -> dict:
        return {"op": "open", "deposit_wei": str(int(self.a.deposit_wei))}

    def open_timeout(self) -> float:
        return OPEN_TIMEOUT_S

    def quiesce_facts(self, rep: dict) -> dict:
        return {"dropped": rep.get("dropped"), "version": rep.get("version")}

    def record_state(self, life: dict, rep: dict):
        """Put the state reply in life; `before_close` (a dict) is what reconcile reads."""
        life["channel_before_close"] = {"version": rep.get("version"), "balances_wei": rep.get("balances_wei"),
                                        "broken": bool(rep.get("broken"))}

    def reconcile(self, life: dict, ledger, facts_missing: list):
        before = life.get("channel_before_close")
        if before is None:
            return
        try:                                              # the customer is participant 0
            observed = int(self.a.deposit_wei) - int(before["balances_wei"][0])
            life["reconciliation"] = dict(ledger.reconcile(observed_wei=observed), observed_source="channel")
        except (TypeError, ValueError, IndexError, KeyError) as e:
            life["state_error"] = f"channel balances unusable: {type(e).__name__}: {e}"
            facts_missing.append("reconciliation")

    def close_timeout(self) -> float:
        return CLOSE_TIMEOUT_S if self.close_timeout_s is None else self.close_timeout_s

    def broken_error(self, life: dict) -> str | None:
        before = life.get("channel_before_close")
        if before is not None and before["broken"]:
            return "channel broken before Close: the run is not complete"
        return None


async def main_async(a) -> int:
    """The Perun lifecycle for one customer (UC1): see run_lifecycle."""
    return await run_lifecycle(a, Lifecycle(a))


async def run_lifecycle(a, lc: Lifecycle) -> int:
    """The Perun lifecycle for one payment client; returns the exit code (0 only
    if complete). Mirrors payclient.main_async: lifecycle.json is complete=false
    until the very end, ledger.csv and lifecycle.json are written in the finally
    block (also on SIGTERM, which payclient.run turns into cancellation), and a
    missing fact (no quiesce, no channel state) is a failure. `lc` holds what
    differs per use case.

    1. start the sidecar, init; 2. pre-open check, open (timed);
    3. socket, then ready; 4. pay_loop; 5. quiesce, sent by pay_loop's
    at_deadline hook at the drain deadline itself (before the pending rows are
    marked unresolved, so no queued pay starts after the deadline), then state
    (meaningful only after quiesce);
    6. ledger.csv, reconciliation; 7. close (a broken channel is still sent to
    close, but the run is never complete);
    8. exit, stop the sidecar, remove state/ if complete."""
    from measure import payclient                         # payclient imports this module lazily

    os.makedirs(a.out, exist_ok=True)
    life_path = os.path.join(a.out, "lifecycle.json")
    ready_path = os.path.join(a.out, "ready")
    state_dir = os.path.join(a.out, "state")
    if os.path.exists(ready_path):
        os.unlink(ready_path)                             # stale from an earlier run
    life = lc.initial_life()
    payclient.write_json(life_path, life)
    ledger = PaymentLedger(os.path.join(a.out, "ledger.log.jsonl"), max_queue=a.max_queue)
    sidecar = None
    sock = None
    try:
        acct = keys.account(a.account)
        sidecar = PerunSidecar(lc.argv(), os.path.join(a.out, "sidecar.log"))
        try:
            await sidecar.start()
            rep, err = await call(sidecar, lc.init_request(acct, state_dir), lc.init_timeout())
            if err:
                life["open_error"] = err                  # "init: ..." or "TimeoutError: ..."
                return 1
            lc.record_init(life, rep)
            err = await lc.pre_open_error(acct)
            if err:
                life["open_error"] = err
                return 1
            if getattr(a, "open_at", None):              # --open-at: every player begins its Open at
                life["open_at"] = a.open_at               # the same agreed time (board clocks in sync)
                await asyncio.sleep(max(0.0, a.open_at - time.time()))
            rep, err = await call(sidecar, lc.open_request(), lc.open_timeout())   # never retried
            if err:
                life["open_error"] = err
                return 1
            life["open"] = rep
        except Exception as e:
            life["open_error"] = f"{type(e).__name__}: {e}"
            log.exception("Open failed")
            return 1
        finally:
            if "open_error" in life:
                log.error("Open failed: %s", life["open_error"])
            payclient.write_json(life_path, life)

        q = payclient.EventQueue()

        async def on_event(ev):
            r = payclient.accept(ledger, q, ev)
            if r == "duplicate":
                life["duplicates"] += 1
                log.warning("duplicate event id %s ignored", ev.id)
            elif r == "late":
                life["late"] += 1
                log.warning("event %s after the end marker recorded as failed", ev.id)

        listening = asyncio.Event()
        sock = asyncio.create_task(payclient.serve_socket(a.socket, on_event, lambda: q.put_nowait(None),
                                                          listening))
        waiter = asyncio.create_task(listening.wait())
        await asyncio.wait({sock, waiter}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        if sock.done():
            sock.result()
        open(ready_path, "w").close()
        life["ready_at"] = time.time()
        payclient.write_json(life_path, life)
        quiesce = {}

        def quiesce_at_deadline():                        # pay_loop's at_deadline hook: no await in here
            quiesce["sent"] = sidecar.send_request({"op": "quiesce"})
            life["quiesce_sent_at"] = time.time()

        # Quiesce goes out AT the drain deadline, before the pending rows are marked
        # unresolved (seconds on a Pi at w10): Go would otherwise keep starting queued pays.
        await payclient.pay_loop(ledger, PerunSubmitter(sidecar, a.max_in_flight), lc.make_tx, q, a.drain,
                                 at_deadline=quiesce_at_deadline)
        if "sent" not in quiesce:                         # the hook failed: send it now
            quiesce["sent"] = sidecar.send_request({"op": "quiesce"})
            life["quiesce_sent_at"] = time.time()
        rep, err = await call(sidecar, {"op": "quiesce"}, a.update_timeout + QUIESCE_EXTRA_S,
                              sent=quiesce["sent"])
        life["ended_at"] = time.time()
        sock.cancel()
        await asyncio.gather(sock, return_exceptions=True)
        sock = None
        facts_missing = []
        if err:
            life["quiesce_error"] = err
            facts_missing.append("quiesce")
        else:
            life["quiesce"] = lc.quiesce_facts(rep)
            rep, err = await call(sidecar, {"op": "state"}, STATE_TIMEOUT_S)
            if err:
                life["state_error"] = err
                facts_missing.append("state")
            else:
                lc.record_state(life, rep)
        ledger.write_csv(os.path.join(a.out, "ledger.csv"))
        lc.reconcile(life, ledger, facts_missing)
        payclient.write_json(life_path, life)
        rep, err = await call(sidecar, {"op": "close"}, lc.close_timeout())
        if err:
            life["close_error"] = err
            log.error("Close failed: %s", err)
            return 1
        life["close"] = rep
        broken = lc.broken_error(life)
        if broken:
            life["error"] = broken
            log.error(life["error"])
            return 1
        if facts_missing:                                 # a missing fact is a failure, never complete
            life["error"] = "missing: " + ", ".join(facts_missing)
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
            await asyncio.gather(sock, return_exceptions=True)
        if sidecar is not None:
            try:
                await sidecar.stop()                      # quiesce if pays are left, exit, then kill
            except Exception:
                log.exception("sidecar stop failed")
            life["sidecar_rc"] = sidecar.returncode
        try:
            ledger.write_csv(os.path.join(a.out, "ledger.csv"))
        except Exception:
            log.exception("ledger.csv not written")
        ledger.close()
        if life["complete"]:
            shutil.rmtree(state_dir, ignore_errors=True)  # kept after a failure, for diagnosis
        payclient.write_json(life_path, life)
