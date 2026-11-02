# Two-user physical-LAN campaign — 2026-07-28

First campaign in which the clients are **separate physical machines on real
Wi-Fi** rather than processes on one host. Covers all three scaling families
plus an L1 baseline, under one workload definition.

Supersedes nothing: `DISPERSION.md` (laptop) and `DISPERSION-msi.md` (PC) remain
the single-host baselines. These numbers are a **different quantity** — the same
workloads with a physical network in the payment path — and must not be pooled
with them.

## Reproducibility envelope

- Code: repository at `b325f1e`. **Binary provenance was not uniform** — see
  "Binary provenance" below. The measurements are unaffected; the recorded
  provenance is imprecise, and this is stated rather than smoothed over.
- Date: 2026-07-28, 20:57–22:07 local (18:57–20:07 UTC).
- Network: home 5 GHz Wi-Fi, SSID `home-wifi`, channel 120, 802.11ax,
  subnet `192.168.178.0/24`. Pre-run ICMP from the laptop: 50/50 to each peer,
  **2.75 ms** mean to G16, **2.97 ms** mean to the PC, 0% loss.
- Repeats: **n = 2 per family** (see "Variance" — the third battery was cut
  deliberately, not lost).

### Machines

| Role | Machine | CPU | Notes |
|---|---|---|---|
| Infrastructure | PC `workstation`, `192.168.178.22` | i7-10700K, 8C/16T | Perun hub + Anvil; all rollup sequencers |
| User / payer | ASUS G16 `laptop-g16`, `192.168.178.35` | Ultra 7 155H, 16C/22T | Perun Alice; rollup sender `0` |
| User / counterparty | HP x360 `laptop`, `192.168.178.27` | i5-8250U, 4C/8T | Perun Bob; rollup sender `1` |

The **same** machines act as users in both campaigns, deliberately: swapping
client hardware between families would confound any cross-family comparison.
The laptop's CPU governor was left at `powersave`, matching the single-host
batteries.

### Binary provenance (known defect in this campaign)

Every result JSON records that the two clients ran binaries built from
different points:

- **Alice (G16)** — `d7c374d`, clean worktree
- **Bob (laptop)** — `2cfd5ec`, **dirty** worktree

Cause: the laptop's `perun-node` was built during trace-mode development, while
the trace changes were still uncommitted, and was never rebuilt after they were
committed. The G16 and PC were rebuilt from a clean tree via git bundle; the
laptop was not.

**Impact on the measurements: none.** `git diff cbff89c HEAD -- 'perun/hub-demo/*.go'`
is empty — every commit after the laptop's build point touched only shell
scripts, Python harnesses and documentation, none of which compile into
`perun-node`. Bob's binary is therefore built from Go source identical to
Alice's; only the embedded VCS stamp differs. Bob also runs `-payments 0` and
never executes the trace path at all.

**Impact on the reproducibility claim: real.** This campaign cannot claim
"identical clean commit on both clients", which is an explicit item in
`perun/hub-demo/BETA-CHECKLIST.md`. `bench/summarize_perun_lan.py` correctly
refuses to summarize the bundle, failing with `client commits differ` — the
guard worked as designed and is what surfaced this. Any future campaign must
rebuild on all three machines *after* the final commit, and confirm the
generated summary before trusting the run.

### Systems under test

| Family | Implementation | Endpoint |
|---|---|---|
| State channels | go-perun hub-demo (virtual channel Alice⇄Bob via Ingrid) | Anvil `:8545` |
| ZK rollup | anvil-zksync 0.6.11, chain 260 | `:8011` |
| Optimistic rollup | nitro-testnode `v3.9.6`, chain 412346 | `:18547` |
| L1 baseline | Anvil 1.7.1, 1 s blocks, chain 31337 | `:8555` |

## What "two users" means per family — and why it differs

This asymmetry is a **result**, not a caveat:

- **State channels are peer-to-peer.** Each user runs a real node, holds its own
  keys and channel state, and **co-signs every payment**. Both machines are
  active in every single update.
- **Rollups are client–server.** One sequencer, many clients. The **receiver is
  passive** — only the sender transacts. UC-B is entirely `0 → 1`, so the rollup
  trace runs have exactly one active machine by construction.

Consequently the rollup **burst** workload is the only one where both machines
genuinely transact at once (each from its own prefunded account, so nonces stay
isolated). The rollup trace workloads have a single active sender. Reported as
such rather than implied.

**UC-A (4 players) and UC-C (6 players) are structurally out of scope at two
users** — the traces demand more senders than the deployment has. The harness
rejects them outright rather than replaying a subset and reporting it as
complete.

## 1. Perun state channels (n = 2)

Closed-loop, each payment awaiting the peer's counter-signature.

| Workload | payments | throughput /s | mean ms | p99 ms |
|---|---|---|---|---|
| burst500 | 500 | 298.5 / 290.2 | 3.348 / 3.446 | 6.666 / 5.579 |
| burst379 | 379 | 282.9 / 270.8 | 3.535 / 3.693 | 10.043 / 14.048 |
| UC-B closed | 600 | 238.9 / 281.3 | 4.186 / 3.554 | 12.489 / 7.898 |

