> **ARCHIVED** — executed 2026-09-30; the code and the cluster spec §8 are authoritative.

# UC1 on Arbitrum — first complete measurement path — Implementation Plan

**Goal:** One command on the workstation — `deploy/cluster run uc1-arbitrum-w1000` — runs UC1 on the Arbitrum stack on the Pi cluster end to end (pre-flight, Open, paced payments per data window, Close, recording, collection) and a second command turns the run folder into the thesis numbers.

**Architecture:** A new Python package `measure/` holds everything that runs on the boards: a GESL sensor that streams rows over TCP, customers that group rows into windows and emit one payment event per window, and a *local payment client* per board that receives those events over a Unix socket (JSON lines), records them in a payment ledger and executes them on the system under test. The Arbitrum client does the full lifecycle: Open = bridge ETH from the L1 (`Inbox.depositEth`) and wait until it is credited on the L2; payments = ETH transfers to the sensor, submitted in nonce order and pipelined over one WebSocket per account; Close = withdrawal submission (`ArbSys.withdrawEth`). A bash script `deploy/cluster` drives the boards over SSH and collects a run folder with a manifest; `measure/analyze_uc1.py` and `measure/aggregate.py` produce the numbers.

**Tech Stack:** Python 3.12 (stdlib `asyncio`, `unittest`), `web3==8.0.0` (with `eth_account`, `websockets` as its dependencies), bash, OpenSSH; Arbitrum Nitro stack in Docker on `bath0`; chrony time from `bath1`.

**Spec:** `docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md` (§5 control and pre-flight, §6 recording, §7.2 UC1, §7.3 *Payment ledger*, §8 step 3) with the parent spec `docs/superpowers/specs/2026-09-03-l2-benchmark-design.md` (§3.1 UC1, §5 metrics, §8 run matrix, §11.4 Close asymmetry, §11.5 deposits).

**Decisions this plan rests on (2026-09-30, project owner):** first path = UC1 on Arbitrum; use-case code talks to a local payment client per system; the first path includes the full lifecycle (Open, payments, Close); runs are driven by `deploy/cluster` over SSH, the ansible installer comes later; **GESL recording 6063** (the most complete, 92.31 % of cells filled, and the only one longer than 5 minutes: 16.2 min; 6006 90.38 %/4.5 min, 6021 67.31 %/4.4 min, 6062 91.91 %/5.2 min); **15 minutes of replay per run**; a **cooling gate** before every run (every board within 3 °C of its own idle temperature — revised at Checkpoint 2 because the Pi 3Bs idle above 50 °C — waiting at most 20 minutes, idle and start temperatures recorded); ports 8545 and 8548 on `bath0` opened to the LAN (Checkpoint 2); deposit 1 ETH per customer; clock tolerance 5 ms; price 10¹² wei per window (arbitrary on a test chain; it only has to be covered by the deposit).

## Checkpoints — stop and ask the project owner

Execution **stops and asks** at these points, and at any point where a result would change a spec decision:

1. **After Task 1, Step 6** — report the integration result (500 of 500 at 50/s from one account?). If it failed, present the options before continuing.
2. **Before Task 9, Step 3** — before opening the L1 port on `bath0` to the network.
3. **After Task 11, Step 2** — show the first dry run's summary (outcomes, reconciliation, latencies, temperatures) before the nine real runs start.
4. **Anything unexpected** — a spec rule that cannot be met, a number far from expectation, a board fault: describe it and ask; do not choose silently.

## Global Constraints

- Boards `bath0`…`bath7` at `192.168.178.21<N>`, user `i_wanilin`, `ssh bath<N>` from WSL on the workstation; `sudo` works without a password on every board.
- UC1 layout (cluster spec §7.2): `bath0` = the stack under test; `bath1` = the sensor (it also receives the payments); `bath2`…`bath7` = 6 customers.
- GESL data replays at its real cadence, **120 rows per second**, the **whole stream** to every customer over its own **direct TCP connection**.
- **Postpay:** after a window is received, the customer pays for it. Window sweep: **1 s, 100 ms, 10 ms** → 1, 10, 100 payments per second per customer. Latency = window arrival → payment confirmation.
- UC1 pays in **native ETH**, no token.
- Payment ledger (cluster spec §7.3): stable IDs derived from the event; no duplicates (a retry reuses the ID); bounded queue; fixed retry policy, the same for all systems; **pipelined submission, never one at a time**, nonces in order per account, bounded in flight; drain deadline; exactly one outcome per payment out of *generated, queued, submitted, succeeded, failed, unresolved*; **succeeded** = included with success status; reconciliation at the end.
- Every event carries **five timestamps**: intended, generated, queued, submitted, confirmed.
- The ledger never rewrites a growing file during a run: it appends one line per status change (a 15-minute run at the 10 ms window is 90 000 payments per customer on an SD card) and builds `ledger.csv` at the end; a crashed run is rebuilt from the log.
- Pre-flight, every run, abort on failure (cluster spec §5): all boards reachable; each card in its own board (Wi-Fi address and serial as in `deploy/FLEET.md`); every board on BSSID `02:00:00:00:00:01`; one kernel on all boards; clock offset to `bath1` under a threshold; Wi-Fi power save off; `bath0` and `bath1` with `get_throttled` = `0x0` (the bit `0x80000` excepted), the others recorded; no `netem` delay; enough free disk; the expected stack and only that stack up.
- Recording (cluster spec §6): one folder per repetition, `results/pi/uc1/<date>-arbitrum-<experiment>-rep<k>/` with `manifest.json`, `results/`, `logs/`, `recorder/`.
- Run matrix (parent §8): **3 repetitions** per configuration, reported as mean ± sample sd (n−1) with min–max and per-run values. Sequential runs only.
- Arbitrum Close is **the withdrawal submission**; the 7-day challenge period is documentation, reported in its own column, never in one cell with Perun's Close (parent §11.4).
- A deposit covers the whole run plus a margin, reported next to the results (parent §11.5).
- Snapshots (cluster spec §4): each run starts from the byte-identical chain; the users hold ETH **on the L1 only**; bridging is the rollup's timed *Open*.
- Arbitrum stack on `bath0`: L1 geth chain ID **1337** at port 8545, L2 chain ID **412346** at port 8547, Inbox `0xA520303d332b27763E3518000861EdBc9843ceCc`, ArbSys precompile `0x0000000000000000000000000000000000000064`. Verified on the workstation copy on 2026-09-30: deposit credited on the L2 1.9 s after sending, withdrawal submitted in 28 ms.
- Test keys come from the public development mnemonic `test test test test test test test test test test test junk`; never real funds. Index 0 is the L1 funder; **index 100 = sensor, 101…106 = customers 1…6** (fresh accounts, zero balance in the snapshot).
- GESL raw data is not published (`data/` is git-ignored); only IDs and CSV SHA-256 are in the repo.
- Style: follow `tank-arena` — `unittest`, module docstrings that say why, no framework beyond the stdlib and `web3`.

## Review Focus

1. **A payment-client restart or crash mid-run** — the ledger must still be written for everything that happened before (flush on every outcome change), and the run must be marked incomplete, not silently shorter. Pinned in Task 2 (`test_log_is_appended_on_every_change_and_rebuilds_the_rows`) and Task 10 (`test_a_crashed_payment_client_marks_the_run_incomplete`).
2. **Nonce gaps on Arbitrum** — the sequencer rejects a transaction whose nonce is ahead of the account; out-of-order arrival must never lose a payment: the submitter resends the same signed transaction (same ID) after a short delay. Pinned in Task 1.
3. **A window whose payment is still pending when the run ends** — it must end as *unresolved* after the drain deadline, never dropped, and reconciliation must report the difference. Pinned in Task 2 and Task 10.
4. **A customer that connects late or disconnects** — the sensor waits for all customers before the start instant; a customer disconnect ends that customer's stream and is logged, the other customers continue. Pinned in Task 4.
5. **A 10 ms window that is 1.2 rows long** — windows are defined on the GESL time column, not on row counts, so every 10 ms window has one or two rows and none is empty or skipped. Pinned in Task 4.

---

## File structure

| File | Responsibility |
|---|---|
| `measure/__init__.py` | package marker, one-line docstring |
| `measure/requirements.txt` | `web3==8.0.0` |
| `measure/keys.py` | deterministic test accounts by index |
| `measure/events.py` | `PaymentEvent` and its JSON-lines encoding |
| `measure/ledger.py` | `PaymentLedger`: outcomes, timestamps, bounded queue, CSV, reconciliation |
| `measure/submitter.py` | `OrderedSubmitter`: in-order pipelined `eth_sendRawTransaction` over one WebSocket, receipts, retries |
| `measure/gesl.py` | GESL CSV: SHA-256 check, completeness, row iteration |
| `measure/wire.py` | length-prefixed JSON frames over TCP (as `tank-arena/net/protocol.py`) |
| `measure/sensor.py` | sensor process: waits for customers, streams rows at 120 Hz |
| `measure/customer.py` | customer process: receives rows, closes windows, emits payment events |
| `measure/arbitrum.py` | Arbitrum lifecycle: deposit + wait for credit, payment transaction, withdrawal |
| `measure/payclient.py` | local payment client: Unix socket server + ledger + submitter + lifecycle |
| `measure/preflight.py` | pre-flight checks against `deploy/FLEET.md` |
| `measure/manifest.py` | builds `manifest.json` from per-board fact files |
| `measure/analyze_uc1.py` | one run folder → `summary.json` |
| `measure/aggregate.py` | three run folders → mean ± sd table |
| `measure/tests/test_*.py` | unit tests; integration tests skip unless `ARB_L1`/`ARB_L2` are set |
| `deploy/recorder.sh` | per-board sampler (from the feasibility `sampler.sh`) |
| `deploy/board-facts.sh` | per-board facts for the manifest and pre-flight |
| `deploy/cluster` | the control script: `sync`, `up`, `down`, `status`, `preflight`, `run`, `collect` |
| `deploy/experiments/uc1-arbitrum-w{1000,100,10}.env` | experiment parameters |

Workstation test chain for the integration tests: the Arbitrum stack in `nitro-testnode/` started with the port override used on 2026-09-29 (`docker compose -f docker-compose.yaml -f <override without 9642> up -d geth redis sequencer`), then `export ARB_L1=http://127.0.0.1:8545 ARB_L2=ws://127.0.0.1:8547`. Stop it after the task (`docker compose stop -t 60 sequencer && docker compose stop geth redis`).

---

### Task 1: Ordered pipelined submitter (the riskiest piece first)

On 2026-09-29 a sender with many threads per account lost 2 803 of 3 000 Arbitrum transfers to *nonce too high*, and one-at-a-time submission capped each account at about one per second on the Pi. This task proves that one asyncio task per account, sending signed transactions in nonce order over one WebSocket without waiting for replies, keeps many in flight and loses none.

**Files:**
- Create: `measure/__init__.py`, `measure/requirements.txt`, `measure/keys.py`, `measure/submitter.py`
- Test: `measure/tests/__init__.py`, `measure/tests/test_submitter.py`

**Interfaces:**
- Produces:
  - `keys.account(index: int) -> eth_account.signers.local.LocalAccount`
  - `class SubmitResult` (dataclass): `tx_hash: str | None`, `t_submitted: float | None`, `t_confirmed: float | None`, `status: int | None`, `block: int | None`, `gas_used: int | None`, `effective_gas_price: int | None`, `attempts: int`, `error: str`
  - `class OrderedSubmitter(ws_url: str, account: LocalAccount, chain_id: int, max_in_flight: int, retry_delays: tuple[float, ...] = (0.25, 0.5, 1.0))`
    - `async start() -> None` (opens the WebSocket, reads the pending nonce)
    - `async submit(tx: dict) -> asyncio.Future[SubmitResult]` — assigns the next nonce, signs, sends in order, returns a future that resolves when the receipt is known or the payment has failed
    - `async close() -> None`

- [ ] **Step 1: Package skeleton and pinned dependency**

```python
# measure/__init__.py
"""measure — code that runs on the Pi cluster during a measurement run."""
```

```text
# measure/requirements.txt
web3==8.0.0
```

```python
# measure/keys.py
"""Deterministic test accounts from the public development mnemonic.

Index 0 funds the L1; 100 is the UC1 sensor, 101..106 are customers 1..6.
Local dev chains only -- this mnemonic is public.
"""
from eth_account import Account

Account.enable_unaudited_hdwallet_features()
MNEMONIC = "test test test test test test test test test test test junk"
FUNDER, SENSOR = 0, 100


def customer_index(n: int) -> int:
    """Customer n (1-based) -> account index."""
    return SENSOR + n


def account(index: int):
    return Account.from_mnemonic(MNEMONIC, account_path=f"m/44'/60'/0'/0/{index}")
```

```python
# measure/tests/__init__.py
```

Then create the workstation environment once; **every `python3` in this plan means this environment** (activate it in each terminal):

```bash
python3 -m venv .venv && .venv/bin/pip install -q -r measure/requirements.txt && source .venv/bin/activate
python3 -c "import web3, rlp, websockets; print(web3.__version__)"   # 8.0.0
```

- [ ] **Step 2: Write the failing unit test (fake JSON-RPC server that rejects nonce gaps like the sequencer)**

