> **ARCHIVED** — executed 2026-10-01/02; the code and the cluster spec §8 are authoritative.

# UC1 on zkSync Era — second measurement path — Implementation Plan

> **Status: DRAFT, written 2026-09-30 while the Arbitrum series was running. The owner has not answered the open questions yet.** Do not start before (a) the owner has answered the *Open questions* below and (b) the Arbitrum series is finished and committed on `v2`. The Arbitrum series checks that its `measure/` code does not change during the series, so no task here may touch `v2` before then. Execute on `v2` (the active thesis branch), not on the `plans/zksync-perun` branch that holds this draft.

**Goal:** One command on the workstation, `deploy/cluster run uc1-zksync-w1000`, runs UC1 on the zkSync Era stack on the Pi cluster end to end: pre-flight, Open, paced payments per data window, Close, recording and collection. A second command turns the run folder into the thesis numbers. The run must be comparable with `uc1-arbitrum-w1000`: same sensor, same customers, same payment ledger, same analysis.

**Architecture:** The Arbitrum path stays unchanged in what it does. The sensor, the customers, the Unix-socket event interface, the payment ledger, the recorder, the manifest and the analysis are shared. The following parts are new or changed:
- **`measure/zksync.py`** is a lifecycle module with the same interface as `measure/arbitrum.py`. Open = `Bridgehub.requestL2TransactionDirect` on the L1, timed until the L2 balance shows the credit. Payment = one EIP-1559 ETH transfer to the sensor. Close = `L2BaseToken.withdraw(address)` on the L2 (the withdrawal *submission*).
- **`OrderedSubmitter`** gets per-system parameters: the error texts, and how often it polls for receipts. The defaults stay the Arbitrum values.
- **`payclient`** gets `--system zksync`, a longer per-payment receipt timeout, a Close timeout, and nonce bookkeeping. The nonce bookkeeping lets payments that were *executed after the drain deadline* be counted instead of guessed.
- **`deploy/cluster`** learns `up zksync` / `down` for the native `zksync_server` plus its reth and PostgreSQL containers on `bath0`.
- **`measure/analyze_uc1.py`** gains achieved throughput, backlog and late-execution fields. These fields are backward compatible with the Arbitrum folders.

**Tech Stack:** as the Arbitrum path: Python 3.12 (stdlib `asyncio`, `unittest`), `web3==8.0.0` (with `eth_abi`, `eth_account`, `websockets`), `coincurve`/`rlp` signing (`measure/txsign.py`), bash, OpenSSH. zkSync Era `zksync_server` 31.5.0 (our arm64 build, commit `ff5f519b`, SHA-256 `e77b1cc8…`) runs natively on `bath0`. reth v1.8.2 (the L1) and postgres:18 run in Docker on `bath0`.

**Spec:** `docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md` (§4 snapshots, §5 control and pre-flight, §6 recording, §7.1 capacity probes, §7.2 UC1, §7.3 payment ledger with zkSync's in-flight bound of 20, §8 zkSync feasibility entries, §9 U3/U5) with the parent spec `docs/superpowers/specs/2026-09-03-l2-benchmark-design.md` (§3.1 UC1, §5 metrics, §6 "sealing is tuned / cadence policy", §8 run matrix, §11.4 Close asymmetry, §11.5 deposits). Background: `bench/results/zksync-pi-cpu-2026-09-30.md` (why zkSync is slow on the Pi 4) and `bench/results/README-zksync.md` (the per-account nonce window, `max_nonce_ahead`).

**Template:** `archive/plans/2026-09-30-uc1-arbitrum-measurement-path.md`, together with its execution ledger (working note, not kept). Execution replaced parts of that plan's text (rulings R2–R17). **The code on `v2` is the truth.** Every interface named below refers to the code as of commit `1ebc0c0`, not to the Arbitrum plan's text.

---

## Owner's answers (2026-10-01)

Q1 **A** (not the recommended C): all three windows × 3 reps, as for Arbitrum. If `w100`/`w10` saturate or fail, that is the result; the point is numbers for comparison. Follow every `[Q1≠rec]` A marker: Task 8 creates all three env files with their deposits (`w10` about 5 ETH), Task 10 runs all three. bath0 may throttle near 85 °C; the recorder captures it. Q2 **a** (20 000 ms). Q3 **a**. Q4 **a**. Q5 **a**. Q6 **a**. Q7 **a**, with the Arbitrum hardening applied the same day: the L1 HTTP API limited to `eth,net,web3`, no `corsdomain=*`.

## Open questions for the owner

Answer these before Task 2. The body of this plan is written for the **recommended** answer. `[Qn≠rec]` marks the places where another answer changes a task.

### Q1 (the important one). How does the known capacity limit shape the experiments?

**Facts.**
- zkSync on `bath0` carries **about 4 transfers per second**, with all four cores busy (cluster spec §8).
- Confirmation median was **16.7 s** at 2 tx/s over 20 min (bare board, 20 s batch window). Overnight on 2026-09-30 (heatsinks, 2 tx/s, 10 min) the per-run medians were **32–52 s** and the p99 values **106–151 s** (`bench/results/zksync-pi-cpu-2026-09-30.md`).
- The cause is the CPU. About 80 % of it goes into KZG commitments, at about 29 s per batch. No build or configuration change tried so far moves it.
- UC1 with 6 customers offers **6 / 60 / 600 payments per second** at the 1 s / 100 ms / 10 ms windows. At 10/s offered on the Pi, 234 of 600 transfers were still unconfirmed after 2 minutes.
- The confirmation *floor* alone (the L2 block every `miniblock_commit_deadline_ms: 1000`) is already about as long as the 1 s window's deadline. So the deadline-miss share will be close to 100 % at **every** window. What differs between the windows is only how fast the backlog and the queue-full failures grow.

| Option | What runs | Consequence for comparability with Arbitrum | Cost / risk |
|---|---|---|---|
| **A. All three windows, as specified** | `w1000`, `w100`, `w10`, 6 customers, 15 min, 3 reps each | Full 3×3 matrix, cell for cell. `w100` and `w10` show the same ceiling as `w1000`, with ever larger queue-full / unresolved counts (`MAX_QUEUE` 10 000 binds at `w10` as it did for Arbitrum) | About 9 × 50–60 min at 84 °C on `bath0`, the only Pi 4 (no spare, spec §2). `w10` needs a bigger deposit (about 5 ETH, Task 8) |
| **B. Fewer customers** (e.g. 2 customers = 2/s, known to be sustainable) | `w1000` with 2 customers | **Breaks** comparability: another topology and another offered load than Arbitrum and Perun. The result then says only "zkSync keeps up with a smaller use case" | Cheap. Changes the use case, not the system |
| **C. Only the 1 s window, as specified** (recommended) | `w1000`, 6 customers, 15 min, 3 reps | Comparable cell for cell at `w1000`, the smallest offered load. `w100`/`w10` are reported as **not run: saturated already at `w1000`** (offered 60/600 per s against a measured ceiling of about 4/s), with this reason in the thesis. It is the same kind of stated gap as parent §5.3's "known gap" for Perun | About 3 × 50–60 min. The env files for `w100`/`w10` are still created, so running them later is one command |
| **D. Capacity probe first** (`one-node`, cluster spec §7.1) | A sweep of offered rates through the new payment client, then decide | Adds the ceiling number the thesis needs anyway (§7.1). It does not replace a UC1 cell | Needs the probe tooling on the new pipeline, which is not in this plan. `w1000` itself already measures achieved vs offered rate at 6/s |
| **E. Raise capacity by configuration** | — | Measured, and it does not help (§8, CPU report): `block_commit_deadline_ms` 2 000 → about **1 transfer per 5 s** (every batch closes after one transfer); 20 000 → batches of 17–149, **about 4/s**; calldata instead of blobs: median/p99 1.3 s/15 s at 2/s, KZG work unchanged; pre-Fusaka blobs 1.4 s/21 s; a `cortex-a72` build: commitment time the same (29.3 s per batch); `commitment_generator.max_parallelism` is not wired into the main server; fewer `RAYON_NUM_THREADS` make it worse; **Validium** would drop the KZG work but is a different system | Rejected as an option. Q2 still has to fix the one setting that *is* open |

**Recommendation: C.** It keeps the one cell that can be compared honestly. It avoids five more hours near the soft temperature limit on the only board that can run zkSync. And it reports the saturation as the finding the spec already records ("reported, not engineered around", §8). Also recommended: run the capacity probes (D) later as their own plan (*After this plan*), because the thesis needs the ceiling anyway.
`[Q1≠rec]`: A → Task 8 creates all three env files with the right deposits, and Task 10 runs all three. B → Task 8 adds `EXPECTED_CUSTOMERS`/`CUSTOMERS` overrides (the cluster script currently hard-codes six customers). The analysis already reads `EXPECTED_CUSTOMERS`.

### Q2. Server configuration for the runs (the batch window, and everything else)

The spec leaves the batch window open (§8: "**Open decision** for the thesis setup"). The PC snapshot has `block_commit_deadline_ms: 2000`. `bath0`'s copy was changed by hand to `20000` (the original is kept as `general.yaml.orig-2000ms`). The workstation today has 2000 (read 2026-09-30).

- **a. 20 000 ms (recommended).** It is the fastest honest setting on the Pi (parent §6: "run at the fastest honest setting"): about 4/s. It is reported next to the results as zkSync's cadence constant, together with `miniblock_commit_deadline_ms: 1000` (unchanged).
- b. 2 000 ms (the local-development default, as on the workstation). About 0.2/s on the Pi, so the result would be dominated by a setting that is known to be wrong for the hardware.
- c. Something between (e.g. 10 000). Not measured, so it needs its own feasibility run first.

Everything else stays **exactly as in the snapshot** and is recorded in the manifest (Task 6): `max_nonce_ahead: 2500`, `pubdata_sending_mode: BLOBS`, `miniblock_commit_deadline_ms: 1000`, and the debug `log_directives` (the feasibility figures were taken with them). The spec's bound of **20 in flight per account** (§7.3, mainnet's limit) is enforced by the client (`MAX_INFLIGHT=20`). The server's 2 500 then never binds, because a customer never has more than 20 unconfirmed transactions. Alternative: set the server's `max_nonce_ahead` to 20 as well. It would add realism on paper but no difference in behaviour, and it would add a rejection path that the tests would have to cover.
`[Q2≠rec]`: only the value `ZK_COMMIT_DEADLINE_MS` in Task 8's env file changes, plus the thesis text.

### Q3. Drain deadline and payments executed after it

