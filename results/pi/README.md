# Results on the Pi cluster: what is final, what is superseded

Every run folder is named `<date>-<time>-<stack>-<experiment>-rep<k>` and holds its own copy of the
experiment file (`experiment.env`), `manifest.json` (commit, code hash, boards, Wi-Fi, clocks, images),
`summary.json` (from `measure.analyze_uc1` / `analyze_uc2`), the raw ledgers, logs and recorder samples.
Design and discussion of every number: the cluster spec,
[`docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md`](../../docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md) §8.

## Final thesis data

Board set: `bath0` (Pi 4, the stack), `bath1` (sensor / host + Perun hub, time server), customers or
players `bath2`, `bath4`–`bath7`; `bath3` dead and idle. Mixed Wi-Fi: `bath1`, `bath2` on 5 GHz, the
rest on 2.4 GHz. All summaries were analysed with one version of the analysis code (each `summary.json` records it as `analysis_commit`).

| Use case | Runs | Aggregates (mean ± sd, n = 3) |
|---|---|---|
| UC1, 15-min runs, windows 1 s / 100 ms / 10 ms (spec §8 step 3, *Final UC1 series*) | `uc1/2026101[12]-*-uc1-{perun,arbitrum,zksync}5mx7-w{1000,100,10}-rep*`: 27 complete runs. zkSync `w1000` = `20261011-215439`, `20261011-222529`, `20261012-033531` (the last replaces `20261011-230326`, failed: `bath1` rebooted mid-run; kept, `complete: false`) | `uc1/aggregate-{perun,arbitrum,zksync}5mx7-w{1000,100,10}.txt` |
| UC1 smokes (3 min, `w10`; did `bath7` keep up) | `uc1/20261011-133026-…-arbitrum5mx7-smoke-rep1`, `uc1/20261011-134345-…-perun5mx7-smoke-rep1` | — |
| UC2, 60-s matches, seed 42 (spec §8 step 6, *UC2 series*) | `uc2/20261006-*-{perun,arbitrum,zksync}-uc2-{perun,arbitrum,zksync}-rep{1,2,3}`: 9 complete runs, every board's event log = the reference | `uc2/aggregate-uc2-{perun,arbitrum,zksync}.txt` |
| UC2 smokes | `uc2/20261006-*-uc2-*-smoke-rep1` (3) | — |
| Wi-Fi baseline (no chain running; spec §8 step 3) | `baseline/20261012-171149-wifi-baseline/` (the `bath4` → `bath1` pair from `baseline/20261012-173455-wifi-baseline/`: `bath1` rebooted during the first) | `summary.json` in each folder |
| zkSync's L1 alone (reth, empty blocks, no zkSync server), 300 ms and 1 s block time, 5-min averages | `reth-idle-cpu-2026-10-29.txt` (`deploy/reth-idle-cpu.sh`) | whole board 0.10 % idle, 2.47 % at 300 ms, 0.89 % at 1 s |

Re-create any aggregate from the run folders: `.venv/bin/python -m measure.aggregate <run dirs>`.

## Superseded (kept as the record; not thesis numbers)

| Experiment names | Dates | Why superseded |
|---|---|---|
| `uc1-arbitrum-*`, `uc1-zksync-smoke`, `uc1-zksync-w1000` (6 customers `bath2`–`bath7`) | 2026-09-30 – 10-01 | `bath7` (undervolted, 11 Mbit/s link) was dropped by the sensor; Arbitrum `w10` collapsed on the shared 2.4 GHz channel. Moved to 5 customers. Aggregates: `uc1/aggregate-arbitrum-w*.txt`. |
| `uc1-perun-*`, `uc1-arbitrum5-*`, `uc1-zksync5-*`, `uc1-arbitrum5own-w10` (5 customers with `bath3`, all 2.4 GHz) | 2026-10-02 – 10-04 | Arbitrum `w10` not measurable on all-2.4 GHz Wi-Fi; replaced by the mixed-Wi-Fi final series after `bath3` died. Aggregates: `uc1/aggregate-{perun,arbitrum5,zksync5}-w*.txt`. |
| `diag-*` | 2026-10-02 – 10-05 | Diagnostics of the Wi-Fi collapse (old code, old sensor, 5 GHz, own receipts, no `bath7`). |
| `probe-zksync5-w100-if200` | 2026-10-04 | U9 probe: 200 in flight per account gave no more zkSync throughput (the chain's limit under that load). |
| `idle-temps-2026-09-30.txt` | 2026-09-30 | Replaced by `idle-temps.txt` (2026-10-01), the cooling-gate reference of all final runs. |

The interrupted mixed-Wi-Fi series with `bath3` (2026-10-05, 7 runs) is not in the
repository. The experiment files of the superseded series are in
`deploy/experiments/archive/`, except three diagnostic files (`diag-arbitrum5-w10-oldcode`,
`diag-arbitrum5-w10-oldsensor`, `diag-zksync-w1000-no7`), which survive only as
`experiment.env` in their run folders.