```python
# measure/tests/test_submitter.py
"""The submitter keeps nonces in order, pipelines, and survives nonce gaps."""
import asyncio
import json
import os
import time
import unittest

import rlp
import websockets

from measure import keys
from measure.submitter import OrderedSubmitter


class FakeSequencer:
    """Accepts a raw tx only if its nonce is the next one; replies after a delay,
    like Arbitrum answering once a transfer is sequenced. `gap_every` makes every
    n-th transaction arrive 'too early' once (reply: nonce too high)."""

    def __init__(self, delay=0.05, gap_every=0):
        self.delay, self.gap_every = delay, gap_every
        self.next_nonce = 0
        self.receipts = {}
        self.max_concurrent = 0
        self._open = 0
        self._seen = set()

    async def handler(self, ws):
        async for raw in ws:
            msg = json.loads(raw)
            asyncio.create_task(self._answer(ws, msg))

    async def _answer(self, ws, msg):
        m, p, rid = msg["method"], msg.get("params", []), msg["id"]
        if m == "eth_getTransactionCount":
            return await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "result": hex(self.next_nonce)}))
        if m == "eth_getTransactionReceipt":
            r = self.receipts.get(p[0])
            return await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "result": r}))
        if m == "eth_sendRawTransaction":
            # EIP-1559 raw tx = 0x02 || rlp([chainId, nonce, ...]); the fake needs only the nonce
            fields = rlp.decode(bytes.fromhex(p[0][2:])[1:])
            nonce = int.from_bytes(fields[1], "big")
            self._open += 1
            self.max_concurrent = max(self.max_concurrent, self._open)
            await asyncio.sleep(self.delay)
            self._open -= 1
            first_time = nonce not in self._seen
            self._seen.add(nonce)
            if nonce != self.next_nonce or (self.gap_every and nonce % self.gap_every == 0 and first_time and nonce):
                return await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid,
                                                 "error": {"code": -32000, "message": "nonce too high"}}))
            h = "0x%064x" % (nonce + 1)
            self.receipts[h] = {"status": "0x1", "blockNumber": hex(nonce + 1), "gasUsed": "0x5208",
                                "effectiveGasPrice": "0x5f5e100", "transactionHash": h}
            self.next_nonce += 1
            return await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "result": h}))


class TestOrderedSubmitter(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, fake, n):
        async with websockets.serve(fake.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            sub = OrderedSubmitter(f"ws://127.0.0.1:{port}", keys.account(101), 412346, max_in_flight=32,
                                   retry_delays=(0.1, 0.2, 0.4, 0.8, 1.6))
            await sub.start()
            futs = [await sub.submit({"to": keys.account(100).address, "value": 1, "gas": 21000,
                                      "maxFeePerGas": 2 * 10**8, "maxPriorityFeePerGas": 0}) for _ in range(n)]
            results = await asyncio.gather(*futs)
            await sub.close()
            return results

    async def test_all_succeed_in_order_and_pipelined(self):
        fake = FakeSequencer(delay=0.05)
        t0 = time.monotonic()
        results = await self.run_fake(fake, 100)
        self.assertTrue(all(r.status == 1 for r in results))
        self.assertEqual(fake.next_nonce, 100)
        self.assertGreater(fake.max_concurrent, 1)          # pipelined, not one at a time
        self.assertLess(time.monotonic() - t0, 100 * 0.05)  # faster than one-at-a-time

    async def test_nonce_too_high_is_retried_with_the_same_transaction(self):
        fake = FakeSequencer(delay=0.01, gap_every=7)
        results = await self.run_fake(fake, 50)
        self.assertTrue(all(r.status == 1 for r in results), [r.error for r in results if r.status != 1])
        self.assertTrue(any(r.attempts > 1 for r in results))
        self.assertEqual(len({r.tx_hash for r in results}), 50)  # no duplicates


@unittest.skipUnless(os.environ.get("ARB_L2"), "needs the workstation Arbitrum stack (ARB_L2)")
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
```

- [ ] **Step 3: Run it to see it fail**

Run: `python3 -m unittest measure.tests.test_submitter -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'measure.submitter'`

- [ ] **Step 4: Implement the submitter**

```python
# measure/submitter.py
"""In-order, pipelined transaction submission over one WebSocket per account.

Why: Arbitrum answers eth_sendRawTransaction only once the transaction is
sequenced, so waiting for each reply caps an account at 1/latency; and it
rejects a nonce that is ahead of the account ("nonce too high"), so parallel
threads that race each other lose transactions (2 803 of 3 000 on 2026-09-29).
Here one task sends signed transactions strictly in nonce order over a single
connection and does not wait for replies; up to `max_in_flight` are open at
once. A "nonce too high" reply resends the *same* signed transaction (same
payment ID, no duplicate) after a short delay. Cluster spec §7.3.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import time
from dataclasses import dataclass

import websockets


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


class OrderedSubmitter:
    def __init__(self, ws_url, account, chain_id, max_in_flight, retry_delays=(0.25, 0.5, 1.0),
                 receipt_timeout=120.0):
        self.ws_url, self.account, self.chain_id = ws_url, account, chain_id
        self.retry_delays, self.receipt_timeout = retry_delays, receipt_timeout
        self._slots = asyncio.Semaphore(max_in_flight)
        self._ids = itertools.count(1)
        self._replies: dict[int, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()
        self._nonce = None
        self._ws = None
        self._reader = None

    async def start(self):
        self._ws = await websockets.connect(self.ws_url, max_size=None, ping_interval=None)
        self._reader = asyncio.create_task(self._read_loop())
        self._nonce = int(await self._call("eth_getTransactionCount", [self.account.address, "pending"]), 16)

    async def close(self):
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()

    async def _read_loop(self):
        async for raw in self._ws:
            msg = json.loads(raw)
            fut = self._replies.pop(msg.get("id"), None)
            if fut and not fut.done():
                fut.set_result(msg)

    async def _send(self, method, params) -> asyncio.Future:
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._replies[rid] = fut
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        return fut

    async def _call(self, method, params):
        msg = await (await self._send(method, params))
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", str(msg["error"])))
        return msg["result"]

    async def submit(self, tx: dict) -> asyncio.Future:
        """Assign the next nonce, sign and send now (in order); the returned
        future resolves to a SubmitResult once the outcome is known."""
        await self._slots.acquire()
        async with self._send_lock:                       # strict nonce order on the wire
            full = dict(tx, nonce=self._nonce, chainId=self.chain_id, type=2)
            self._nonce += 1
            raw = "0x" + self.account.sign_transaction(full).raw_transaction.hex().removeprefix("0x")
            result = SubmitResult(attempts=1)
            reply = await self._send("eth_sendRawTransaction", [raw])
            result.t_submitted = time.time()
        done = asyncio.get_running_loop().create_future()
        asyncio.create_task(self._follow(raw, reply, result, done))
        return done

    async def _follow(self, raw, reply, result, done):
        try:
            for delay in (*self.retry_delays, None):
                msg = await reply
                if "error" not in msg:
                    result.tx_hash = msg["result"]
                    break
                text = msg["error"].get("message", "")
                if "nonce too high" in text and delay is not None:
                    await asyncio.sleep(delay)
                    result.attempts += 1
                    reply = await self._send("eth_sendRawTransaction", [raw])
                    continue
                if "already known" in text or "nonce too low" in text:
                    from eth_utils import keccak
                    result.tx_hash = "0x" + keccak(bytes.fromhex(raw[2:])).hex()
                    break
                result.error = text[:200]
                return
            deadline = time.monotonic() + self.receipt_timeout
            while time.monotonic() < deadline:
                r = await self._call("eth_getTransactionReceipt", [result.tx_hash])
                if r:
                    result.t_confirmed = time.time()
                    result.status = int(r["status"], 16)
                    result.block = int(r["blockNumber"], 16)
                    result.gas_used = int(r["gasUsed"], 16)
                    result.effective_gas_price = int(r.get("effectiveGasPrice", "0x0"), 16)
                    return
                await asyncio.sleep(0.05)
            result.error = "unresolved: no receipt"
        except Exception as e:                            # the ledger records it; never raise into the run
            result.error = f"{type(e).__name__}: {e}"[:200]
        finally:
            self._slots.release()
            if not done.done():
                done.set_result(result)
```

- [ ] **Step 5: Run the unit tests**

Run: `python3 -m unittest measure.tests.test_submitter -v`
Expected: the two `TestOrderedSubmitter` tests PASS, `TestAgainstArbitrum` skipped.

- [ ] **Step 6: Run the integration test against the workstation Arbitrum**

Start the stack as described under *File structure*, then:
Run: `ARB_L2=ws://127.0.0.1:8547 python3 -m unittest measure.tests.test_submitter.TestAgainstArbitrum -v`
Expected: PASS — 500 of 500 confirmed. Stop the stack. **Checkpoint 1: report the result to the project owner and wait for the go.** If it failed, present the failure (error texts, how many, when) and the options, e.g. longer `retry_delays`, a JSON-RPC batch per burst, or more accounts per customer; do not pick one silently.

- [ ] **Step 7: Commit**

```bash
echo '/.venv/' >> .gitignore
git add .gitignore measure/__init__.py measure/requirements.txt measure/keys.py measure/submitter.py measure/tests/__init__.py measure/tests/test_submitter.py
git commit -m "measure: in-order pipelined submitter for Arbitrum"
```

---

### Task 2: Payment events and the payment ledger

**Files:**
- Create: `measure/events.py`, `measure/ledger.py`
- Test: `measure/tests/test_ledger.py`

**Interfaces:**
- Consumes: `SubmitResult` (Task 1)
- Produces:
  - `class PaymentEvent` (dataclass): `id: str`, `to: str`, `amount_wei: int`, `t_intended: float`, `t_generated: float`; `to_line() -> bytes`; `PaymentEvent.from_line(line: bytes) -> PaymentEvent`
  - `event_id(run: str, customer: int, window: int) -> str` → `"uc1/<run>/c<customer>/w<window>"`
  - `OUTCOMES = ("generated", "queued", "submitted", "succeeded", "failed", "unresolved")`
  - `class PaymentLedger(log_path: str, max_queue: int)`: `add(ev) -> bool` (False = duplicate ID), `queued(id)`, `submitted(id, t)`, `finish(id, result: SubmitResult)`, `unresolve_pending()`, `pending() -> int`, `rows() -> list[dict]`, `write_csv(path)`, `reconcile(observed_wei: int) -> dict`
  - `load_log(log_path) -> list[dict]` — rebuilds the rows from the append-only log (used after a crash and by the analysis)

- [ ] **Step 1: Write the failing test**

```python
# measure/tests/test_ledger.py
"""Every payment ends with exactly one outcome and five timestamps."""
import csv
import os
import tempfile
import unittest

from measure.events import PaymentEvent, event_id
from measure.ledger import OUTCOMES, PaymentLedger, load_log
from measure.submitter import SubmitResult


def ev(w, t=100.0):
    return PaymentEvent(event_id("r1", 1, w), "0x" + "11" * 20, 10**12, t, t + 0.001)


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "ledger.log.jsonl")

    def test_event_roundtrip(self):
        e = ev(3)
        self.assertEqual(PaymentEvent.from_line(e.to_line()), e)
        self.assertEqual(e.id, "uc1/r1/c1/w3")

    def test_duplicate_id_is_refused(self):
        led = PaymentLedger(self.path, max_queue=10)
        self.assertTrue(led.add(ev(1)))
        self.assertFalse(led.add(ev(1)))
        self.assertEqual(len(led.rows()), 1)

    def test_bounded_queue_fails_instead_of_dropping(self):
        led = PaymentLedger(self.path, max_queue=2)
        for w in range(3):
            led.add(ev(w))
        outcomes = [r["outcome"] for r in led.rows()]
        self.assertEqual(outcomes.count("failed"), 1)
        self.assertIn("queue full", [r["error"] for r in led.rows()])

    def test_lifecycle_and_five_timestamps(self):
        led = PaymentLedger(self.path, max_queue=10)
        led.add(ev(1))
        led.queued("uc1/r1/c1/w1")
        led.submitted("uc1/r1/c1/w1", 100.01)
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 100.01, 100.3, 1, 7, 21000, 10**8, 1, ""))
        r = led.rows()[0]
        self.assertEqual(r["outcome"], "succeeded")
        for k in ("t_intended", "t_generated", "t_queued", "t_submitted", "t_confirmed"):
            self.assertIsNotNone(r[k], k)

    def test_reverted_is_failed_not_succeeded(self):
        led = PaymentLedger(self.path, max_queue=10)
        led.add(ev(1)); led.queued("uc1/r1/c1/w1"); led.submitted("uc1/r1/c1/w1", 1.0)
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 1.0, 1.2, 0, 7, 21000, 10**8, 1, ""))
        self.assertEqual(led.rows()[0]["outcome"], "failed")

    def test_pending_at_drain_deadline_becomes_unresolved_and_reconciliation_shows_it(self):
        led = PaymentLedger(self.path, max_queue=10)
        for w in (1, 2):
            led.add(ev(w)); led.queued(f"uc1/r1/c1/w{w}"); led.submitted(f"uc1/r1/c1/w{w}", 1.0)
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 1.0, 1.2, 1, 7, 21000, 10**8, 1, ""))
        led.unresolve_pending()
        self.assertEqual(sorted(r["outcome"] for r in led.rows()), ["succeeded", "unresolved"])
        rec = led.reconcile(observed_wei=10**12)
        self.assertEqual(rec["succeeded_wei"], 10**12)
        self.assertEqual(rec["difference_wei"], 0)
        self.assertEqual(rec["counts"]["unresolved"], 1)

    def test_log_is_appended_on_every_change_and_rebuilds_the_rows(self):
        led = PaymentLedger(self.path, max_queue=10)
        led.add(ev(1)); led.queued("uc1/r1/c1/w1")
        with open(self.path) as f:
            self.assertEqual(len(f.readlines()), 2)          # appended, never rewritten
        rebuilt = load_log(self.path)                        # as after a crash
        self.assertEqual(rebuilt[0]["outcome"], "queued")
        self.assertIn(rebuilt[0]["outcome"], OUTCOMES)
        out = self.path.replace(".log.jsonl", ".csv")
        led.write_csv(out)
        with open(out) as f:
            self.assertEqual(list(csv.DictReader(f))[0]["id"], "uc1/r1/c1/w1")
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 -m unittest measure.tests.test_ledger -v`
Expected: ERROR `No module named 'measure.events'`

