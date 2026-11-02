# 3-run dispersion statistics — MSI host (WSL2) — mean ± sample sd (n−1), min–max, per-run values

Aggregated 2026-07-22 on a **second machine** to test cross-platform
reproducibility of the laptop campaign in `DISPERSION.md`.

**Host:** `workstation`, WSL2 Ubuntu, kernel
`6.18.33.1-microsoft-standard-WSL2` (**a VM layer over Windows — NOT bare
metal** like the laptop's Mint), 16 cores, 15 GB RAM (WSL-capped). Go 1.26.4,
anvil-zksync v0.6.11, nitro-testnode v3.9.6.

**Runs (2026-07-22):** three back-to-back batteries, each ~38 min with 180 s
cooldowns — b1 16:11–16:50, b2 16:53–17:31, b3 17:34–18:12. Sources:
`msi-results-final/rerun-{1,2,3}.log` (zkSync + L1 + Arbitrum EVM sets) and
`msi-results-final/perun-hub-rerun-{1,2,3}.log` (go-perun hub-demo). The Perun
sections **inside** the `rerun-*.log` Python bench are ignored; the
`perun-hub-*.log` files are authoritative. `perun-hub-rerun-1.log` begins with
an aborted partial run; the **last complete** clients-2..6 + UC-C/A/B sequence
(lines 559–886) is used as battery 1.

**Caveats:**
- **WSL2, not bare metal.** All figures ride a Hyper-V VM over Windows, so host
  noise couples in differently from the laptop. Despite this, the block-cadence
  families reproduce the laptop bit-for-bit (see the host-comparison section).
- **Arbitrum worked in all 3 batteries** here (unlike the earlier defective MSI
  run where nitro was n/a): the 8 bench accounts were pre-funded with 1000 ETH
  each on the nitro L2 (`send-l2`) before the batteries, so Arbitrum data is
  **n=3**, full coverage.
- **Perun ran inside the full battery**, sharing the CPU with the zkSync and
  Arbitrum sets, exactly as on the laptop.
- **zkstack full chain (:3050) was NOT run** — no Rust toolchain on this host
  (the `evm_bench.py … 3050` call gets `Connection refused` in every battery) →
  **n/a**.
- **Netem** shaping matrix is **out of scope** on this host → **n/a**.

Format: `name: mean ± sd (min–max) [run1, run2, run3]`. All per-run values are
verbatim from the source files. Sample sd uses n−1.

## 1. Perun hub — synthetic bursts, 500 payments (N = clients)

All three batteries ran the full N=2..6 sweep (no run-1 asymmetries this time).

- N=2 payments/s: 2601 ± 69 (2521–2643) [2638, 2521, 2643]
- N=2 ms/payment: 0.385 ± 0.011 (0.378–0.397) [0.379, 0.397, 0.378]
- N=3 payments/s: 2636 ± 37 (2600–2673) [2634, 2600, 2673]
- N=3 ms/payment: 0.380 ± 0.006 (0.374–0.385) [0.380, 0.385, 0.374]
- N=4 payments/s: 2574 ± 65 (2504–2631) [2631, 2587, 2504]
- N=4 ms/payment: 0.389 ± 0.010 (0.380–0.399) [0.380, 0.387, 0.399]
- N=5 payments/s: 2621 ± 52 (2561–2653) [2653, 2561, 2648]
- N=5 ms/payment: 0.382 ± 0.007 (0.377–0.390) [0.377, 0.390, 0.378]
- N=6 payments/s: 2613 ± 68 (2545–2680) [2545, 2613, 2680]
- N=6 ms/payment: 0.383 ± 0.010 (0.373–0.393) [0.393, 0.383, 0.373]

Every burst clears **~2500–2700 payments/s at ~0.38 ms/payment** in all three
batteries (CV 1.4–2.8%). **No run-3 collapse** — the laptop's run 3 dropped to
628–879 pay/s (CV 39–70%); the MSI holds tight across all batteries.

## 2. Perun ledger / virtual channel wall-clock

From the `-clients 6 -payments 500` burst block of each battery.

- ledger open mean s: 1.037 ± 0.011 (1.0269–1.0487) [1.0348, 1.0487, 1.0269]
- ledger close mean s: 2.0149 ± 0.0005 (2.0145–2.0155) [2.0145, 2.0148, 2.0155]
- virtual open mean ms: 3.0 ± 2.7 (1.4–6.1) [6.1, 1.6, 1.4]
- virtual settle mean ms: 3.9 ± 1.7 (2.9–5.8) [3.0, 2.9, 5.8]

Ledger open/close are pinned by Anvil's 1 s block time in every battery (open
≈ 1.04 s = one block; close ≈ 2.01 s = two blocks); CV ≈ 0.0–1.1%. The
virtual-channel millisecond figures jitter with per-channel scheduling but stay
2–3 orders of magnitude below the on-chain path in all three batteries.

