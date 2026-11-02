# Design spec — use-case-driven L2 comparison (v2 rebuild)

> **Design frame written before the Pi cluster work; where it conflicts with the cluster
> spec ([`2026-09-21-pi-cluster-suite-design.md`](2026-09-21-pi-cluster-suite-design.md)),
> the cluster spec wins. Status: superseded in parts, see notes.** (Notes added
> 2026-10-12, marked *Superseded:* inline; the text they annotate is left as written.)

**Status:** superseded in parts (2026-10-12) · **Date:** 2026-09-03 · **Branch:** `v2`
**Supersedes:** the exploratory spike (branch `main`, archived under `archive/spike-results/`, moved there 2026-10-17)
**Commits and branches** named in this spec (`v2`, `main`, short hashes such as `73f7595`)
come from the history of the work and are not part of this copy.
**Relationship to the proposal:** the approved proposal (submitted ~June 2026) is the
directional frame. This spec refines it and, in three places, deliberately overrides it.
Those three are listed in §2 and must be raised with the supervisor. *(Historical: the
plan of September 2026. The thesis states its departures from the proposal in Chapter 5.)*

---

## 1. Research question (revised wording)

> For two use cases — high-frequency IoT micropayments and real-time on-chain token
> gaming — and the workloads derived from them, how do one state-channel implementation
> (Perun), one optimistic rollup (Arbitrum Nitro) and one ZK rollup (zkSync Era) compare
> on (i) **sub-process latency**, (ii) deposit and on-chain transaction cost, and
> (iii) sustained throughput at the workload's native cadence? Under which conditions
> does each approach appear preferable?

**Change from the proposal:** "latency to finality" → "sub-process latency".
The proposal's Metric 1 never measured finality — Update is explicitly soft confirmation,
and Close for both rollups is taken from documentation. The RQ now names what is measured.

### 1.1 What this thesis may and may not claim

**May claim:** cost structure, sub-process decomposition, topology cost, and the
amortisation crossover between families.

**May NOT claim:** production rollup throughput. All three systems are **local
development stacks**. Production Arbitrum and zkSync Era operate orders of magnitude
above the figures here. Any sentence of the form "rollups are slow" is out of scope and
must not appear. This constraint is stated in the abstract, the methodology and the
conclusion.

---

## 2. Deliberate overrides of the approved proposal

| # | Proposal says | This spec does | Why |
|---|---|---|---|
| O1 | UC2 gaming is **two-player 1-to-1**; multi-player is future work (p6) | **Many-to-many mesh only** | The bilateral constraint the proposal cites is already lifted by `hub-demo`'s virtual channels (0 gas, 0 on-chain tx). *(Revised 2026-09-28: an earlier second reason — "1-to-1 pins Arbitrum at ~4 tx/s" — is withdrawn. ~4 tx/s was `1/latency` of a client with one transaction in flight; with several in flight one account reached 157 tx/s, see T6.)* |
| O2 | All three protocols share **one local Anvil L1** (p2, p9) | **Native L1 per stack**, plus a written config policy | Never tested; nitro-testnode ships its own geth L1 (in dev mode; Prysm only with its `--pos` option, which we do not use) and zkstack ran against reth. Buys comparability that identical configuration also buys, at the cost of two unverified integrations on the critical path. |
| O3 | Sweep fixed frequencies `{1 ms, 10 ms, 100 ms, 1 s}` (p6) | **Native cadence, with payment granularity as the load knob** | *Justification revised 2026-09-21 — the spike's "ceilings" (4 and 9 tx/s) were `1/latency` of a one-in-flight harness, not ceilings.* Measured since (workstation, `archive/bench-workstation/results/`): Arbitrum sustains **333 tx/s** of plain transfers (7 M gas/s speed limit, identical to Arbitrum One; ~1,200 tx/s only as a fee-escalating burst); zkSync Era ~20–30 tx/s per node, and mainnet caps one account at ~20 in flight. So 1 ms (1,000 tx/s) is unreachable on both; 10 ms (100 tx/s) is reachable on Arbitrum only with ≥ ~25 transactions in flight and only for 21k-gas payments, and not on zkSync. The override stands. p7 already defines the metric as a *ceiling*, which is searched for, not sampled. |