- [ ] **Step 3: Implement events and ledger**

```python
# measure/events.py
"""A payment event: what the use-case code asks the payment client to pay.

JSON lines over a Unix socket, one object per line. The ID is derived from the
event (run, customer, window), so a retry or a resent line never pays twice.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PaymentEvent:
    id: str
    to: str
    amount_wei: int
    t_intended: float   # when the window was received (the deadline clock starts)
    t_generated: float  # when the customer emitted the event

    def to_line(self) -> bytes:
        return (json.dumps(asdict(self), separators=(",", ":")) + "\n").encode()

    @classmethod
    def from_line(cls, line: bytes) -> "PaymentEvent":
        return cls(**json.loads(line))


def event_id(run: str, customer: int, window: int) -> str:
    return f"uc1/{run}/c{customer}/w{window}"
```

```python
# measure/ledger.py
"""The payment ledger (cluster spec §7.3): one row per payment, exactly one
outcome, five timestamps.

During a run it only *appends*: one JSON line per status change, flushed at
once, so a crash loses nothing already known and the SD card never rewrites a
growing file (a 15-minute run at the 10 ms window is 90 000 payments per
customer). `write_csv` builds ledger.csv at the end; `load_log` rebuilds the
rows from the log after a crash."""
from __future__ import annotations

import csv
import json
import time

OUTCOMES = ("generated", "queued", "submitted", "succeeded", "failed", "unresolved")
FIELDS = ("id", "to", "amount_wei", "outcome", "t_intended", "t_generated", "t_queued",
          "t_submitted", "t_confirmed", "tx_hash", "block", "gas_used", "effective_gas_price",
          "attempts", "error")


def load_log(log_path) -> list[dict]:
    rows: dict[str, dict] = {}
    with open(log_path) as f:
        for line in f:
            if line.strip():
                change = json.loads(line)
                rows.setdefault(change["id"], {k: None for k in FIELDS}).update(change)
    return list(rows.values())


class PaymentLedger:
    def __init__(self, log_path: str, max_queue: int):
        self.max_queue = max_queue
        self._rows: dict[str, dict] = {}
        self._log = open(log_path, "a", buffering=1)          # line-buffered: one write per change

    def _change(self, id_, **fields):
        self._rows[id_].update(fields)
        self._log.write(json.dumps(dict(fields, id=id_), separators=(",", ":")) + "\n")

    def pending(self) -> int:
        return sum(r["outcome"] in ("generated", "queued", "submitted") for r in self._rows.values())

    def add(self, ev) -> bool:
        if ev.id in self._rows:
            return False
        self._rows[ev.id] = {k: None for k in FIELDS}
        self._change(ev.id, to=ev.to, amount_wei=ev.amount_wei, outcome="generated",
                     t_intended=ev.t_intended, t_generated=ev.t_generated, attempts=0, error="")
        if self.pending() > self.max_queue:
            self._change(ev.id, outcome="failed", error="queue full")
        return True

    def queued(self, id_):
        self._change(id_, outcome="queued", t_queued=time.time())

    def submitted(self, id_, t):
        self._change(id_, outcome="submitted", t_submitted=t)

    def finish(self, id_, res):
        ok = res.status == 1
        self._change(id_,
            outcome="succeeded" if ok else ("unresolved" if res.error.startswith("unresolved") else "failed"),
            t_submitted=res.t_submitted or self._rows[id_]["t_submitted"], t_confirmed=res.t_confirmed,
            tx_hash=res.tx_hash, block=res.block, gas_used=res.gas_used,
            effective_gas_price=res.effective_gas_price, attempts=res.attempts,
            error=res.error or ("" if ok else "reverted"))

    def unresolve_pending(self):
        for id_, r in self._rows.items():
            if r["outcome"] in ("generated", "queued", "submitted"):
                self._change(id_, outcome="unresolved", error=r["error"] or "drain deadline")

    def rows(self):
        return list(self._rows.values())

    def write_csv(self, path):
        self._log.flush()
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(self._rows.values())

    def reconcile(self, observed_wei: int) -> dict:
        ok = sum(int(r["amount_wei"]) for r in self._rows.values() if r["outcome"] == "succeeded")
        counts = {o: 0 for o in OUTCOMES}
        for r in self._rows.values():
            counts[r["outcome"]] += 1
        return {"succeeded_wei": ok, "observed_wei": observed_wei, "difference_wei": observed_wei - ok,
                "counts": counts}
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest measure.tests.test_ledger -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add measure/events.py measure/ledger.py measure/tests/test_ledger.py
git commit -m "measure: payment events and the payment ledger"
```

---

### Task 3: GESL reader

**Files:**
- Create: `measure/gesl.py`
- Test: `measure/tests/test_gesl.py`

**Interfaces:**
- Produces:
  - `KNOWN_SHA256: dict[int, str]` (the four signatures from cluster spec §7.2)
  - `csv_sha256(path) -> str`
  - `completeness(path) -> float` — share of non-NaN, non-empty cells
  - `pick_recording(paths: dict[int, str]) -> int` — highest completeness, ties → longer recording
  - `rows(path) -> Iterator[tuple[float, str]]` — (GESL `Time` in seconds, the raw CSV line without newline)

- [ ] **Step 1: Write the failing test**

```python
# measure/tests/test_gesl.py
"""GESL CSVs: verified by hash, rows yielded with their own time column."""
import glob
import os
import tempfile
import unittest
import zipfile

from measure import gesl

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def write(text):
    p = os.path.join(tempfile.mkdtemp(), "x.csv")
    with open(p, "w") as f:
        f.write(text)
    return p


class TestGesl(unittest.TestCase):
    def test_rows_yield_time_and_raw_line(self):
        p = write("Time,a,b\n0,1,2\n0.00833333333333333,3,NaN\n")
        self.assertEqual(list(gesl.rows(p)), [(0.0, "0,1,2"), (0.00833333333333333, "0.00833333333333333,3,NaN")])

    def test_completeness_counts_nan_and_empty(self):
        self.assertAlmostEqual(gesl.completeness(write("Time,a,b\n0,1,NaN\n1,,2\n")), 4 / 6)

    def test_pick_prefers_complete_then_longer(self):
        a = write("Time,a\n0,1\n1,NaN\n")
        b = write("Time,a\n0,1\n1,2\n")
        c = write("Time,a\n0,1\n1,2\n2,3\n")
        self.assertEqual(gesl.pick_recording({1: a, 2: b}), 2)
        self.assertEqual(gesl.pick_recording({2: b, 3: c}), 3)

    @unittest.skipUnless(glob.glob(os.path.join(REPO, "data/gesl-samples/sigId-*.zip")), "GESL data not present")
    def test_real_csv_hashes_match_the_spec(self):
        for sig, sha in gesl.KNOWN_SHA256.items():
            z = os.path.join(REPO, f"data/gesl-samples/sigId-{sig}.zip")
            out = tempfile.mkdtemp()
            with zipfile.ZipFile(z) as zf:
                name = f"sigId-{sig}-PMU.csv"
                zf.extract(name, out)
            self.assertEqual(gesl.csv_sha256(os.path.join(out, name)), sha, sig)
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 -m unittest measure.tests.test_gesl -v`
Expected: ERROR `No module named 'measure.gesl'`

- [ ] **Step 3: Implement**

```python
# measure/gesl.py
"""GESL (ORNL, LLNL 2023, https://gesl.ornl.gov) microPMU recordings.

The raw files are not published; the repository records only the signature
IDs and the SHA-256 of each CSV (cluster spec §7.2), checked here before use.
A row is replayed as its raw CSV line; its own `Time` column (seconds) defines
the UC1 windows, so a 10 ms window at 120 Hz holds one or two rows, never none.
"""
from __future__ import annotations

import hashlib
from typing import Iterator

KNOWN_SHA256 = {
    6006: "a08d7c330d2641c2568c85192e4e2c1221bcfe7b5de645f20404d0525f86be72",
    6021: "0198a44ce15611bf9febb884ac4cbf49250441277fba33442d11f91b4e2b5094",
    6062: "bf59576f7754a6ad013b9155bca23ca15e60be9c43b2d982bf9c1a11b7dd707f",
    6063: "87b2ad10cd5ba219a43f91d871635a142fa24e75016f2760108e297a6b6fd009",
}


def csv_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rows(path) -> Iterator[tuple[float, str]]:
    with open(path) as f:
        next(f)                                   # header
        for line in f:
            line = line.rstrip("\r\n")
            if line:
                yield float(line.split(",", 1)[0]), line


def completeness(path) -> float:
    good = total = 0
    for _, line in rows(path):
        cells = line.split(",")[1:]
        total += len(cells)
        good += sum(1 for c in cells if c not in ("", "NaN"))
    return good / total if total else 0.0


def pick_recording(paths: dict[int, str]) -> int:
    def key(sig):
        n = sum(1 for _ in rows(paths[sig]))
        return (round(completeness(paths[sig]), 4), n)
    return max(paths, key=key)
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest measure.tests.test_gesl -v`
Expected: 4 tests PASS (the hash test runs because `data/gesl-samples/` exists on the workstation)

- [ ] **Step 5: Decide the recording and record it**

Run:
```bash
python3 - <<'EOF'
import os, tempfile, zipfile
from measure import gesl
out = tempfile.mkdtemp(); paths = {}
for sig in gesl.KNOWN_SHA256:
    with zipfile.ZipFile(f"data/gesl-samples/sigId-{sig}.zip") as z:
        z.extract(f"sigId-{sig}-PMU.csv", out)
    paths[sig] = os.path.join(out, f"sigId-{sig}-PMU.csv")
for sig, p in paths.items():
    print(sig, round(gesl.completeness(p), 4), sum(1 for _ in gesl.rows(p)))
print("picked", gesl.pick_recording(paths))
EOF
```
Expected: four lines matching the figures measured on 2026-09-30 (6006 0.9038/32358 rows, 6021 0.6731/31984, 6062 0.9191/37122, 6063 0.9231/116988) and `picked 6063`. Write the decision into cluster spec §7.2 under *Decided in flight* (replace "which of the four recordings is used" with: recording 6063, the most complete and the only one long enough for 15-minute runs).

- [ ] **Step 6: Commit**

```bash
git add measure/gesl.py measure/tests/test_gesl.py docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md
git commit -m "measure: GESL reader; pick the most complete recording"
```

---

### Task 4: Sensor and customer (data path and windows)

**Files:**
- Create: `measure/wire.py`, `measure/sensor.py`, `measure/customer.py`
- Test: `measure/tests/test_uc1_stream.py`

**Interfaces:**
- Consumes: `gesl.rows` (Task 3), `PaymentEvent`, `event_id` (Task 2)
- Produces:
  - `wire.send_frame(writer, obj)`, `wire.read_frame(reader) -> dict | None`
  - `sensor.serve(csv_path, port, customers: int, duration_s: float, rate_hz: float, out_csv) -> dict` (async; returns `{"rows": n, "start": t0}`)
  - CLI: `python3 -m measure.sensor --csv P --port 7000 --customers 6 --duration 300 --out rows_sent.csv`
  - `customer.window_of(t: float, window_s: float) -> int`
  - `customer.closes_window(t: float, window_s: float, dt: float) -> bool`
  - `customer.run(sensor: str, name: int, run_id: str, window_ms: int, pay_to: str, price_wei: int, emit, out_csv) -> dict` (async; `emit(event)` is an async callable)
  - CLI: `python3 -m measure.customer --sensor 192.168.178.211:7000 --customer 1 --run R --window-ms 1000 --pay-to 0x.. --price-wei 1000000000000 --socket /tmp/measure-pay.sock --out rows_received.csv`
  - Frames: `{"t":"hello","c":n}` (customer→sensor), `{"t":"start","at":t0}`, `{"t":"row","s":seq,"ts":sent_time,"row":line}`, `{"t":"end","rows":n}`

- [ ] **Step 1: Write the failing test**