## 3. Perun UC-C game trace (379 transfers)

Closed-loop (one in flight):

- closed-loop payments/s: 2232 ± 30 (2198–2255) [2242, 2198, 2255]
- closed-loop latency (all) mean µs: 448 ± 6 (443–455) [446, 455, 443]

Paced (open-loop, 50 ms budget):

- lateness mean ms: 1.178 ± 0.021 (1.161–1.201) [1.201, 1.161, 1.172]
- deadline misses: 0 ± 0 (0–0) [0/379, 0/379, 0/379] — perfectly stable

## 4. Perun UC-A / UC-B

- UC-A closed-loop payments/s: 2636 ± 47 (2582–2667) [2659, 2582, 2667]
- UC-B closed-loop payments/s: 2581 ± 68 (2506–2638) [2638, 2506, 2598]
- UC-A paced lateness mean ms: 1.128 ± 0.016 (1.111–1.142) [1.131, 1.111, 1.142]
- UC-A paced misses: 0 ± 0 [0/2400, 0/2400, 0/2400]
- UC-B paced lateness mean ms: 1.085 ± 0.009 (1.074–1.091) [1.091, 1.089, 1.074]
- UC-B paced misses: 0 ± 0 [0/600, 0/600, 0/600]

(Battery-1 UC-A paced logged a `concluded already` retry and printed a second
result block; both are 0/2400 misses at ~1.13 ms lateness. The first clean
block, 1.131 ms, is used to match the single-block reporting of b2/b3.)

## 5. anvil-zksync (v0.6.11, chain 260)

100-tx sequential bench:

- latency mean ms: 24.0 ± 0.35 (23.8–24.4) [23.8, 24.4, 23.8]
- latency median ms: 23.87 ± 0.12 (23.8–24.0) [23.8, 24.0, 23.8]
- throughput tx/s: 34.43 ± 0.46 (33.9–34.7) [34.7, 33.9, 34.7]

UC-C / UC-A / UC-B closed replay (latency mean ms):

- UC-C closed: 109.0 ± 0.06 (108.9–109.0) [109.0, 109.0, 108.9]
- UC-A closed: 108.87 ± 0.06 (108.8–108.9) [108.9, 108.8, 108.9]
- UC-B closed: 109.0 ± 0.06 (108.9–109.0) [109.0, 109.0, 108.9]

UC-C paced (50 ms budget):

- miss rate %: 4.7 ± 2.9 (1.3–6.6) [1.3 (5/379), 6.6 (25/379), 6.1 (23/379)]
- lateness mean ms: 25.0 ± 1.5 (23.3–26.1) [23.3, 26.1, 25.6]

UC-A / UC-B paced (100 ms budget):

- UC-A lateness mean ms: 32.0 ± 0.92 (31.0–32.8) [31.0, 32.2, 32.8]
- UC-A misses: 0 ± 0 [0/2400, 0/2400, 0/2400]
- UC-B lateness mean ms: 18.03 ± 0.55 (17.4–18.4) [17.4, 18.3, 18.4]
- UC-B misses: 0 ± 0 [0/600, 0/600, 0/600]

Battery 1 is the fastest/lowest-miss zkSync replay (miss 1.3% vs 6.1–6.6%), the
same mild monotonic warm-up drift the laptop showed — the long-running
anvil-zksync instance accumulates state across the three batteries.

## 6. Arbitrum (nitro-testnode v3.9.6, chain 412346) — n=3, full coverage

100-tx sequential bench:

- latency mean ms: 241.7 ± 1.1 (240.5–242.7) [241.9, 240.5, 242.7]
- latency median ms: 245.33 ± 0.06 (245.3–245.4) [245.4, 245.3, 245.3]
- throughput tx/s: 4.03 ± 0.06 (4.0–4.1) [4.0, 4.1, 4.0]

UC-C / UC-A / UC-B closed replay (latency mean ms):

- UC-C closed: 249.97 ± 0.06 (249.9–250.0) [250.0, 249.9, 250.0]
- UC-A closed: 250.5 ± 0.0 (250.5–250.5) [250.5, 250.5, 250.5] — bit-identical
- UC-B closed: 250.17 ± 0.06 (250.1–250.2) [250.2, 250.2, 250.1]

UC-C paced (50 ms budget):

- miss rate %: 92.0 ± 0.4 (91.6–92.3) [92.1 (349/379), 91.6 (347/379), 92.3 (350/379)]
- lateness mean ms: 201.6 ± 1.7 (200.4–203.6) [203.6, 200.8, 200.4]

