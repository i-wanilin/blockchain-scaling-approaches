# Spike results (June–August 2026) — archived, not thesis results

These are measurements from the **exploratory spike** that preceded the current
design. They are kept because they are real numbers that informed decisions, and
because the reasoning is worth citing in the methodology chapter. They are **not**
the thesis results and must not be quoted as such.


## Use-case labels in this archive

The spike used a **three** use-case scheme; the thesis uses **two** (UC1 = IoT
micropayments, UC2 = on-chain token gaming). The labels below are the spike's and are
left **unchanged on purpose** — including the `RESULTS-ucab.md` filename and the trace
paths recorded inside `lan-results/*.json`, which are measurement provenance, not prose.

| Archive label | What it was | Maps to |
|---|---|---|
| `UC-A` | truck platooning | **nothing** — the use case was dropped (see defect 6) |
| `UC-B` | synthetic 10 Hz PMU stream | **not UC1** — superseded by the GESL dataset; `pmu.py` was dropped precisely because it synthesises a cadence GESL does not have |
| `UC-C` | tank-arena game trace | **UC2** |

## What these numbers are

Closed-loop and paced replay of a synthetic tank-game trace and synthetic UC-A/UC-B
workloads against Perun (go-perun v0.15.0 + eth-backend v0.6.0 on Anvil), Arbitrum
(nitro-testnode v3.9.6) and zkSync Era (anvil-zksync v0.6.11), on single hosts and
across a two-user Wi-Fi LAN.

The headline figures, kept here because the *ceilings* they establish are what the
v2 load ladder is calibrated against:

| Family | Throughput ceiling | Per-transfer latency |
|---|---:|---:|
| Perun (off-chain) | ~1,900–2,400 /s | ~0.5 ms |
| zkSync Era | ~9 /s | ~112 ms |
| Arbitrum | ~4 /s | ~249 ms |

## Known defects — the reasons these are not results

1. **The cross-family latency comparison is not like-for-like.** Perun's ~0.5 ms is a
   signed off-chain state update between two local processes; Arbitrum's ~249 ms is
   the testnode sequencer's ~250 ms block cadence. These are largely the *block-time
   constants of three local dev-nodes*, not properties of the three families.
2. **An unresolved anomaly was quoted anyway.** `RESULTS.md` records zkSync at 25.7 ms
   single-account vs 111.7 ms multi-account with the note "investigate before
   quoting"; it was then quoted in the cross-family table.
3. **Binary provenance defect** in the 2026-07-28 LAN campaign — the two clients ran
   binaries built from different commits (see `RESULTS-lan-two-user-2026-07-28.md`).
   Measurements unaffected; the reproducibility claim is not.
4. **n = 2 repeats** in the LAN campaign.
5. **Measured over Wi-Fi**, whose jitter is the same order as the zkSync figure and
   far larger than the Perun figure.
6. **UC-A (platooning) was subsequently dropped** from the thesis, so a large share of
   `RESULTS-ucab.md` covers a use case that no longer exists.

## What carried forward instead

The measurement *discipline* rather than the numbers: recorded environment captures,
per-run provenance guards (`summarize_perun_lan.py` refusing to summarize a bundle
whose client commits differ — which is how defect 3 was found), and dispersion
reporting as mean ± sd over repeats.