Not an override: the Raspberry Pi cluster is already anticipated on p7–8.

---

## 3. Use cases

### 3.1 UC1 — IoT micropayments

- **Topology family:** many-to-one payments. *(Exact shape OPEN — see §11.1.)*
- **Data source:** [Grid Event Signature Library](https://gesl.ornl.gov/) (GESL), ORNL/LLNL.
  Free, open-access, **account required**. ~2,500 labelled PMU and PoW event signatures
  plus ~3,200 unlabelled PoW voltage records.
- **Shape:** each signature is a **continuous PMU recording around a labelled event**
  (the four downloaded: microPMU, 120 Hz, 4.5–16 min, several measurement points per
  row, about 584 bytes per row). The spike's synthetic `pmu.py` stays dropped.
- **Payment granularity:** **one payment per window of samples, paid after the window
  is received (postpay).** Data always replays at GESL's real cadence (120 rows/s) over
  its own TCP connection; the window size sets the payment rate (1 s → 1/s; 100 ms →
  10/s; 10 ms → 100/s per customer). Every setting is a legitimate commercial model, so
  this is a parameter of the marketplace, not an artificial ramp. Decided 2026-09-25.
- **Why not per sample:** PMU is typically 30–120 Hz and PoW kilohertz. Per-sample
  payment exceeds both rollups at every rate and hands Perun the result by construction.
- **Constant rate — the earlier burstiness claim is withdrawn (2026-09-25).** A PMU
  reports at a fixed 120 Hz whether or not an event is happening, so the data stream,
  and with it the payment rate, is constant. The supervisor's *nicht konstante
  Frequenzen* requirement is met by UC2 (bursty automatic fire), not by UC1.
- **Device model:** one device is **both sensor and transmitter**. Any sensor→transmitter
  delay is injected as a **deterministic, seeded offset in the trace**, not as a physical
  hop. A physical hop would add a constant to all three families equally while injecting
  Wi-Fi jitter larger than Perun's sub-millisecond signal (spike defect #5).

### 3.2 UC2 — On-chain token gaming

- **Topology:** many-to-many mesh. Every player transacts with every other player.
- **Workload:** `tank-arena` deterministic trace, replayed (not simulated) on the clients.
- **Payment semantics:** `fire` and `hit` events are token transfers; `move` and `break`
  are messages. Unchanged from the spike.
- **Load knob:** player count. ⚠️ **Not tick rate** — the 0.70 s reload caps each player
  at ~1.4 fires/s, and the spike's 6 players already ran at that cap (250 fires / 30 s =
  1.39/player/s). Raising 20 Hz → 60 Hz does not raise payment load.
- **Perun mapping:** hub (Ingrid) + full mesh of virtual channels, as in `hub-demo`.

*Superseded:* the final UC2 is a **live six-player game**, not a replayed trace: six bot
players, one per Pi, play one 60 s match at 20 Hz (seed 42) in peer-to-peer lockstep;
**movement travels over the game's own mesh**, and **only shots (1 point) and hits
(5 points) are payments** on the system under test. The weapon was redesigned (10
rounds/s, 30-round magazine) and frozen, so the reload cap and load knob above no
longer apply (cluster spec §7.3, `tank-arena/TUNING.md`).

### 3.3 Fairness rule (replaces the proposal's "1-to-1 like-for-like")

Identical event trace, identical load discipline, **native topology per family**. The
asymmetry — rollups serve many-to-many natively while Perun must route through a hub that
locks O(N) liquidity and is single-hop — is a **headline result**, not a caveat.

---

## 4. Data flow vs payment flow

**Separated is primary.** Payments travel on the L2 rail as bare token transfers; data
travels over its own transport (`tank-arena/net`, already described in commit `73f7595`
as *"the transmitter ↔ consumer transport for the sensor workload"*).

**Plus one bounded combined variant.** A fixed payload is embedded in the transaction to
quantify the per-byte cost gradient: nonzero for both rollups, zero for Perun.