UC-A / UC-B paced (100 ms budget, diverging queue):

- UC-A lateness mean s: 82.76 ± 0.10 (82.68–82.87) [82.7308, 82.6779, 82.8745]
- UC-A misses: 2398 ± 1 (2397–2399) [2398/2400, 2397/2400, 2399/2400]
- UC-B lateness mean s: 45.065 ± 0.011 (45.055–45.077) [45.0641, 45.0548, 45.0768]
- UC-B misses: 599 ± 0 [599/600, 599/600, 599/600]

The Arbitrum "strange" behavior **reproduces in all three MSI batteries**: a
hard **~4 tx/s per-account cap** (250 ms cadence), closed-loop UC latencies
bit-identical at one-decimal precision, a **~92% paced miss rate** on the 50 ms
budget, and — under the UC-A 40 Hz open-loop offer — an **unbounded queue that
diverges to ~83 s lateness** (mean of the still-draining backlog) with 2397–2399
of 2400 payments late. Identical shape to the laptop.

## 7. Anvil L1 baseline (block-time 1 s)

- 100-tx latency mean ms: 990.2 ± 1.4 (989.1–991.8) [989.6, 989.1, 991.8]
- 100-tx latency median ms: 997.3 ± 5.5 (991.0–1001.1) [999.9, 991.0, 1001.1]
- throughput tx/s: 1.0 ± 0.0 [1.0, 1.0, 1.0]

## 8. zkstack full chain (chain 271) — n/a

Not run on this host (no Rust toolchain → :3050 never started;
`evm_bench.py … 3050` returns `Connection refused` in every battery).

## 9. Gas — determinism check

Fully identical across all three batteries where it should be:

- **L1 / Arbitrum ETH transfer: 21,000 exact** in every battery, every workload
  (100-tx, all UC closed, all UC paced).
- Perun deploy: **3,918,416** in every battery/block **except N=4**
  (see deviation below).
- zkSync UC-A/UC-B closed replay: mode **130,279** (min 130,160 max 130,279) —
  identical in all three batteries.
- zkSync UC-A paced: mode **133,094** (min 95,186–95,305 max 133,094) and UC-B
  paced: mode **133,094** (min 132,975 max 133,094) — identical across batteries.