```python
# measure/tests/test_uc1_stream.py
"""Sensor streams every row to every customer; customers pay once per window."""
import asyncio
import os
import tempfile
import unittest

from measure import customer, sensor


def csv_120hz(seconds):
    p = os.path.join(tempfile.mkdtemp(), "g.csv")
    with open(p, "w") as f:
        f.write("Time,a\n")
        for k in range(int(seconds * 120)):
            f.write(f"{k / 120!r},{k}\n")
    return p


class TestWindows(unittest.TestCase):
    def test_ten_ms_windows_are_never_empty(self):
        ts = [k / 120 for k in range(240)]
        wins = {customer.window_of(t, 0.010) for t in ts}
        closed = [t for t in ts if customer.closes_window(t, 0.010, 1 / 120)]
        self.assertEqual(len(closed), len(wins) - 0)      # every window closes exactly once
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

        srv = asyncio.create_task(sensor.serve(path, 17000, customers=2, duration_s=2.0, rate_hz=120,
                                               out_csv=os.path.join(tmp, "sent.csv")))
        await asyncio.sleep(0.2)
        res = await asyncio.gather(*[
            customer.run("127.0.0.1:17000", n, "t", 100, "0x" + "22" * 20, 7, emitter(n),
                         os.path.join(tmp, f"recv{n}.csv")) for n in (1, 2)])
        sent = await srv
        self.assertEqual(sent["rows"], 240)
        for r in res:
            self.assertEqual(r["rows"], 240)
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

        srv = asyncio.create_task(sensor.serve(path, 17001, customers=2, duration_s=0.5, rate_hz=120,
                                               out_csv=os.path.join(tmp, "sent.csv")))
        await asyncio.sleep(0.2)
        first = asyncio.create_task(customer.run("127.0.0.1:17001", 1, "t", 1000, "0x" + "22" * 20, 1, emit,
                                                 os.path.join(tmp, "r1.csv")))
        await asyncio.sleep(1.0)                               # the second customer is late
        second = asyncio.create_task(customer.run("127.0.0.1:17001", 2, "t", 1000, "0x" + "22" * 20, 1, emit,
                                                  os.path.join(tmp, "r2.csv")))
        r1, r2 = await asyncio.gather(first, second)
        await srv
        self.assertEqual(r1["rows"], r2["rows"])               # nobody missed the beginning
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 -m unittest measure.tests.test_uc1_stream -v`
Expected: ERROR `cannot import name 'customer'`

- [ ] **Step 3: Implement wire, sensor and customer**

```python
# measure/wire.py
"""Length-prefixed JSON frames over TCP, as tank-arena/net/protocol.py."""
import asyncio
import json
import struct

_HDR = struct.Struct(">I")
MAX_FRAME = 1 << 20


async def send_frame(writer: asyncio.StreamWriter, obj) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode()
    writer.write(_HDR.pack(len(body)) + body)
    await writer.drain()


async def read_frame(reader: asyncio.StreamReader):
    try:
        (n,) = _HDR.unpack(await reader.readexactly(_HDR.size))
        if n > MAX_FRAME:
            raise ValueError(f"oversized frame: {n}")
        return json.loads(await reader.readexactly(n))
    except (asyncio.IncompleteReadError, ConnectionResetError):
        return None
```

```python
# measure/sensor.py
"""UC1 sensor: replays one GESL recording at 120 rows per second and sends the
whole stream to every customer over its own TCP connection (cluster spec §7.2).
It waits until all customers have said hello, announces a common start instant
2 s ahead, then paces rows by the monotonic clock. A customer that disconnects
is logged and dropped; the others continue."""
from __future__ import annotations

import argparse
import asyncio
import csv
import time

from measure import gesl
from measure.wire import read_frame, send_frame


async def serve(csv_path, port, customers, duration_s, rate_hz, out_csv) -> dict:
    conns: dict[int, asyncio.StreamWriter] = {}
    all_in = asyncio.Event()

    async def on_connect(reader, writer):
        hello = await read_frame(reader)
        if not hello or hello.get("t") != "hello":
            writer.close()
            return
        conns[int(hello["c"])] = writer
        if len(conns) >= customers:
            all_in.set()

    server = await asyncio.start_server(on_connect, "0.0.0.0", port)
    await all_in.wait()
    t0 = time.time() + 2.0
    for w in conns.values():
        await send_frame(w, {"t": "start", "at": t0})
    await asyncio.sleep(max(0.0, t0 - time.time()))
    m0 = time.monotonic()
    n = 0
    dropped = []
    with open(out_csv, "w", newline="") as f:
        log = csv.writer(f)
        log.writerow(["seq", "gesl_time", "t_sent"])
        for seq, (gt, line) in enumerate(gesl.rows(csv_path)):
            if seq >= int(duration_s * rate_hz):
                break
            await asyncio.sleep(max(0.0, m0 + seq / rate_hz - time.monotonic()))
            ts = time.time()
            for c, w in list(conns.items()):
                try:
                    await send_frame(w, {"t": "row", "s": seq, "ts": ts, "row": line})
                except (ConnectionError, OSError):
                    dropped.append(c)
                    conns.pop(c)
            log.writerow([seq, gt, ts])
            n += 1
    for w in conns.values():
        await send_frame(w, {"t": "end", "rows": n})
        w.close()
    server.close()
    return {"rows": n, "start": t0, "dropped": dropped}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--port", type=int, default=7000)
    ap.add_argument("--customers", type=int, default=6)
    ap.add_argument("--duration", type=float, default=300)
    ap.add_argument("--rate", type=float, default=120)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(asyncio.run(serve(a.csv, a.port, a.customers, a.duration, a.rate, a.out)), flush=True)


if __name__ == "__main__":
    main()
```

```python
# measure/customer.py
"""UC1 customer: receives the whole stream, closes windows on the GESL time
column and, postpay, emits one payment event per received window to the local
payment client (cluster spec §7.2). A window closes on the row whose successor
(one row period later) would fall into the next window, so the payment is not
delayed by waiting for that successor."""
from __future__ import annotations

import argparse
import asyncio
import csv
import math
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
    rows = windows = 0
    with open(out_csv, "w", newline="") as f:
        log = csv.writer(f)
        log.writerow(["seq", "t_sent", "t_received"])
        while True:
            fr = await read_frame(reader)
            if fr is None or fr["t"] == "end":
                break
            if fr["t"] != "row":
                continue
            now = time.time()
            rows += 1
            log.writerow([fr["s"], fr["ts"], now])
            gt = float(fr["row"].split(",", 1)[0])
            if closes_window(gt, window_s, dt):
                w = window_of(gt, window_s)
                await emit(PaymentEvent(event_id(run_id, name, w), pay_to, price_wei, now, time.time()))
                windows += 1
    writer.close()
    return {"rows": rows, "windows": windows}


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

    async def go():
        _, w = await asyncio.open_unix_connection(a.socket)

        async def emit(ev):
            w.write(ev.to_line())
            await w.drain()

        res = await run(a.sensor, a.customer, a.run, a.window_ms, a.pay_to, a.price_wei, emit, a.out)
        w.write(b'{"end":true}\n')
        await w.drain()
        print(res, flush=True)

    asyncio.run(go())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest measure.tests.test_uc1_stream -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add measure/wire.py measure/sensor.py measure/customer.py measure/tests/test_uc1_stream.py
git commit -m "measure: UC1 sensor and customer, windows on the GESL time column"
```

---

### Task 5: Arbitrum lifecycle — Open, payment, Close

**Files:**
- Create: `measure/arbitrum.py`
- Test: `measure/tests/test_arbitrum.py`

**Interfaces:**
- Consumes: `keys.account` (Task 1)
- Produces:
  - `INBOX`, `ARBSYS`, `L1_CHAIN_ID = 1337`, `L2_CHAIN_ID = 412346`
  - `open_deposit(l1: Web3, l2: Web3, acct, amount_wei) -> dict` — keys `t_start`, `t_l1_confirmed`, `t_l2_credited`, `l1_gas_used`, `l1_fee_wei`, `l2_balance_wei`
  - `payment_tx(to: str, amount_wei: int, gas: int, max_fee: int) -> dict` (unsigned fields for `OrderedSubmitter.submit`)
  - `estimate_payment_gas(l2: Web3, frm: str, to: str) -> int` — estimate × 1.5
  - `close_withdraw(l2: Web3, acct, amount_wei) -> dict` — keys `t_start`, `t_confirmed`, `l2_gas_used`, `l2_fee_wei`, `status`
  - `fund_on_l1(l1: Web3, funder, to: str, amount_wei) -> None` — untimed setup

- [ ] **Step 1: Write the failing test (integration; skipped without the workstation stack)**

```python
# measure/tests/test_arbitrum.py
"""Open = deposit credited on the L2; Close = withdrawal submitted and included."""
import os
import unittest

from web3 import Web3

from measure import arbitrum, keys


@unittest.skipUnless(os.environ.get("ARB_L1") and os.environ.get("ARB_L2"), "needs the workstation Arbitrum stack")
class TestLifecycle(unittest.TestCase):
    def test_open_then_close_on_a_fresh_account(self):
        l1 = Web3(Web3.HTTPProvider(os.environ["ARB_L1"]))
        l2 = Web3(Web3.HTTPProvider(os.environ["ARB_L2"].replace("ws://", "http://")))
        fresh = keys.account(190)                       # outside the run range
        start_l2 = l2.eth.get_balance(fresh.address)
        arbitrum.fund_on_l1(l1, keys.account(keys.FUNDER), fresh.address, 10**18)
        o = arbitrum.open_deposit(l1, l2, fresh, 5 * 10**17)
        self.assertEqual(o["l2_balance_wei"] - start_l2, 5 * 10**17)
        self.assertLess(o["t_l1_confirmed"], o["t_l2_credited"])
        self.assertGreater(o["l1_gas_used"], 0)
        c = arbitrum.close_withdraw(l2, fresh, 10**17)
        self.assertEqual(c["status"], 1)
        self.assertGreater(c["l2_fee_wei"], 0)
```

- [ ] **Step 2: Run it to see it fail**

Start the workstation stack, then:
Run: `ARB_L1=http://127.0.0.1:8545 ARB_L2=ws://127.0.0.1:8547 python3 -m unittest measure.tests.test_arbitrum -v`
Expected: ERROR `cannot import name 'arbitrum'`

- [ ] **Step 3: Implement**

```python
# measure/arbitrum.py
"""Arbitrum lifecycle for UC1 (parent spec §5.1, §11.4).

Open  = bridge ETH from the L1 with Inbox.depositEth and wait until the L2
        balance shows it (timed; on 2026-09-30 the workstation copy credited in
        1.9 s).
Close = withdrawal *submission* with ArbSys.withdrawEth on the L2 (timed). The
        7-day challenge period that follows is documentation, reported apart.
Funding the fresh account on the L1 before Open is setup, not measured.
"""
from __future__ import annotations

import time

INBOX = "0xA520303d332b27763E3518000861EdBc9843ceCc"
ARBSYS = "0x0000000000000000000000000000000000000064"
L1_CHAIN_ID, L2_CHAIN_ID = 1337, 412346
_DEPOSIT = "0x439370b1"          # depositEth()
_WITHDRAW = "0x25e16063"         # withdrawEth(address)


def _send(w3, acct, tx):
    tx = dict(tx, nonce=w3.eth.get_transaction_count(acct.address, "pending"), chainId=w3.eth.chain_id,
              maxFeePerGas=2 * w3.eth.gas_price + 1, maxPriorityFeePerGas=0, type=2)
    tx.setdefault("gas", int(w3.eth.estimate_gas({k: tx[k] for k in ("from", "to", "value", "data") if k in tx}) * 1.5))
    h = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    return w3.eth.wait_for_transaction_receipt(h, timeout=300, poll_latency=0.05)


def fund_on_l1(l1, funder, to, amount_wei):
    r = _send(l1, funder, {"from": funder.address, "to": to, "value": amount_wei, "gas": 21000})
    if r["status"] != 1:
        raise RuntimeError("L1 funding reverted")


def open_deposit(l1, l2, acct, amount_wei) -> dict:
    before = l2.eth.get_balance(acct.address)
    t_start = time.time()
    r = _send(l1, acct, {"from": acct.address, "to": INBOX, "value": amount_wei, "data": _DEPOSIT})
    t_l1 = time.time()
    if r["status"] != 1:
        raise RuntimeError("depositEth reverted")
    while True:
        bal = l2.eth.get_balance(acct.address)
        if bal - before >= amount_wei:
            break
        if time.time() - t_start > 600:
            raise TimeoutError("deposit not credited on the L2 within 600 s")
        time.sleep(0.05)
    return {"t_start": t_start, "t_l1_confirmed": t_l1, "t_l2_credited": time.time(),
            "l1_gas_used": r["gasUsed"], "l1_fee_wei": r["gasUsed"] * r["effectiveGasPrice"],
            "l2_balance_wei": bal}


def estimate_payment_gas(l2, frm, to) -> int:
    return int(l2.eth.estimate_gas({"from": frm, "to": to, "value": 1}) * 1.5)


def payment_tx(to, amount_wei, gas, max_fee) -> dict:
    return {"to": to, "value": amount_wei, "gas": gas, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": 0}


def close_withdraw(l2, acct, amount_wei) -> dict:
    t_start = time.time()
    data = _WITHDRAW + acct.address[2:].lower().rjust(64, "0")
    r = _send(l2, acct, {"from": acct.address, "to": ARBSYS, "value": amount_wei, "data": data})
    return {"t_start": t_start, "t_confirmed": time.time(), "status": r["status"],
            "l2_gas_used": r["gasUsed"], "l2_fee_wei": r["gasUsed"] * r["effectiveGasPrice"]}
```

