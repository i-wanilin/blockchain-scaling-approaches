# 3-run dispersion statistics — mean ± sample sd (n−1), min–max, per-run values

Aggregated 2026-07-20. Runs:

- **Run 1** — original campaign 2026-07-18/19: `RESULTS.md`, `RESULTS-ucab.md`,
  `RESULTS-perun-phase2.md`, `results-ucab-runs.log`, `RESULTS-zkstack.md` (2026-07-20),
  `../emulation/RESULTS-netem.md` (raw: `../emulation/results/netem-20260719-143135/`).
- **Run 2** — `rerun-battery.sh 2`, 2026-07-20 13:20–14:05: `rerun-2.log`
  (+ the Perun set, which the script wrote to `../perun/hub-demo/rerun-2.log`
  because `run()` uses a log path relative to the pushd'd cwd).
- **Run 3** — `rerun-battery.sh 3`, started 2026-07-20 14:05, RESUMED 18:45,
  done 19:15: `rerun-3.log` (+ `../perun/hub-demo/rerun-3.log`). The pre-resume
  "UC-B zks closed run3" attempt produced no output (truncated at the pause);
  the post-resume completion is used.
- Netem runs 2/3: `../emulation/results/netem-20260720-191539/` and
  `netem-20260720-192333/`.

Format: `name: mean ± sd (min–max) [run1, run2, run3]`. All per-run values are
verbatim from the source files.

## 1. Perun hub — synthetic bursts, 500 payments (N = clients)

Run-1 asymmetries: N=3 in run 1 was a **200-payment** burst (still included);
run 1 has **no N=5 burst** (RESULTS.md lists clients 2/3/4/6 only) — N=5 stats
are over runs 2–3 (n=2).

- N=2 payments/s: 1341 ± 681 (628–1985) [1409, 1985, 628]
- N=2 ms/payment: 0.935 ± 0.577 (0.504–1.591) [0.710, 0.504, 1.591]
- N=3 payments/s: 1497 ± 582 (879–2035) [1578 (200-payment burst), 2035, 879]
- N=3 ms/payment: 0.754 ± 0.340 (0.491–1.138) [0.634, 0.491, 1.138]
- N=4 payments/s: 1731 ± 881 (735–2407) [2407, 2050, 735]
- N=4 ms/payment: 0.754 ± 0.526 (0.415–1.360) [0.415, 0.488, 1.360]
- N=5 payments/s: 1453 ± 842 (858–2048) [run 1 missing, 2048, 858] — n=2
- N=5 ms/payment: 0.827 ± 0.479 (0.488–1.165) [run 1 missing, 0.488, 1.165] — n=2
- N=6 payments/s: 1678 ± 849 (722–2346) [2346, 1966, 722]
- N=6 ms/payment: 0.773 ± 0.531 (0.426–1.385) [0.426, 0.509, 1.385]

Anomaly: the **entire Perun set of run 3 is ~2–3× slower** (628–879 pay/s vs
~2000 in run 2). Battery 3's Perun set started at 14:05:33, seconds after
battery 2's zkstack bench (which bursts the CPU to ~6 cores) finished — CPU
contention/thermal throttling on the i5-8250U is the plausible cause. The
off-chain path is CPU-bound, so Perun absorbs host noise directly; the EVM
families (block-cadence-bound) do not.

## 2. Perun ledger / virtual channel wall-clock

Run 1 = phase-2 Run 1 default-mode block (`RESULTS-perun-phase2.md`, n=6/15);
runs 2/3 = the `-clients 6 -payments 500` block of the rerun logs.

- ledger open mean s: 1.034 ± 0.008 (1.0248–1.0409) [1.0248, 1.0409, 1.0349]
- ledger close mean s: 2.067 ± 0.077 (2.0160–2.1558) [2.1558, 2.0160, 2.0283]
- virtual open mean ms: 7.2 ± 4.4 (2.6–11.4) [11.4, 2.6, 7.7]
- virtual settle mean ms: 6.9 ± 2.8 (3.6–8.7) [8.3, 3.6, 8.7]

Ledger open/close are pinned by Anvil's 1 s block time in every run; the
virtual-channel millisecond figures jitter with host load but stay 2 orders of
magnitude below the on-chain path in all three runs.

## 3. Perun UC-C game trace (379 transfers)

Closed-loop (run 1 = RESULTS.md headline; note the same campaign also contains
a second measurement in RESULTS-perun-phase2.md Run 2 — 590 payments/s, mean
1.693 ms — i.e. the intra-campaign spread was already of the same magnitude as
the cross-run spread):

- closed-loop payments/s: 1385 ± 631 (682–1904) [1904, 1568, 682]
- closed-loop latency mean µs: 876 ± 515 (525–1467) [525, 637, 1467]

Paced (open-loop, 50 ms budget):

- lateness mean ms: 2.31 ± 0.85 (1.364–2.997) [2.997, 1.364, 2.579]
- deadline misses: 0 ± 0 (0–0) [0/379, 0/379, 0/379] — perfectly stable

## 4. Perun UC-A / UC-B

- UC-A closed-loop payments/s: 2022 ± 289 (1726–2303) [2303, 2037, 1726]
- UC-B closed-loop payments/s: 2049 ± 106 (1946–2158) [2158, 2042, 1946]
- UC-A paced lateness mean ms: 1.330 ± 0.023 (1.303–1.346) [1.34, 1.303, 1.346]
- UC-A paced misses: 0 ± 0 [0/2400, 0/2400, 0/2400]
- UC-B paced lateness mean ms: 1.122 ± 0.017 (1.107–1.140) [1.14, 1.107, 1.118]
- UC-B paced misses: 0 ± 0 [0/600, 0/600, 0/600]

## 5. anvil-zksync (v0.6.11, chain 260)

100-tx sequential bench:

- latency mean ms: 25.87 ± 0.15 (25.7–26.0) [25.7, 26.0, 25.9]
- latency median ms: 25.57 ± 0.49 (25.0–25.9) [25.0, 25.9, 25.8]
- throughput tx/s: 29.43 ± 0.15 (29.3–29.6) [29.6, 29.4, 29.3]

UC-C closed replay:

- latency mean ms: 113.3 ± 1.4 (111.7–114.1) [111.7, 114.0, 114.1]

UC-C paced (50 ms budget):

- miss rate %: 14.7 ± 2.8 (12.1–17.7) [12.1 (46/379), 14.2 (54/379), 17.7 (67/379)]
- lateness mean ms: 32.3 ± 2.7 (29.4–34.7) [29.4, 32.9, 34.7]

UC-A / UC-B paced (100 ms budget):

- UC-A lateness mean ms: 46.5 ± 4.2 (41.7–49.7) [41.7, 48.0, 49.7]
- UC-A misses: 0 ± 0 [0/2400, 0/2400, 0/2400]
- UC-B lateness mean ms: 21.9 ± 2.1 (19.5–23.6) [19.5, 23.6, 22.6]
- UC-B misses: 0 ± 0 [0/600, 0/600, 0/600]

UC-A / UC-B closed replay:

- UC-A latency mean ms: 113.3 ± 1.2 (111.9–114.1) [111.9, 113.9, 114.1]
- UC-B latency mean ms: 113.3 ± 1.2 (112.0–114.1) [112.0, 113.9, 114.1]

Run 1 is consistently the fastest zkSync multi-account replay (~2 ms) and the
lowest paced miss rate — the node accumulated state across the three batteries
(same long-running instance), a mild monotonic drift, not noise.

## 6. Arbitrum (nitro-testnode v3.9.6, chain 412346)

100-tx sequential bench:

- latency mean ms: 236.5 ± 0.8 (235.6–237.0) [237.0, 235.6, 237.0]
- latency median ms: 240.9 ± 1.4 (239.7–242.4) [242.4, 240.6, 239.7]
- throughput tx/s: 4.07 ± 0.06 (4.0–4.1) [4.1, 4.1, 4.0]

UC-C closed replay:

- latency mean ms: 249.3 ± 0.0 (249.3–249.3) [249.3, 249.3, 249.3] — identical to 0.1 ms

UC-C paced (50 ms budget):

- miss rate %: 95.3 ± 0.6 (94.7–95.8) [95.5 (362/379), 94.7 (359/379), 95.8 (363/379)]
- lateness mean ms: 214.2 ± 3.8 (210–217.3) [210, 215.4, 217.3]

UC-A / UC-B paced (100 ms budget, diverging queue):

- UC-A lateness mean s: 82.83 ± 0.08 (82.76–82.91) [82.9081, 82.8131, 82.7594]
- UC-A misses: 2398.3 ± 1.5 (2397–2400) [2400/2400, 2398/2400, 2397/2400]
- UC-B lateness mean s: 44.880 ± 0.007 (44.873–44.887) [44.8867, 44.8802, 44.8733]
- UC-B misses: 597 ± 0 [597/600, 597/600, 597/600]

UC-A / UC-B closed replay:

- UC-A latency mean ms: 250.4 ± 0.0 [250.4, 250.4, 250.4] — identical
- UC-B latency mean ms: 249.8 ± 0.0 [249.8, 249.8, 249.8] — identical

## 7. Anvil L1 baseline (block-time 1 s)

- 100-tx latency mean ms: 985.8 ± 0.6 (985.2–986.2) [986.2, 985.2, 986.1]
- throughput tx/s: 1.0 ± 0.0 [1.0, 1.0, 1.0]

## 8. zkstack full chain (chain 271, no proofs)

- 100-tx latency mean ms: 3974 ± 149 (3835–4131) [4130.5, 3834.7, 3956.6]
- 100-tx latency median ms: 3993 ± 76 (3941–4080) [4079.6, 3940.5, 3958.7]
- throughput tx/s: 0.27 ± 0.06 (0.2–0.3) [0.2, 0.3, 0.3]

Within-run spread is huge in every run (run 2 p10 1266 / p90 5716; run 3 min
107.8 / max 8892) — the state-keeper's batch sealing makes individual-tx
latency inherently load-sensitive; only mean/median are comparable.

## 9. Gas — determinism check

Fully identical across all three runs:

- L1 / Arbitrum ETH transfer: **21,000 exact** in every run, every workload.
- Perun deploy: 3,918,416 gas in run 1 and in every rerun block **except N=4**
  (see deviation below).
- zkSync UC-A/UC-B closed replay: mode 130,279 (min 130,160 max 130,279) —
  identical in all three runs.
- zkSync UC-A paced: mode 133,094 (min 95,305 max 133,094) and UC-B paced:
  mode 133,094 (min 132,975 max 133,094) — identical in all three runs.
- zkstack transfer max: 128,704 in runs 2 and 3.

DEVIATIONS (deterministic in principle, not in practice — reported loudly):

- **Perun deploy gas at N=4: 3,918,404 (−12 gas)** in both rerun batteries
  (rerun-2 and rerun-3, `-clients 4` blocks), vs 3,918,416 everywhere else.
  Run 1 quoted only the single 3,918,416 figure.
- **Perun ledger open/settle totals jitter by tens–hundreds of gas** between
  runs (e.g. N=6 settle 1,437,060 [run 1] / 1,437,008 [run 2] / 1,437,056
  [run 3]; gas/ledger-open spans 91,617–91,626 across all runs). Address/state
  dependent, ±0.01 % — cosmetic but not bit-identical.
- **anvil-zksync 100-tx gas: run 3 differs.** Runs 1 and 2 are identical
  (129,845 mode, min 129,845 max 132,187); run 3 reports mode 130,363
  (min 129,726 max 130,363). Same for UC-C closed replay: runs 1/2 mode
  129,761 (min 129,642 max 132,103), run 3 mode 130,279 (min 129,761 max
  130,363). zkSync gas depends on accumulated account/state history of the
  long-running node, so cross-run gas equality holds only from equal state.
- **zkSync UC-C paced gas differs between reruns**: run 2 mode 132,576
  (min 100,949 max 133,178) vs run 3 mode 133,094 (min 101,467 max 133,178);
  run 1 did not record gas for this cell.
- **zkstack transfer gas: run 1 mode 128,186 vs runs 2/3 mode 128,704**, and
  the rerun minima differ (83,905 vs 84,423) — same state-history effect as
  anvil-zksync, amplified.

## 10. Netem matrix (closed-loop UC-C, per-tx latency MEDIANS, ms)

Run 1 = `../emulation/RESULTS-netem.md` (warm baseline); runs 2/3 = the
per-cell logs in `netem-20260720-191539/` and `netem-20260720-192333/`.

- zkSync baseline (none): 119.0 ± 9.2 (112.5–129.5) [112.5, 129.5, 115.1]
- zkSync wifi: 128.2 ± 0.9 (127.2–129.0) [127.2, 128.3, 129.0]
- zkSync lte: 174.7 ± 1.2 (173.9–176.0) [173.9, 176.0, 174.1]
- Arbitrum baseline (none): 250.57 ± 0.06 (250.5–250.6) [250.6, 250.5, 250.6]
- Arbitrum wifi: 250.63 ± 0.06 (250.6–250.7) [250.7, 250.6, 250.6]
- Arbitrum lte: 250.17 ± 0.23 (249.9–250.3) [250.3, 250.3, 249.9]

Measured RTTs (ping avg, ms; egress-only shaping → RTT ≈ one-way profile delay):

- none/zksync: 0.045 ± 0.017 (0.030–0.063) [0.063, 0.043, 0.030]
- none/arbitrum: 0.036 ± 0.006 (0.029–0.040) [0.038, 0.040, 0.029]
- wifi/zksync: 3.20 ± 0.19 (3.078–3.42) [3.42, 3.078, 3.091]
- wifi/arbitrum: 3.57 ± 0.28 (3.383–3.89) [3.89, 3.383, 3.442]
- lte/zksync: 49.6 ± 1.3 (48.382–51.0) [51.0, 48.382, 49.545]
- lte/arbitrum: 46.6 ± 1.5 (45.587–48.4) [48.4, 45.587, 45.900]

Anomaly: the run-2 zkSync baseline median (129.5) is a **cold-start artifact** —
each netem matrix starts a fresh `netem-zksync` container and `none/zksync` is
its first cell. Run 1 saw the same effect (first cold pass mean 119.8, warm
re-pass 112.8 — RESULTS-netem.md anomaly 2) and reported the warm value; the
run-2/3 matrices did not repeat the baseline cell. The shaped cells (wifi/lte)
run against an already-warm node and are tight.

## Stability verdict

**Tight (CV < 5%):** everything block-cadence-bound or protocol-bound. All
Arbitrum quantities are essentially exact reproductions — UC-A/B/C closed-loop
means are bit-identical at one-decimal precision (249.3/250.4/249.8 in all
three runs, CV 0.0%), the 100-tx bench, paced lateness/miss counts, and all
netem medians (CV ≤ 0.1%). The L1 baseline (CV 0.06%) and the anvil-zksync
100-tx bench (CV 0.5–0.6%) are equally tight. zkSync closed-loop replays
(CV ~1.2%), Perun paced lateness on UC-A/UC-B (CV < 2%) and the Perun ledger
open/close wall-clocks (CV 0.8% / 3.7%) also reproduce well. Perun's headline
real-time result — **0 deadline misses in all paced runs, all three batteries**
— is perfectly stable. Netem shaped-cell medians (zkSync wifi/lte CV 0.7%) and
zkstack mean/median (CV 3.7% / 1.9%) round out the tight set.

**Variable:** everything CPU-bound. The Perun off-chain throughput numbers
(bursts, UC-C closed-loop) have CV 39–70%, driven almost entirely by run 3's
battery starting seconds after run 2's zkstack bench saturated the CPU —
sub-millisecond in-process payments amplify host noise ~1000× relative to the
block-bound families. Order-of-magnitude claims (10³ payments/s, sub-2 ms)
survive; exact throughput figures should be quoted as ranges. zkSync's paced
UC-C miss rate (12.1→17.7%, CV 19%) and paced lateness (CV 8–10%) drift
upward monotonically across batteries — consistent with the long-running
anvil-zksync instance accumulating state, plus host load.

**Anomalies:** (1) run-3 Perun global slowdown (CPU contention/thermal, see
§1); (2) cold-start effects on fresh anvil-zksync instances (~7–17 ms on the
first pass: netem run-2 baseline 129.5 vs warm 112.5/115.1, RESULTS-netem.md
anomaly 2); (3) zkstack per-tx latency is load-sensitive with a ~80× within-run
min–max spread, though its aggregate mean/median reproduce within 4%; (4) gas
is NOT fully deterministic across runs: EVM ETH-transfer gas (21,000) is exact
everywhere, but zkSync-family gas depends on node state history (run-3
anvil-zksync and run-1 zkstack modes differ) and Perun on-chain totals jitter
by ±0.01% with a reproducible −12 gas deploy quirk at N=4 (§9).