At 6/s offered against about 4/s, a backlog builds. Expected at the end of the stream: about 20 submitted but unconfirmed payments per customer (the in-flight bound), plus a growing queue in each payment client. The Arbitrum runs used `DRAIN_S=60`. Whatever is pending at the deadline is *unresolved* (spec §7.3). A payment that was *submitted* before the deadline stays in the zkSync mempool and still executes later. The sensor's balance then rises by more than the ledger's *succeeded* sum, and the strict reconciliation reports a difference.

- **a. The same 60 s drain as Arbitrum, plus exact late-execution accounting (recommended).** The payment client records each account's nonce when payments start and after Close. `executed on chain = close nonce − start nonce`, so the payments that executed after the deadline are counted exactly (Task 4, Task 7). Outcomes are not rewritten: a payment unresolved at the deadline stays unresolved. The reconciliation reports `ok` (strict, as today) and `ok_with_late`. The same policy then applies to all systems, and the numbers stay honest.
- b. A long drain (e.g. 900 s), so that almost every submitted payment gets a confirmation time. This gives a fuller latency distribution, but a different policy than the one Arbitrum was measured under (Arbitrum drained in about a second, so its numbers would not have changed, but the stated policy would differ).
- c. Drain until the mempool is empty, capped at 30 min. The policy is the same as b, with an unbounded run length.

Either way, **Close waits behind the backlog.** The withdrawal takes the next nonce, and zkSync executes in nonce order, so the Close latency includes the up to 20 payments ahead of it. Task 4 records `pending_before_close` and waits for the Close receipt up to `CLOSE_TIMEOUT_S`. The analysis reports Close with that count beside it.
`[Q3≠rec]`: b/c only change `DRAIN_S` in Task 8 (and `RECEIPT_TIMEOUT_S ≥ DURATION_S + DRAIN_S`). The late-execution accounting stays useful as a check.

### Q4. Cooling gate under zkSync's heat

The gate (spec §5): every board within **3 °C of its own idle temperature**, waiting at most **20 min**. `bath0` idles at 48.7 °C, so it must fall to ≤ 51.7 °C. zkSync runs held `bath0` at **83.7–84.2 °C** even with the heatsinks (soft limit 85 °C). It is not known how long `bath0` needs to fall from 84 °C to within 3 °C of idle with the stack down. If it takes longer than 20 min, the gate aborts the series after rep 1.

- **a. Keep the 3 °C rule. Measure the cooling time in the smoke run (Task 9), and if it is over 15 min, set `COOL_MAX_S` for the zkSync experiments to 45 min (recommended).** The start state stays the same as Arbitrum's (only the waiting changes), so comparability holds. Also: **re-measure the idle temperatures** (`deploy/cluster idle-temps`) at the start of the zkSync series day, because the room may differ from 2026-09-30. Both files are kept.
- b. Keep 20 min and accept that the series may stop. Each rep then needs a manual restart.
- c. Relax the rule for `bath0` (e.g. within 8 °C). The runs would start warmer than Arbitrum's, which is not comparable.

Note: `up zksync` itself heats `bath0` (restore + server catch-up, minutes of CPU) *after* the gate, so the stream starts warmer than the gate reading. Arbitrum had the same order (gate before `up`), but its `up` was lighter. Task 5 therefore also records the temperatures at **stream start** (a record, not a gate).
`[Q4≠rec]`: b → no change. c → Task 5 adds a per-board delta to `cool_gate`.

### Q5. `bath0`'s throttle flags after a zkSync run

Pre-flight requires `get_throttled` = `0x0` on `bath0` and `bath1`, with only the soft-temperature bit `0x80000` excepted. The since-boot bits clear only on a reboot. At 84 °C the firmware may also set **arm-frequency-capped-occurred (`0x20000`)** or **throttled-occurred (`0x40000`)**. If it does, every zkSync rep after the first fails pre-flight ("reboot it"). **Unknown** whether it does: the feasibility reports give the share of soft-limit samples, not the other bits. Task 1 reads the flags. The smoke run (Task 9) shows the flags after a run.

- **a. If the smoke run shows these bits: on `bath0`, accept the temperature-caused since-boot bits `0x20000`/`0x40000`/`0x80000` at the start, *as long as no undervoltage bit (`0x1`, `0x10000`) and no current bit (`0x2`, `0x4`, `0x8`) is set*. Record them in the manifest (recommended).** The rule exists to catch bad power, and the cooling gate already guarantees the thermal start state.
- b. Reboot `bath0` before every zkSync rep (clears the bits). This costs about 2 min and brings back the band-after-boot risk (FLEET.md: the dual-band boards joined 5 GHz after boot; pre-flight would then abort).
- c. Keep the rule strict. The series then stops after rep 1.
`[Q5≠rec]`: b → Task 5 adds a `reboot bath0` + wait step to `one_run` for zkSync. c → no change. If the smoke run shows no such bits, nothing changes, whatever the answer.

### Q6. Snapshot and how it is restored

The snapshot that `bath0` already runs is `zksync-feas-20260929`, which is git-ignored. Its files, from `SHA256SUMS`:
- `postgres.dump` 427 MB, `68de540c…`;
- `chain.tgz` 47 MB, `f745771d…` (chain directory with RocksDB and configs, plus `etc/reth/chaindata`);
- `reth-data.tgz` 15 MB, `1828ffbe…`;
- `era-artifacts.tgz` 65 MB, `bd2ffe0c…`.

The Arbitrum path restores its *feasibility* snapshot too, so using it keeps the two paths symmetrical. It holds no points token, which UC1 does not need. The restore on `bath0` took `pg_restore` **2 min 13 s** of CPU.

- **a. Use `zksync-feas-20260929`. On `bath0`, run `pg_restore` once into a fresh PostgreSQL volume, stop, and pack that volume raw (`postgres-vol.tgz`). Every run then restores the raw volume, like Arbitrum's volumes (recommended).** A raw restore is byte-identical (spec §4: "every run begins from a byte-identical chain"), whereas a `pg_restore` gives the same rows but not the same bytes. It is also faster, and heats `bath0` less after the cooling gate.
- b. `pg_restore` from the dump in every run. This is proven on `bath0`, but costs about 2+ min of CPU heat per run and is not byte-identical.
- c. A fresh snapshot from a new `zkstack` init on the workstation. It gives a cleaner provenance but is a day's work (spec §8 lists it as "not yet done"). Not needed for UC1.
`[Q6≠rec]`: b → Task 5's restore uses `pg_restore` (the code is sketched there as the fallback).

### Q7. Open `bath0`'s zkSync L1 to the network

The customers deposit on the L1 themselves (Open is timed on their board). The committed compose file binds reth to **`127.0.0.1:8945`** only (`zksync-setup/thesis_local/docker-compose.yml`). Whether `bath0`'s copy differs is verified in Task 1. The L2 API (`3050` HTTP, `3051` WS) was reachable from `bath1` and from the PC in the feasibility runs, so it is probably bound to all interfaces. Task 1 verifies this.

- **a. Yes: bind reth's 8945 to `0.0.0.0` on `bath0` (and 3050/3051 if Task 1 finds them bound to localhost), as for Arbitrum's 8545/8548 at its Checkpoint 2 (recommended).** PostgreSQL's 5432 stays on localhost.
- b. No: then Open would have to be sent from `bath0` itself, which is not the customer's board, and the timing would not be comparable with Arbitrum.
`[Q7≠rec]`: b blocks the plan as written.

---

## Decisions this plan rests on

**Already decided (spec or owner), not open:**
- UC1 layout: `bath0` = stack, `bath1` = sensor, `bath2`…`bath7` = 6 customers.
- GESL 6063, 15 min of replay per run, windows as in spec §7.2, price 10¹² wei per window, deposit 1 ETH per customer, clock tolerance 5 ms, the cooling gate rule, the run-folder layout, n = 3, ledgers gzip-compressed and committed with the run folders (owner, 2026-09-30).
- zkSync **at most 20 transactions in flight per account** (spec §7.3), so `MAX_INFLIGHT=20` (Arbitrum used 64, having no such limit). A slot is freed only by a real outcome or at the drain deadline, never by a per-payment timeout during the run (Task 4).
- Payments are ETH transfers with a **fixed generous gas limit** (spec §7.3, "zkSync's non-deterministic gas is handled with a fixed generous gas limit, as in the workstation runs"). Gas figures stay in zkSync's own units (parent §5.2).
- Close = the withdrawal **submission**, timed to its L2 receipt. Batch commit, proof and execution on the L1 are documentation, reported in their own column (parent §11.4).
- *Succeeded* = included in an L2 block with status 1. That is the sequencer's inclusion, the same meaning as for Arbitrum. The L1 batch commit comes later and is not measured here.
- The same retry policy as Arbitrum: `retry_delays (0.25, 0.5, 1.0)`, `reply_timeout 30 s`, resend of the same signed bytes, "resync and continue" on permanent rejection (owner, Checkpoint 1 of the Arbitrum path).

**Assumed from the recommended answers:** Q1 C, Q2 a (20 000 ms), Q3 a (60 s drain + late accounting), Q4 a, Q5 a (conditional), Q6 a, Q7 a.

## Checkpoints — stop and ask the project owner

1. **After Task 1**: show the facts file (`bench/results/zksync-uc1-facts-<date>.md`) and any fact that contradicts this plan. Get the answers to Q1–Q7 if they are not in yet.
2. **Before Task 5, Step 4**: before changing `bath0`'s compose file (ports, Q7) and before packing the PostgreSQL volume (Q6).
3. **After Task 9 (smoke)**: show the smoke summary, `bath0`'s temperature curve and cooling time, and its throttle flags after the run. Confirm Q4/Q5 with those numbers.
4. **After Task 10, Step 2 (15-minute dry run)**: show the summary (outcomes, achieved rate, backlog, late executions, reconciliation, Open/Close, latency percentiles, temperatures) before the real repetitions.
5. **Anything unexpected**: a spec rule that cannot be met, a number far from expectation (e.g. far below 4/s or far above), a board fault. Describe it and ask; do not choose silently.

## Global Constraints

- All constraints of the Arbitrum plan's *Global Constraints* apply unchanged. The exceptions are the stack facts below and `MAX_INFLIGHT=20`.
- **Never two stacks at once** (spec §5, parent §8). `up zksync` refuses if any Arbitrum container or Anvil is running on `bath0`. `up arbitrum` refuses if `zksync_server` or a zkSync container is running.
- **Shared code must not change Arbitrum's behaviour.** Every change to `submitter.py`, `payclient.py`, `analyze_uc1.py`, `manifest.py`, `preflight.py`, `board-facts.sh` and `deploy/cluster` keeps its defaults at the Arbitrum values. All existing tests must stay green. Re-analysing the nine Arbitrum run folders must give **identical values for every existing summary key** (Task 7, Step 5).
- **Processes are found by full path**, never by a short name. On 2026-09-30 a lookup by the name `zksync_server` missed `zksync_server.a72` and left a server running (CPU report, *Mistakes*).
- zkSync stack facts. Each is labelled *repo* (committed), *snapshot* (the git-ignored `deploy/dist/snapshots/zksync-feas-20260929`, read 2026-09-30), *workstation* (read 2026-09-30), or **F#** (verified in Task 1):