Channel lifecycle (UC-B runs, on-chain against the remote Anvil over Wi-Fi):

- ledger open **1060–1113 ms** — pinned by the 1 s block time
- virtual open **30–33 ms** (Alice; Bob 0, it is the responder)
- virtual settle **301–397 ms** (Alice) / **11–13 ms** (Bob)
- ledger settle **2115–2571 ms**

The two-orders-of-magnitude split between the off-chain path (~3–4 ms) and the
on-chain path (~1–2.5 s) survives physical distribution intact.

### Paced (open-loop), 50 ms budget

| Battery | ok | misses | latency mean / p99 ms | lateness mean / max ms | backlog |
|---|---|---|---|---|---|
| b1 | 600/600 | **31 (5.2%)** | 10.685 / 138.596 | 11.983 / 179.332 | 2 |
| b2 | 600/600 | **0 (0%)** | 4.753 / 12.011 | 5.392 / 20.500 | 1 |

Both sustained the full 10 Hz offered rate (wall ≈ trace span). See "Variance".

## 2. Rollups and L1 (n = 2)

### Burst — both users concurrent, 100 tx each

Sizing matches every `evm_bench.py` call in `rerun-battery.sh`. Per-user means:

| Chain | G16 mean ms | laptop mean ms | throughput /s |
|---|---|---|---|
| zkSync | 41.8 / 43.5 | 36.2 / 41.2 | ~17–21 |
| Arbitrum | 252.5 / 242.5 | 235.7 / 235.8 | ~3.8–4.1 |
| L1 Anvil | 1084.9 / 1022.9 | 990.5 / 994.0 | ~0.9–1.0 |

Both users sustained near-identical latency concurrently — no nonce collisions
and no evidence of one client starving the other at these rates.

### UC-B closed-loop, 600 transfers, single sender

| Chain | mean ms | median | p90 | max | rate /s |
|---|---|---|---|---|---|
| zkSync | 122.3 / 115.1 | 129.4 / 118.5 | 178.3 / 164.3 | 277.8 / 284.5 | 8.2 / 8.7 |
| Arbitrum | 259.5 / 249.9 | 254.4 / 250.6 | 275.9 / 252.8 | 430.0 / 344.0 | 3.9 / 4.0 |
| L1 Anvil | 1108.8 / 1014.6 | 1126.8 / 984.7 | 1173.4 / 1085.2 | 1284.7 / **3359.4** | 0.9 / 1.0 |

All 600/600 succeeded on every chain in both batteries.

### UC-B paced, 100 ms budget — the decisive result

| Chain | misses | achieved rate (offered 10.0) | lateness mean | total wall (trace = 59.9 s) | backlog |
|---|---|---|---|---|---|
| zkSync | 5 (0.8%) / 17 (2.8%) | 10.0 / 10.0 | 40.5 / 40.8 ms | 59.93 / 59.92 s | 2 |
| L1 Anvil | 591 (98.5%) / 572 (95.3%) | 10.0 / 9.9 | 612.3 / 546.6 ms | 60.25 / 60.52 s | 12 |
| Arbitrum | 597 (99.5%) / 597 (99.5%) | **3.9 / 4.0** | **47 465 / 44 885 ms** | **154.84 / 149.89 s** | 1 |

**Arbitrum does not merely miss the budget — it fails to keep up at all.** It
achieved ~4 tx/s against 10 tx/s offered, mean lateness grew to ~45 *seconds*,
and draining took ~2.5× the trace duration. This is unbounded divergence, not a
fixed penalty.

**This reproduces, over a physical network, a property already established on a
single host.** `DISPERSION.md` and the thesis §7.7 record the same ~4 tx/s
per-payer cap and a 95.5% paced miss rate, bit-identical across the laptop and
the MSI/WSL2 host. The contribution here is not the discovery but the
confirmation that it survives physical distribution unchanged — it is an
intrinsic sequencer property, not a host or emulation artifact.

The mechanism is visible in `send lag`, which on Arbitrum tracks total lateness
almost exactly (mean 47 454 ms of 47 465 ms): `eth_sendRawTransaction` itself
blocks until the sequencer accepts, ~250 ms per call. With strictly sequential
per-account nonces, **one account cannot exceed ~4 tx/s**, below the workload's
10 Hz demand, so the dispatch queue diverges without bound. Max backlog stays at
1 precisely because the *sender* is the bottleneck, not receipt collection.

L1 Anvil behaves differently despite far worse per-tx latency: send lag is only
~10 ms (the mempool accepts immediately, backlog reaches 12, one block absorbs
many queued txs), so it **sustains 10 tx/s** while every individual payment
lands ~600 ms late. Same miss rate, structurally different failure.

## 3. Cross-family summary

UC-B, identical 600-event workload, same Wi-Fi. Perun's budget is the *stricter*
50 ms; the EVM families use the 100 ms convention from `rerun-battery.sh`.