**Why not combined as primary:** payload rides free in a Perun channel state update
(never touches chain) and costs per byte forever on both rollups. Combining hands the
conclusion to state channels by construction. Separating also removes payload size as an
undefined parameter — the proposal names it in the workload triple (p6) and never
specifies it.

**Limitation to state explicitly:** on-chain data availability and provenance is a real
rollup advantage this design declines to measure.

**Open:** confirm against Kuehn [3] whether his payment service separates the two. He is
the direct anchor and settles it authoritatively.

---

## 5. Metrics

### 5.1 Metric 1 — sub-process latency

Open / Update / Close, per the proposal's decomposition (Negka & Spathoulas).

**Cadence decomposition (new, and the fix for spike defect #1).** Four of the five
latency figures the spike produced are node-configuration constants:

| measured | what it actually is |
|---|---|
| Perun 0.525 ms | signed message RTT — **no block wait** |
| anvil-zksync 25.7 ms | in-memory VM + RPC, no sealing |
| Arbitrum 249.3 ms | ≈ `--execution.sequencer.max-block-speed` (250 ms default): a **minimum delay between blocks**, not a clock — blocks are demand-driven, and `eth_sendRawTransaction` blocks until sealing. A floor on latency, **not** a cap on throughput (flat ~251 ms from 4 to ~1,000 tx/s) |
| Anvil L1 986.2 ms | ≈ `--block-time 1` |
| zkstack 4,130 ms | state-keeper sealing deadline (`miniblock_commit_deadline_ms: 1000`; re-measured 2026-09-08 at 1.1–2.2 s, mean 1,552 ms, one in flight) |

Therefore every latency figure is reported **alongside its node's cadence constant**, and
the headline claim is structural rather than numeric:

> State channels remove the block-cadence term from the update path entirely. Both
> rollups' update latency is dominated by their sequencer's sealing cadence — a
> configuration parameter, not a protocol property.

Once all three families cross a real network link (§7), the claim becomes symmetric:
each family's update latency is dominated by its own transport-or-cadence constant.

### 5.2 Metric 2 — deposit and on-chain cost

*(Revised 2026-09-28 after review.)*

- **Primary figure: gas, reported per system in that system's own units.** Gas is
  **not** a common unit across chains: Perun's open and close are paid in base-chain
  gas; Arbitrum folds its data-posting cost into L2 gas at the L2 gas price; zkSync
  has its own computation and pubdata accounting. Nor is it config-independent:
  identical zkSync transfers measured 127 499 and 83 334 gas alternately (pubdata
  pricing). Gas figures are therefore never divided across systems.
- **Economic comparison: fee scenarios.** Costs are converted to ETH under
  **explicitly stated fee scenarios** (a gas price per chain, and for the rollups
  their data-cost component), and include setup and settlement, not only updates.
  Perun is costed in two deployments: on the base chain, and on Arbitrum (cluster
  spec §7.4). One euro conversion at a single stated reference price may follow.
  *Superseded:* Perun on Arbitrum was not built or measured — **future work** (owner,
  2026-10-11; cluster spec §7.4). Perun is measured on its bare L1 (Anvil) only.
- **Deposit comparability:** resolved in §11.5.
- **Amortisation crossover:** the payment count at which Perun's setup and
  settlement cost falls below cumulative rollup per-payment cost — computed **in
  ETH per fee scenario**, never as a gas ratio. The earlier spike table (Perun
  "amortises past ~3 / ~5 / ~19 / ~32 payments") divided base-chain gas by rollup
  gas and is **withdrawn**.

### 5.3 Metric 3 — sustained throughput

- **Paced (open-loop), primary.** Events dispatched at their trace timestamps. Reports
  deadline-miss rate, lateness, send lag, max in-flight backlog. Per-sender worker
  threads keep **in-order nonces per account** — real protocol behaviour, kept and
  reported rather than bypassed with throwaway accounts.
- **Unpaced (closed-loop) run — DROPPED 2026-09-25.** With one in flight it measured
  `1/latency`, not a ceiling — it is how the spike produced "Arbitrum = 4 tx/s". Ceilings
  come from the capacity probes instead (`one-client`, `one-node`, in-flight depth as the
  swept variable, `archive/bench-workstation/probe_ceiling.py`), and the UC1 window sweep shows where each
  system stops keeping up with a real workload. *Superseded:* the capacity probes were
  not run on the cluster — **future work** (owner, 2026-10-11; cluster spec §7.1); only
  zkSync's ceiling is known (U9, about 4–4.6 per second on the Pi 4).
- **UC2 has no unpaced run.** Game rules bound the event rate, so an unpaced game replay
  is not a realistic scenario. An overloaded paced run reports the ceiling anyway
  (spike UC-A: 40.1 tx/s offered → Arbitrum achieved 8.0).
- **Known gap:** Perun will not saturate at UC2's native cadence (0/379 misses), so
  UC2 × Metric 3 has one empty cell for Perun. Accepted.

### 5.4 New experiments (proposed — cut candidates)

Every measurement above is one Perun is expected to win. These probe the other side:

- **N1 — Liveness under intermittent connectivity — NOT MEASURED (2026-09-25).** An
  offline sensor cannot co-sign a channel update, so Perun stalls while a rollup keeps
  accepting payments. The outcome is clear from the protocol; the thesis discusses it
  instead of measuring it.
- **N2 — Onboarding cost.** Marginal on-chain cost of adding one counterparty:
  zero for a rollup, a full channel open for Perun.
- **N3 — Client hardware sensitivity.** Identical client role on Pi 3B+ vs Pi 4,
  isolating constrained-hardware cost from protocol cost.

*Superseded:* N2 and N3 were **not measured** (no results in the cluster spec); N3 would
need a second Pi 4, and the cluster has only one (`bath0`, the stack board). Both are
**future work**; see also T7.

---

## 6. Systems under test

| Family | Representative | Version | L1 |
|---|---|---|---|
| State channels | go-perun + perun-eth-backend | v0.15.0 / v0.6.0 | Anvil 1.7.1, `--block-time 1` |
| Optimistic rollup | Arbitrum nitro-testnode | `62f6cae`, node `v3.9.6-91bf578` | its own geth in dev mode (Prysm only with `--pos`, not used) |
| ZK rollup | **zkSync Era via `zkstack`** | to pin | reth (docker) |

**zkSync: `zkstack` only.** `anvil-zksync` is dropped as a measurement target because it
**has no L1 bridge** — no Open, no Close, no deposit — and understates real-sequencer
latency by ~160× (25.7 ms vs 4,130 ms). The 160× figure is **cited from existing spike
data**; no second campaign is run.

**Sealing is tuned.** 4,130 ms is a deadline, not a physical limit — ZKsync's
`conditional_sealer` is criteria-driven. Locating the parameters in the `zksync-era`
config schema is a concrete task. Run at the fastest honest setting.

**Cadence policy.** All three nodes run under one written, documented cadence policy so
Metric 1 differences reflect protocol work rather than configuration.

**Asset (revised 2026-09-25): UC1 pays in native ETH, UC2 moves an ERC-20 points token.**
UC1 is micropayments, so it uses the cheapest real money — a plain ETH transfer (21 000
gas on Arbitrum against ~35 000 for a token); in practice IoT payments would more likely
use a stablecoin, itself an ERC-20, which the thesis states as a limitation. UC2's
points have no value (cluster spec §7.3). The notes below apply to UC2.
- `hub-demo/setup.go` is hard-wired to ETH (`ValidateAssetHolderETH`, ETH depositor, zero
  ERC-20 references). The library **does** support it —
  `bindings/assetholdererc20/`, `channel/erc20_depositor.go`. Bounded change.
- Consequence: Perun's Open gets *more* expensive (token `approve` before deposit);
  rollup Updates ~1.7× more expensive; Perun's Update stays exactly 0 gas.
- ERC-20 bridging differs from ETH bridging on both rollups, so the spike's bridge
  figures (Arbitrum 91,067 gas / 1.80 s; zkSync 304,641 gas / 4.57 s) do **not** carry
  forward. The known-working ETH bridge is the documented fallback if the token-gateway
  integration overruns.

---

## 7. Measurement environment

**Transport tier 3.** All families cross a real network link.

⚠️ **Blocking prerequisite:** v2's `hub-demo` uses `wire.NewLocalBus()` (`main.go:102`) —
every Perun client and the hub live in **one process**, talking over Go channels, while
the rollups cross a real socket. Perun's 0.525 ms is in-process IPC. Restoring the
distributed layer from `main` (**2,756 lines**: `distributed.go` 1,120, `trace.go` 373,
`lanwire/` 191, results/trace/preflight/tests) is **mandatory and the long pole**. The
cost is *review* of unreviewed spike code, not development.

*Superseded:* **resolved.** The distributed layer was restored (`perun/hub-demo/
distributed.go`, `lanwire/`), and the measurements use the dedicated `uc1-*` and `uc2-*`
modes over TCP between boards (`perun/hub-demo/DISTRIBUTED.md`).

**Hosting:**

| Role | Hardware |
|---|---|
| All three L2 stacks, Perun hub, UC1 service, chrony server, collection | **PC** — `workstation`, i7-10700K, 16 cores, WSL2 |
| Client fleet (sensors, players) — homogeneous | **8 × Pi 3B+** (1 GB), N ≤ 8 |
| Reserve / N3 hardware-sensitivity | 2 × Pi 4 (8 GB) |

**Why stacks are not on Pis.** `matterlabs/server-v2` and `matterlabs/local-node` are
**amd64 only**; only `external-node` (a read-only replica, not a sequencer) ships arm64.
Running zkstack on a Pi would require cross-compiling `zksync_server` from Rust source,
unsupported upstream. Arbitrum *could* run on a Pi — `offchainlabs/nitro-node:v3.9.6-91bf578`
does publish linux/arm64, and `simple=true` is the default in `test-node.bash:61` — but
hosting the two rollups on different hardware would re-confound Metric 1. So all stacks
sit on identical PC hardware.

*Superseded:* **all stacks run on `bath0`, a Raspberry Pi 4 (8 GB)**, one at a time,
each with its own L1 (Anvil, geth, reth); zkSync uses our own arm64 `zksync_server` build
(`zksync-setup/ARM64.md`). The workstation is never in the measured path. The cluster is
**1 Pi 4, 2 Pi 3B+ and 5 Pi 3B** (the second Pi 4 is defective; no spares). Hub, sensor
and players run on the Pi 3s (cluster spec §2).

**Network: Wi-Fi, 5 GHz** (Pi 3B+ is 802.11ac dual-band).
*Superseded:* the Pi 3Bs are 2.4 GHz only; the final setup is **mixed Wi-Fi** (`bath1`,
`bath2` on 5 GHz, all other boards incl. `bath0` on 2.4 GHz; cluster spec §1, U4).
⚠️ `deploy/README.md` §5's accuracy budget ("tens of µs, worst case a few hundred µs")
is derived for **quiet wired 1 GbE** and does **not** hold. It must be re-derived, and
its own fallback applies: below the sync floor, derive one-way latency as RTT/2 rather
than differencing clocks. **Wi-Fi jitter characterisation is a required first experiment.**

**Also to correct:** `deploy/README.md` §1 and §2 specify wired Ethernet and a switch
throughout; the whole document needs rewriting for Wi-Fi.

**RAM note:** the proposal's "32 GB" (p7) is the Windows host. WSL2 sees **15.6 GB** with
no `.wslconfig` (default 50 %). Raisable if zkstack needs it. `DISPERSION-msi.md` also
flags this host as *"a VM layer over Windows — NOT bare metal"*; both facts belong in the
setup section.

**Rule: one entity = one key, no aggregation.** Carried from `tank-arena/README.md:26`.
If a transmitter ever signs for several sensors, N streams collapse onto one account and
per-account limits then cap rollup throughput. (Not the "~4 tx/s cap" first written here —
that was a client artifact; one Arbitrum account reaches 157 tx/s. The real per-account
limits are nonce ordering and, on zkSync, `max_nonce_ahead`: 20 in flight on mainnet.)

---

## 8. Run matrix

Per protocol × use case × configuration, **3 repeats**, reported as **mean ± sample sd
(n−1)** with min–max and per-run values — the `DISPERSION.md` discipline.

Sequential execution only; never two benchmarks at once (CPU-contention skew).
Per-run environment capture and the provenance guard that refuses to summarise a bundle
whose client commits differ — this is how the spike found its binary-provenance defect.

Repeat count is **n = 3**, decided 2026-09-08 (§11.6).

---

## 9. Repository plan

**Reuse as-is:** `setup.sh`, `Makefile`, pinned versions, `STATUS.md` (needs refresh).

**Restore from `main` and review:** the distributed Perun layer, 2,756 lines. Mandatory.

*Superseded:* `setup.sh`, the `Makefile` and `STATUS.md` were not reused; they are archived
(`archive/workstation-setup/`, `archive/history/`). The distributed Perun layer was
restored and extended into `perun/hub-demo/`.

**Review before reuse (unreviewed spike code, 3,554 lines):** `tank-arena/` 3,039 LOC and
`deploy/` 515 lines, all imported unreviewed in commit `73f7595` (+3,987 lines, 0
deletions). `arena/multiplayer.py` (318) and `arena/showcase.py` (48) exist on **no other
branch** — they came from a working tree, not a reviewed commit.

**Rebuild from spec:** the benchmark harnesses. `replay_bench.py` (closed-loop) and
`paced_bench.py` (open-loop) exist on `main` and are the reference; the adapter layer
(`tank-arena/backends/`, the `PaymentBackend` interface) was deleted in `73f7595`, so
**nothing in v2 can currently talk to a blockchain**.
*Superseded:* the measurement harness on v2 is `measure/` (payment clients for all
three systems, paying live for the UC1 sensor's stream and the UC2 game's shots and
hits; no trace replay). `replay_bench.py`/`paced_bench.py` and `tank-arena/backends/`
exist on `main` only.

**Stale documentation to fix:** `tank-arena/README.md` documents five files that do not
exist on v2 (`platooning.py`, `pmu.py`, `replay.py`, all of `backends/`) and references
`bench/paced_bench.py`; `workloads/__init__.py` lists four generators when one exists;
`deploy/README.md` §7 describes `../emulation/`, absent from v2, and points at
`hub-demo/DISTRIBUTED.md`, also absent; the root `README.md` layout block omits
`tank-arena/`, `deploy/` and `spike-results/`.
*Superseded (2026-10-12):* the tank-arena README and `workloads/__init__.py` were
corrected; `hub-demo/DISTRIBUTED.md` exists on v2.

✅ **`clean-init.sh` recovered — RESOLVED.** The zkstack sequence that finally passed
after 11 attempts and 7 distinct failure classes is now in the repo:
`zksync-setup/clean-init.sh`, and the workstation variant
`zksync-setup/clean-init-desktop.sh` (secrets excluded).

---

## 10. Threats to validity

| # | Threat | Treatment |
|---|---|---|
| T1 | Latency figures are node-configuration constants | §5.1 cadence decomposition; structural claim, not numeric |
| T2 | Local dev stacks ≠ production L2s | §1.1 explicit non-claim |
| T3 | Perun measured over IPC while rollups cross a socket | Tier 3 makes all three cross a real link (§7) |
| T4 | Wi-Fi jitter exceeds Perun's signal | 5 GHz; jitter characterised first; RTT/2 fallback |
| T5 | Design choices that pre-determine the result | Separated data/payment (§4); per-window granularity (§3.1); both argued explicitly |
| T6 | Arbitrum's ~4 tx/s is a test artifact | It is `1/latency` of a one-in-flight client — neither a per-account nor a node limit (one account: 157 tx/s; node: 333 tx/s sustainable). Never reported as a throughput figure at all |
| T7 | Every planned measurement favours Perun | §5.4 N2–N3 measured; N1 (offline sensor) discussed, not measured. *Superseded:* N2 and N3 were **not** measured (N3 needs a second Pi 4, which the cluster lacks) — future work; the threat remains and is stated |
| T8 | GESL redistribution vs the promised public repo | §11.7 — resolved: raw data not published |

---

## 11. Open items

| # | Item | Recommendation |
|---|---|---|
| 11.1 | **UC1 exact topology** — RESOLVED 2026-09-25 | **One sensor → many customers.** The sensor device is both producer and sender; the customers pay it, so payments are many-to-one. Boards: the sensor on a Pi 4 (produces the data and sends it — the load), the stack under test on the other Pi 4, **6 customers on 6 Pi 3s** (receive the data, pay the sensor). Layout in the cluster spec §7.2. *Superseded:* the sensor runs on `bath1` (Pi 3B+), the stack on `bath0` (the only Pi 4), and the measured series use **5 customers** (final: `bath2`, `bath4`…`bath7`; cluster spec §7.2, §8 step 3). |
| 11.2 | **N** | Hardware caps N ≤ 8. Suggest N = 6 to match existing spike traces, with N = 8 as the high point. |
| 11.3 | **Window sizes for UC1** — RESOLVED 2026-09-25 | Sweep **1 s / 100 ms / 10 ms** → 1, 10, 100 payments/s per customer (6, 60, 600/s at the sensor). *Superseded:* with 5 customers, **5, 50, 500/s** at the sensor. |
| 11.4 | **Metric 1 Close asymmetry** | Perun's Close is measured; the rollups' is documentation (7-day window, proof delay). Report in separate columns, never one cell. |
| 11.5 | **Deposit comparability** — RESOLVED 2026-09-25 | A deposit covers the whole run plus a margin, reported next to the results. A go-perun channel cannot be topped up once open, so longer operation uses **make-before-break**: open a new channel shortly before the old one runs out, switch, then close the old one — described with its cost (one extra open and close per switch), not measured. |
| 11.6 | **Repeat count** — RESOLVED 2026-09-08 | **n = 3**, reported as mean ± sample sd (n−1) with min–max and per-run values. Supersedes the suggestion of n=5 for headline cells. |
| 11.7 | **GESL licence + public repo** — RESOLVED 2026-09-25 | GESL states the data is "free to use, and publicly available" behind a free account, with an official citation, but gives no redistribution licence. **Raw files are not published** (`/data/` stays git-ignored); the thesis cites GESL — *ORNL, LLNL. (2023). Grid Event Signature Library. GESL. https://gesl.ornl.gov* — and records the signature IDs and the SHA-256 of each CSV (cluster spec §7.2). Results derived from the data are published with the citation. Repo licence is still "To be determined". *Superseded:* the licence is MIT for the code and CC BY 4.0 for the results, figures and documentation (root `README.md`). |
| 11.8 | **UC numbering** — RESOLVED 2026-09-03 | Adopted the proposal's **UC1 = IoT micropayments, UC2 = on-chain gaming**. Six live sites relabelled `UC-B` → `UC2`. Archive **keeps** the spike's three-case A/B/C scheme (filenames and JSON provenance included); a mapping table was added to `spike-results/README.md`. |
| 11.9 | **Related-work search record** | "To the best of our knowledge" appears three times. Record databases, queries and dates, or soften. |
| 11.10 | **Kuehn comparability** | He measured Perun at 100 Hz on constrained hardware; the spike saw ~1,900–2,400/s on a laptop. State what differs (hardware, transport, in-process bus) so the delta is explained, not implied as a contribution. |

**Not carried out / for the paper (noted 2026-10-12).** No outcome is recorded for: the
bounded combined data-plus-payment variant (§4; not measured, payments are bare
transfers); the confirmation against Kuehn [3] of whether his payment service separates
data and payment (§4); the related-work search record (11.9); and the Kuehn
comparability statement (11.10). Each is to be settled or stated as a limitation in the
thesis text. *(Historical: the plan at that date.)*

---

## 12. Deliverables

1. Reproducibility artifact: harnesses, contract sources, node configs, raw logs,
   environment captures — in the repository, licence resolved.
2. Comparison table: 3 protocols × 2 use cases × 3 metrics, with dispersion.
3. Amortisation crossover figures (§5.2).
4. Use-case-specific recommendations.
5. Discussion: the adoption paradox — favourable micro-benchmarks, near-zero adoption.
   Evidence already in hand: go-nitro dormant since 2023; perun-node stale since 2021,
   still pinning go-perun v0.9.0. A tooling-vs-protocol maturity gap observed first-hand.