| Fact | Value | Source |
|---|---|---|
| L2 chain ID | 271 | repo `zksync-setup/thesis_local/chains/thesis_chain/ZkStack.yaml`; snapshot `genesis.yaml` |
| L1 chain ID | 9 | snapshot `genesis.yaml` (`l1_chain_id: 9`), `reth_config` `chainId 9`; **F9** |
| L1 (reth v1.8.2, `--dev.block-time 300ms`) | port 8945, bound to 127.0.0.1 in the committed compose | repo compose; **F3** for `bath0` |
| PostgreSQL | postgres:18, 127.0.0.1:5432 | repo compose; **F3** |
| L2 JSON-RPC | HTTP 3050, WS 3051 | snapshot `general.yaml` `api.web3_json_rpc`; **F6** for binding |
| Health check | port 3071 | snapshot `general.yaml` `api.healthcheck.port` |
| Bridgehub (L1) | `0xe965f94cd784d274e6970f603576265b17cfa3f4` | snapshot `configs/contracts.yaml` `bridgehub_proxy_addr`; **F16** |
| L2 base token (ETH) | `0x000000000000000000000000000000000000800A` | zkSync system contract; **F16** |
| Deposit call | `requestL2TransactionDirect((uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address))` = `0xd52471c1`; base cost `l2TransactionBaseCost(uint256,uint256,uint256,uint256)` = `0x71623274` | `cast sig` 1.7.1 on 2026-09-30; that zksync-ethers' `deposit` uses these for an ETH-based chain is **F16** |
| Withdrawal call | `withdraw(address)` = `0x51cff8d9` on `0x…800A` | `cast sig`; **F16** |
| L2 gas per pubdata for deposits | 800 | zksync-ethers constant `REQUIRED_L1_TO_L2_GAS_PER_PUBDATA_LIMIT`; **F16** |
| L1 funder | `0x36615Cf349d7F6344891B1e7CA7C72883F5dc049`, the ecosystem `deployer`, 100 ETH in the reth genesis; key `0x7726827c…4110` (public zksync-era dev key, already in `zksync-setup/bench-bridge/bridge_bench.js`) | snapshot `configs/wallets.yaml`, `reth_config` alloc; **F8** for the current balance. **The test-mnemonic index 0 (`0xf39F…`) is *not* in the reth genesis**, unlike on Arbitrum's L1 |
| `block_commit_deadline_ms` | 2000 in the snapshot and on the workstation; 20000 on `bath0` (hand edit) | snapshot/workstation `general.yaml`; spec §8; **F4** |
| `miniblock_commit_deadline_ms` | 1000 | snapshot `general.yaml` |
| `max_nonce_ahead` | 2500 (raised from 200 on 2026-09-09; `general.yaml.bak-nonce200` kept) | snapshot `general.yaml`; `bench/results/README-zksync.md` |
| `minimal_l2_gas_price` | 10⁸ wei; `gas_price_scale_factor` 1.5 | snapshot `general.yaml` |
| `zksync_server` binary | our arm64 build (not distributed), SHA-256 `e77b1cc838a0bb459509b5f7067b02ebdb318758f14061e08339a2740b2ad8a6` | spec §9 U2; **F1** for its path on `bath0` |
| How the server is started (args, components, working directory) | **unknown in the repo** | **F2** |
| Where the ecosystem lives on `bath0` | the snapshot's paths are absolute (`/home/i_wanilin/zksync-ecosystem/...`), so presumably the same there | **F1** |

- Test keys: index **100 = sensor, 101…106 = customers 1…6**, as for Arbitrum. Whether they hold nothing on zkSync's L2 in the snapshot is **F8**. `bench/fund_accounts.py` funded mnemonic accounts on the workstation zkSync on 2026-09-08, probably low indices only.

## Review Focus

1. **Duplicate and resend answers on zkSync.** The submitter resends the same bytes after `reply_timeout` or a reconnect. If zkSync's answer to a duplicate is not in `accepted_texts`, the submitter treats it as a permanent rejection and resyncs, which is a pause and a spurious "failed". Pinned in Task 2 by a fake that answers with zkSync's texts (from **F12**).
2. **The in-flight bound of 20 must really hold.** A slot is released only by a receipt, a proven failure, or the drain deadline. The current per-payment `receipt_timeout` of 120 s would free slots mid-run at zkSync's latencies (p99 up to 151 s at 2/s) and silently exceed 20. Pinned in Task 4 (`test_slot_is_not_freed_before_the_receipt_timeout`) and by `RECEIPT_TIMEOUT_S` in the env file.
3. **Receipt polling must not load the API it measures.** Today every open hash is also polled once per second (`eth_getTransactionReceipt`). That is harmless on Arbitrum, where hashes resolve in 0.3 s. On zkSync, 120 open hashes at 30–50 s each would mean about 120 extra requests per second on a CPU-starved Pi. Pinned in Task 2 (`receipt_sweep_s`).
4. **Late executions are counted, not guessed.** The count is `close nonce − start nonce − (rows with a receipt)`, per customer. It must be 0 on all nine Arbitrum folders (Task 7, Step 5).
5. **`down` really stops zkSync.** The native server is stopped by full path, with TERM and then KILL, *before* the containers. A server left running keeps working off its commitment backlog (90–120 s of full CPU) and heats `bath0` during the next cooling gate. Pinned in Task 5, Step 6.
6. **Open's `mintValue`.** A wrong base cost reverts the deposit. That is loud (Open fails, the run stops), but Task 3's integration test must pass on the workstation first.

---

## File structure

| File | Change |
|---|---|
| `bench/results/zksync-uc1-facts-<date>.md` | **new** (Task 1): the verified facts F1–F18 |
| `bench/zksync_uc1_probe.py` | **new** (Task 1): workstation probe for F9–F15, like `bench/zksync_sanity.py` |
| `zksync-setup/bench-bridge/bridge_bench.js` | modify (Task 1): print the deposit and withdrawal tx hashes (for F16) |
| `measure/submitter.py` | modify (Task 2): `accepted_texts`, `retry_texts`, `low_texts`, `receipt_sweep_s`, public `start_nonce`; defaults unchanged |
| `measure/zksync.py` | **new** (Task 3): lifecycle, same interface as `arbitrum.py` |
| `measure/payclient.py` | modify (Task 4): `--system zksync`, `--receipt-timeout`, `--close-timeout`, nonce bookkeeping |
| `deploy/cluster` | modify (Task 5): per-stack endpoints, `up zksync`, `down` for both, zkSync collect, funder/budget per stack, stream-start temperatures |
| `deploy/zk-sampler.sh` | **new** (Task 5): `bath0` process and chain-progress sampler (zkSync runs) |
| `deploy/board-facts.sh`, `measure/preflight.py` | modify (Task 6): containers and `zksync_server` in the facts; "only the expected stack" per stack; Q5 rule |
| `measure/manifest.py` | modify (Task 6): stack-aware images, binary and config hashes |
| `measure/analyze_uc1.py`, `measure/aggregate.py` | modify (Task 7): achieved rate, backlog, late executions, `ok_with_late` |
| `deploy/experiments/uc1-zksync-{smoke,w1000,w100,w10}.env` | **new** (Task 8) |
| `measure/tests/test_zksync.py` | **new**; integration tests skip unless `ZK_L1`/`ZK_L2` are set |

**Workstation zkSync for the integration tests (run them later, not while boards or the Arbitrum stack are busy):** the chain in `~/zksync-ecosystem/thesis_local`, same origin as the snapshot.
```bash
cd ~/zksync-ecosystem/thesis_local && docker compose up -d reth postgres
# then start the server exactly as recorded in F2 (from ~/zksync-ecosystem/zksync-era,
# binary ~/zksync-ecosystem/zksync-era/core/target/release/zksync_server), log to /tmp/zk-ws.log
export ZK_L1=http://127.0.0.1:8945 ZK_L2=ws://127.0.0.1:3051
# stop after the task:
pkill -TERM -f "$HOME/zksync-ecosystem/zksync-era/core/target/release/zksync_server"; sleep 20
docker compose stop
```
The Arbitrum stack must be down meanwhile (CPU; parent §8). The workstation has `block_commit_deadline_ms: 2000` (read 2026-09-30), so its latencies are not the Pi's. The integration tests check behaviour, not numbers.

---

### Task 1: Verify the unknown facts (read-only on `bath0`, probes on the workstation)

Nothing in this task changes a board. Part A reads `bath0` over SSH once the Arbitrum series is finished and the cluster is idle. Part B starts the **workstation's** zkSync (not a board).

**Files:**
- Create: `bench/results/zksync-uc1-facts-<date>.md`, `bench/zksync_uc1_probe.py`
- Modify: `zksync-setup/bench-bridge/bridge_bench.js` (print hashes)

- [ ] **Step 1 (Part A, read-only): `bath0`'s zkSync installation, F1–F7**

```bash
ssh bath0 'set -x
  ls -la ~ ~/zksync-ecosystem ~/zksync-ecosystem/thesis_local 2>&1 | head -60
  ls -la ~/zksync-ecosystem/thesis_local/chains/thesis_chain/configs/
  find ~ -maxdepth 4 -name "zksync_server*" -type f -exec sha256sum {} \; 2>/dev/null     # F1
  dpkg -s liburing2 2>/dev/null | grep -E "^(Status|Version)"                            # F1
  ls -la ~/*zk* ~/*snap* 2>/dev/null; find ~ -maxdepth 3 -name SHA256SUMS 2>/dev/null     # F5
  cat ~/zksync-ecosystem/thesis_local/docker-compose.yml                                  # F3
  docker volume ls; docker ps -a --format "{{.Names}} {{.Image}} {{.Status}} {{.Ports}}"  # F3 (read-only)
  grep -nE "block_commit_deadline_ms|miniblock_commit_deadline_ms|max_nonce_ahead|pubdata_sending_mode|http_port|ws_port|l1_batch_commit_data_generator_mode" \
    ~/zksync-ecosystem/thesis_local/chains/thesis_chain/configs/general.yaml*              # F4
  grep -hn "zksync_server\|pg_restore\|docker compose\|RAYON" ~/.bash_history 2>/dev/null | tail -40   # F2
  ls ~/*.sh ~/zksync-ecosystem/*.sh 2>/dev/null; cat ~/*zk*.sh 2>/dev/null | head -80          # F2
  vcgencmd get_throttled; vcgencmd measure_temp; uptime                                   # F7
  env | grep -i rayon'
```