- [ ] **Step 4: Check the two function selectors, then run the test**

Run: `cast sig 'depositEth()' && cast sig 'withdrawEth(address)'`
Expected: `0x439370b1` and `0x25e16063` — if either differs, put the printed value into `_DEPOSIT` / `_WITHDRAW`.
Run: `ARB_L1=http://127.0.0.1:8545 ARB_L2=ws://127.0.0.1:8547 python3 -m unittest measure.tests.test_arbitrum -v`
Expected: PASS. Stop the stack.

- [ ] **Step 5: Commit**

```bash
git add measure/arbitrum.py measure/tests/test_arbitrum.py
git commit -m "measure: Arbitrum Open (deposit credited) and Close (withdrawal submitted)"
```

---

### Task 6: Local payment client

**Files:**
- Create: `measure/payclient.py`
- Test: `measure/tests/test_payclient.py`

**Interfaces:**
- Consumes: `OrderedSubmitter`, `SubmitResult` (Task 1); `PaymentEvent`, `PaymentLedger` (Task 2); `arbitrum.*` (Task 5)
- Produces:
  - `async serve_socket(path: str, on_event, on_end) -> None` — reads JSON lines; `{"end": true}` calls `on_end`
  - `async pay_loop(ledger, submitter, make_tx, events: asyncio.Queue, drain_s: float) -> None`
  - CLI: `python3 -m measure.payclient --system arbitrum --l1 http://192.168.178.210:8545 --l2 ws://192.168.178.210:8547 --account 101 --pay-to 0x.. --deposit-wei 1000000000000000000 --max-in-flight 64 --drain 60 --socket /tmp/measure-pay.sock --out DIR`
  - Output files in `DIR`: `ledger.log.jsonl` (append-only, during the run), `ledger.csv` (at the end), `lifecycle.json` (`open`, `close`, `ready_at`, `ended_at`, `complete: bool`, `reconciliation`), `payclient.log`
  - Readiness: after Open, it writes `DIR/ready` (the cluster script waits for this file on every customer board before starting the sensor)

- [ ] **Step 1: Write the failing test (fake submitter, real socket)**

```python
# measure/tests/test_payclient.py
"""Events in over the socket, one ledger row each, drained or unresolved."""
import asyncio
import json
import os
import tempfile
import time
import unittest

from measure import payclient
from measure.events import PaymentEvent
from measure.ledger import PaymentLedger
from measure.submitter import SubmitResult


class FakeSubmitter:
    def __init__(self, delay, fail_ids=()):
        self.delay, self.fail_ids = delay, set(fail_ids)

    async def submit(self, tx):
        fut = asyncio.get_running_loop().create_future()

        async def later():
            await asyncio.sleep(self.delay)
            ok = tx["_id"] not in self.fail_ids
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
        q = asyncio.Queue()
        for w in range(n):
            await q.put(ev(w))
        await q.put(None)
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
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 -m unittest measure.tests.test_payclient -v`
Expected: ERROR `cannot import name 'payclient'`

- [ ] **Step 3: Implement**

```python
# measure/payclient.py
"""Local payment client: the only code on a board that talks to the system
under test. It receives payment events (JSON lines, Unix socket) from the
use-case code, records each in the payment ledger and executes it. The same
interface serves UC1 and UC2; this file implements the rollup side for
Arbitrum (cluster spec §7.3).

Lifecycle for one run: Open (timed) -> write DIR/ready -> pay events as they
arrive -> on {"end": true} drain for at most --drain seconds -> Close (timed)
-> lifecycle.json. The ledger CSV is written on every change, so a crash
leaves everything known so far (the ledger appends to ledger.log.jsonl;
ledger.csv is written at the end); lifecycle.json has complete=false until
the very end.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

from web3 import Web3

from measure import arbitrum, keys
from measure.events import PaymentEvent
from measure.ledger import PaymentLedger
from measure.submitter import OrderedSubmitter


async def serve_socket(path, on_event, on_end):
    if os.path.exists(path):
        os.unlink(path)

    async def handle(reader, writer):
        while line := await reader.readline():
            obj = json.loads(line)
            if obj.get("end"):
                on_end()
                break
            await on_event(PaymentEvent(**obj))
        writer.close()

    server = await asyncio.start_unix_server(handle, path)
    async with server:
        await server.serve_forever()


async def pay_loop(ledger, submitter, make_tx, events: asyncio.Queue, drain_s):
    inflight = []

    async def track(ev_id, fut):
        res = await fut
        ledger.finish(ev_id, res)

    while True:
        ev = await events.get()
        if ev is None:
            break
        if not ledger.add(ev):
            continue                                          # duplicate ID: never pay twice
        if ledger.rows()[-1]["outcome"] == "failed":          # queue full
            continue
        ledger.queued(ev.id)
        fut = await submitter.submit(make_tx(ev))
        ledger.submitted(ev.id, time.time())
        inflight.append(asyncio.create_task(track(ev.id, fut)))
    if inflight:
        await asyncio.wait(inflight, timeout=drain_s)
    ledger.unresolve_pending()


def write_json(path, obj):
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(path + ".tmp", path)


async def main_async(a):
    os.makedirs(a.out, exist_ok=True)
    life = {"system": a.system, "account": a.account, "complete": False}
    write_json(os.path.join(a.out, "lifecycle.json"), life)
    acct = keys.account(a.account)
    l1 = Web3(Web3.HTTPProvider(a.l1))
    l2http = Web3(Web3.HTTPProvider(a.l2.replace("ws://", "http://")))
    life["open"] = await asyncio.to_thread(arbitrum.open_deposit, l1, l2http, acct, a.deposit_wei)
    write_json(os.path.join(a.out, "lifecycle.json"), life)
    gas = arbitrum.estimate_payment_gas(l2http, acct.address, a.pay_to)
    max_fee = 2 * l2http.eth.gas_price + 1
    sub = OrderedSubmitter(a.l2, acct, arbitrum.L2_CHAIN_ID, a.max_in_flight)
    await sub.start()
    ledger = PaymentLedger(os.path.join(a.out, "ledger.log.jsonl"), max_queue=a.max_queue)
    q: asyncio.Queue = asyncio.Queue()
    ended = asyncio.Event()

    async def on_event(ev):
        await q.put(ev)

    def on_end():
        ended.set()
        q.put_nowait(None)

    sock = asyncio.create_task(serve_socket(a.socket, on_event, on_end))
    open(os.path.join(a.out, "ready"), "w").close()
    life["ready_at"] = time.time()
    await pay_loop(ledger, sub, lambda e: arbitrum.payment_tx(e.to, e.amount_wei, gas, max_fee), q, a.drain)
    life["ended_at"] = time.time()
    ledger.write_csv(os.path.join(a.out, "ledger.csv"))
    sock.cancel()
    await sub.close()
    spent = sum(int(r["amount_wei"]) for r in ledger.rows() if r["outcome"] == "succeeded")
    life["reconciliation"] = ledger.reconcile(observed_wei=spent)
    left = l2http.eth.get_balance(acct.address)
    life["close"] = await asyncio.to_thread(arbitrum.close_withdraw, l2http, acct, left // 2)
    life["payment_gas_limit"] = gas
    life["complete"] = True
    write_json(os.path.join(a.out, "lifecycle.json"), life)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=["arbitrum"], required=True)
    ap.add_argument("--l1", required=True)
    ap.add_argument("--l2", required=True)
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--pay-to", required=True)
    ap.add_argument("--deposit-wei", type=int, required=True)
    ap.add_argument("--max-in-flight", type=int, default=64)
    ap.add_argument("--max-queue", type=int, default=10000)
    ap.add_argument("--drain", type=float, default=60)
    ap.add_argument("--socket", default="/tmp/measure-pay.sock")
    ap.add_argument("--out", required=True)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
```

Note on reconciliation: in this first path `observed_wei` is the sum the client itself confirmed; Task 10 compares it with the sensor's L2 balance change, which is the independent check (the sensor receives all customers' payments).

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest measure.tests.test_payclient -v`
Expected: 4 tests PASS

- [ ] **Step 5: End-to-end on the workstation (one customer, 20 s)**

Start the workstation stack. Then, in three terminals from the repo root:
```bash
# 1: payment client for customer 1 (account 101) -> pays the sensor (account 100)
python3 -c "from measure import keys, arbitrum; from web3 import Web3; l1=Web3(Web3.HTTPProvider('http://127.0.0.1:8545')); [arbitrum.fund_on_l1(l1, keys.account(0), keys.account(i).address, 2*10**18) for i in (100,101)]"
python3 -m measure.payclient --system arbitrum --l1 http://127.0.0.1:8545 --l2 ws://127.0.0.1:8547 --account 101 --pay-to $(python3 -c "from measure import keys; print(keys.account(100).address)") --deposit-wei 1000000000000000000 --out /tmp/uc1-e2e/c1
# 2: sensor (after /tmp/uc1-e2e/c1/ready exists)
unzip -o -q data/gesl-samples/sigId-6063.zip sigId-6063-PMU.csv -d /tmp/uc1-e2e
python3 -m measure.sensor --csv /tmp/uc1-e2e/sigId-6063-PMU.csv --port 7000 --customers 1 --duration 20 --out /tmp/uc1-e2e/sent.csv
# 3: customer
python3 -m measure.customer --sensor 127.0.0.1:7000 --customer 1 --run e2e --window-ms 100 --pay-to $(python3 -c "from measure import keys; print(keys.account(100).address)") --price-wei 1000000000000 --out /tmp/uc1-e2e/recv1.csv
```
Expected: `/tmp/uc1-e2e/c1/lifecycle.json` with `"complete": true`, `open` and `close` filled, and `ledger.csv` with 200 rows, all `succeeded`. Stop the stack.

- [ ] **Step 6: Commit**

```bash
git add measure/payclient.py measure/tests/test_payclient.py
git commit -m "measure: local payment client (Arbitrum) with Open, ledger, drain and Close"
```

---

### Task 7: Recorder and board facts

**Files:**
- Create: `deploy/recorder.sh`, `deploy/board-facts.sh`
- Test: run both on `bath2` and check the output

**Interfaces:**
- Produces:
  - `recorder.sh OUT_CSV DURATION_S` — every 5 s: `t,temp_c,throttled,arm_mhz,mem_used_mb,load1,sd_read_kb,sd_write_kb,sd_io_ms,bssid,freq_mhz,signal_dbm,chrony_offset_ms`
  - `board-facts.sh` — prints one JSON object: `host`, `model`, `serial`, `wifi_mac`, `kernel`, `bssid`, `freq_mhz`, `power_save`, `throttled`, `chrony_source`, `chrony_offset_ms`, `netem`, `disk_free_gb`, `temp_c`, `docker_up`

- [ ] **Step 1: Write the recorder**

```bash
#!/usr/bin/env bash
# recorder.sh — per-board sampler for a measurement run (cluster spec §6).
# Usage: recorder.sh OUT_CSV DURATION_S   (started detached by deploy/cluster)
out=$1; dur=$2; end=$((SECONDS + dur))
echo "t,temp_c,throttled,arm_mhz,mem_used_mb,load1,sd_read_kb,sd_write_kb,sd_io_ms,bssid,freq_mhz,signal_dbm,chrony_offset_ms" > "$out"
read r0 w0 io0 < <(awk '$3=="mmcblk0"{print $6, $10, $13}' /proc/diskstats)
while [ $SECONDS -lt $end ]; do
  t=$(vcgencmd measure_temp | tr -dc '0-9.')
  th=$(vcgencmd get_throttled | cut -d= -f2)
  mhz=$(( $(vcgencmd measure_clock arm | cut -d= -f2) / 1000000 ))
  mem=$(free -m | awk '/^Mem:/{print $3}')
  l1=$(cut -d' ' -f1 /proc/loadavg)
  read r w io < <(awk '$3=="mmcblk0"{print $6, $10, $13}' /proc/diskstats)
  link=$(iw dev wlan0 link)
  bssid=$(awk '/^Connected to/ {print $3}' <<< "$link")
  freq=$(awk '/freq:/ {print int($2)}' <<< "$link")
  sig=$(awk '/signal:/ {print $2}' <<< "$link")
  off=$(chronyc -c tracking 2>/dev/null | awk -F, '{printf "%.3f", $5*1000}')
  echo "$(date +%s.%N),$t,$th,$mhz,$mem,$l1,$(( (r - r0) / 2 )),$(( (w - w0) / 2 )),$((io - io0)),$bssid,$freq,$sig,$off" >> "$out"
  r0=$r; w0=$w; io0=$io
  sleep 5
done
```

- [ ] **Step 2: Write the board-facts script**

```bash
#!/usr/bin/env bash
# board-facts.sh — one JSON object describing this board right now, for the
# run manifest and the pre-flight check (cluster spec §5, §6).
link=$(iw dev wlan0 link)
python3 - "$link" <<'EOF'
import json, os, shutil, subprocess, sys
def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
link = sys.argv[1]
def field(prefix):
    for l in link.splitlines():
        l = l.strip()
        if l.startswith(prefix):
            return l.split(prefix, 1)[1].strip().split()[0]
    return None
