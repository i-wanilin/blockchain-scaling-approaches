> **ARCHIVED** — executed 2026-10-03/04; the code and the cluster spec §8 are authoritative.

# UC1 on Perun — direct state channels on the Pi cluster — Implementation Plan

**Status:** draft, written 2026-09-30 while the Arbitrum series ran. **Not approved.** The owner answers the open questions below first (Checkpoint 0).

---

## Owner's answers (2026-10-01)

Q1–Q7: all **A** (the recommended options), so the plan body applies as written. Q3 is still checked again at Checkpoint 1 against the measured per-channel rate.

## Open questions for the owner

Each question lists its options and a recommendation. The body of this plan is written for the **recommended** option. A marker **[Qn]** shows each place where another answer would change a task.

**Q1 — Where does the payment ledger live?** A Go program has to drive the channel. The question is who owns the socket and the ledger.
- **A (recommended): the Python `measure.payclient` keeps the socket, ledger, queue, drain and `lifecycle.json`, and drives a Go *sidecar* over stdin/stdout JSON lines.** The Go process only opens the channel, pays and closes. Why: `payclient.py` and `ledger.py` went through 15+ review rounds on the Arbitrum path (progress ledger, Tasks 1, 2 and 6). Their semantics are pinned by 30+ tests: exactly one outcome per payment, the append-only log, the queue bound, the drain clock that starts at the end marker, no second outcome after the deadline, and SIGTERM safety. Option A reuses all of that unchanged, and the analysis reads the same files. Cost: one local pipe hop per payment (well under 1 ms, still to be measured in Task 1). Both processes use the same CLOCK_REALTIME, so the Go process can report its own `t_submitted`/`t_confirmed`.
- B: an all-Go client that owns the socket and writes byte-compatible `ledger.log.jsonl`, `ledger.csv` and `lifecycle.json` itself. One process less, but it re-implements the hardened ledger semantics in a second language, and every rule then needs two sets of tests.
- *If B:* Tasks 3 and 4 are replaced by a Go ledger with a port of `measure/tests/test_ledger.py` and `test_payclient.py`. Task 7's payclient start line changes.

**Q2 — Close: who sends the on-chain transactions, and what is timed?**
- **A (recommended): the customer finalizes the channel off-chain and is the *primary* on-chain. It sends `concludeFinal` and withdraws its remainder. The sensor is *secondary* and withdraws its earnings, so it pays only its own withdrawal fee.** Close is timed **on the customer's board**, from the start of finalize to the confirmation of the customer's own withdrawal. The sensor's own settle duration is recorded beside it. Why: this is the role split the distributed launcher already runs and that passed the six-process mesh test: the client is primary, and the hub is secondary for six channels at once (`distributed.go` `closeDistributedClient` / `/v1/settle-ledger`). It also keeps every Close figure on one board's clock, as on Arbitrum.
- B: the sensor is primary. This is the literal reading of spec §7.2: "the sensor collects its earnings, paying the close fee". The sensor would then send 6 concludes and 6 withdrawals from one account at once, a path that has not been tested (nonce handling in the eth backend, still to be verified in Task 1).
- C: as A, but Close is timed until **both** withdrawals are confirmed. This needs the time on two boards, so the chrony offset enters the figure.
- *If B or C:* Task 1 changes the settle roles in `uc1_sensor.go` and `uc1_customer.go`, and Task 6 changes the Close definition.

**Q3 — The 10 ms window and one update in flight per channel.** A go-perun channel carries one update at a time. At the 10 ms window a customer offers 100 payments per second on its one channel. The four-board check on 2026-09-29 measured p50 26 ms per update (about 37 per second, one at a time; spec §8). That check probably ran with Wi-Fi power save still on. If the per-channel round trip stays above 10 ms, one update per window cannot keep up, and the spec's sentence in §7.3 ("about a millisecond per update … not a bottleneck") does not hold on the Pis.
- **A (recommended): strict, one signed update per window,** exactly like one transaction per window on the rollups. A shortfall is then a result: backlog, queue-full failures and deadline misses, just as Arbitrum's shortfall at 600/s would be.
- B: *coalescing*. When windows queue up behind the update in flight, the next update pays for all of them at once (amount = k × price). Every window still has its own ledger row and outcome, and each row gets the timestamps of the update that carried it. Latency stays bounded, but the payment count and the update count differ, and the method differs from the rollups (which could also batch, but do not here).
- C: A as the headline, B as a labelled extra experiment `uc1-perun-w10-coalesced`.
- Decide **at Checkpoint 1**, with the per-channel rate that Task 2 measures on the boards. *If B or C:* Task 1 adds a coalescing queue to the sidecar (`-coalesce`), Task 3 maps one reply to several ledger rows, and Task 7 adds one experiment file.

**Q4 — Persist channel state to the SD card?** go-perun can persist every signed state (LevelDB). A real channel node must do this, or it cannot dispute after a crash. At the 10 ms window that is up to 600 writes per second on `bath1`'s card.
- **A (recommended): on,** at both ends, as the distributed launcher does today. The recorder already samples SD I/O, so any cost shows up in the data.
- B: off (state in memory only). This is faster, and the thesis would have to call it unsafe.
- *If B:* one flag in Tasks 1 and 7 (`-persist=false`).

**Q5 — Which accounts?**
- **A (recommended): the same indices as Arbitrum.** 100 = sensor, 101…106 = customers, derived from the public mnemonic (`measure/keys.py`) and funded on Anvil from index 0 during setup, untimed. The keys and the setup code are then the same for both systems.
- B: Anvil's pre-funded dev accounts 1…7 (the hard-coded `anvilKeys` in `main.go`). This skips funding, but these accounts differ from the Arbitrum runs.

**Q6 — Retries.** Spec §7.3 asks for "retries with a fixed policy, the same for all systems". A Perun update that times out cannot be retried safely: the sensor may already hold the fully signed state, so a retry could pay twice, or fail against a version the two ends no longer share.
- **A (recommended): no payment retries on Perun.** An update that times out ends as *unresolved* ("update timeout"), and the channel is marked broken. Every later payment on it fails fast with "channel broken". Wire-level reconnects stay inside go-perun. The thesis states this difference, and reconciliation against the final channel state shows what happened.
- B: retry an update only after a *rejection* (never after a timeout). This is safe, but a rejection in UC1 means a policy violation, and retrying that makes no sense.

**Q7 — Payment policy at the sensor.** `validatePaymentUpdate` (client.go) rejects any update that raises the actor's balance or lowers the counterparty's.
- **A (recommended): on.** A sensor that accepts any state would be naive. The check costs a few big-int comparisons per update.
- B: off. This removes it from the payment path. The DISTRIBUTED.md "Scope" note asks for policy-on and policy-off to be benchmarked separately, if at all.

**Not open (decided in the specs, repeated so they are not reopened by accident):** direct channels customer→sensor, no hub (cluster spec §7.2, 2026-09-25). Anvil on `bath0` with `--block-time 1` (parent §6, spec §2). UC1 pays native ETH. Deposit 1 ETH per customer, price 10¹² wei per window, 15 minutes per run, GESL recording 6063, three windows × 3 repetitions (spec §7.2, parent §8). Perun on Arbitrum is postponed (spec §7.4). The cooling gate, the code check and gzip compression stay exactly as in the Arbitrum path.

---

**Goal:** One command on the workstation, `deploy/cluster run uc1-perun-w1000`, runs UC1 on Perun on the Pi cluster end to end: pre-flight, Open (channel open and funding on Anvil), paced off-chain payments per data window, Close (cooperative settle and withdrawal), recording and collection. It uses the same run-folder layout as Arbitrum, so `measure/analyze_uc1.py` and `measure/aggregate.py` produce the thesis numbers.

**Architecture:** `bath0` runs a fresh Anvil (`--block-time 1`) for every run. The Perun contracts (Adjudicator and ETH AssetHolder) are deployed on it from the workstation as untimed setup. `bath1` runs the unchanged `measure/sensor.py` and, beside it, a Go process `perun-node -mode uc1-sensor`. That process accepts one ledger channel from each of the 6 customers (a star of **direct** channels, 6 on-chain channels), accepts their payment updates and, at the end, withdraws its earnings. Each customer board (`bath2`…`bath7`) runs the unchanged `measure/customer.py` and the local payment client `measure.payclient --system perun` behind the same Unix socket. The payment client spawns `perun-node -mode uc1-customer` as a sidecar and talks to it in JSON lines over stdin/stdout **[Q1]**. The sidecar opens and funds the channel (the timed *Open*), turns each `pay` request into one go-perun `Channel.Update` **[Q3]** and on `close` finalizes, concludes and withdraws (the timed *Close*) **[Q2]**. `deploy/cluster` gains `up perun`, a Perun branch in `run`, and a code check for the Go binary.