Record in the facts file:
- **F1**: the ecosystem directory and the binary path, and that its SHA-256 equals `e77b1cc8…` (not the `.a72` build).
- **F2**: the exact server command line, working directory and environment. If `bath0` holds no record: reconstruct it read-only from zkstack's own `server` command in the pinned source (`git -C ~/zksync-ecosystem/zksync-era log -1 --format=%H` must print `ff5f519b…`; then read `zkstack_cli/crates/common/src/server.rs` and its callers for `--genesis-path`, `--config-path`, `--secrets-path`, `--contracts-config-path`, `--wallets-path` and `--components`). Mark it "reconstructed, to be confirmed by Task 5 Step 5".
- **F3**: the compose project name, services, port bindings (is 8945 on `127.0.0.1`?), and volume names (expected `thesis_local_postgres-data`, `thesis_local_reth-data`).
- **F4**: the values on `bath0`. Expected: `block_commit_deadline_ms: 20000`, `.orig-2000ms` present, and **`pubdata_sending_mode: BLOBS` restored** after the overnight calldata / pre-Fusaka tests. No `RAYON_NUM_THREADS` anywhere.
- **F5**: where the snapshot files are on `bath0`, and whether they match `SHA256SUMS` (`sha256sum -c`, read-only).
- **F6**: which interface the L2 API binds to. With the stack down this comes from the config and zksync-era's defaults. Confirmed in Task 5 Step 5 with `ss -ltn`.
- **F7**: `bath0`'s throttle flags now, and when it last booted.

- [ ] **Step 2 (Part B): the probe script, F8–F15**

```python
# bench/zksync_uc1_probe.py
"""Facts the zkSync payment client relies on, checked on the workstation chain
before anything runs on the Pi (UC1 zkSync plan, Task 1). Prints one line per fact.

F8  balances: deployer on the L1, accounts 100..106 on L1 and L2 (must be 0 on L2)
F9  chain ids (L1 9, L2 271)
F10 does eth_sendRawTransaction answer before sequencing? (reply time vs receipt time)
F11 is the tx hash keccak(raw) for a type-2 transaction? (the submitter assumes it)
F12 exact error texts: nonce too high, nonce too low, duplicate, fee below base fee, insufficient funds
F13 does eth_getTransactionCount(pending) count mempool transactions?
F14 eth_subscribe newHeads on the WS port; eth_getBlockReceipts supported?
F15 eth_gasPrice, a transfer's gas estimate (10 samples), effectiveGasPrice vs maxPriorityFeePerGas
Usage: zksync_uc1_probe.py <l1 http> <l2 http> <l2 ws>
Local dev chains only: the keys are public.
"""
import asyncio, json, sys, time
import websockets
from eth_hash.auto import keccak
from web3 import Web3
from measure import keys
from measure.txsign import Eip1559Signer

DEPLOYER_KEY = "0x7726827caac94a7f9e1b160f7ea819f172f7b6f9d2a97f992c38edeab82d4110"
L1, L2, WS = sys.argv[1:4]
l1, l2 = Web3(Web3.HTTPProvider(L1)), Web3(Web3.HTTPProvider(L2))
dep = l1.eth.account.from_key(DEPLOYER_KEY)
print("F9 ", "L1", l1.eth.chain_id, "L2", l2.eth.chain_id)
print("F8 ", "deployer L1", l1.eth.get_balance(dep.address), "deployer L2", l2.eth.get_balance(dep.address))
for i in range(100, 107):
    a = keys.account(i).address
    print("F8 ", i, a, "L1", l1.eth.get_balance(a), "L2", l2.eth.get_balance(a))

probe = keys.account(195)                    # outside the run range; funded on L2 by the deployer
signer, s_dep = Eip1559Signer(probe.key), Eip1559Signer(dep.key)
gp = l2.eth.gas_price
base = dict(chainId=271, gas=500_000, maxFeePerGas=2 * gp, maxPriorityFeePerGas=0)

def send(sgn, tx):
    raw, h = sgn.sign(tx)
    t0 = time.time()
    try:
        got = l2.eth.send_raw_transaction(raw).hex()
        return raw, h, got, time.time() - t0, None
    except Exception as e:
        return raw, h, None, time.time() - t0, str(e)

n = l2.eth.get_transaction_count(dep.address, "pending")
raw, h, got, dt, err = send(s_dep, dict(base, nonce=n, to=probe.address, value=10**17))
t_reply = time.time()
r = l2.eth.wait_for_transaction_receipt(h, timeout=120, poll_latency=0.05)
print("F10", f"funding: reply {dt:.3f}s, receipt {time.time() - t_reply + dt:.3f}s after the send", err or "")
print("F11", "hash==keccak(raw):", ("0x" + got.removeprefix("0x")) == h, got, h)
print("F15", "effectiveGasPrice", r["effectiveGasPrice"], "eth_gasPrice", gp, "gasUsed", r["gasUsed"])
# F10: reply vs receipt for 5 transfers at once
n = l2.eth.get_transaction_count(probe.address, "pending")
sent = [send(signer, dict(base, nonce=n + k, to=keys.account(100).address, value=1)) for k in range(5)]
t_sent = time.time()
for _, h, _, dt, err in sent:
    rr = l2.eth.wait_for_transaction_receipt(h, timeout=120, poll_latency=0.05)
    print("F10", f"reply {dt:.3f}s  receipt {time.time() - t_sent:.3f}s after the last send  block {rr['blockNumber']}", err or "")
# F13: pending nonce with 3 transactions in the mempool
n = l2.eth.get_transaction_count(probe.address, "pending")
batch = [send(signer, dict(base, nonce=n + k, to=keys.account(100).address, value=1)) for k in range(3)]
print("F13", "latest", l2.eth.get_transaction_count(probe.address, "latest"),
      "pending", l2.eth.get_transaction_count(probe.address, "pending"), "sent up to", n + 2)
# F12: error texts
print("F12 duplicate    :", send(signer, dict(base, nonce=n + 2, to=keys.account(100).address, value=1))[4])
for _, h, _, _, _ in batch:
    l2.eth.wait_for_transaction_receipt(h, timeout=120)
print("F12 nonce too low:", send(signer, dict(base, nonce=n, to=keys.account(100).address, value=2))[4])
print("F12 nonce too high:", send(signer, dict(base, nonce=n + 5000, to=keys.account(100).address, value=1))[4])
print("F12 fee < base   :", send(signer, dict(base, maxFeePerGas=1, nonce=n + 3, to=keys.account(100).address, value=1))[4])
print("F12 no funds     :", send(signer, dict(base, nonce=n + 3, to=keys.account(100).address, value=10**24))[4])
# F15: gas estimate spread for a transfer to the sensor
est = [l2.eth.estimate_gas({"from": probe.address, "to": keys.account(100).address, "value": 1}) for _ in range(10)]
print("F15", "estimate min/max", min(est), max(est))

async def ws_checks():
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]}))
        print("F14 subscribe:", (await ws.recv())[:120])
        head = json.loads(await asyncio.wait_for(ws.recv(), 30))
        num = head["params"]["result"]["number"]
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "eth_getBlockReceipts", "params": [num]}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 30))
            if m.get("id") == 2:
                print("F14 eth_getBlockReceipts:", "ok" if "result" in m else m.get("error"))
                break
asyncio.run(ws_checks())
```

Start the workstation zkSync (see *File structure*). The script funds index 195 on the L2 in its own first step. Then:
Run: `python3 bench/zksync_uc1_probe.py http://127.0.0.1:8945 http://127.0.0.1:3050 ws://127.0.0.1:3051 | tee /tmp/zk-probe.txt`
Expected: `F9 L1 9 L2 271`; F8: accounts 100–106 with **0 on the L2** (otherwise record it; the payment client already warns on a non-zero start balance); F11 `True`; F10 reply times well below the receipt times (answers *before* sequencing); F13 `pending` = sent + 1; F14 both ok. The F12 lines give the texts for Task 2.

- [ ] **Step 3 (Part B): the deposit and withdrawal calls, F16–F18**

In `zksync-setup/bench-bridge/bridge_bench.js`, print `dep.hash` after `w.deposit(...)` and `wd.hash` after `w.withdraw(...)` (two `console.log` lines). Then:
```bash
cd zksync-setup/bench-bridge && npm ci && node bridge_bench.js | tee /tmp/zk-bridge.txt
cast tx <deposit hash> --rpc-url http://127.0.0.1:8945          # to, value, input
cast calldata-decode 'requestL2TransactionDirect((uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address))' <input>
cast tx <withdraw hash> --rpc-url http://127.0.0.1:3050          # to, value, input
cast sig 'requestL2TransactionDirect((uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address))'   # 0xd52471c1
cast sig 'l2TransactionBaseCost(uint256,uint256,uint256,uint256)'                                                # 0x71623274
cast sig 'withdraw(address)'                                                                                     # 0x51cff8d9
cast code 0xe965f94cd784d274e6970f603576265b17cfa3f4 --rpc-url http://127.0.0.1:8945 | head -c 20               # non-empty
```
Record:
- **F16**: the deposit's `to` (expected the Bridgehub above), its selector (expected `0xd52471c1`), and the decoded tuple: chain ID 271, `mintValue`, `l2Contract` = the sender, `l2Value` = amount, empty calldata, `l2GasLimit`, `l2GasPerPubdataByteLimit` (expected 800), no factory deps, `refundRecipient`. Also the withdrawal's `to` (expected `0x…800A`), its selector (expected `0x51cff8d9`) and its value.
- **F17**: `mintValue − l2Value`, compared with `l2TransactionBaseCost(271, maxFeePerGas of the L1 tx, l2GasLimit, 800)` from `cast call`.
- **F18**: the credit and withdrawal latencies printed on the workstation.

If a `cast sig` prints a different value, or zksync-ethers calls another contract or function, Task 3 uses what the chain shows and says so in its docstring.

- [ ] **Step 4: Write the facts file and commit**

Write `bench/results/zksync-uc1-facts-<date>.md`: one table with F1–F18, the value found, the command that showed it, and "as expected" or "differs: …". Then:
```bash
git add bench/results/zksync-uc1-facts-*.md bench/zksync_uc1_probe.py zksync-setup/bench-bridge/bridge_bench.js
git commit -m "bench: zkSync facts for the UC1 path (bath0 layout, API behaviour, bridge calls)"
```
**Checkpoint 1.** Stop the workstation stack.

---

### Task 2: Submitter — per-system error texts and receipt polling