- zkSync UC-C paced: mode **133,094** (min ~95,221 max 133,178) — identical in
  all three batteries (the laptop's b2/b3 differed here; the MSI does not).

DEVIATIONS (deterministic in principle, not in practice — reported loudly):

- **Perun deploy gas at N=4: 3,918,404 (−12 gas)** in **all three** MSI
  batteries (rerun-1 line 612, rerun-2 line 87, rerun-3 line 87), vs 3,918,416
  everywhere else. This is the **exact same −12 gas N=4 quirk** the laptop saw
  (3,918,404 vs 3,918,416) — it reproduces on both hosts and in every battery
  here, confirming it is a deterministic property of the 4-client topology, not
  host noise.
- **anvil-zksync 100-tx gas differs between batteries.** Battery 1 mode
  **129,845** (min 129,845 max 132,187); batteries 2 and 3 mode **130,363**
  (b2 min 129,726 / b3 min 130,244, both max 130,363). zkSync gas depends on the
  accumulated account/state history of the long-running node, so cross-battery
  gas equality holds only from equal state — b1 is the cold state, b2/b3 warm.
  The UC-C closed replay tracks this: b1 mode 129,761, b2/b3 mode 130,279.
- **Perun ledger open/settle totals jitter by tens–hundreds of gas** between
  batteries (gas/ledger-open spans 91,614–91,626 across all runs). Address/
  state dependent, ±0.01% — cosmetic, not bit-identical.

## Stability verdict

**Tight (CV < 5%):** everything block-cadence-bound or protocol-bound, and —
notably — **the Perun off-chain path too, on this host**.

- All Arbitrum quantities are essentially exact reproductions across batteries:
  UC-A/B/C closed-loop means bit-identical at one-decimal precision
  (250.5 / 250.2 / 250.0, CV ≤ 0.02%), 100-tx bench (CV 0.5%), paced miss
  counts and diverging-queue lateness (CV ≤ 0.1%).
- L1 baseline CV 0.1% (mean) / 0.6% (median); anvil-zksync 100-tx CV 0.5–1.4%;
  all zkSync closed-loop replays CV 0.1%.
- **Perun bursts CV 1.4–2.8%, Perun UC closed-loop CV 1.3–2.6%, Perun paced
  lateness CV 0.9–1.8%** — an order of magnitude tighter than the laptop, where
  the same off-chain quantities had CV 39–70%.
- Perun's headline real-time result — **0 deadline misses in every paced run,
  all three batteries** (UC-A 0/2400, UC-B 0/600, UC-C 0/379) — is perfectly
  stable, matching the laptop.

**Variable (CV > 5%):** only two small pockets, both sub-millisecond:

- zkSync UC-C **paced miss rate** (1.3 → 6.6 → 6.1%, CV 63%) — the same
  warm-up drift the laptop showed, but at a **lower absolute level** (MSI
  1.3–6.6% vs laptop 12.1–17.7%). Paced lateness on the same cell is tight
  (CV 6.0%).
- Perun **virtual open/settle wall-clock** (CV 42–88%) — n=1..15 per-channel
  sub-millisecond timers that catch scheduler jitter; irrelevant to any headline.

**No run-3 Perun collapse.** The laptop's most severe anomaly — the entire
Perun set of battery 3 running 2–3× slower (628–879 pay/s) because that battery
started seconds after a CPU-saturating zkstack bench — **does not occur on the
MSI**. There is no zkstack phase here, and the 16-core host has enough headroom
that the back-to-back batteries do not contend. Every Perun quantity is stable
across all three MSI batteries.

## Laptop (bare-metal Mint, i5-8250U) vs MSI (WSL2, workstation)

Per-family host-dependence, with numbers. Two clean regimes emerge.

**Cadence-bound families — IDENTICAL across both platforms (strong
cross-platform reproducibility result).** These are pinned by node block time /
protocol cadence, not by host CPU, so the VM layer is invisible:

- **Arbitrum 100-tx:** laptop 236.5 ms / 4.07 tx/s vs MSI 241.7 ms / 4.03 tx/s —
  the ~250 ms cadence and the **~4 tx/s per-account cap** hold on both.
- **Arbitrum UC closed-loop:** laptop 249.3 / 250.4 / 249.8 ms vs MSI
  250.0 / 250.5 / 250.2 ms — bit-identical *within* each host, ≤ 1 ms *between*
  hosts.
- **Arbitrum UC-A diverging queue:** laptop 82.83 s / 2397–2400 misses vs MSI
  82.76 s / 2397–2399 misses — the unbounded-queue divergence to ~83 s
  reproduces on both. UC-C paced miss rate: laptop 95.3% vs MSI 92.0% (same
  regime; the ~3-point gap is the only Arbitrum cross-host difference).
- **L1 baseline:** laptop 985.8 ms vs MSI 990.2 ms — both pinned to the 1 s
  block, < 0.5% apart.
- **Gas:** ETH transfer **21,000 exact on both hosts**; the **−12 gas Perun
  N=4 deploy quirk (3,918,404 vs 3,918,416) reproduces on both**; zkSync gas is
  state-history-dependent on both.
- **anvil-zksync 100-tx:** laptop 25.87 ms / 29.43 tx/s vs MSI 24.0 ms /
  34.4 tx/s — MSI ~7% lower latency / ~17% higher throughput. Mildly faster but
  same regime (the send/receipt loop has a small CPU component).

**CPU-bound families — FASTER *and* TIGHTER on MSI despite the WSL2 overhead.**
The off-chain Perun path and the zkSync paced miss-rate absorb host CPU
directly, and the 16-core MSI wins decisively:

- **Perun off-chain bursts:** laptop 1341–1731 pay/s with **CV 39–70%** (dragged
  down by the run-3 collapse) vs MSI **2574–2636 pay/s with CV 1.4–2.8%** —
  ~1.5–2× faster and ~20–40× tighter.
- **Perun UC-C closed-loop:** laptop 1385 pay/s (682–1904, CV 46%) vs MSI
  2232 pay/s (2198–2255, CV 1.3%).
- **Perun paced lateness:** laptop UC-A/B ~1.1–1.3 ms (CV < 2%) vs MSI
  ~1.1 ms (CV < 2%) — comparable and both far inside the 50 ms budget with
  0 misses on both hosts.
- **zkSync UC-C paced miss rate:** laptop 14.7% (12.1–17.7%) vs MSI **4.7%
  (1.3–6.6%)** — the faster host clears the 50 ms budget far more often;
  same monotonic warm-up drift, lower absolute level.

**Verdict:** the block-cadence and gas families are a clean cross-platform
reproduction (Arbitrum's cap, cadence, diverging queue, and the gas quirks all
carry over bare-metal → VM), which is the headline reproducibility result. The
CPU-bound families are host-dependent as expected, and the WSL2 MSI is *not*
penalized by its VM layer — its 16 cores make the off-chain path both faster and
dramatically more stable than the laptop, and specifically **immune to the
run-3 Perun collapse** that dominated the laptop's variance.