**Tech Stack:** Go 1.26 on the workstation (`go1.26.4 linux/amd64`, checked 2026-09-30), go-perun v0.15.0, perun-eth-backend v0.6.0, go-ethereum v1.15.11 (`perun/hub-demo/go.mod`), a static arm64 binary (`CGO_ENABLED=0`), Anvil 1.7.1 (workstation `anvil --version` → 1.7.1, commit `4072e48`; on `bath0` per `deploy/FLEET.md`), Python 3.12 with the existing `measure/` package and `web3==8.0.0`, bash and OpenSSH.

**Spec:** `docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md` (§2 placement, §4 snapshots, §5 control, §6 recording, §7.2 UC1, §7.3 *Payment ledger*, §8, §9). The parent spec is `docs/superpowers/specs/2026-09-03-l2-benchmark-design.md` (§3.1, §5, §8, §11.4, §11.5). Format template: `archive/plans/2026-09-30-uc1-arbitrum-measurement-path.md`.

**Decisions this plan rests on:** the recommended answers to Q1–Q7 (pending), plus everything under *Not open* above. It also rests on the Arbitrum path's rulings as the code implements them today (the code is the truth, not the Arbitrum plan text): R2 (O(1) ledger pending count), R6/R10 (URLs are never derived by string games), R9 (per-customer sensor queues), R11 (a worker crash never reports `complete: true`), R12 (a missing fact is a failure), R15–R17 (deadline miss counts every non-succeeded window, deadline = `t_intended + WINDOW_MS`, type-7 percentiles), and the owner's rulings "run folders go into git, big files gzipped" and "v2 must not change while a series runs".

## Checkpoints — stop and ask the project owner

1. **Checkpoint 0, before Task 1:** the owner has answered Q1–Q7. The Arbitrum nine-run series has finished and been aggregated, because it checks that `measure/` stays unchanged and refuses runs from mixed commits. Only then may commits land on `v2`. Until then, work only in a worktree.
2. **Checkpoint 1, after Task 2:** report the Pi probe: the per-channel update rate and latency at 1/10/100 payments per second, `bath1`'s CPU with 6 channels, and whether any update timed out. Decide Q3 with these numbers.
3. **Checkpoint 2, before Task 7 Step 4 (the first run on the boards through `deploy/cluster`):** show the `deploy/cluster` diff, and show that the Arbitrum branch still passes `bash -n`, shellcheck and one `deploy/cluster run uc1-arbitrum-smoke` (so the change did not break the finished path).
4. **Checkpoint 3, after Task 8 Step 2:** show the first 15-minute dry run: outcomes, the three-way reconciliation, Open/Close times, latency percentiles, update rate achieved, and start and peak temperatures. The nine runs start only after the owner's go.
5. **Anything unexpected:** a spec rule that cannot be met, a number far from expectation, or a board fault. Describe it and ask; do not choose silently.

## Global Constraints