The submitter's logic stays exactly as it is. Only the strings it matches and the fallback polling interval become parameters.

**Files:**
- Modify: `measure/submitter.py`
- Test: `measure/tests/test_submitter.py`

**Interfaces:**
- `OrderedSubmitter(..., accepted_texts=("already known",), retry_texts=("nonce too high",), low_texts=("nonce too low",), receipt_sweep_s=1.0)`. The defaults are today's behaviour.
- `OrderedSubmitter.start_nonce: int | None`: the pending nonce read in `start()`.

- [ ] **Step 1: Failing tests**

Add to `measure/tests/test_submitter.py`:
```python
ZK_DUP = "<duplicate text from F12>"          # e.g. "known transaction" -- fill in from the facts file
ZK_LOW = "<nonce too low text from F12>"

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
```
`run_fake` is the module's existing helper, extended to pass keyword arguments to `OrderedSubmitter`. `fake.calls` is a new `Counter` of methods in `FakeSequencer._answer`. If the helper has another name in the code, adapt it; the tests must pin the behaviour above.

- [ ] **Step 2: Run them to see them fail** (`TypeError: unexpected keyword argument 'accepted_texts'`).

- [ ] **Step 3: Implement**

In `__init__`, store the four parameters and set `self.start_nonce = None`. In `start()`, set `self.start_nonce = self._nonce` after reading it. In `_judge`, replace the literals:
```python
            if any(t in text for t in self.accepted_texts):
                return "accepted", s.tx_hash
            if any(t in text for t in self.low_texts):
                ...                                     # unchanged body
            elif any(t in text for t in self.retry_texts):
                high = True
```
In `_check_receipts`, replace `interval = 1.0 if (...) else 0.05` with `interval = self.receipt_sweep_s if (...) else 0.05`. Update the module docstring: a paragraph on zkSync (answers after mempool admission, before sequencing, **F10**; its texts come from `measure/zksync.py`).

- [ ] **Step 4: All submitter tests, and the integration test against the workstation zkSync**

Run: `python3 -m unittest measure.tests.test_submitter -v`. Expected: all old tests PASS, the three new ones PASS.
Add `TestAgainstZkSync` (skipped unless `ZK_L2`): 200 transfers at 5/s from account 195 with `max_in_flight=20`, `accepted_texts` etc. from `measure.zksync` (Task 3; until then, inline). Assert 200 of 200 succeeded, the in-flight count never above 20 (count unresolved futures at each submit), and `start_nonce` equal to the account's pending nonce before the test.
Run: `ZK_L2=ws://127.0.0.1:3051 python3 -m unittest measure.tests.test_submitter.TestAgainstZkSync -v`. Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add measure/submitter.py measure/tests/test_submitter.py
git commit -m "measure: submitter error texts and receipt sweep per system (defaults unchanged)"
```

---

### Task 3: zkSync lifecycle — Open, payment, Close

**Files:**
- Create: `measure/zksync.py`, `measure/tests/test_zksync.py`

**Interfaces (the same names as `measure/arbitrum.py`, so `payclient` and `deploy/cluster` can pick a module by `--system`):**
- `L1_CHAIN_ID = 9`, `L2_CHAIN_ID = 271`, `BRIDGEHUB`, `L2_BASE_TOKEN`
- `http_url(ws_url) -> str`: `ws://…:3051` → `http://…:3050`
- `funder() -> LocalAccount`: the deployer (Arbitrum has no such function yet; Task 5 calls `getattr(mod, "funder", lambda: keys.account(keys.FUNDER))()`)
- `fund_on_l1(l1, funder, to, amount_wei) -> None`
- `open_deposit(l1, l2, acct, amount_wei) -> dict` with the keys of `arbitrum.open_deposit`, plus `mint_value_wei`, `base_cost_wei`, `l2_gas_limit`
- `estimate_payment_gas(l2, frm, to) -> int`: returns the fixed `PAYMENT_GAS` (spec §7.3)
- `payment_tx(to, amount_wei, gas, max_fee) -> dict`: as Arbitrum's
- `close_withdraw(l2, acct, amount_wei, timeout=300) -> dict` with the keys of `arbitrum.close_withdraw`, plus `nonce`
- `SUBMITTER_KW: dict`: the `accepted_texts` / `retry_texts` / `low_texts` from F12 and `receipt_sweep_s=10.0`

- [ ] **Step 1: Failing test (integration, skipped without the workstation stack)**

```python
# measure/tests/test_zksync.py
"""Open = deposit credited on zkSync's L2; Close = withdrawal included; payments as for Arbitrum."""
import os
import unittest

from web3 import Web3

from measure import keys, zksync


class TestPure(unittest.TestCase):
    def test_http_url(self):
        self.assertEqual(zksync.http_url("ws://192.168.178.210:3051"), "http://192.168.178.210:3050")

    def test_selectors(self):
        self.assertEqual((zksync._REQUEST_DIRECT, zksync._BASE_COST, zksync._WITHDRAW),
                         ("0xd52471c1", "0x71623274", "0x51cff8d9"))

    def test_deposit_calldata_roundtrip(self):
        data = zksync.deposit_calldata(271, 5 * 10**17 + 123, "0x" + "11" * 20, 5 * 10**17, 300000)
        self.assertTrue(data.startswith("0xd52471c1"))
        # decoded by eth_abi back to the same tuple
        from eth_abi import decode
        (t,) = decode(["(uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address)"],
                      bytes.fromhex(data[10:]))
        self.assertEqual(t[0], 271); self.assertEqual(t[3], 5 * 10**17); self.assertEqual(t[6], 800)


@unittest.skipUnless(os.environ.get("ZK_L1") and os.environ.get("ZK_L2"), "needs the workstation zkSync stack")
class TestLifecycle(unittest.TestCase):
    def test_open_then_close_on_a_fresh_account(self):
        l1 = Web3(Web3.HTTPProvider(os.environ["ZK_L1"]))
        l2 = Web3(Web3.HTTPProvider(zksync.http_url(os.environ["ZK_L2"])))
        fresh = keys.account(191)                       # outside the run range
        start_l2 = l2.eth.get_balance(fresh.address)
        zksync.fund_on_l1(l1, zksync.funder(), fresh.address, 2 * 10**18)
        o = zksync.open_deposit(l1, l2, fresh, 5 * 10**17)
        self.assertGreaterEqual(o["l2_balance_wei"] - start_l2, 5 * 10**17)   # plus the base-cost refund
        self.assertLess(o["t_l1_confirmed"], o["t_l2_credited"])
        self.assertGreater(o["base_cost_wei"], 0)
        c = zksync.close_withdraw(l2, fresh, 10**17)
        self.assertEqual(c["status"], 1)
        self.assertGreater(c["l2_fee_wei"], 0)
```

- [ ] **Step 2: Run to see it fail** (`cannot import name 'zksync'`).

- [ ] **Step 3: Implement**

```python
# measure/zksync.py
"""zkSync Era lifecycle for UC1 (parent spec §5.1, §11.4), same interface as arbitrum.py.

Open  = bridge ETH from the L1 with Bridgehub.requestL2TransactionDirect (the call
        zksync-ethers' deposit makes for an ETH-based chain, verified in the UC1
        zkSync facts F16) and wait until the L2 balance shows it (timed).
        mintValue = amount + base cost; the unused part of the base cost is refunded
        to the same account, so the credit check is "delta >= amount".
Payment = one EIP-1559 ETH transfer, fixed generous gas limit (cluster spec §7.3:
        zkSync's gas varies with pubdata pricing, 83 334 / 127 499 for identical
        transfers). zkSync ignores the priority fee; the effective price is the base
        fee (F15). eth_sendRawTransaction answers after mempool admission, before
        sequencing (F10), so the pipelined submitter needs no nonce-gap handling in
        practice; the per-account bound of 20 in flight comes from the caller.
Close = withdrawal *submission* with L2BaseToken.withdraw(address) at 0x...800A,
        timed to its L2 receipt. Batch commit, proof and execution are documentation.
The L1 funder is the ecosystem deployer (the only rich account in the reth genesis
that zkstack does not use at run time); its key is the public zksync-era dev key.
Local dev chains only.
"""
from __future__ import annotations

import time

from eth_abi import encode
from eth_account import Account

L1_CHAIN_ID, L2_CHAIN_ID = 9, 271
BRIDGEHUB = "0xe965f94cd784d274e6970f603576265b17cfa3f4"          # snapshot contracts.yaml; F16
L2_BASE_TOKEN = "0x000000000000000000000000000000000000800A"      # F16
GAS_PER_PUBDATA = 800                                              # F16
PAYMENT_GAS = 0          # FILL IN from F15: about 2x the largest estimate seen (spec §7.3, fixed and generous)
DEPLOYER_KEY = "0x7726827caac94a7f9e1b160f7ea819f172f7b6f9d2a97f992c38edeab82d4110"
_REQUEST_DIRECT = "0xd52471c1"   # requestL2TransactionDirect((uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address))
_BASE_COST = "0x71623274"        # l2TransactionBaseCost(uint256,uint256,uint256,uint256)
_WITHDRAW = "0x51cff8d9"         # withdraw(address)
SUBMITTER_KW = {                 # texts from F12; defaults kept where zkSync uses the same words
    "accepted_texts": ("already known", "<F12 duplicate>"),
    "retry_texts": ("nonce too high",),
    "low_texts": ("nonce too low",),
    "receipt_sweep_s": 10.0,     # newHeads + eth_getBlockReceipts is the main path (F14)
}


def http_url(ws_url: str) -> str:
    url = ws_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    return url.replace(":3051", ":3050", 1)


def funder():
    return Account.from_key(DEPLOYER_KEY)


def _send(w3, acct, tx, timeout=300):
    tx = dict(tx, nonce=w3.eth.get_transaction_count(acct.address, "pending"), chainId=w3.eth.chain_id,
              maxFeePerGas=2 * w3.eth.gas_price + 1, maxPriorityFeePerGas=1, type=2)
    tx.setdefault("gas", int(w3.eth.estimate_gas({k: tx[k] for k in ("from", "to", "value", "data") if k in tx}) * 1.5))
    h = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    r = w3.eth.wait_for_transaction_receipt(h, timeout=timeout, poll_latency=0.05)
    return r, tx


def fund_on_l1(l1, funder_acct, to, amount_wei):
    r, _ = _send(l1, funder_acct, {"from": funder_acct.address, "to": to, "value": amount_wei, "gas": 21000})
    if r["status"] != 1:
        raise RuntimeError("L1 funding reverted")


def deposit_calldata(chain_id, mint_value, to, l2_value, l2_gas_limit) -> str:
    args = encode(["(uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address)"],
                  [(chain_id, mint_value, to, l2_value, b"", l2_gas_limit, GAS_PER_PUBDATA, [], to)])
    return _REQUEST_DIRECT + args.hex()


def _l2_gas_limit(l2, acct, amount_wei) -> int:
    est = l2.provider.make_request("zks_estimateGasL1ToL2",
                                   [{"from": acct.address, "to": acct.address, "value": hex(amount_wei)}])
    return int(est["result"], 16)


def _base_cost(l1, gas_price, l2_gas_limit) -> int:
    data = _BASE_COST + encode(["uint256", "uint256", "uint256", "uint256"],
                               [L2_CHAIN_ID, gas_price, l2_gas_limit, GAS_PER_PUBDATA]).hex()
    return int(l1.eth.call({"to": BRIDGEHUB, "data": data}).hex(), 16)


def open_deposit(l1, l2, acct, amount_wei) -> dict:
    before = l2.eth.get_balance(acct.address)
    t_start = time.time()
    l2_gas = _l2_gas_limit(l2, acct, amount_wei)
    max_fee = 2 * l1.eth.gas_price + 1                  # the contract prices with tx.gasprice <= max_fee
    base = _base_cost(l1, max_fee, l2_gas)
    mint = base + amount_wei
    r, _ = _send(l1, acct, {"from": acct.address, "to": BRIDGEHUB, "value": mint,
                             "data": deposit_calldata(L2_CHAIN_ID, mint, acct.address, amount_wei, l2_gas)})
    t_l1 = time.time()
    if r["status"] != 1:
        raise RuntimeError("requestL2TransactionDirect reverted")
    while True:
        bal = l2.eth.get_balance(acct.address)
        if bal - before >= amount_wei:
            break
        if time.time() - t_start > 600:
            raise TimeoutError("deposit not credited on the L2 within 600 s")
        time.sleep(0.05)
    return {"t_start": t_start, "t_l1_confirmed": t_l1, "t_l2_credited": time.time(),
            "l1_gas_used": r["gasUsed"], "l1_fee_wei": r["gasUsed"] * r["effectiveGasPrice"],
            "l2_balance_wei": bal, "mint_value_wei": mint, "base_cost_wei": base, "l2_gas_limit": l2_gas}


def estimate_payment_gas(l2, frm, to) -> int:
    if not PAYMENT_GAS:
        raise RuntimeError("zksync.PAYMENT_GAS not set (Task 3, from F15)")
    return PAYMENT_GAS


def payment_tx(to, amount_wei, gas, max_fee) -> dict:
    return {"to": to, "value": amount_wei, "gas": gas, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": 0}


def close_withdraw(l2, acct, amount_wei, timeout=300) -> dict:
    t_start = time.time()
    data = _WITHDRAW + acct.address[2:].lower().rjust(64, "0")
    r, tx = _send(l2, acct, {"from": acct.address, "to": L2_BASE_TOKEN, "value": amount_wei, "data": data},
                  timeout=timeout)
    return {"t_start": t_start, "t_confirmed": time.time(), "status": r["status"], "nonce": tx["nonce"],
            "l2_gas_used": r["gasUsed"], "l2_fee_wei": r["gasUsed"] * r["effectiveGasPrice"]}
```