track = sh("chronyc -c tracking").split(",")
print(json.dumps({
    "host": sh("hostname"),
    "model": open("/proc/device-tree/model").read().rstrip("\x00"),
    "serial": open("/proc/device-tree/serial-number").read().rstrip("\x00").lstrip("0"),
    "wifi_mac": open("/sys/class/net/wlan0/address").read().strip(),
    "kernel": os.uname().release,
    "bssid": field("Connected to"),
    "freq_mhz": int(float(field("freq:") or 0)),
    "power_save": sh("iw dev wlan0 get power_save").split(":")[-1].strip(),
    "throttled": sh("vcgencmd get_throttled").split("=")[-1],
    "chrony_source": track[1] if len(track) > 1 else None,
    "chrony_offset_ms": float(track[4]) * 1000 if len(track) > 4 else None,
    "netem": "netem" in sh("tc qdisc show dev wlan0"),
    "disk_free_gb": round(shutil.disk_usage("/").free / 2**30, 1),
    "temp_c": float(sh("vcgencmd measure_temp").split("=")[-1].rstrip("'C") or 0),
    "docker_up": sh("docker ps -q 2>/dev/null | wc -l"),
}))
EOF
```

- [ ] **Step 3: Test both on a board**

Run:
```bash
chmod +x deploy/recorder.sh deploy/board-facts.sh
scp deploy/recorder.sh deploy/board-facts.sh bath2:
ssh bath2 'bash board-facts.sh; bash recorder.sh /tmp/rec.csv 12; cat /tmp/rec.csv'
```
Expected: one JSON object with `bssid` `02:00:00:00:00:01`, `freq_mhz` 2462 and `power_save` `off`; a CSV header plus two or three rows with every column filled.

- [ ] **Step 4: Commit**

```bash
git add deploy/recorder.sh deploy/board-facts.sh
git commit -m "deploy: per-board recorder and board facts for runs"
```

---

### Task 8: Pre-flight

**Files:**
- Create: `measure/preflight.py`
- Test: `measure/tests/test_preflight.py`

**Interfaces:**
- Consumes: the JSON printed by `board-facts.sh` (Task 7)
- Produces:
  - `fleet_register(fleet_md_path) -> dict[str, dict]` — `host -> {"wifi_mac", "serial"}` from the table in `deploy/FLEET.md`
  - `check(facts: dict[str, dict], register, *, bssid, clean_power: set[str], max_offset_ms: float, min_disk_gb: float, stack_board: str, expect_docker: bool) -> list[str]` — list of failure messages, empty = pass
  - CLI: `python3 -m measure.preflight FACTS_DIR` (one `<host>.json` per board) → exit 0 on pass, 1 with the failures printed

- [ ] **Step 1: Write the failing test**

```python
# measure/tests/test_preflight.py
"""Each pre-flight rule of cluster spec §5 fails on its own condition."""
import os
import unittest

from measure import preflight

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOOD = dict(model="Raspberry Pi 3 Model B Rev 1.2", kernel="6.8.0-1065-raspi", bssid="02:00:00:00:00:01",
            freq_mhz=2462, power_save="off", throttled="0x0", chrony_source="C0A8B2D3",
            chrony_offset_ms=0.4, netem=False, disk_free_gb=20.0, docker_up="0")


def facts(**over):
    reg = preflight.fleet_register(os.path.join(REPO, "deploy/FLEET.md"))
    out = {}
    for h, r in reg.items():
        out[h] = dict(GOOD, host=h, wifi_mac=r["wifi_mac"], serial=r["serial"])
    out["bath0"]["docker_up"] = "3"
    for (h, k), v in over.items():
        out[h][k] = v
    return out, reg


def run(f, reg):
    return preflight.check(f, reg, bssid="02:00:00:00:00:01", clean_power={"bath0", "bath1"},
                           max_offset_ms=5.0, min_disk_gb=5.0, stack_board="bath0", expect_docker=True)