- Everything in the Arbitrum plan's Global Constraints still holds, except the Arbitrum-specific lines (chain IDs, Inbox, ArbSys, WebSocket ports). In particular: the boards, users and addresses; the UC1 layout; 120 rows/s, the whole stream to every customer; postpay; the window sweep; the ledger rules; five timestamps; the append-only ledger log; pre-flight; the run-folder layout; n = 3; the cooling gate (3 °C over idle, at most 20 min); gzip of the big files (`measure/compress.py`, unchanged).
- **Unchanged files:** `measure/sensor.py`, `measure/customer.py`, `measure/events.py`, `measure/wire.py`, `measure/gesl.py`, `measure/ledger.py` (docstring note only, see Task 3), `measure/compress.py`, `measure/fileio.py`, `measure/submitter.py`, `measure/arbitrum.py`. Every existing test in `measure/tests/` must pass **unchanged**, except `test_preflight.py`, whose fixtures gain `anvil_up` (Task 5).
- **The Arbitrum path must behave identically after this plan.** Every Perun change in shared files (`payclient.py`, `preflight.py`, `manifest.py`, `analyze_uc1.py`, `deploy/cluster`, `deploy/board-facts.sh`) branches on the stack. Arbitrum stays the default wherever a stack is not given.
- **Perun layout:** Anvil on `bath0`, `ws://192.168.178.210:8545` (HTTP on the same port), chain ID **31337** (the launcher's default, `main.go`). Sensor Perun node on `bath1`: wire `192.168.178.211:9000`, control HTTP `192.168.178.211:8090` (8090, not the launcher's 8080, so that a stray UC2 hub never answers; verify the port is free in Task 1). Customer sidecar wire on `192.168.178.21<N>:9000`.
- **"Succeeded" for Perun:** the customer's `Channel.Update` returned nil. In go-perun v0.15 that happens only after the sensor's `ChannelUpdateAccMsg` arrived, its signature was added (`machine.AddSig`) and the new state was enabled and persisted (`client/update.go` lines 185–239, read 2026-09-30). **`t_confirmed` = the customer's wall clock (`time.Now()`) right after `Update` returned nil.** **`t_submitted` = right before `Update` was called** (after any wait in the sidecar's FIFO). Both are taken on the customer's board, like `t_intended`, so latency needs no clock-offset correction.
- **One update in flight per channel** (go-perun protocol). The customer is index 0, the channel opener, so it has priority (`update_policy.go`). The sensor never proposes during the run, so collisions cannot happen until finalize, and only the customer finalizes.
- **No payment retries on Perun [Q6].** An update timeout (`update_timeout_s`, default 10 s as in the launcher) → *unresolved*, and the channel is broken. A rejection → *failed*. A channel with too little balance → *failed* ("channel empty", spec §7.3).
- **Open** = from the channel proposal to "funded" (`ProposeChannel` returned). This covers the sensor's acceptance, the customer's deposit transaction, one confirmation block (`txFinalityDepth = 1`, `util.go`) and the funding watch. The sensor deposits **0**: go-perun's funder skips zero funding (`perun-eth-backend@v0.6.0/channel/funder.go:294`), so the sensor sends no transaction at Open.
- **Close [Q2]** = customer finalize (one off-chain update) → `concludeFinal` (the customer, primary) → each party withdraws its own non-zero balance (`withdraw.go`: zero withdrawals are skipped). The secondary waits `secondaryWaitBlocks (2) + txFinalityDepth (1)` blocks for the primary's conclude (`conclude.go:34,192`). Close is timed on the customer. It is a real, measured on-chain close; the rollups' Close is a withdrawal *submission*. Report them in **separate columns, never one cell** (parent §11.4).
- **Deposit** (parent §11.5, spec §7.2): 1 ETH per customer, locked in the channel. At the 10 ms window 90 000 × 10¹² wei = 0.09 ETH, so the margin is ~11×. The sensor deposits nothing. A go-perun channel cannot be topped up; make-before-break is described, not measured. Unlike Arbitrum's deposit, Perun's deposit pays **no fees**: open and close gas are paid from the account outside the channel (FUND_WEI 2 ETH on Anvil).
- **Fees on Anvil are an artefact of Anvil's base-fee setting.** The thesis reports Perun's gas in L1 gas units (parent §5.2). ETH fees appear only under the stated fee scenarios.
- **Snapshots:** Perun needs none. Every run starts a fresh Anvil and deploys the two contracts with the deployer (index 0) at nonces 0 and 1, so the contract addresses should be identical in every run (verify in Task 1, then pin them as constants that `up perun` checks). **Spec §4 is inaccurate here:** `-mode init` only creates the wire identity (`distributed.go` `distributedMain`); the contracts are deployed by `-mode hub`. Task 9 corrects the spec.
- **Binary provenance:** `perun-node` is cross-compiled from the committed tree in `deploy/cluster sync` with `-trimpath -buildvcs=false -ldflags "-s -w"` (whether this build is reproducible is verified in Task 1). Its SHA-256 is compared on every board before every run and written into the run folder. The existing `code_hash` (measure/*.py only) is **not** changed, so the Arbitrum runs stay comparable.
- **Keys:** from the public mnemonic, local chains only **[Q5]**. The sidecar gets its key on stdin (`init`), never on the command line. The sensor gets its key from a 0600 file in the run directory.
- Style: Go as in `perun/hub-demo` (stdlib `testing`, integration tests skip without `ANVIL_URL`); Python as in `measure/` (`unittest`, docstrings that say why).

## Review Focus

1. **An update that times out after the sensor accepted it.** The customer discards the update, but the sensor holds a fully signed state one version ahead. The ledger row must be *unresolved*, never *succeeded* or *failed*. The channel is then broken, and all later rows are *failed* ("channel broken"). Reconciliation must show the difference against the sensor's final state, not hide it. Pinned in Task 1 (`TestUC1TimeoutMarksChannelBroken`) and Task 6 (`test_perun_unresolved_update_in_final_state_is_reported`).
2. **The drain deadline with pays still queued inside the sidecar.** At the deadline Python marks them unresolved; the sidecar must not start any of them afterwards (`quiesce`). A late reply must never give a row a second outcome (the existing `test_late_result_after_drain_does_not_change_the_outcome` must also cover the Perun adapter). Pinned in Task 3.
3. **The sidecar dies mid-run.** Every outstanding future resolves "unresolved: sidecar exited", the payment client writes `ledger.csv` and `lifecycle.json` with `complete: false`, and the exit code is non-zero. Pinned in Task 3 (`test_sidecar_death_resolves_everything_unresolved`).
4. **Three-way reconciliation.** For each customer, the sum of *succeeded* amounts, the sensor's final channel balance and the customer's final channel balance must agree. Across all customers, the sensor's L1 balance change plus its own close fees must equal the sum of the earnings. Pinned in Task 6.
5. **Clock domain.** `t_intended` is taken by Python (`time.time()`), `t_submitted`/`t_confirmed` by Go (`time.Now()`), all on the same board. A unit test checks that the two languages' clocks agree within 1 ms on the workstation (Task 3). Any cross-board figure (the sensor's settle) is reported apart.
6. **The Arbitrum path is untouched.** All existing tests pass unchanged, and a smoke run passes at Checkpoint 2.

## What is verified and what is not

| Fact | Status | Source |
|---|---|---|
| go-perun v0.15.0, perun-eth-backend v0.6.0, go-ethereum v1.15.11 | verified | `perun/hub-demo/go.mod` |
| `go build` and `go vet ./...` pass on today's tree | verified 2026-09-30 in the worktree | — |
| Zero-balance deposit and zero-balance withdrawal are skipped | verified (code read) | `funder.go:294`, `withdraw.go` `ensureWithdrawn` |
| `Update` returns nil only after the peer's signature is added and the state is enabled | verified (code read) | `client/update.go:185–239` |
| Incoming updates are handled in their own goroutine (the sensor serves 6 channels in parallel) | verified (code read) | `client/client.go:202–206` |
| Secondary waits 2 + 1 blocks for the primary's conclude | verified (code read) | `conclude.go:34,192` |
| Wire messages are not signed per message (RSA only in the connection handshake) | verified (code read) | `wire/account.go:107` |
| Client primary + hub secondary for 6 ledger channels works | verified by the six-process mesh test (DISTRIBUTED.md) | — |
| Four boards over Wi-Fi: update p50 26 ms, p99 46 ms, ~37/s one at a time | measured 2026-09-29, probably with power save on | spec §8 |
| Per-channel update rate on the Pis with power save off | **unknown — Task 2** | — |
| `bath1` (Pi 3B+) serving 6 channels at 600 updates/s plus `sensor.py` | **unknown — Task 2** | — |
| Gas of a single-sided ETH ledger open (only the customer deposits) | **verify in Task 1** (README gives 91 622 per channel with **two** deposits) | — |
| Gas of the close (conclude + one or two withdrawals) | **verify in Task 1** (README: 718 384 / 9 txs for 3 channels with two-sided funds) | — |
| Contract addresses identical on every fresh Anvil | **verify in Task 1** | — |
| Reproducible `perun-node` build hash | **verify in Task 1** | — |
| Anvil binary path and launch on `bath0` | **verify in Task 1** (installed by hand, `deploy/FLEET.md`; path not recorded) | — |
| Anvil's base fee over 15 minutes of mostly empty blocks | **verify in Task 1** | — |
| Port 8090 free on `bath1`, 9000 free on `bath2`…`bath7` | **verify in Task 2** | — |
| LevelDB persistence cost per update on an SD card | **unknown — Task 2** | — |

---

## File structure

| File | Responsibility |
|---|---|
| `perun/hub-demo/uc1.go` | UC1 shared types: `uc1Contracts` (JSON), the sensor manifest and registration, `-mode uc1-deploy`, `uc1Main` dispatch |
| `perun/hub-demo/uc1_sensor.go` | `-mode uc1-sensor`: accepts one direct ledger channel per registered customer (customer deposit = expected, sensor 0), accepts payment updates under the policy **[Q7]**, settles as secondary **[Q2]**, writes `sensor-result.json` |
| `perun/hub-demo/uc1_customer.go` | `-mode uc1-customer`: the sidecar (JSON lines on stdin/stdout: `init`, `open`, `pay`, `state`, `quiesce`, `close`, `exit`) and a Go-only probe (`-probe-rate`, `-probe-seconds`) |
| `perun/hub-demo/gas.go` | modify: also sum the fee (`gasUsed × effectiveGasPrice`) per phase |
| `perun/hub-demo/main.go` | modify: modes starting with `uc1-` go to `uc1Main` before the cluster config is loaded |
| `perun/hub-demo/uc1_test.go` | unit tests: protocol parsing, proposal and update policies, the sidecar FIFO, quiesce |
| `perun/hub-demo/uc1_integration_test.go` | multi-process tests against a local Anvil (skip without `ANVIL_URL`) |
| `measure/perun.py` | `PerunSidecar` (process and JSON-lines RPC) and `PerunSubmitter` (`submit` → `Future[SubmitResult]`), used by `pay_loop` unchanged |
| `measure/payclient.py` | modify: `--system perun` dispatches to `perun.main_async`; the Arbitrum code stays as it is |
| `measure/preflight.py` | modify: stack-aware "expected stack and only that stack" (`anvil_up`, `docker_up`) |
| `measure/manifest.py` | modify: stack-aware `images()` (Anvil version, `perun-node` SHA-256) |
| `measure/analyze_uc1.py` | modify: per-system Open/Close/gas extraction, Perun three-way reconciliation, `close_definition` |
| `measure/tests/test_perun.py` | fake-sidecar unit tests; an end-to-end test that skips without `PERUN_ANVIL` |
| `measure/tests/fixtures/perun-run/` | a small Perun run folder for the analysis tests |
| `deploy/board-facts.sh` | modify: `anvil_up`, `perun_node_sha256` |
| `deploy/cluster` | modify: `sync` builds and ships `perun-node`; `up perun`; `down` also stops Anvil; code check for the binary; Perun branch in `one_run` |
| `deploy/experiments/uc1-perun-{smoke,w1000,w100,w10}.env` | experiment parameters |

Workstation test chain for the integration tests: `anvil --chain-id 31337 --block-time 1` in a second terminal, `export ANVIL_URL=ws://127.0.0.1:8545 PERUN_ANVIL=ws://127.0.0.1:8545`. Stop it after the task. Nothing else runs: no Docker, no Arbitrum stack.

---

### Task 1: The direct-channel mode in the launcher (the riskiest piece first)

This builds and proves the Go side on the workstation: deployment, the sensor that accepts direct channels, and the customer sidecar. Step 1 settles every "verify in Task 1" fact before any other code depends on it.

**Files:**
- Create: `perun/hub-demo/uc1.go`, `perun/hub-demo/uc1_sensor.go`, `perun/hub-demo/uc1_customer.go`, `perun/hub-demo/uc1_test.go`, `perun/hub-demo/uc1_integration_test.go`
- Modify: `perun/hub-demo/main.go` (dispatch), `perun/hub-demo/gas.go` (fees)

**Interfaces:**
- Consumes: `newNode`, `Hub`-style proposal handling, `newNetworkRuntime`, `lanwire.LoadOrCreate`, `validatePaymentUpdate`, `updateWithPolicy`/`finalize`/`settleContext`, `deployContracts(…, assetETH)`, `newGasMeterFrom`, `encodeWire`/`decodeWire`, `httpJSON` (all existing).
- Produces (command line):
  - `perun-node -mode uc1-deploy -url ws://H:8545 -chainid 31337 -out contracts.json`. Deployer = `anvilKeys[0]`. Writes `{"chain_id":31337,"adjudicator":"0x…","asset_holder":"0x…","deploy_gas":N,"deploy_txs":2}`.
  - `perun-node -mode uc1-sensor -url ws://H:8545 -chainid 31337 -contracts contracts.json -key-file F -bind 0.0.0.0:9000 -endpoint 192.168.178.211:9000 -control 0.0.0.0:8090 -customers 6 -allow 0xA1,…,0xA6 -deposit-wei 1000000000000000000 -challenge 10 -update-timeout 10 -strict=true -persist=true -state DIR/state -settle-deadline 600 -out DIR`
    - HTTP `GET /v1/manifest` → `{"wire":"<b64>","ethereum":"0x…","adjudicator":"0x…","asset_holder":"0x…","chain_id":31337}`
    - HTTP `POST /v1/register` `{"name":"c1","endpoint":"192.168.178.212:9000","wire":"<b64>","ethereum":"0x…"}` → 204. It refuses any Ethereum address not in `-allow`. It registers the dialer and the admission.
    - HTTP `GET /v1/status` → `{"channels_open":n,"channels_settled":m,"errors":[…]}`
    - It writes `DIR/sensor-result.json` atomically when every channel is settled (exit 0), when `-settle-deadline` seconds after the first finalize have passed (exit 1), or on SIGTERM (exit 143). `complete` is true only in the first case.
  - `perun-node -mode uc1-customer [-probe-rate R -probe-seconds S -probe-out F]`. Without probe flags it is the sidecar. Requests on stdin, one JSON object per line; replies on stdout, one per request, in request order; logs on stderr:

```text
-> {"op":"init","key":"<hex>","name":"c1","chain":"ws://…:8545","chain_id":31337,
    "sensor_control":"http://192.168.178.211:8090","bind":"0.0.0.0:9000",
    "endpoint":"192.168.178.212:9000","state_dir":"…","update_timeout_s":10,
    "challenge_s":10,"persist":true,"startup_timeout_s":120}
<- {"op":"init","ok":true,"address":"0x…"}
-> {"op":"open","deposit_wei":"1000000000000000000"}
<- {"op":"open","ok":true,"t_start":…,"t_funded":…,"channel_id":"0x…",
    "l1_gas_used":N,"l1_fee_wei":"…","txs":1}
-> {"op":"pay","id":"uc1/<run>/c1/w17","amount_wei":"1000000000000"}
<- {"op":"pay","id":"uc1/<run>/c1/w17","ok":true,"t_submitted":…,"t_confirmed":…,"version":18,"error":""}
   (ok:false, error one of "rejected: <reason>", "unresolved: update timeout",
    "channel broken: <first error>", "channel empty")
-> {"op":"state"}      <- {"op":"state","ok":true,"version":v,"balances_wei":["…","…"],"broken":false}
-> {"op":"quiesce"}    <- {"op":"quiesce","ok":true,"dropped":k,"version":v}
-> {"op":"close"}      <- {"op":"close","ok":true,"t_start":…,"t_finalized":…,"t_withdrawn":…,
                           "final_version":v,"final_balances_wei":["…","…"],
                           "l1_gas_used":N,"l1_fee_wei":"…","txs":n,"by_contract":{…}}
-> {"op":"exit"}
```

  Amounts are decimal strings; times are Unix seconds as float64. `quiesce` stops the FIFO. Pays not yet started get a reply `ok:false, error:"unresolved: dropped at quiesce"`. It then waits for the update in flight (at most `update_timeout_s`) and replies. After `quiesce`, `pay` is refused with `"unresolved: after quiesce"`.

- [ ] **Step 1: Settle the open facts on the workstation (throwaway checks, nothing committed)**

With a local `anvil --chain-id 31337 --block-time 1`:
1. Deploy twice on two fresh Anvils with the existing code (`go run . -url ws://127.0.0.1:8545` deploys first). Compare the adjudicator and asset-holder addresses. If they are equal, pin them as `uc1ExpectedAdjudicator`/`uc1ExpectedAssetHolder` in `uc1.go`, and `up perun` checks them (Task 7). If not, `up perun` ships `contracts.json` instead (it does anyway) and records that the addresses vary.
2. A ledger channel with balances `[1 ETH, 0]`: the proposal is accepted, `ProposeChannel` returns, and only the customer sends a transaction. Record its gas (expected somewhat above the 45 811 average that the README's two-sided figure implies; `ethDepositGasHint` is 50 000).
3. The close with the customer primary and the sensor secondary: transaction count and gas (expected: 1 conclude + 2 withdrawals). With the sensor secondary on 6 channels at once, no nonce error occurs. (The mesh test did this for the hub; repeat it with 6 customers in Step 6.)
4. `CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -buildvcs=false -ldflags "-s -w"`, twice from a clean tree: equal SHA-256? If not, the code check compares against the hash of the binary that `sync` shipped, which is recorded in `deploy/dist/arm64/perun-node.sha256`.
5. Anvil's base fee after 900 s of `--block-time 1` with little traffic (`cast base-fee` at start and end). This is recorded for the fee-scenario text only.
6. The per-update cost on the workstation over loopback (a probe at 1 000/s for 10 s, one channel). This is the reference for Task 2.

Record the six results in the SDD ledger. Numbers far from expectation → Checkpoint (item 5).

- [ ] **Step 2: Write the failing unit tests**

```go
// uc1_test.go — the UC1 direct-channel mode without a chain: protocol, policies, FIFO.
package main

func TestUC1RequestParsing(t *testing.T)            // every op; amounts as strings; unknown op -> error reply, process continues
func TestUC1SensorAcceptsOnlyExpectedLedger(t *testing.T) // 2 parties, balances [deposit, 0], our asset, challenge == -challenge, peer in -allow; anything else rejected with a reason
func TestUC1SensorUpdatePolicy(t *testing.T)        // strict: customer (idx 0) may only lower its own balance; a final state is accepted; a state that pays the customer is rejected
func TestUC1FIFOOneInFlightInOrder(t *testing.T)    // a fake updater records call order and concurrency: max 1, replies in request order
func TestUC1QuiesceDropsQueuedAndWaitsForInFlight(t *testing.T)
func TestUC1TimeoutMarksChannelBroken(t *testing.T) // fake updater returns client.RequestTimedOutError: that pay "unresolved: update timeout", every later pay "channel broken: …", no call reaches the updater
func TestUC1ChannelEmptyIsFailedNotBroken(t *testing.T) // balance < amount -> "channel empty", later smaller pays still go through
```

The FIFO takes an `updater func(amount *big.Int) (version uint64, err error)` so the tests need no chain. In production it wraps `updateWithPolicy(ch, TransferBalance(0→1))`. Run `go test -run 'TestUC1' -v` and expect it to fail to compile (undefined symbols).

- [ ] **Step 3: Implement `uc1.go`, `uc1_sensor.go`, `uc1_customer.go`, the dispatch and the fee sum**

Key points (the implementer writes the code; these are binding):
- `main.go`: right after `flag.Parse()`, `if strings.HasPrefix(*mode, "uc1-") { if err := uc1Main(*mode); err != nil { log.Fatal(err) }; return }`. The UC1 flags are registered in `uc1.go` with `flag.String` in an `init()`, so the global `flag.Parse` sees them. The cluster config is not read in UC1 modes.
- Sensor: `NewHubWithIdentity`-style node, but with its own proposal handler (the Hub's accepts any ledger channel; UC1's checks balances `[deposit, 0]` and `-allow`). It accepts with `client.WithRandomNonce()` and calls `startWatching`. Update handler: `validatePaymentUpdate` when `-strict` **[Q7]**, plus "the actor is index 0". It counts accepted and rejected updates per channel and keeps the last version and balances. When an accepted update has `IsFinal`, it starts `settleContext(ctx, ch, true)` in a **new goroutine after `Accept` returned** (the machine lock is held during the handler), and times it **[Q2: secondary]**. The sensor's gas meter is `newGasMeterFrom(url, sensorAddr)`, split per channel by time window (the settles overlap, so the per-channel split uses the withdrawal receipts of that channel's asset-holder call: the sidecar and the sensor record each receipt's `gasUsed × effectiveGasPrice` via the backend's receipt; if the backend does not expose the receipt, sum per sender over the whole close phase and report the total only — verify in Step 3).
- Customer sidecar: `init` builds the node (`newNode`, key from stdin) and the network runtime, fetches `/v1/manifest` and registers. It retries HTTP with capped backoff up to `startup_timeout_s` (the first dial on the Pis failed once with *no route to host*, spec §8). `open` calls `OpenLedgerToWire(sensorWire, deposit, 0, challenge)`, with amounts in wei, not floats (a new `OpenLedgerWei` helper, because `kind.units(float64)` would round 10¹⁸). The time is taken around the call and the gas is metered from the customer's own address. `pay` goes into an unbounded Go channel; one goroutine runs the FIFO. The customer is index 0, so `updateWithPolicy` uses `Long = update_timeout_s` and never yields. `close` = `finalize(ch)` (time `t_finalized`), then `settleContext(ctx, ch, false)` **[Q2: primary]** (time `t_withdrawn`), then the final state.
- `gas.go`: `gasSinceByContract` also returns the fee sum from `receipt.EffectiveGasPrice` (go-ethereum's `types.Receipt` has it). `gasFile` gets `FeeWei string json:"fee_wei"`, and the existing callers keep working.
- Every reply is written with one `json.Encoder.Encode` under a mutex, then flushed. Stdout carries **only** protocol lines; logrus goes to stderr.

- [ ] **Step 4: Run the unit tests**

Run: `cd perun/hub-demo && go test -count=1 -run 'TestUC1' -v && go vet ./...`
Expected: the 7 new tests PASS, and all existing tests still PASS (`go test -count=1 ./...`; the Anvil tests skip without `ANVIL_URL`).

- [ ] **Step 5: Write the integration tests**

```go
// uc1_integration_test.go — real processes against a local Anvil (skip without ANVIL_URL).
func TestUC1DirectChannelsProbe(t *testing.T)
// builds the binary, runs uc1-deploy, starts uc1-sensor (-customers 2), funds two
// fresh accounts (indices 101,102 of the public mnemonic) from anvilKeys[0], runs two
// uc1-customer probes at 50/s for 10 s each; expects: both probes 500/500 ok, the
// sensor's sensor-result.json complete with final balances equal to 500 * amount
// each, each customer's final_balances == sensor's view, sensor exit 0.
func TestUC1SidecarProtocolEndToEnd(t *testing.T)
// one customer driven over stdin/stdout exactly as measure/perun.py will:
// init, open, 100 pays, state, quiesce, close, exit; checks reply order, versions
// 1..100 (verify the first payment's version: the funded state is version 0?),
// open.txs == 1, close ok.
func TestUC1SixCustomersCloseTogether(t *testing.T)
// 6 probes at 10/s for 5 s, then all six close at once: no nonce error at the
// sensor (secondary withdrawals from one account), all six settled.
```

- [ ] **Step 6: Run the integration tests on the workstation**

Run (Anvil running): `ANVIL_URL=ws://127.0.0.1:8545 go test -count=1 -run 'TestUC1.*' -v -timeout 15m`
Expected: 3 PASS. Record the loopback probe latency (p50/p99) and the Open/Close gas in the SDD ledger. Stop Anvil.

- [ ] **Step 7: Commit**

```bash
git add perun/hub-demo/uc1.go perun/hub-demo/uc1_sensor.go perun/hub-demo/uc1_customer.go \
        perun/hub-demo/uc1_test.go perun/hub-demo/uc1_integration_test.go perun/hub-demo/main.go perun/hub-demo/gas.go
git commit -m "perun: UC1 direct-channel mode (deploy, sensor, customer sidecar and probe)"
```

---

### Task 2: Pi feasibility probe (Go only, no Python, no recorder)

This answers Q3's question with numbers before the Python side is built on an assumption. **It needs the boards: start only when the Arbitrum series has finished and the owner has released the cluster.**

**Files:**
- Create: none in the repo (the binary goes to `~/perun-probe/` on the boards and is removed afterwards).
- Modify: `docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md` §8 step 1 (a new ✅/⚠️ entry with the result).

- [ ] **Step 1: Build and copy**

```bash
cd perun/hub-demo && CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -buildvcs=false -ldflags "-s -w" -o /tmp/perun-node-arm64 .
for h in bath1 bath2 bath3 bath4 bath5 bath6 bath7; do scp -q /tmp/perun-node-arm64 $h:perun-probe-node; done
```

- [ ] **Step 2: Anvil on `bath0`, contracts, funding**

Find the Anvil binary on `bath0` (`ssh bath0 'command -v anvil || ls ~/.foundry/bin ~/foundry 2>/dev/null'`) and record its path; `deploy/cluster` needs it. Check that nothing else is running (`docker ps -q` empty). Start `setsid nohup <anvil> --host 0.0.0.0 --chain-id 31337 --block-time 1 > ~/anvil-probe.log 2>&1 < /dev/null &`. Then run `uc1-deploy` from the workstation and fund 100…106 with `cast send` from index 0 (2 ETH each).

- [ ] **Step 3: Probe matrix (60 s each, `bath1` sensor with `-customers 6`, all six customers at once)**

| Run | Rate per customer | Persist | Offered at the sensor |
|---|---|---|---|
| p1 | 1/s | on | 6/s |
| p10 | 10/s | on | 60/s |
| p100 | 100/s | on | 600/s |
| p100-nopersist | 100/s | off | 600/s (only to size Q4) |

For each run, record per customer: achieved rate, update latency p50/p99/max, timeouts, and final-state agreement. On `bath1`: CPU per process (`pidstat 1` or `top -b -d 1`), SD writes (`/proc/diskstats`), temperature and the throttle flag. Also check that ports 8090/9000 are free before starting.

- [ ] **Step 4: Clean up the boards**

Stop Anvil and every `perun-probe-node`, and remove `~/perun-probe-node` and the state directories. `deploy/cluster status` must show no leftovers.

- [ ] **Step 5: Checkpoint 1**

Report the table to the owner. The decision needed is Q3 (strict or coalescing at w10), plus Q4 if persistence dominates. If p100 already fails at the sensor (not per channel), also report whether `bath1` or the radio is the limit. That is a result (spec §2: "at UC1's 10 ms window the sensor may be the limit (recorded as a result)").

- [ ] **Step 6: Record and commit**

```bash
git add docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md
git commit -m "spec: Perun UC1 feasibility probe on the Pi cluster (per-channel update rate, sensor load)"
```

---

### Task 3: The sidecar adapter in Python

**Files:**
- Create: `measure/perun.py`, `measure/tests/test_perun.py`, `measure/tests/fixtures/fake_perun_sidecar.py`
- Modify: `measure/ledger.py` (docstring only: for Perun, the `block` column holds the channel version, `gas_used` and `effective_gas_price` are 0 and `tx_hash` is empty)

**Interfaces:**
- Consumes: `SubmitResult` (`measure/submitter.py`, unchanged); the sidecar protocol from Task 1.
- Produces:
  - `class PerunSidecar(argv: list[str], log_path: str)`: `async start()`, `async request(obj: dict, timeout: float) -> dict` (for `init`/`open`/`state`/`quiesce`/`close`), `async pay(id_, amount_wei) -> asyncio.Future[dict]`, `async stop()`; the property `exited: bool`, and `returncode`. A single reader task dispatches the replies: `pay` replies by `id`, the rest in FIFO order. At EOF every pending future resolves `{"ok": false, "error": "unresolved: sidecar exited (rc N)"}`.
  - `class PerunSubmitter(sidecar, max_in_flight: int)`: `async submit(tx: dict) -> asyncio.Future[SubmitResult]` with `tx = {"id":…, "amount_wei":…}`. It blocks while `max_in_flight` pays are outstanding (as `OrderedSubmitter` does). It maps a reply to `SubmitResult(tx_hash=None, t_submitted, t_confirmed, status=1|0, block=version, gas_used=0, effective_gas_price=0, attempts=1, error)`; `error` that starts with "unresolved" becomes an unresolved row through the existing `PaymentLedger.finish`. `async close()`: no-op (the lifecycle stops the sidecar).
  - `make_tx(ev) -> {"id": ev.id, "amount_wei": ev.amount_wei}`

`pay_loop`, `accept`, `EventQueue` and `serve_socket` in `payclient.py` are used **as they are**.

- [ ] **Step 1: Write the failing tests (fake sidecar = a small Python script speaking the protocol)**

```python
# measure/tests/test_perun.py
"""The Perun adapter keeps the ledger semantics: one outcome per payment,
drain and late replies, sidecar death -> unresolved, never a hang."""

class TestPerunSubmitter(unittest.IsolatedAsyncioTestCase):
    async def test_all_paid_in_order_with_go_timestamps(self): ...
        # fake answers every pay after 5 ms with t_submitted/t_confirmed; pay_loop +
        # PaymentLedger: 200 rows succeeded, block == 1..200, t_confirmed from the reply
    async def test_rejection_is_failed_timeout_is_unresolved_then_broken(self): ...
    async def test_in_flight_bound_blocks_submit(self): ...
    async def test_drain_deadline_then_quiesce_late_reply_does_not_change_outcome(self): ...
    async def test_sidecar_death_resolves_everything_unresolved(self): ...
        # fake exits after 50 pays; the remaining futures -> "unresolved: sidecar exited"
    async def test_stdout_noise_is_an_error_not_a_hang(self): ...
        # a non-JSON line on stdout: logged, the sidecar is treated as dead (protocol broken)
    def test_python_and_go_clocks_agree(self): ...
        # skip unless PERUN_BIN is set: run `perun-node -mode uc1-customer -clock-check`
        # (prints time.Now()), compare with time.time() before/after: |diff| < 1 ms
```

(`-clock-check` is a tiny flag added in Task 1's `uc1_customer.go`; if it is missing at this point, add it here and commit it with this task.)

Run: `python3 -m unittest measure.tests.test_perun -v`. Expected: ERROR, `No module named 'measure.perun'`.

- [ ] **Step 2: Implement `measure/perun.py`**

Use `asyncio.create_subprocess_exec(*argv, stdin=PIPE, stdout=PIPE, stderr=<log file>)`. Line reads use a limit large enough for `close` replies (`limit=1<<20`). Writes go through one lock plus `drain()`. Every `request` has a timeout: on timeout the sidecar is killed and the call raises, and the lifecycle turns that into `open_error`/`close_error`. `pay` never times out on its own: the drain deadline in `pay_loop` bounds it, and `quiesce` releases it.

- [ ] **Step 3: Run the tests**

Run: `python3 -m unittest measure.tests.test_perun measure.tests.test_payclient measure.tests.test_ledger -v`
Expected: the new tests PASS, and the existing `test_payclient`/`test_ledger` tests PASS unchanged.

- [ ] **Step 4: Commit**

```bash
git add measure/perun.py measure/tests/test_perun.py measure/tests/fixtures/fake_perun_sidecar.py measure/ledger.py
git commit -m "measure: Perun sidecar adapter behind the unchanged pay_loop and ledger"
```

---

### Task 4: `payclient --system perun`: Open, payments, drain, Close

**Files:**
- Modify: `measure/payclient.py` (argparse `choices=["arbitrum","perun"]`, the Perun arguments, dispatch), `measure/perun.py` (`main_async`)
- Test: `measure/tests/test_perun.py` (lifecycle tests with the fake sidecar; an end-to-end test that skips without `PERUN_ANVIL`)

**Interfaces:**
- CLI: `python3 -m measure.payclient --system perun --chain ws://192.168.178.210:8545 --chain-id 31337 --sensor-control http://192.168.178.211:8090 --sidecar ~/measure-code/bin/perun-node --wire-bind 0.0.0.0:9000 --wire-endpoint 192.168.178.212:9000 --account 101 --pay-to 0x.. --deposit-wei 1000000000000000000 --max-in-flight 64 --max-queue 10000 --drain 60 --update-timeout 10 --persist 1 --socket /tmp/measure-pay.sock --out DIR`. `--l1`/`--l2` stay required only for `arbitrum` (argparse: validated after parsing, per system).
- Output in `DIR`: as Arbitrum (`ledger.log.jsonl`, `ledger.csv`, `lifecycle.json`, `payclient.log`, `ready`), plus `sidecar.log` and `state/` (LevelDB, deleted at the end of a complete run; kept after a failure for diagnosis).
- `lifecycle.json` for Perun: `system: "perun"`, `account`, `complete`, `max_in_flight`, `max_queue`, `drain_s`, `duplicates`, `late`, `open` (the sidecar's reply: `t_start`, `t_funded`, `channel_id`, `l1_gas_used`, `l1_fee_wei`, `txs`), `ready_at`, `ended_at`, `quiesce` (`dropped`, `version`), `channel_before_close` (`version`, `balances_wei`, `broken`), `reconciliation` (`succeeded_wei`, `observed_wei` = deposit − own final balance, `difference_wei`, `counts`, `observed_source: "channel"`), `close` (the sidecar's reply), and `open_error`/`close_error`/`error` when something failed.

The lifecycle (mirrors `payclient.main_async`; the same finally-block guarantees):
1. Start the sidecar and send `init` (timeout = startup 120 s + 10 s).
2. Check that the L1 balance of the account is > deposit (else `open_error`, "not funded"). Send `open`, with a timeout of 300 s → `life["open"]`.
3. Socket, then `ready` (only after listening, as today).
4. `pay_loop(ledger, PerunSubmitter(sidecar, max_in_flight), make_tx, q, drain)`.
5. Send `quiesce` (timeout = update timeout + 10 s), then `state` → `channel_before_close`.
6. `ledger.write_csv`, then reconciliation against the channel.
7. Send `close` (timeout 300 s) → `life["close"]`. `complete = close.ok and not broken`. **A broken channel still closes (with the last state both parties signed), but the run is `complete: false`**, so a broken channel can never enter an aggregate unnoticed.
8. Send `exit`, stop the sidecar, and remove `state/` if complete.

- [ ] **Step 1: Failing lifecycle tests (fake sidecar)**

`test_perun_success_is_complete`, `test_perun_failed_open_is_recorded_and_no_socket`, `test_perun_broken_channel_closes_but_is_not_complete`, `test_perun_close_error_is_not_complete`, `test_perun_sigterm_still_writes_lifecycle_and_csv`, `test_perun_reconciliation_uses_the_channel_state`, `test_arbitrum_arguments_unchanged` (the old command line still parses and dispatches to the Arbitrum code).

- [ ] **Step 2: Implement, then run the unit tests**

Run: `python3 -m unittest discover -s measure/tests -v`
Expected: all PASS (the old ones unchanged).

- [ ] **Step 3: End-to-end on the workstation (one customer, 20 s, real Anvil, real sensor node)**

```bash
anvil --chain-id 31337 --block-time 1        # terminal 0
cd perun/hub-demo && go build -o /tmp/pn . && /tmp/pn -mode uc1-deploy -url ws://127.0.0.1:8545 -chainid 31337 -out /tmp/uc1p/contracts.json
python3 - <<'PY'                              # fund 100, 101 from index 0 (setup, untimed)
from web3 import Web3; from measure import arbitrum, keys
l1 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
[arbitrum.fund_on_l1(l1, keys.account(0), keys.account(i).address, 2 * 10**18) for i in (100, 101)]
PY
python3 -c "from measure import keys; print(keys.account(100).key.hex())" > /tmp/uc1p/sensor.key && chmod 600 /tmp/uc1p/sensor.key
/tmp/pn -mode uc1-sensor -url ws://127.0.0.1:8545 -chainid 31337 -contracts /tmp/uc1p/contracts.json \
  -key-file /tmp/uc1p/sensor.key -bind 127.0.0.1:9100 -endpoint 127.0.0.1:9100 -control 127.0.0.1:8090 \
  -customers 1 -allow $(python3 -c "from measure import keys; print(keys.account(101).address)") \
  -deposit-wei 1000000000000000000 -state /tmp/uc1p/sensor-state -out /tmp/uc1p/sensor &      # terminal 1
python3 -m measure.payclient --system perun --chain ws://127.0.0.1:8545 --chain-id 31337 \
  --sensor-control http://127.0.0.1:8090 --sidecar /tmp/pn --wire-bind 127.0.0.1:9101 --wire-endpoint 127.0.0.1:9101 \
  --account 101 --pay-to $(python3 -c "from measure import keys; print(keys.account(100).address)") \
  --deposit-wei 1000000000000000000 --out /tmp/uc1p/c1                                          # terminal 2
# terminal 3 and 4: sensor.py and customer.py exactly as in the Arbitrum plan Task 6 Step 5 (20 s, --window-ms 100)
```

Expected: `/tmp/uc1p/c1/lifecycle.json` with `"complete": true`, `open.txs == 1`, and `reconciliation.difference_wei == 0`; `ledger.csv` with 200 rows, all `succeeded`, `block` 1…200 (or 2…201, see the Task 1 Step 5 note); `/tmp/uc1p/sensor/sensor-result.json` complete, with the sensor's final balance on the channel = 200 × 10¹². Stop Anvil.

- [ ] **Step 4: Commit**

```bash
git add measure/payclient.py measure/perun.py measure/tests/test_perun.py
git commit -m "measure: payment client for Perun (Open, pipelined sidecar pays, drain, quiesce, Close)"
```

---

### Task 5: Board facts, pre-flight and manifest know the stack

**Files:**
- Modify: `deploy/board-facts.sh`, `measure/preflight.py`, `measure/manifest.py`, `measure/tests/test_preflight.py` (fixtures gain `anvil_up`), `measure/tests/test_analyze.py` (one manifest test for Perun)

**Interfaces:**
- `board-facts.sh` adds `"anvil_up": sh("pgrep -x anvil | wc -l")` and `"perun_node_sha256": sh("sha256sum ~/measure-code/bin/perun-node 2>/dev/null | cut -d' ' -f1")` (empty on `bath0`).
- `preflight.check(..., stack="arbitrum")`. Stack board: `arbitrum` → `docker_up > 0` and `anvil_up == 0`; `perun` → `anvil_up > 0` and `docker_up == 0`. Every other board: both 0. `REQUIRED` gains `anvil_up`. `main()` reads the stack from `sys.argv[2]` (default `arbitrum`, so the current call in `deploy/cluster` keeps working until Task 7 passes `$STACK`).
- `manifest.build` reads `STACK` from `experiment.env`. `images()` becomes `stack_versions(stack)`: `arbitrum` → today's code unchanged; `perun` → `{"anvil": ssh bath0 '<anvil> --version', "perun_node_sha256": contents of perun-node-sha256.txt, "contracts": contents of results/contracts.json}`. The key stays `"images"` for Arbitrum; Perun writes `"stack_versions"`.

- [ ] **Step 1: Failing tests**

`test_perun_stack_needs_anvil_and_no_containers`, `test_arbitrum_stack_fails_if_anvil_runs`, `test_missing_anvil_up_is_a_failure`, `test_manifest_perun_records_binary_and_anvil` (with an unreachable `bath0`, as in the existing test).

- [ ] **Step 2: Implement and run**

Run: `python3 -m unittest measure.tests.test_preflight measure.tests.test_analyze -v`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add deploy/board-facts.sh measure/preflight.py measure/manifest.py measure/tests/test_preflight.py measure/tests/test_analyze.py
git commit -m "measure: stack-aware pre-flight and manifest (Anvil for Perun, containers for the rollups)"
```

---

### Task 6: Analysis and aggregation for Perun

**Files:**
- Modify: `measure/analyze_uc1.py`
- Create: `measure/tests/fixtures/perun-run/` (2 customers, 5 windows each, `experiment.env` with `STACK=perun EXPECTED_CUSTOMERS=2`, `sensor-balance.json`, `results/sensor/sensor-result.json`)
- Test: `measure/tests/test_analyze.py` (new Perun tests; the existing ones unchanged)

**What changes** (the stack comes from `experiment.env` `STACK`; a missing `STACK` means `arbitrum`, so the old folders analyse as before):

| Item | Arbitrum (today, unchanged) | Perun |
|---|---|---|
| `open_s[c]` | `open.t_l2_credited − open.t_start` | `open.t_funded − open.t_start` |
| open gas / fee | `open.l1_gas_used`, `open.l1_fee_wei` | same keys (Anvil is the L1) |
| `close_s[c]` | `close.t_confirmed − close.t_start` | `close.t_withdrawn − close.t_start` **[Q2]**; `close_finalize_s[c]` = `t_finalized − t_start` beside it |
| close gas / fee | `close.l2_gas_used`, `close.l2_fee_wei` | customer: `close.l1_gas_used`, `close.l1_fee_wei`; sensor: from `sensor-result.json` per channel, reported apart as `gas.close_sensor_l1_median` |
| payment gas / fee | per row | 0 per row (`gas_used = 0`, truthful: an off-chain update costs no gas) |
| `close_definition` | "withdrawal submission on the L2; the 7-day challenge period is documentation (parent §11.4)" | "cooperative close: off-chain finalize + concludeFinal + withdrawal, confirmed on the L1, timed on the customer" |
| reconciliation | sensor L2 balance change == Σ succeeded | **three-way**, `ok` needs all: (a) per customer Σ succeeded == sensor's final channel balance for that customer (`sensor-result.json`); (b) per customer the customer's `close.final_balances_wei` == the sensor's view (same version); (c) the sensor's L1 balance change + the sensor's own close fees == Σ over customers of the sensor's final balances |
| extra | — | `channels`: per customer `final_version`, `broken`, `updates_accepted` (sensor), and `unresolved_in_final_state` = (sensor's final balance − Σ succeeded) / PRICE_WEI when positive (Review Focus 1) |

The deadline miss, percentiles, outcome mapping, `complete` rules, recorder summary and `windows_expected` are unchanged. `complete` additionally needs `results/sensor/sensor-result.json` with `complete: true`. `aggregate.py`: no change, except that `HEADLINE` gains `close_finalize_s_median` (None for Arbitrum, which the existing `_get` tolerates). Mixing stacks is already refused by the experiment-equality guard.

- [ ] **Step 1: Failing tests**

`test_perun_fixture_reconciles_three_ways`, `test_perun_channel_view_mismatch_fails_reconciliation`, `test_perun_unresolved_update_in_final_state_is_reported`, `test_perun_payments_cost_no_gas_and_open_close_do`, `test_perun_missing_sensor_result_is_incomplete`, `test_old_arbitrum_folder_without_stack_is_unchanged` (the existing real-layout fixture gives a summary identical to today's, byte for byte except the new `close_definition`).

- [ ] **Step 2: Implement, then run the whole suite**

Run: `python3 -m unittest discover -s measure/tests -v`
Expected: all PASS.

- [ ] **Step 3: Re-analyse the committed Arbitrum folders as a regression check**

Run: `for d in results/pi/uc1/*-arbitrum-*; do python3 -c "import json,sys; from measure.analyze_uc1 import analyze; print(analyze(sys.argv[1])['reconciliation'])" "$d"; done`
Expected: every result equal to the committed `summary.json` values (do not rewrite the committed summaries).

- [ ] **Step 4: Commit**

```bash
git add measure/analyze_uc1.py measure/aggregate.py measure/tests/test_analyze.py measure/tests/fixtures/perun-run
git commit -m "measure: UC1 analysis for Perun (open/close definitions, zero-gas payments, three-way reconciliation)"
```

---

### Task 7: `deploy/cluster` for Perun, and the smoke run

**Files:**
- Modify: `deploy/cluster`
- Create: `deploy/experiments/uc1-perun-smoke.env`, `uc1-perun-w1000.env`, `uc1-perun-w100.env`, `uc1-perun-w10.env`

**Interfaces (commands):**
- `sync`: as today, plus: build `perun/hub-demo` for arm64 (`deploy/dist/arm64/perun-node`, the flags in Global Constraints) and amd64 (`deploy/dist/amd64/perun-node`, for the deploy step), write `deploy/dist/arm64/perun-node.sha256`, and ship the arm64 binary to `~/measure-code/bin/perun-node` on `bath1`…`bath7`. It **refuses to build from a dirty `perun/` tree** (the binary must match a commit).
- `up perun`: on `bath0`, refuse if any container runs (`docker ps -q`; "another stack is up"). Kill any Anvil, start `setsid nohup $ANVIL_BIN --host 0.0.0.0 --port 8545 --chain-id 31337 --block-time 1 > ~/perun/anvil.log 2>&1 < /dev/null &` (`ANVIL_BIN` from Task 2 Step 2), wait for `rpc_ok http://192.168.178.210:8545`, run `deploy/dist/amd64/perun-node -mode uc1-deploy … -out $PERUN_CONTRACTS` and check the addresses against the pinned constants (Task 1 Step 1).
- `up arbitrum` gains the mirror check: refuse if `pgrep -x anvil` on `bath0`.
- `down`: as today, plus `pkill -TERM -x anvil; sleep 2; pkill -KILL -x anvil` on `bath0` (Anvil keeps no state, and a fresh one starts every run).
- `LEFTOVERS` gains `[p]erun-node`.
- `one_run`, when `STACK=perun` (the Arbitrum branch stays byte-identical in behaviour):
  - code check: as today, plus `perun_node_sha256` on `bath1`…`bath7` == `deploy/dist/arm64/perun-node.sha256`; written to `perun-node-sha256.txt` in the run folder;
  - pre-flight: `measure.preflight <dir> perun`;
  - setup: fund 100…106 on Anvil (the existing `arbitrum.fund_on_l1`, pointed at `http://192.168.178.210:8545`). The budget check for Perun is `n × PRICE_WEI ≤ DEPOSIT_WEI`, and `FUND_WEI − DEPOSIT_WEI` ≥ 10 × (the measured open + close gas × the current base fee). Copy `contracts.json` into `results/`;
  - reachability: from every customer board, `curl -s -m 3 http://192.168.178.211:8090/v1/manifest` must answer once the sensor node is up. (Do not probe 9000 with `nc`: DISTRIBUTED.md.) Every customer→`bath1` pair is thereby checked, following the four-board lesson (spec §8);
  - sensor node: write `$rd/sensor.key` through `ssh bath1 "umask 077; cat > $rd/sensor.key"` with the key on **stdin** (not `on`, which uses `ssh -n`). Start `bg bath1 "$rd/perun-sensor.out" "~/measure-code/bin/perun-node -mode uc1-sensor … -customers 6 -allow <101..106> -out $rd/ps"` and wait until `/v1/manifest` answers (deadline `READY_WAIT_S`);
  - payment clients: `--system perun …` with `--wire-endpoint 192.168.178.21<N>:9000`; wait for `ready` as today;
  - the sensor's balance **before**: `cast balance <sensor> --rpc-url http://192.168.178.210:8545` after every customer is ready (the sensor sends no transaction before Close);
  - sensor stream and customers: unchanged commands;
  - wait: payment clients `complete` (as today), then the sensor node exits with `sensor-result.json` `complete: true` (deadline `SETTLE_WAIT_S`, default 600); the balance **after**; `results/sensor-balance.json` = `{"stack":"perun","sensor":…,"l1_balance_before_wei":…,"l1_balance_after_wei":…}`;
  - collect: also `bath1:$rd/ps/.` → `results/sensor/` and `perun-sensor.out` → `logs/bath1-perun-sensor.out`; each customer's `sidecar.log` travels inside `pc/`; `bath0:~/perun/anvil.log` → `logs/bath0-anvil.log`.

- [ ] **Step 1: Experiment files**

```bash
# deploy/experiments/uc1-perun-w1000.env
USE_CASE=uc1
STACK=perun
WINDOW_MS=1000
DURATION_S=900
PRICE_WEI=1000000000000
DEPOSIT_WEI=1000000000000000000
FUND_WEI=2000000000000000000
MAX_IN_FLIGHT=64
MAX_QUEUE=10000
DRAIN_S=60
EXPECTED_CUSTOMERS=6
UPDATE_TIMEOUT_S=10
CHALLENGE_S=10
PERSIST=1
STRICT_POLICY=1
```

`-w100` and `-w10` differ only in `WINDOW_MS`. `-smoke` is `WINDOW_MS=1000 DURATION_S=60`. `PERSIST` [Q4], `STRICT_POLICY` [Q7], and `COALESCE=1` only if Q3 = B/C (then as `uc1-perun-w10-coalesced.env`).

- [ ] **Step 2: Implement; static checks**

Run: `bash -n deploy/cluster && shellcheck deploy/cluster`
Expected: clean.

- [ ] **Step 3: Board-free checks**

`deploy/cluster status` (read-only; shows `anvil_up`). Run `deploy/cluster sync` only after the owner's go at Checkpoint 2.

- [ ] **Step 4: Checkpoint 2, then sync, the Arbitrum regression smoke and the Perun smoke**

```bash
deploy/cluster sync
deploy/cluster run uc1-arbitrum-smoke      # regression: must still pass as on 2026-09-30
deploy/cluster run uc1-perun-smoke
python3 -m measure.analyze_uc1 results/pi/uc1/<the perun smoke folder>
```

Expected, Perun smoke: 6/6 `complete`, 360/360 `succeeded`, reconciliation `ok` (three ways), `open_s` a few seconds (block time 1 s + one confirmation), `close_s` a few seconds, `payment_latency_s.p50` in tens of milliseconds (Task 2 sets the expectation), and zero payment gas.

- [ ] **Step 5: Commit**

```bash
git add deploy/cluster deploy/experiments/uc1-perun-*.env results/pi/uc1/*-perun-uc1-perun-smoke-rep1 results/pi/uc1/*-arbitrum-uc1-arbitrum-smoke-rep*
git commit -m "deploy: Perun in deploy/cluster (up perun, binary code check, sensor node); UC1 Perun smoke run"
```

---

### Task 8: The first 15-minute run and the nine runs

**Files:**
- Create: `results/pi/uc1/*-perun-*` (run folders, big files gzipped by `finish_run` as today)

- [ ] **Step 1: One dry repetition at the slowest window**

Run: `deploy/cluster run uc1-perun-w1000`
Expected: a run folder with `manifest.json`; `results/c1`…`c6`, each with `ledger.csv.gz` (~900 rows) and `lifecycle.json` `complete: true`; `results/sensor/sensor-result.json` complete; `recorder/bath0.csv`…`bath7.csv`; `perun-node-sha256.txt`.

- [ ] **Step 2: Analyse, then Checkpoint 3**

Run: `python3 -m measure.analyze_uc1 <run folder>`
Expected: `complete: true`; only `succeeded` (5 400); `reconciliation.ok: true`; `payment_latency_s` well under the window; `deadline_miss_share` near 0. **Show the summary** (outcomes, reconciliation, Open/Close with `close_definition`, latency percentiles, achieved update rate, start and peak temperatures, `bath1` CPU from the recorder) and wait for the go.

- [ ] **Step 3: Three repetitions, all three windows**

```bash
for w in 1000 100 10; do deploy/cluster run uc1-perun-w$w --reps 3; done
for d in results/pi/uc1/*-perun-uc1-perun-w*-rep*; do python3 -m measure.analyze_uc1 "$d" > /dev/null; done
for w in 1000 100 10; do echo "== w$w"; python3 -m measure.aggregate results/pi/uc1/*-uc1-perun-w$w-rep*; done
```

Expected: nine folders and three aggregate tables. At w10, a backlog, queue-full failures or misses are **a result** (where Perun on these Pis stops keeping up with one update per window, **[Q3]**), not a bug. The ledger still accounts for every window, and reconciliation still holds. A broken channel (update timeout) makes that run incomplete: report it at Checkpoint 5 and do not quietly rerun it.

- [ ] **Step 4: Commit**

```bash
git add results/pi/uc1/*-perun-*
git commit -m "results: UC1 on Perun, three windows x 3 repetitions"
```

---

### Task 9: Record the result and fix the docs

**Files:**
- Modify: `docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md` (§4: "Perun needs no snapshot: every run starts a fresh Anvil, and `deploy/cluster up perun` deploys the Adjudicator and the ETH AssetHolder" instead of "`-mode init` deploys"; §7.2 Perun rows: the recorded Close roles **[Q2]** and "no payment retries" **[Q6]**; §7.3: replace "about a millisecond per update … not a bottleneck" with the measured per-channel figure from Task 2; §8 step 3: ✅ entry with the nine folders and aggregates), `deploy/README.md` §4 (`up perun`), `perun/hub-demo/DISTRIBUTED.md` (a short *UC1 direct channels* section: modes, ports, the sidecar protocol pointer), `deploy/FLEET.md` (the Anvil path on `bath0`)

- [ ] **Step 1: Write the entries** (dates, run folders, aggregate lines, and whether the pipeline needed fixes)
- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md deploy/README.md perun/hub-demo/DISTRIBUTED.md deploy/FLEET.md
git commit -m "docs: UC1 on Perun recorded; spec §4/§7.2/§7.3 corrected to the measured behaviour"
```

---

## Integration tests to run later (workstation first, then boards)

| Where | Command | Needs |
|---|---|---|
| workstation | `cd perun/hub-demo && go test -count=1 ./... && go vet ./...` | nothing |
| workstation | `ANVIL_URL=ws://127.0.0.1:8545 go test -count=1 -run 'TestUC1.*' -v -timeout 15m` | `anvil --chain-id 31337 --block-time 1` |
| workstation | `ANVIL_URL=… go test -count=1 -run 'TestDistributed.*|TestHub.*' -v` (the old launcher still works after the `gas.go` change) | the same Anvil |
| workstation | `python3 -m unittest discover -s measure/tests -v` | venv |
| workstation | `PERUN_ANVIL=ws://127.0.0.1:8545 PERUN_BIN=/tmp/pn python3 -m unittest measure.tests.test_perun -v` | Anvil and a local `perun-node` build |
| workstation | Task 4 Step 3 end-to-end (one customer, 20 s) | Anvil |
| boards | Task 2 probe matrix | the cluster, released by the owner |
| boards | `deploy/cluster run uc1-arbitrum-smoke`, then `uc1-perun-smoke` | Checkpoint 2 |

## After this plan (not part of it)

- `perun-on-arbitrum` (spec §7.4): the same UC1 path with `uc1-deploy` pointed at the Arbitrum L2 (`-url`, `-chainid 412346`). The sidecar needs no change, but Open/Close then become Arbitrum transactions.
- The capacity probes (spec §7.1) and the `baseline` RTT, which would also set a principled `update_timeout_s`.
- The ansible `perun` role (spec §3): `anvil.service` and the units for the UC1 sensor and customer modes, replacing the ssh starts in `deploy/cluster`.