Notes for the implementer:
- `_l2_gas_limit` uses `zks_estimateGasL1ToL2`, which is what zksync-ethers calls. If F16 shows a different `l2GasLimit` rule (e.g. a fixed value), follow F16 and say so in the docstring.
- `_send`'s `estimate_gas` for the withdrawal can vary (the spike measured 115 536–236 540 gas); × 1.5 is kept as in Arbitrum. If F18 shows reverts for out-of-gas, use a fixed generous limit as for payments.
- Fill in `PAYMENT_GAS` and the F12 text. The unit test `test_payment_gas_is_set` asserts `PAYMENT_GAS > 0`.

- [ ] **Step 4: Check the selectors, then run**

Run: `cast sig 'requestL2TransactionDirect((uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address))' && cast sig 'l2TransactionBaseCost(uint256,uint256,uint256,uint256)' && cast sig 'withdraw(address)'`
Expected: `0xd52471c1`, `0x71623274`, `0x51cff8d9` (computed with cast 1.7.1 on 2026-09-30). If any differs, or F16 shows another function, use the chain's value.
Run: `python3 -m unittest measure.tests.test_zksync -v` (pure tests PASS, lifecycle skipped). Then, with the workstation stack: `ZK_L1=http://127.0.0.1:8945 ZK_L2=ws://127.0.0.1:3051 python3 -m unittest measure.tests.test_zksync -v`. Expected: PASS. Record Open and Close times in the commit message.

- [ ] **Step 5: Commit**
```bash
git add measure/zksync.py measure/tests/test_zksync.py
git commit -m "measure: zkSync Open (Bridgehub deposit credited), payment and Close (withdrawal submitted)"
```

---

### Task 4: Payment client — `--system zksync`, timeouts, nonce bookkeeping

**Files:**
- Modify: `measure/payclient.py`
- Test: `measure/tests/test_payclient.py`