| Family | closed-loop mean | paced misses | sustains 10 Hz? |
|---|---|---|---|
| **State channels (Perun)** | **3.55–4.19 ms** | 0–31 / 600 @ **50 ms** | yes |
| **ZK rollup (zkSync)** | 115.1–122.3 ms | 5–17 / 600 @ 100 ms | yes |
| **L1 baseline (Anvil)** | 1014.6–1108.8 ms | 572–591 / 600 | yes, ~600 ms late |
| **Optimistic rollup (Arbitrum)** | 249.9–259.5 ms | 597 / 600 | **no — diverges** |

Two findings worth carrying into the thesis:

1. **~30× separates the state channel from the fastest rollup**, and ~280× from
   the L1 — on the same network, same workload, same machines.
2. **Ranking by mean latency reverses the ranking by fitness for purpose.**
   Arbitrum is ~4× faster than L1 per transaction yet is the only system that
   cannot sustain the workload, because a 10 Hz single-account demand exceeds
   what its blocking submit path allows. Mean latency alone would have hidden
   this; only the paced, use-case-driven measurement exposes it.

## 4. Variance, and why n = 2

The third battery of each campaign was cut to save wall-clock, after two
batteries showed the EVM families are **highly reproducible** — Arbitrum's paced
miss count was *identical* (597/600) across batteries, and closed-loop means
moved <4%. This matches `DISPERSION.md`: block-cadence-bound systems do not
absorb host noise.

**Perun does, and it showed.** The paced run gave **31 misses in b1 and 0 in
b2** (p99 138.6 ms vs 12.0 ms). b1 was the first Perun battery after the rollup
teardown; despite a 120 s settle the PC had likely not fully quiesced. This is
the same effect `DISPERSION.md` attributes to CPU contention in its run 3, where
the entire Perun set came out 2–3× slow.

With n = 1 this campaign would have reported either "flawless" or "5.2% miss
rate" with no way to tell which was the artefact. **n = 2 gives a range and
catches gross outliers; it does not support a standard deviation.** Report both
values and the spread — do not average them into a single figure, and do not
call it a sd.

## 5. Threats to validity

- **n = 2.** No dispersion statistics. The Perun paced figures in particular
  straddle a 5% miss rate and 0.
- **Hardware asymmetry.** Alice (payer) runs on the fastest machine (16C), Bob
  on the weakest (4C). Held constant across families, so cross-family
  comparison is sound, but absolute figures are not hardware-neutral.
- **Different budgets.** Perun's 50 ms is hardcoded (`pacedBudget`); the EVM
  harness used 100 ms. Perun therefore cleared a *stricter* bar — the gap is
  understated, not flattered.
- **Local dev chains.** anvil-zksync and nitro-testnode are development
  sequencers, not production networks; absolute latencies are optimistic for
  all rollups. Relative ordering is the transferable result.
- **Single sender in trace runs.** Rollup UC-B exercises one client; sequencer
  behaviour under many concurrent writers is only probed by the bursts.
- **b2 L1 closed-loop max 3359 ms** — a single ~3× outlier against a p90 of
  1085 ms, retained rather than discarded.

## 6. Artifacts

- `bench/lan-results/*.json` — 20 Perun result files (Alice + Bob per run),
  including the `trace` block: latency, lateness, deadline misses, backlog.
- `bench/lan-logs/b*-{alice,bob,ingrid}.log` — per-node Perun logs.
- `bench/lan-logs/rollup/*.log` — 26 rollup run logs.
- `bench/lan-logs/environment-*.txt` — per-node CPU, RAM, Wi-Fi link, ping.
- Orchestrators: `bench/lan-battery.sh` (Perun), `bench/lan-rollup-battery.sh`.

### Extrapolating to a Raspberry Pi cluster

The off-chain state-channel path is CPU-bound and the rollup paths are not, so
the two families should extrapolate in **opposite** directions on weaker
hardware. A Pi should degrade Perun's ~3.5 ms materially while barely moving
Arbitrum's ~250 ms or L1's ~1 s, which are set by block cadence and submit
behaviour. Per-node CPU is captured in the environment files for exactly this
purpose. Note also that Arbitrum's divergence is a property of the *submit path*
rather than client speed, so it should persist on a Pi.

## 7. Operational notes (reproducing this setup)

- nitro-testnode publishes the sequencer on `127.0.0.1:8547` only. A
  `docker-compose.override.yml` republishes it on `0.0.0.0:18547` as a *second*
  host port; adding `0.0.0.0:8547` would collide with the existing binding.
- Docker's `credsStore: desktop.exe` makes every registry call invoke the
  Windows credential helper, which fails over SSH with `A specified logon
  session does not exist`. Removing the key lets local images be used.
- nitro-testnode funds only its own funnel/dev keys; the standard Anvil test
  accounts start at **zero balance on L2** and must be funded via
  `./test-node.bash script send-l2`.
- anvil-zksync runs inside the PC's WSL2 (NAT), so it is not LAN-visible; a
  `netsh interface portproxy` entry bridges `0.0.0.0:8011` to the current WSL
  IP, which changes whenever the distro restarts.
- Rollup infrastructure **must be torn down before the Perun campaign**: nitro's
  geth holds `127.0.0.1:8545`, the port Perun's Anvil needs, and leaving ~24
  containers running loads the machine that also hosts Ingrid.