class TestPreflight(unittest.TestCase):
    def test_register_has_eight_boards(self):
        reg = preflight.fleet_register(os.path.join(REPO, "deploy/FLEET.md"))
        self.assertEqual(sorted(reg), [f"bath{i}" for i in range(8)])

    def test_all_good_passes(self):
        self.assertEqual(run(*facts()), [])

    def test_each_rule(self):
        cases = {
            ("bath3", "wifi_mac"): "aa:bb:cc:dd:ee:ff",
            ("bath2", "bssid"): "02:00:00:00:00:02",
            ("bath4", "kernel"): "6.8.0-1064-raspi",
            ("bath5", "chrony_offset_ms"): 12.0,
            ("bath6", "power_save"): "on",
            ("bath1", "throttled"): "0x50000",
            ("bath7", "netem"): True,
            ("bath0", "disk_free_gb"): 1.0,
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                self.assertTrue(run(*facts(**{key: value})), key)

    def test_undervolted_pi3b_is_recorded_not_failed(self):
        self.assertEqual(run(*facts(**{("bath5", "throttled"): "0x50005"})), [])

    def test_soft_temperature_bit_is_allowed_on_clean_boards(self):
        self.assertEqual(run(*facts(**{("bath0", "throttled"): "0x80000"})), [])

    def test_missing_board_fails(self):
        f, reg = facts()
        del f["bath6"]
        self.assertIn("bath6: unreachable", run(f, reg))
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 -m unittest measure.tests.test_preflight -v`
Expected: ERROR `cannot import name 'preflight'`

- [ ] **Step 3: Implement**

```python
# measure/preflight.py
"""Pre-flight checks before every run (cluster spec §5): abort on any failure.

Power: bath0 and bath1 must be clean (the soft-temperature bit 0x80000
excepted); the Pi 3Bs are undervolted under load on every supply, so their
flags are recorded in the manifest, not a reason to stop (deploy/FLEET.md).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

SOFT_TEMP = 0x80000


def fleet_register(path) -> dict:
    reg = {}
    for line in open(path):
        m = re.match(r"\|\s*\d+\s*\|\s*`(bath\d)`\s*\|[^|]*\|\s*`([0-9a-f:]+)`\s*\|\s*`([0-9a-f]+)`", line)
        if m:
            reg[m.group(1)] = {"wifi_mac": m.group(2), "serial": m.group(3).lstrip("0")}
    return reg


def check(facts, register, *, bssid, clean_power, max_offset_ms, min_disk_gb, stack_board, expect_docker):
    bad = []
    kernels = {f["kernel"] for f in facts.values()}
    if len(kernels) > 1:
        bad.append(f"kernels differ: {sorted(kernels)}")
    for host, reg in sorted(register.items()):
        f = facts.get(host)
        if f is None:
            bad.append(f"{host}: unreachable")
            continue
        if f["wifi_mac"] != reg["wifi_mac"] or f["serial"] != reg["serial"]:
            bad.append(f"{host}: card not in its own board ({f['wifi_mac']}, {f['serial']})")
        if f["bssid"] != bssid:
            bad.append(f"{host}: on {f['bssid']} ({f['freq_mhz']} MHz), not {bssid}")
        if f["power_save"] != "off":
            bad.append(f"{host}: Wi-Fi power save {f['power_save']}")
        if host in clean_power and int(f["throttled"], 16) & ~SOFT_TEMP:
            bad.append(f"{host}: throttle flags {f['throttled']} (reboot it)")
        off = f["chrony_offset_ms"]
        if host != "bath1" and (off is None or abs(off) > max_offset_ms):
            bad.append(f"{host}: clock offset {off} ms")
        if f["netem"]:
            bad.append(f"{host}: netem delay present")
        if f["disk_free_gb"] < min_disk_gb:
            bad.append(f"{host}: {f['disk_free_gb']} GB free")
        up = int(f["docker_up"] or 0)
        if host == stack_board and expect_docker and up == 0:
            bad.append(f"{host}: stack not up")
        if host != stack_board and up:
            bad.append(f"{host}: unexpected containers running")
    return bad


def main():
    d = sys.argv[1]
    facts = {os.path.basename(p)[:-5]: json.load(open(p)) for p in glob.glob(os.path.join(d, "*.json"))}
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = check(facts, fleet_register(os.path.join(repo, "deploy/FLEET.md")), bssid="02:00:00:00:00:01",
                clean_power={"bath0", "bath1"}, max_offset_ms=5.0, min_disk_gb=5.0, stack_board="bath0",
                expect_docker=True)
    for b in bad:
        print("PRE-FLIGHT FAIL:", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest measure.tests.test_preflight -v`
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add measure/preflight.py measure/tests/test_preflight.py
git commit -m "measure: pre-flight checks from cluster spec §5"
```

---

### Task 9: The control script `deploy/cluster`

**Files:**
- Create: `deploy/cluster`, `deploy/experiments/uc1-arbitrum-w1000.env`, `deploy/experiments/uc1-arbitrum-w100.env`, `deploy/experiments/uc1-arbitrum-w10.env`
- Modify: `bath0:~/arbitrum/docker-compose.yaml` (L1 port `127.0.0.1:8545` → `0.0.0.0:8545`, so customers can deposit; the L2 port is already open)

**Interfaces:**
- Consumes: everything above; `deploy/recorder.sh`, `deploy/board-facts.sh`; the Arbitrum snapshot tarballs in `bath0:~/arbitrum/*.tgz`
- Produces (commands):
  - `deploy/cluster sync` — copies `measure/`, `deploy/recorder.sh`, `deploy/board-facts.sh` to `bath1`…`bath7` (`~/measure-code/`), creates `~/venv` with `measure/requirements.txt` where missing, copies the picked GESL CSV to `bath1:~/gesl/`
  - `deploy/cluster up arbitrum` — restores the snapshot volumes from the tarballs on `bath0`, starts the three containers, waits for both RPCs
  - `deploy/cluster down` — stops every container on `bath0`, removes any `netem` qdisc on every board
  - `deploy/cluster status` — one line per board from `board-facts.sh`
  - `deploy/cluster preflight` — gathers facts into a temp dir and runs `measure.preflight`
  - `deploy/cluster run <experiment> [--reps N]` — per repetition: `down`, `up`, **cooling gate** (every board within 3 °C of its idle temperature from `results/pi/idle-temps.txt`, at most 20 min, start temperatures to `start-temps.txt`), `preflight`, setup (fund accounts 100–106 on the L1), recorder on all boards, payment clients on `bath2`–`bath7`, wait for `ready`, sensor on `bath1`, customers, wait, collect into `results/pi/uc1/<date>-arbitrum-<exp>-rep<k>/`, write `manifest.json` (Task 10), `down`

- [ ] **Step 1: Experiment files**

```bash
# deploy/experiments/uc1-arbitrum-w1000.env
USE_CASE=uc1
STACK=arbitrum
WINDOW_MS=1000
DURATION_S=900
PRICE_WEI=1000000000000
DEPOSIT_WEI=1000000000000000000
FUND_WEI=2000000000000000000
MAX_IN_FLIGHT=64
DRAIN_S=60
```
`uc1-arbitrum-w100.env` and `uc1-arbitrum-w10.env` are identical except `WINDOW_MS=100` and `WINDOW_MS=10`. (`DURATION_S=900` = 15 minutes of GESL recording 6063 per run, decided 2026-09-30 so that heat and steady state show; 900, 9 000 and 90 000 payments per customer. The deposit of 1 ETH covers 90 000 payments of 10¹² wei (0.09 ETH) plus fees (about 2·10⁻⁶ ETH each on the local chain, 0.18 ETH) with a wide margin — parent §11.5.)

- [ ] **Step 2: Write the script**

```bash
#!/usr/bin/env bash
# cluster — drive the Pi cluster from the workstation (cluster spec §5).
# Usage: deploy/cluster sync|up <stack>|down|status|preflight|run <experiment> [--reps N]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BOARDS=(bath0 bath1 bath2 bath3 bath4 bath5 bath6 bath7)
CUSTOMERS=(bath2 bath3 bath4 bath5 bath6 bath7)
PY='~/venv/bin/python -u'
L1=http://192.168.178.210:8545; L2=ws://192.168.178.210:8547
GESL_SIG=${GESL_SIG:-6063}                 # decided 2026-09-30: most complete and the only 15-minute recording

on() { local h=$1; shift; ssh -o ConnectTimeout=8 "$h" "$@"; }
bg() { local h=$1; shift; ssh -f -o ConnectTimeout=8 "$h" "cd ~/measure-code && setsid nohup $* > /dev/null 2>&1 < /dev/null &"; }

cmd_sync() {
  local tmp; tmp=$(mktemp -d)
  unzip -o -q "$REPO/data/gesl-samples/sigId-$GESL_SIG.zip" "sigId-$GESL_SIG-PMU.csv" -d "$tmp"
  for h in "${BOARDS[@]:1}"; do
    on "$h" 'mkdir -p ~/measure-code/deploy ~/gesl'
    scp -q -r "$REPO/measure" "$h:measure-code/"
    scp -q "$REPO/deploy/recorder.sh" "$REPO/deploy/board-facts.sh" "$h:measure-code/deploy/"
    on "$h" '[ -x ~/venv/bin/python ] || { sudo -n apt-get install -y -qq python3-venv >/dev/null && python3 -m venv ~/venv; }; ~/venv/bin/pip install -q -r ~/measure-code/measure/requirements.txt' &
  done
  wait
  scp -q "$tmp/sigId-$GESL_SIG-PMU.csv" bath1:gesl/
  scp -q "$REPO/deploy/recorder.sh" "$REPO/deploy/board-facts.sh" bath0:
  rm -rf "$tmp"; echo "synced"
}

cmd_up() {
  [ "$1" = arbitrum ] || { echo "only arbitrum is wired so far" >&2; exit 2; }
  on bath0 'cd ~/arbitrum && docker compose down >/dev/null 2>&1; for v in config seqdata l1data l1keystore tokenbridge-data; do docker volume rm -f arbitrum_$v >/dev/null; docker volume create arbitrum_$v >/dev/null; docker run --rm -v arbitrum_$v:/v -v ~/arbitrum:/in alpine tar xzf /in/$v.tgz -C /v; done; docker compose up -d >/dev/null 2>&1'
  until curl -s -m 2 -X POST -H 'content-type: application/json' --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' "${L2/ws:/http:}" | grep -q result; do sleep 2; done
  until curl -s -m 2 -X POST -H 'content-type: application/json' --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' "$L1" | grep -q result; do sleep 2; done
  echo "arbitrum up (snapshot restored)"
}

cmd_down() {
  on bath0 'cd ~/arbitrum && docker compose stop -t 60 sequencer >/dev/null 2>&1; docker compose stop >/dev/null 2>&1; true'
  for h in "${BOARDS[@]}"; do on "$h" 'sudo -n tc qdisc del dev wlan0 root 2>/dev/null; true' & done; wait
  echo "down"
}

gather_facts() {
  local d=$1
  for h in "${BOARDS[@]}"; do
    (on "$h" 'bash ~/board-facts.sh 2>/dev/null || bash ~/measure-code/deploy/board-facts.sh' > "$d/$h.json" 2>/dev/null || rm -f "$d/$h.json") &
  done
  wait
}

cmd_status() {
  local d; d=$(mktemp -d); gather_facts "$d"
  for f in "$d"/*.json; do python3 -c "import json,sys; f=json.load(open(sys.argv[1])); print(f['host'], f['kernel'], f['bssid'], f['freq_mhz'], 'ps='+f['power_save'], f['throttled'], 'off=%.2fms' % (f['chrony_offset_ms'] or 0), 'docker='+f['docker_up'])" "$f"; done
}

cmd_preflight() {
  local d; d=$(mktemp -d); gather_facts "$d"
  (cd "$REPO" && python3 -m measure.preflight "$d")
}

addr() { python3 -c "from measure import keys; print(keys.account($1).address)"; }

# Cooling gate (decided 2026-09-30): start a run only when every board is below
# COOL_C, waiting at most COOL_MAX_S; the start temperatures go into the run folder.
COOL_C=${COOL_C:-50}; COOL_MAX_S=${COOL_MAX_S:-1200}
cool_gate() {
  local out=$1 waited=0 hot
  while :; do
    hot=""
    for h in "${BOARDS[@]}"; do
      t=$(on "$h" "vcgencmd measure_temp | tr -dc '0-9.'")
      echo "$h $t" >> "$out/start-temps.txt.tmp"
      awk -v t="$t" -v c="$COOL_C" 'BEGIN{exit !(t >= c)}' && hot="$hot $h:$t"
    done
    if [ -z "$hot" ]; then mv "$out/start-temps.txt.tmp" "$out/start-temps.txt"; echo "cool: all below ${COOL_C} C after ${waited}s"; return 0; fi
    rm -f "$out/start-temps.txt.tmp"
    if [ "$waited" -ge "$COOL_MAX_S" ]; then echo "cooling gate: still hot after ${COOL_MAX_S}s:$hot — run aborted"; return 1; fi
    sleep 30; waited=$((waited + 30))
  done
}

one_run() {
  local exp=$1 rep=$2
  # shellcheck disable=SC1090
  source "$REPO/deploy/experiments/$exp.env"
  local run_id; run_id="$(date +%Y%m%d-%H%M%S)-$STACK-$exp-rep$rep"
  local out="$REPO/results/pi/$USE_CASE/$run_id"
  mkdir -p "$out"/{results,logs,recorder,facts}
  cmd_down >/dev/null; cmd_up "$STACK"
  cool_gate "$out" || return 1
  gather_facts "$out/facts"; (cd "$REPO" && python3 -m measure.preflight "$out/facts") || { echo "pre-flight failed, run aborted"; return 1; }
  local sensor; sensor=$(cd "$REPO" && addr 100)
  (cd "$REPO" && python3 -c "
from web3 import Web3; from measure import keys, arbitrum
l1 = Web3(Web3.HTTPProvider('$L1'))
for i in range(100, 107): arbitrum.fund_on_l1(l1, keys.account(0), keys.account(i).address, $FUND_WEI)
print('funded 100..106 on the L1')")
  local rec_s=$((DURATION_S + DRAIN_S + 240))
  for h in "${BOARDS[@]}"; do
    if [ "$h" = bath0 ]; then ssh -f bath0 "setsid nohup bash ~/recorder.sh /tmp/rec-$run_id.csv $rec_s > /dev/null 2>&1 < /dev/null &"
    else bg "$h" "bash deploy/recorder.sh /tmp/rec-$run_id.csv $rec_s"; fi
  done
  local n=1
  for h in "${CUSTOMERS[@]}"; do
    on "$h" "rm -rf /tmp/pc-$run_id"
    bg "$h" "$PY -m measure.payclient --system $STACK --l1 $L1 --l2 $L2 --account $((100 + n)) --pay-to $sensor --deposit-wei $DEPOSIT_WEI --max-in-flight $MAX_IN_FLIGHT --drain $DRAIN_S --out /tmp/pc-$run_id"
    n=$((n + 1))
  done
  for h in "${CUSTOMERS[@]}"; do until on "$h" "[ -f /tmp/pc-$run_id/ready ]"; do sleep 2; done; done
  local l2http=${L2/ws:/http:}
  local before; before=$(cast balance "$sensor" --rpc-url "$l2http")
  bg bath1 "$PY -m measure.sensor --csv ~/gesl/sigId-$GESL_SIG-PMU.csv --port 7000 --customers ${#CUSTOMERS[@]} --duration $DURATION_S --out /tmp/sent-$run_id.csv"
  sleep 2
  n=1
  for h in "${CUSTOMERS[@]}"; do
    bg "$h" "$PY -m measure.customer --sensor 192.168.178.211:7000 --customer $n --run $run_id --window-ms $WINDOW_MS --pay-to $sensor --price-wei $PRICE_WEI --out /tmp/recv-$run_id.csv"
    n=$((n + 1))
  done
  for h in "${CUSTOMERS[@]}"; do
    until on "$h" "python3 -c \"import json,sys; sys.exit(0 if json.load(open('/tmp/pc-$run_id/lifecycle.json')).get('complete') else 1)\"" 2>/dev/null; do sleep 10; done
  done
  local after; after=$(cast balance "$sensor" --rpc-url "$l2http")
  echo "{\"sensor\": \"$sensor\", \"l2_balance_before_wei\": $before, \"l2_balance_after_wei\": $after}" > "$out/results/sensor-balance.json"
  scp -q "bath1:/tmp/sent-$run_id.csv" "$out/results/rows_sent.csv"
  n=1
  for h in "${CUSTOMERS[@]}"; do
    mkdir -p "$out/results/c$n"
    scp -q -r "$h:/tmp/pc-$run_id/." "$out/results/c$n/"
    scp -q "$h:/tmp/recv-$run_id.csv" "$out/results/c$n/rows_received.csv"
    n=$((n + 1))
  done
  for h in "${BOARDS[@]}"; do scp -q "$h:/tmp/rec-$run_id.csv" "$out/recorder/$h.csv" 2>/dev/null || true; done
  on bath0 "cd ~/arbitrum && docker compose logs --no-color --since 30m" > "$out/logs/bath0-docker.log" 2>&1 || true
  cp "$REPO/deploy/experiments/$exp.env" "$out/experiment.env"
  (cd "$REPO" && python3 -m measure.manifest "$out")
  cmd_down >/dev/null
  echo "$out"
}

cmd_run() {
  local exp=$1 reps=1
  [ "${2:-}" = --reps ] && reps=$3
  for rep in $(seq 1 "$reps"); do one_run "$exp" "$rep"; done
}

case "${1:-}" in
  sync) cmd_sync ;; up) cmd_up "$2" ;; down) cmd_down ;; status) cmd_status ;;
  preflight) cmd_preflight ;; run) shift; cmd_run "$@" ;;
  *) echo "usage: $0 sync|up <stack>|down|status|preflight|run <experiment> [--reps N]" >&2; exit 2 ;;
esac
```

- [ ] **Step 3: Open the L1 port on `bath0` and check `sync`, `up`, `status`, `preflight`, `down`**

**Checkpoint 2: ask the project owner before running the `sed` below** (it makes the L1 reachable from the whole home network, which the customers need for their deposits).

Run:
```bash
chmod +x deploy/cluster
ssh bath0 "sed -i 's/127.0.0.1:8545:8545/0.0.0.0:8545:8545/' ~/arbitrum/docker-compose.yaml && grep -n '8545:8545' ~/arbitrum/docker-compose.yaml"
deploy/cluster sync && deploy/cluster up arbitrum && deploy/cluster status && deploy/cluster preflight; echo "preflight rc=$?"; deploy/cluster down
```
Expected: `0.0.0.0:8545:8545`; `synced`; `arbitrum up (snapshot restored)`; eight status lines with the same kernel, `02:00:00:00:00:01`, `ps=off`; `preflight rc=0` (fix any board it names and rerun); `down`.

- [ ] **Step 4: Commit**

```bash
git add deploy/cluster deploy/experiments/
git commit -m "deploy: cluster control script and UC1 Arbitrum experiments"
```

---

### Task 10: Manifest, analysis and aggregation

**Files:**
- Create: `measure/manifest.py`, `measure/analyze_uc1.py`, `measure/aggregate.py`
- Test: `measure/tests/test_analyze.py`

**Interfaces:**
- Consumes: the run folder written by Task 9 (`experiment.env`, `facts/*.json`, `results/c<n>/ledger.csv`, `results/c<n>/lifecycle.json`, `results/sensor-balance.json`, `recorder/*.csv`)
- Produces:
  - `python3 -m measure.manifest RUN_DIR` → `RUN_DIR/manifest.json` with `git_commit`, `git_dirty`, `experiment`, `boards` (the facts), `images` (from `bath0` `docker compose images`), `recorder_summary` per board (share of samples undervolted, mean clock, max temperature, BSSIDs seen)
  - `analyze(run_dir) -> dict` and `python3 -m measure.analyze_uc1 RUN_DIR` → `RUN_DIR/summary.json` with: `open_s` (per customer, L2-credited − start), `payment_latency_s` (p50/p90/p99/max over all succeeded payments, `t_confirmed − t_intended`), `deadline_miss_share` (payment of window k not confirmed before window k+1's `t_intended`), `outcomes` (counts), `reconciliation` (succeeded sum vs sensor's L2 balance change), `close_s`, `gas` (L1 deposit gas, L2 payment gas median, withdrawal gas), `fees_eth` (deposit, payments total, withdrawal), `complete` (all customers complete)
  - `python3 -m measure.aggregate RUN_DIR RUN_DIR RUN_DIR` → prints mean ± sd (n−1), min–max and per-run values for the headline figures; refuses when `git_commit` differs between runs

- [ ] **Step 1: Write the failing test (a synthetic run folder)**

```python
# measure/tests/test_analyze.py
"""The analysis turns a run folder into the thesis numbers, and says so when
something does not add up."""
import csv
import json
import os
import tempfile
import unittest

from measure import aggregate, analyze_uc1
from measure.ledger import FIELDS


def make_run(latencies, unresolved=0, sensor_delta=None, commit="abc", complete=True):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "results", "c1"))
    rows, total = [], 0
    for k, lat in enumerate(latencies):
        t = 1000.0 + k
        rows.append(dict({f: "" for f in FIELDS}, id=f"uc1/r/c1/w{k}", amount_wei=10, outcome="succeeded",
                         t_intended=t, t_generated=t, t_queued=t, t_submitted=t, t_confirmed=t + lat,
                         gas_used=21000, effective_gas_price=10**8))
        total += 10
    for k in range(unresolved):
        rows.append(dict({f: "" for f in FIELDS}, id=f"uc1/r/c1/u{k}", amount_wei=10, outcome="unresolved",
                         t_intended=2000.0, t_generated=2000.0))
    with open(os.path.join(d, "results", "c1", "ledger.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    life = {"complete": complete, "open": {"t_start": 900.0, "t_l2_credited": 902.5, "l1_gas_used": 91067,
                                       "l1_fee_wei": 91067 * 10**9},
            "close": {"t_start": 3000.0, "t_confirmed": 3000.03, "l2_gas_used": 67571, "l2_fee_wei": 67571 * 10**8}}
    json.dump(life, open(os.path.join(d, "results", "c1", "lifecycle.json"), "w"))
    json.dump({"l2_balance_before_wei": 0, "l2_balance_after_wei": total if sensor_delta is None else sensor_delta},
              open(os.path.join(d, "results", "sensor-balance.json"), "w"))
    json.dump({"git_commit": commit}, open(os.path.join(d, "manifest.json"), "w"))
    return d


class TestAnalyze(unittest.TestCase):
    def test_latency_and_deadline(self):
        s = analyze_uc1.analyze(make_run([0.2, 0.3, 1.5, 0.25]))
        self.assertAlmostEqual(s["payment_latency_s"]["p50"], 0.3, places=6)
        self.assertAlmostEqual(s["payment_latency_s"]["max"], 1.5)
        self.assertAlmostEqual(s["deadline_miss_share"], 1 / 4)   # 1.5 s > the 1 s to the next window
        self.assertAlmostEqual(s["open_s"]["c1"], 2.5)
        self.assertAlmostEqual(s["close_s"]["c1"], 0.03, places=6)

    def test_unresolved_is_counted_and_reconciliation_flags_a_gap(self):
        s = analyze_uc1.analyze(make_run([0.2, 0.2], unresolved=1, sensor_delta=10))
        self.assertEqual(s["outcomes"]["unresolved"], 1)
        self.assertEqual(s["reconciliation"]["difference_wei"], -10)
        self.assertFalse(s["reconciliation"]["ok"])

    def test_a_crashed_payment_client_marks_the_run_incomplete(self):
        s = analyze_uc1.analyze(make_run([0.2, 0.2], complete=False))
        self.assertFalse(s["complete"])

    def test_aggregate_mean_sd_and_refuses_mixed_commits(self):
        runs = [make_run([0.2, 0.2]), make_run([0.4, 0.4]), make_run([0.3, 0.3])]
        for r in runs:
            json.dump(analyze_uc1.analyze(r), open(os.path.join(r, "summary.json"), "w"))
        agg = aggregate.aggregate(runs)
        self.assertAlmostEqual(agg["payment_latency_p50_s"]["mean"], 0.3, places=6)
        self.assertAlmostEqual(agg["payment_latency_p50_s"]["sd"], 0.1, places=6)
        mixed = runs[:2] + [make_run([0.3], commit="other")]
        json.dump(analyze_uc1.analyze(mixed[2]), open(os.path.join(mixed[2], "summary.json"), "w"))
        with self.assertRaises(ValueError):
            aggregate.aggregate(mixed)
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 -m unittest measure.tests.test_analyze -v`
Expected: ERROR `cannot import name 'aggregate'`

- [ ] **Step 3: Implement the analysis and aggregation**

```python
# measure/analyze_uc1.py
"""One UC1 run folder -> summary.json (parent spec §5; cluster spec §7.2, §7.3).

Payment latency = confirmation - window arrival (t_intended), over succeeded
payments. Deadline miss = the payment for window k is not confirmed before
window k+1 arrives. Reconciliation compares the sum the customers' ledgers
call succeeded with the sensor's L2 balance change, which is independent of
the ledgers. Close is the withdrawal submission (parent §11.4).
"""
from __future__ import annotations

import csv
import glob
import json
import os
import statistics
import sys

from measure.ledger import load_log


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else None


def analyze(run_dir) -> dict:
    lat, misses, windows, outcomes = [], 0, 0, {}
    open_s, close_s, gas_pay, fees_pay, complete = {}, {}, [], 0, True
    dep_gas, dep_fee, wd_gas, wd_fee = [], 0, [], 0
    ok_sum = 0
    for cdir in sorted(glob.glob(os.path.join(run_dir, "results", "c*"))):
        c = os.path.basename(cdir)
        csv_path = os.path.join(cdir, "ledger.csv")
        rows = (list(csv.DictReader(open(csv_path))) if os.path.exists(csv_path)
                else load_log(os.path.join(cdir, "ledger.log.jsonl")))   # crashed client: rebuild
        life = json.load(open(os.path.join(cdir, "lifecycle.json")))
        complete &= bool(life.get("complete"))
        if "open" in life:
            open_s[c] = life["open"]["t_l2_credited"] - life["open"]["t_start"]
            dep_gas.append(life["open"]["l1_gas_used"]); dep_fee += life["open"]["l1_fee_wei"]
        if "close" in life:
            close_s[c] = life["close"]["t_confirmed"] - life["close"]["t_start"]
            wd_gas.append(life["close"]["l2_gas_used"]); wd_fee += life["close"]["l2_fee_wei"]
        rows.sort(key=lambda r: float(r["t_intended"]))
        for i, r in enumerate(rows):
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
            if r["outcome"] != "succeeded":
                continue
            ok_sum += int(r["amount_wei"])
            conf, intended = float(r["t_confirmed"]), float(r["t_intended"])
            lat.append(conf - intended)
            gas_pay.append(int(r["gas_used"]))
            fees_pay += int(r["gas_used"]) * int(r["effective_gas_price"])
            nxt = float(rows[i + 1]["t_intended"]) if i + 1 < len(rows) else intended + (intended - float(rows[i - 1]["t_intended"]) if i else 1.0)
            windows += 1
            misses += conf > nxt
    bal = json.load(open(os.path.join(run_dir, "results", "sensor-balance.json")))
    observed = bal["l2_balance_after_wei"] - bal["l2_balance_before_wei"]
    return {
        "complete": complete,
        "open_s": open_s, "close_s": close_s,
        "payment_latency_s": {"n": len(lat), "p50": _pct(lat, .5), "p90": _pct(lat, .9), "p99": _pct(lat, .99),
                              "max": max(lat) if lat else None},
        "deadline_miss_share": misses / windows if windows else None,
        "outcomes": outcomes,
        "reconciliation": {"succeeded_wei": ok_sum, "sensor_received_wei": observed,
                           "difference_wei": observed - ok_sum, "ok": observed == ok_sum},
        "gas": {"deposit_l1_median": statistics.median(dep_gas) if dep_gas else None,
                "payment_l2_median": statistics.median(gas_pay) if gas_pay else None,
                "withdraw_l2_median": statistics.median(wd_gas) if wd_gas else None},
        "fees_eth": {"deposit_l1": dep_fee / 1e18, "payments_l2": fees_pay / 1e18, "withdraw_l2": wd_fee / 1e18},
    }


def main():
    d = sys.argv[1]
    s = analyze(d)
    json.dump(s, open(os.path.join(d, "summary.json"), "w"), indent=1)
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
```

```python
# measure/aggregate.py
"""Three repetitions -> mean ± sample sd (n-1), min-max, per-run values
(parent spec §8). Refuses runs made from different code (the provenance guard)."""
from __future__ import annotations

import json
import os
import statistics
import sys

HEADLINE = {
    "payment_latency_p50_s": lambda s: s["payment_latency_s"]["p50"],
    "payment_latency_p99_s": lambda s: s["payment_latency_s"]["p99"],
    "deadline_miss_share": lambda s: s["deadline_miss_share"],
    "open_s_median": lambda s: statistics.median(s["open_s"].values()),
    "close_s_median": lambda s: statistics.median(s["close_s"].values()),
    "payment_fees_eth": lambda s: s["fees_eth"]["payments_l2"],
}


def aggregate(run_dirs) -> dict:
    commits = {json.load(open(os.path.join(d, "manifest.json")))["git_commit"] for d in run_dirs}
    if len(commits) > 1:
        raise ValueError(f"runs come from different commits: {sorted(commits)}")
    sums = [json.load(open(os.path.join(d, "summary.json"))) for d in run_dirs]
    out = {}
    for name, get in HEADLINE.items():
        vals = [get(s) for s in sums]
        out[name] = {"mean": statistics.mean(vals), "sd": statistics.stdev(vals) if len(vals) > 1 else 0.0,
                     "min": min(vals), "max": max(vals), "runs": vals}
    return out


def main():
    agg = aggregate(sys.argv[1:])
    for k, v in agg.items():
        print(f"{k:24s} {v['mean']:.4g} ± {v['sd']:.2g}  (min {v['min']:.4g}, max {v['max']:.4g})  runs {v['runs']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement the manifest**

```python
# measure/manifest.py
"""manifest.json for a run folder (cluster spec §6): code version, experiment
parameters, per-board facts and a recorder summary per board."""
from __future__ import annotations

import csv
import glob
import json
import os
import subprocess
import sys


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd).stdout.strip()


def recorder_summary(path):
    rows = list(csv.DictReader(open(path)))
    if not rows:
        return {}
    uv = sum(int(r["throttled"], 16) & 0x1 != 0 for r in rows)
    return {"samples": len(rows), "undervolted_share": uv / len(rows),
            "mean_arm_mhz": sum(int(r["arm_mhz"]) for r in rows) / len(rows),
            "max_temp_c": max(float(r["temp_c"]) for r in rows),
            "bssids": sorted({r["bssid"] for r in rows})}


def main():
    d = sys.argv[1]
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(l.strip().split("=", 1) for l in open(os.path.join(d, "experiment.env")) if "=" in l)
    man = {
        "git_commit": sh("git", "rev-parse", "HEAD", cwd=repo),
        "git_dirty": bool(sh("git", "status", "--porcelain", cwd=repo)),
        "experiment": env,
        "boards": {os.path.basename(p)[:-5]: json.load(open(p)) for p in glob.glob(os.path.join(d, "facts", "*.json"))},
        "images": sh("ssh", "bath0", "cd ~/arbitrum && docker compose images --format json"),
        "recorder_summary": {os.path.basename(p)[:-4]: recorder_summary(p) for p in glob.glob(os.path.join(d, "recorder", "*.csv"))},
    }
    json.dump(man, open(os.path.join(d, "manifest.json"), "w"), indent=1)
    print(os.path.join(d, "manifest.json"))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest measure.tests.test_analyze -v`
Expected: 4 tests PASS. Then the whole suite: `python3 -m unittest discover -s measure/tests -t . -v` — all PASS, the integration tests skipped.

- [ ] **Step 6: Commit**

```bash
git add measure/manifest.py measure/analyze_uc1.py measure/aggregate.py measure/tests/test_analyze.py
git commit -m "measure: run manifest, UC1 analysis and aggregation over repetitions"
```

---

### Task 11: The first complete path on the cluster

**Files:**
- Create: `results/pi/uc1/*` (run folders; committed — they contain no GESL data and no keys beyond the public mnemonic)
- Modify: `docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md` §8 step 3 (result), `deploy/README.md` §4 (the `cluster` commands)

- [ ] **Step 1: One dry repetition at the slowest window**

Run: `deploy/cluster run uc1-arbitrum-w1000`
Expected: the last line is a run folder path. In it: `manifest.json`; `results/c1`…`c6` each with `ledger.csv` (about 900 rows) and `lifecycle.json` with `"complete": true`; `recorder/bath0.csv`…`bath7.csv`.

- [ ] **Step 2: Analyse it**

Run: `python3 -m measure.analyze_uc1 <run folder>`
Expected: `complete: true`; `outcomes` only `succeeded` (about 5 400); `reconciliation.ok: true`; `open_s` a few seconds per customer; `payment_latency_s.p50` under 1 s. If `reconciliation.ok` is false or any outcome is not `succeeded`, stop and fix the cause before any further run (the ledger rows and `logs/` say which payments and why). **Checkpoint 3: show the summary (outcomes, reconciliation, Open/Close times, latency percentiles, start and peak temperatures from `start-temps.txt` and `manifest.json`) to the project owner and wait for the go before Step 3.**

- [ ] **Step 3: The three repetitions, all three windows**

Run:
```bash
for w in 1000 100 10; do deploy/cluster run uc1-arbitrum-w$w --reps 3; done
for d in results/pi/uc1/*-arbitrum-uc1-arbitrum-w*-rep*; do python3 -m measure.analyze_uc1 "$d" > /dev/null; done
for w in 1000 100 10; do echo "== w$w"; python3 -m measure.aggregate results/pi/uc1/*-uc1-arbitrum-w$w-rep*; done
```
Expected: nine run folders, each with `start-temps.txt` showing every board within 3 °C of its idle temperature; three aggregate tables. With 15-minute runs, setup and cooling, the nine runs take about 4–5 hours and can run unattended. The 10 ms window offers 600 payments per second to the chain: failures, unresolved payments or a high deadline-miss share there are a **result** (where Arbitrum on the Pi stops keeping up), not a bug — the ledger must still account for every window.

- [ ] **Step 4: Record the result and the commands**

In cluster spec §8 step 3, add a ✅ entry: date, the nine run folders, the three aggregate lines, and whether the pipeline needed fixes. In `deploy/README.md` §4, replace "Not yet usable" for the run path with the `deploy/cluster` commands (`sync`, `up arbitrum`, `status`, `preflight`, `run`, `down`) and one sentence each.

- [ ] **Step 5: Commit**

```bash
git add results/pi/uc1 docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md deploy/README.md
git commit -m "results: first complete measurement path, UC1 on Arbitrum, three windows x 3"
```

---

## After this plan (not part of it)

- The same path for **Perun** (a Go payment client behind the same socket, direct channels to the sensor, parent §11.5 deposits) and **zkSync** (the same Python client with a zkSync deposit via `bench-bridge/bridge_bench.js` logic and `max_in_flight` 20, cluster spec §7.3).
- The **capacity probes** (cluster spec §7.1) on the new pipeline.
- **UC2**: weapon and bots, game → payment bridge on the same socket, points token payments.
- The **ansible installer** reproducing the hand-made board state (cluster spec §3).