**Interfaces:**
- CLI: `--system {arbitrum,zksync}`, `--receipt-timeout S` (default 120, today's value), `--close-timeout S` (default 300).
- `lifecycle.json` gains `start_nonce`, `pending_before_close` (pending nonce − latest nonce just before Close), `close_nonce`, `executed_onchain` (= `close_nonce − start_nonce`), and `receipt_timeout`. Existing keys are unchanged.

- [ ] **Step 1: Failing tests**
```python
class TestSystems(unittest.TestCase):
    def test_system_modules(self):
        from measure import arbitrum, zksync
        self.assertIs(payclient.SYSTEMS["arbitrum"], arbitrum)
        self.assertIs(payclient.SYSTEMS["zksync"], zksync)

class TestSlots(unittest.IsolatedAsyncioTestCase):
    async def test_slot_is_not_freed_before_the_receipt_timeout(self):
        # FakeSubmitter with max_in_flight=2 whose receipts take 1.0 s and a receipt_timeout of 5 s:
        # at no time are more than 2 submitted-but-unfinished rows in the ledger.
        ...
```
Plus: extend the existing `main_async` test (the one with `FakeWeb3`/`FakeSub`) so that the lifecycle has `start_nonce`, `close_nonce`, `executed_onchain`, `pending_before_close` for a fake `system` module injected through `payclient.SYSTEMS`.

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement**

- `SYSTEMS = {"arbitrum": arbitrum, "zksync": zksync}`; `mod = SYSTEMS[a.system]`. Replace every `arbitrum.` in `main_async` with `mod.`.
- `OrderedSubmitter(a.l2, acct, mod.L2_CHAIN_ID, a.max_in_flight, receipt_timeout=a.receipt_timeout, **getattr(mod, "SUBMITTER_KW", {}))`. After `await sub.start()`, set `life["start_nonce"] = sub.start_nonce`.
- Before Close: `life["pending_before_close"] = pending − latest` from two `get_transaction_count` calls. Close: `mod.close_withdraw(l2http, acct, left // 2, **({"timeout": a.close_timeout} if a.system != "arbitrum" else {}))`. Arbitrum's `close_withdraw` keeps its signature and code. `close_nonce` = `life["close"].get("nonce")`, or for Arbitrum the `latest` nonce after the Close receipt minus 1. `executed_onchain = close_nonce − start_nonce`.
- `--system` choices from `SYSTEMS`. The docstring gains a zkSync paragraph: the receipt timeout must exceed the run (`RECEIPT_TIMEOUT_S`), because a per-payment timeout would free an in-flight slot while the payment is still in the mempool and break the bound of 20 (spec §7.3).

- [ ] **Step 4: Run all payclient tests; then an end-to-end test against the workstation zkSync**

Run: `python3 -m unittest measure.tests.test_payclient -v`. Expected: all PASS.
End to end (as Task 6 of the Arbitrum plan, now on zkSync): start the payment client for account 192 (funded on the L1 by `zksync.funder()` in the test setup), feed 100 events at 2/s over the socket, then the end marker.
Expected: `complete: true`, 100 succeeded, `executed_onchain == 100`, `pending_before_close == 0`.

- [ ] **Step 5: Commit**
```bash
git add measure/payclient.py measure/tests/test_payclient.py
git commit -m "measure: payment client for zkSync (receipt/close timeouts, nonce bookkeeping)"
```

---

### Task 5: `deploy/cluster` — the zkSync stack on `bath0`

**Files:**
- Modify: `deploy/cluster`
- Create: `deploy/zk-sampler.sh`

Every value marked F# comes from the facts file of Task 1. **Do not guess them.**

- [ ] **Step 1: Per-stack endpoints and helpers**

Replace the fixed `L1`/`L2_WS`/`L2_HTTP` with a function called after the experiment env has been sourced (and by `up`):
```bash
stack_endpoints() {
  case "$1" in
    arbitrum) L1=http://$STACK_IP:8545; L2_WS=ws://$STACK_IP:8548; L2_HTTP=http://$STACK_IP:8547; L2_WS_PORT=8548 ;;
    zksync)   L1=http://$STACK_IP:8945; L2_WS=ws://$STACK_IP:3051; L2_HTTP=http://$STACK_IP:3050; L2_WS_PORT=3051 ;;
    *) die "unknown stack '$1'" ;;
  esac
}
ZK_HOME=/home/i_wanilin/zksync-ecosystem           # F1
ZK_BIN=<F1: full path of zksync_server on bath0>   # found by full path only (CPU report, Mistakes)
ZK_SNAP=<F5: snapshot directory on bath0>
ZK_COMPOSE="$ZK_HOME/thesis_local"                 # F3
ZK_PROJECT=<F3: compose project name>              # e.g. thesis_local
ZK_RUN=/home/i_wanilin/zk-run                      # server log and pid for the current up
```
`cmd_up` dispatches: `arbitrum)` the existing body (plus the refusal below), `zksync)` `up_zksync`.

- [ ] **Step 2: "One stack at a time" refusal, both directions**

At the top of both `up` bodies, over one SSH call:
```bash
T=30 on bath0 "pgrep -f '^$ZK_BIN' >/dev/null && echo zksync_server; docker ps --format '{{.Names}}'; pgrep -x anvil >/dev/null && echo anvil" 
```
`up arbitrum` refuses on `zksync_server`, `${ZK_PROJECT}-*` or `anvil`. `up zksync` refuses on `arbitrum-*` or `anvil`. Each gives a clear message naming what is running.

- [ ] **Step 3: `down` stops whatever is up**
```bash
cmd_down() {
  T=240 on bath0 "
    if pgrep -f '^$ZK_BIN' >/dev/null; then
      pkill -TERM -f '^$ZK_BIN'; for i in \$(seq 60); do pgrep -f '^$ZK_BIN' >/dev/null || break; sleep 1; done
      pkill -KILL -f '^$ZK_BIN' 2>/dev/null; true
    fi
    [ -d $ZK_COMPOSE ] && (cd $ZK_COMPOSE && docker compose stop -t 60 >/dev/null 2>&1); 
    cd ~/arbitrum && docker compose stop -t 60 sequencer >/dev/null 2>&1; docker compose stop >/dev/null 2>&1; true" \
    || echo "warning: could not stop the stacks on bath0" >&2
  ...                                   # netem removal as today
}
```
Put the server first: the server must not lose its database while it is still running.

- [ ] **Step 4 (Checkpoint 2 first): prepare `bath0` once**

Ask the owner (Q6, Q7 answers in hand), then:
1. Back up `bath0`'s compose file (`docker-compose.yml.orig-localhost`). Change reth's port to `0.0.0.0:8945:8545`; PostgreSQL stays on `127.0.0.1`. Do the same for 3050/3051 only if F6 showed them on localhost (then in `general.yaml`, recorded).
2. **Q6 a:** start PostgreSQL only; drop and recreate the database named in `secrets.yaml` (F2/F4); `pg_restore` `postgres.dump` (as on 2026-09-29, about 2 min 13 s); stop PostgreSQL; pack the volume raw: `docker run --rm -v ${ZK_PROJECT}_postgres-data:/v alpine tar czf - -C /v . > $ZK_SNAP/postgres-vol.tgz`; add its SHA-256 to `$ZK_SNAP/SHA256SUMS`. Record the size and time in the facts file.
3. Record in the facts file the exact lines changed. Nothing else on `bath0` changes.

- [ ] **Step 5: `up_zksync`**
```bash
up_zksync() {
  local deadline_ms=${ZK_COMMIT_DEADLINE_MS:?set in the experiment env (Q2)}
  T=900 on bath0 "set -eo pipefail
    cd $ZK_SNAP && grep -E '\.(tgz|dump)\$' SHA256SUMS | sha256sum -c --quiet
    (cd $ZK_COMPOSE && docker compose stop -t 60 >/dev/null 2>&1 || true)
    # chain directory: RocksDB, configs, artifacts (absolute paths inside the tarball)
    rm -rf $ZK_HOME/thesis_local/chains/thesis_chain/db $ZK_HOME/thesis_local/chains/thesis_chain/artifacts
    tar xzf $ZK_SNAP/chain.tgz -C /
    # build outputs the server reads at run time (spec §4): verified, not rewritten, if unchanged
    # (F1 says where era-artifacts.tgz was unpacked; unpack again only if its hash file differs)
    for v in reth-data postgres-data; do docker volume rm -f ${ZK_PROJECT}_\$v >/dev/null; docker volume create ${ZK_PROJECT}_\$v >/dev/null; done
    docker run --rm -v ${ZK_PROJECT}_reth-data:/v -v $ZK_SNAP:/in:ro alpine tar xzf /in/reth-data.tgz -C /v
    docker run --rm -v ${ZK_PROJECT}_postgres-data:/v -v $ZK_SNAP:/in:ro alpine tar xzf /in/postgres-vol.tgz -C /v   # Q6 a
    # config: the snapshot's general.yaml with exactly one change (Q2), recorded
    cfg=$ZK_HOME/thesis_local/chains/thesis_chain/configs/general.yaml
    sed -i -E 's/^(  block_commit_deadline_ms:) [0-9]+\$/\1 $deadline_ms/' \$cfg
    grep -q '^  block_commit_deadline_ms: $deadline_ms\$' \$cfg
    mkdir -p $ZK_RUN && sha256sum \$cfg > $ZK_RUN/config.sha256
    grep -nE 'block_commit_deadline_ms|miniblock_commit_deadline_ms|max_nonce_ahead|pubdata_sending_mode' \$cfg > $ZK_RUN/config.txt
    cd $ZK_COMPOSE && docker compose up -d reth postgres 2>&1 | tail -n 3
    for i in \$(seq 60); do docker compose exec -T postgres pg_isready -q && break; sleep 1; done
    cd <F2: working directory>
    setsid nohup $ZK_BIN <F2: arguments> > $ZK_RUN/server.log 2>&1 < /dev/null &
    echo \$! > $ZK_RUN/server.pid" || die "zkSync snapshot restore / start failed on bath0"
  local deadline=$((SECONDS + RPC_WAIT_S))
  until rpc_ok "$L1" && rpc_ok "$L2_HTTP" && tcp_ok "$STACK_IP" 3051 \
        && T=10 on bath0 "curl -s -m 3 http://127.0.0.1:3071/health | grep -q '\"status\":\"ready\"'"; do
    [ "$SECONDS" -lt "$deadline" ] || die "zkSync not healthy after ${RPC_WAIT_S}s (see bath0:$ZK_RUN/server.log)"
    sleep 3
  done
  echo "zksync up (snapshot restored, block_commit_deadline_ms=$deadline_ms)"
}
```
**Q6 b** fallback (`pg_restore` in every run): replace the `postgres-vol.tgz` line with starting PostgreSQL, recreating the database and `pg_restore -j 4` from `postgres.dump`. The server starts after that.
Check the health endpoint's JSON shape once (F-fact added here). `RPC_WAIT_S` may need raising for zkSync (the server catches up its RocksDB). Record the measured time in the facts file.
Then confirm F2/F6 live: `ssh bath0 "ss -ltn | grep -E ':(8945|3050|3051|3071|5432) '"`. Expected: 8945, 3050 and 3051 on `0.0.0.0` or `*`; 5432 on `127.0.0.1`.

- [ ] **Step 6: Test `up`/`down` by hand (the stack only, no run)**

Run: `deploy/cluster up zksync && deploy/cluster status && deploy/cluster down && ssh bath0 "pgrep -af zksync_server; docker ps"`
Expected: `up` prints "zksync up"; status shows `bath0` with 2 containers; after `down`, no `zksync_server` process and no running container. Then `deploy/cluster up arbitrum && deploy/cluster down`: Arbitrum still works (regression). Then `up zksync` while Arbitrum is up must refuse.

- [ ] **Step 7: Run-path differences in `one_run`**

- After `source …env`: `stack_endpoints "$STACK"`.
- Funding: import `measure.$STACK` as `m` and use `getattr(m, "funder", lambda: keys.account(keys.FUNDER))()` with `m.fund_on_l1`. For zkSync, first check that the funder's L1 balance is ≥ 7 × `FUND_WEI` + 1 ETH (F8 gives the starting value). The snapshot restore resets it every run.
- Budget check: `gas = m.estimate_payment_gas(l2, sensor, sensor)` instead of Arbitrum's inline estimate. The rest of the formula is unchanged.
- Payment clients: add `--receipt-timeout ${RECEIPT_TIMEOUT_S:-120} --close-timeout ${CLOSE_TIMEOUT_S:-300}`.
- **Stream-start temperatures (Q4)**: right after the sensor starts listening, `all_temps "$out/.st"` into `stream-start-temps.txt`. This is recorded, not gated.
- Recorder on `bath0` for zkSync: also start `deploy/zk-sampler.sh $rd/zk.csv $rec_s`.
- `collect`: for zkSync, copy `bath0:$ZK_RUN/server.log` to `logs/bath0-zksync-server.log`, `docker compose logs --since $RUN_T0` of `$ZK_PROJECT` to `logs/bath0-docker.log`, `$ZK_RUN/config.txt` and `config.sha256` to `results/`, and `zk.csv` to `recorder/bath0-zk.csv`. Arbitrum: unchanged.
- **Q5 b** (only if chosen): before the cooling gate, `ssh bath0 sudo reboot`, wait for SSH and for the facts to show the 2.4 GHz BSSID, max 5 min.

```bash
# deploy/zk-sampler.sh — bath0 only, zkSync runs: server CPU/RSS and chain progress every 5 s.
# Usage: zk-sampler.sh OUT_CSV DURATION_S   (the server is found by full path in ~/zk-run/server.pid)
out=$1; dur=$2; pid=$(cat ~/zk-run/server.pid)
echo "t,server_cpu_pct,server_rss_mb,l2_block,l1_batch_sealed" > "$out"
rpc() { curl -s -m 2 -X POST -H 'content-type: application/json' --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$1\",\"params\":[]}" http://127.0.0.1:3050 | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["result"],16))' 2>/dev/null; }
start=${EPOCHREALTIME/./}; k=0
while [ $((k * 5000000)) -lt $((dur * 1000000)) ]; do
  read -r cpu rss < <(ps -o %cpu=,rss= -p "$pid" 2>/dev/null || echo " ")
  echo "$(date +%s.%N),$cpu,$(( ${rss:-0} / 1024 )),$(rpc eth_blockNumber),$(rpc zks_L1BatchNumber)" >> "$out"
  k=$((k + 1)); w=$((start + k * 5000000 - ${EPOCHREALTIME/./}))
  [ $w -gt 0 ] && sleep "$(printf '%d.%06d' $((w / 1000000)) $((w % 1000000)))"
done
```
`%cpu` from `ps` is the lifetime average; the implementer switches to two `/proc/<pid>/stat` readings 5 s apart (utime + stime delta) for the per-interval figure the CPU report used (160–270 %). The test is a 20 s run on `bath0` with the stack up, checked by eye: CPU figures plausible, blocks increasing.

- [ ] **Step 8: Static checks and commit**

Run: `bash -n deploy/cluster && shellcheck deploy/cluster deploy/zk-sampler.sh`. Expected: clean.
```bash
git add deploy/cluster deploy/zk-sampler.sh bench/results/zksync-uc1-facts-*.md
git commit -m "deploy: cluster up/down for zkSync on bath0 (native server, reth, postgres), one stack at a time"
```

---

### Task 6: Pre-flight, board facts and manifest for zkSync

**Files:**
- Modify: `deploy/board-facts.sh`, `measure/preflight.py`, `measure/manifest.py`
- Test: `measure/tests/test_preflight.py`, `measure/tests/test_analyze.py` (manifest tests live there)

- [ ] **Step 1: Failing tests**
- `check(..., expect_stack="zksync")` fails on `bath0` when `zksync_server_up == 0`, when an `arbitrum-*` container is up, or when fewer than 2 `<project>-*` containers are up. It passes with 1 server and 2 containers.
- `check(..., expect_stack="arbitrum")` fails when `zksync_server_up > 0`. **All existing preflight tests stay green without passing `expect_stack`** (the default keeps today's rule).
- Q5 a: with `thermal_sticky_ok={"bath0"}`, `throttled="0xe0000"` passes on `bath0`, while `0x50000` (undervoltage occurred), `0xe0002` (currently capped) and `0x80008` (soft limit active now) fail. Without the parameter, today's behaviour holds.
- The manifest for a zkSync run folder records `stack: zksync`, the binary SHA-256, `config.txt` and `config.sha256`, and the compose images. For an Arbitrum folder, the manifest is unchanged (existing test).

- [ ] **Step 2: Implement**
- `board-facts.sh`: add `"containers": sh("docker ps --format '{{.Names}}' 2>/dev/null").split()` and `"zksync_server_up": int(sh("pgrep -fc '/zksync_server( |$)' 2>/dev/null") or 0)`. The regex anchors on the name as a path component, so the `.a72` build does not count as the measured binary; the facts file also records the full path.
- `preflight.check`: new keyword arguments `expect_stack=None` and `thermal_sticky_ok=frozenset()`. `main()` reads `STACK` from the run folder's `experiment.env` (the facts directory is `$out/facts-start`, so the env is at `../experiment.env`). With no env found, it keeps today's behaviour. **Q5 a** is applied only when the owner chose it: `THERMAL_STICKY_OK=bath0` in the zkSync env files.
- `manifest.images()`: read `STACK` from the env. For zkSync: over SSH, `docker compose -p $ZK_PROJECT images --format json`, `sha256sum $ZK_BIN`, and the collected `results/config.txt`. For Arbitrum: today's code path, unchanged.

- [ ] **Step 3: Run the tests; commit**
```bash
python3 -m unittest measure.tests.test_preflight measure.tests.test_analyze -v
git add deploy/board-facts.sh measure/preflight.py measure/manifest.py measure/tests/test_preflight.py measure/tests/test_analyze.py
git commit -m "measure: pre-flight and manifest know which stack is expected (zkSync: server + containers)"
```

---

### Task 7: Analysis — achieved rate, backlog, late executions

**Files:**
- Modify: `measure/analyze_uc1.py`, `measure/aggregate.py`
- Test: `measure/tests/test_analyze.py`

**New summary keys (the existing keys keep their meaning):**
- `offered_rate_per_s` = customers × 1000 / `WINDOW_MS`;
- `achieved_rate_per_s` = succeeded / (last `t_confirmed` − first `t_intended`) over all customers;
- `confirmed_per_10s`: a list of counts per 10 s bin from the stream start, for the figure;
- `backlog_max`: per customer and overall, the largest number of rows with `t_queued ≤ t` and not yet confirmed (or still pending at the drain deadline), found with a sweep over the queue and confirmation events;
- `late_executed`: per customer, `executed_onchain − (succeeded + reverted rows with a receipt)` from `lifecycle.json`. `null` if the lifecycle lacks the nonces (old folders);
- `reconciliation.ok_with_late`: `sensor_received == succeeded_wei + late_executed_total × PRICE_WEI`, with `late_executed_wei` beside it. `ok` stays strict;
- `close_s` gains `pending_before_close` per customer beside it (`close_pending`).

- [ ] **Step 1: Failing tests**
- A synthetic folder: 2 customers at `WINDOW_MS=1000`. Confirmations are slower than arrivals, so the backlog grows to a known maximum. Assert `backlog_max`, `achieved_rate_per_s` and `offered_rate_per_s`.
- A folder where 3 rows are unresolved at the deadline, `executed_onchain` says they executed, and the sensor balance includes them. Assert `ok == False`, `ok_with_late == True`, `late_executed_total == 3`.
- An old-layout folder (no nonces in the lifecycle): `late_executed` is `null` and `ok_with_late` is `null`; nothing raises.

- [ ] **Step 2: Implement; `aggregate.HEADLINE` gains `achieved_rate_per_s`, `backlog_max`, `late_executed_total`.**

- [ ] **Step 3: Run the tests.**

- [ ] **Step 4: Commit.**

- [ ] **Step 5: Regression on the Arbitrum results (read-only on the folders)**
```bash
for d in results/pi/uc1/*-arbitrum-uc1-arbitrum-w*-rep*; do
  python3 - "$d" <<'EOF'
import json, sys
from measure.analyze_uc1 import analyze
d = sys.argv[1]
old = json.load(open(f"{d}/summary.json"))
new = analyze(d)
diff = {k for k in old if old[k] != new.get(k)}
print(d, "OK" if not diff else f"DIFFERS: {sorted(diff)}", "late:", new.get("late_executed_total"))
EOF
done
```
Expected: every folder `OK`. `late` is `null`: the Arbitrum lifecycles have no nonces, since they were recorded before Task 4. Do **not** rewrite the Arbitrum `summary.json` files in this task. Regenerating them with the new keys is a separate, owner-approved step, because they are committed thesis data.

---

### Task 8: Experiment files

**Files:** `deploy/experiments/uc1-zksync-smoke.env`, `-w1000.env`, `-w100.env`, `-w10.env`

```bash
# deploy/experiments/uc1-zksync-w1000.env
USE_CASE=uc1
STACK=zksync
WINDOW_MS=1000
DURATION_S=900
PRICE_WEI=1000000000000
DEPOSIT_WEI=1000000000000000000
FUND_WEI=2000000000000000000
MAX_IN_FLIGHT=20                 # cluster spec §7.3: zkSync mainnet's per-account limit
MAX_QUEUE=10000
DRAIN_S=60                       # Q3 a: the same as Arbitrum
RECEIPT_TIMEOUT_S=1200           # > DURATION_S + DRAIN_S: no slot is freed before an outcome
CLOSE_TIMEOUT_S=1800             # Close waits behind the backlog (Q3)
EXPECTED_CUSTOMERS=6
ZK_COMMIT_DEADLINE_MS=20000      # Q2 a
COOL_MAX_S=1200                  # Q4 a: raised to 2700 after the smoke run if cooling takes > 15 min
THERMAL_STICKY_OK=               # Q5 a: set to bath0 only if the smoke run shows the sticky bits
```
- `smoke`: the same with `DURATION_S=60`.
- `w100`: `WINDOW_MS=100`.
- `w10`: `WINDOW_MS=10`, `DEPOSIT_WEI=5000000000000000000`, `FUND_WEI=6000000000000000000`. The budget: 90 000 × (10¹² + `PAYMENT_GAS` × `gas_price`) must stay under the deposit; recompute it with the real `PAYMENT_GAS`. The funder's L1 balance must cover 7 × 6 ETH.

The `w100`/`w10` files are created in all cases. Under Q1 C they are not run in Task 10. `[Q1≠rec]` A: all three are run.

Commit: `git commit -m "deploy: UC1 zkSync experiments (smoke, w1000, w100, w10)"`.

---

### Task 9: The smoke run on the cluster (60 s)

- [ ] **Step 1:** Run `deploy/cluster sync`, then `deploy/cluster idle-temps` (Q4 a: re-measured on the series day; keep the old file as `idle-temps-2026-09-30.txt`), then `deploy/cluster run uc1-zksync-smoke`.
Expected: the run folder is complete. 6 × 60 = 360 windows. Because of zkSync's latency, many payments will be *unresolved* at the 60 s drain, even at 6/s. That is expected; the ledger must account for every window. `executed_onchain` and `ok_with_late` must reconcile (Task 7). Open should take a few seconds per customer: the spike measured 4.57 s on a laptop with 1 s L1 blocks; the L1 here makes 300 ms blocks.
- [ ] **Step 2: Measure what Q4/Q5 need.** Right after the run (the stack is down), sample `bath0`'s temperature every 30 s until it is within 3 °C of idle, and record the time. Read `vcgencmd get_throttled` on `bath0`.
- [ ] **Step 3: Analyse.** Run `python3 -m measure.analyze_uc1 <folder>` and look at `bath0`'s `recorder/bath0-zk.csv` (server CPU, blocks, batches).
**Checkpoint 3**: show the owner the summary, the cooling time, the throttle flags and the server CPU. Confirm or adjust `COOL_MAX_S` (Q4) and `THERMAL_STICKY_OK` (Q5) in the env files, and commit them.

---

### Task 10: Dry run and the series

- [ ] **Step 1:** Run `deploy/cluster run uc1-zksync-w1000` (one 15-minute rep, the dry run).
- [ ] **Step 2:** Run `python3 -m measure.analyze_uc1 <folder>`.
Expected, from the §8 numbers:
- `offered_rate_per_s` 6;
- `achieved_rate_per_s` about 2–4;
- `backlog_max` growing to the in-flight bound plus the queue;
- `deadline_miss_share` near 1.0;
- `late_executed` > 0 and `ok_with_late: true`;
- Open per customer a few seconds;
- `close_s` together with `close_pending`;
- `bath0` at 80–85 °C during the stream;
- `complete: true`.
A number far off (e.g. achieved above 6, or below 1) is a Checkpoint-5 item.
**Checkpoint 4**: show the summary and wait for the go.
- [ ] **Step 3: The repetitions (Q1 C)**
```bash
deploy/cluster run uc1-zksync-w1000 --reps 3
for d in results/pi/uc1/*-zksync-uc1-zksync-w1000-rep*; do python3 -m measure.analyze_uc1 "$d" > /dev/null; done
python3 -m measure.aggregate results/pi/uc1/*-uc1-zksync-w1000-rep*
```
`[Q1≠rec]` A: `for w in 1000 100 10; do deploy/cluster run uc1-zksync-w$w --reps 3; done`, and aggregate per window.
Each rep takes about 15 min stream + 1 min drain + the Close wait + `up` (a few min) + the cooling gate (up to `COOL_MAX_S`), so about 45–75 min per rep. It can run unattended. The spread between reps is expected to be large (the CPU report found confirmation medians of 32–52 s at one setting), so all three reps are reported with their spread.
- [ ] **Step 4: Record.** Add a zkSync entry to cluster spec §8 step 3: the date, the run folders, the aggregate lines, the chosen answers to Q1–Q7, and which cells were not run and why. In `deploy/README.md` §4, add `up zksync`.
- [ ] **Step 5: Commit**
```bash
git add results/pi/uc1 results/pi/idle-temps*.txt docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md deploy/README.md
git commit -m "results: UC1 on zkSync (w1000 x 3), saturation at the 1 s window"
```

---

## After this plan (not part of it)

- **Capacity probes** (cluster spec §7.1) for zkSync on the new pipeline: `one-client` (a 3B and a 3B+) and `one-node`, with in-flight depth as the swept variable. They give the ceiling that Q1 C refers to.
- **L1 finality for zkSync** (batch commit / execute times from `zks_getL1BatchDetails`) as documentation beside Close, parent §11.4.
- Regenerating the Arbitrum summaries with the new analysis keys (owner decision, see Task 7 Step 5).
- `zksync-stack.service` and the `zksync` ansible role (cluster spec §3), replacing `up_zksync`'s hand-made start.
