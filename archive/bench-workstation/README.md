# archive/bench-workstation/ — pre-cluster workstation probes (formerly `bench/`)

> **OUTDATED** — tools and their raw output from the x86 workstation (September 2026),
> before the Raspberry Pi cluster. **Not thesis results.** The thesis numbers are in
> `results/pi/` (index: [`results/pi/README.md`](../../results/pi/README.md)). The capacity
> probes on the Pi cluster are future work (cluster spec §7.1, §11).

| File | What it is |
|---|---|
| `probe_ceiling.py` | Throughput-ceiling probe: sweeps sender accounts, in-flight depth and processes against one RPC endpoint (Arbitrum, zkSync) |
| `fund_accounts.py` | Funds the probe's test accounts |
| `zksync_sanity.py` | Checks the assumptions the throughput probe relies on, which differ between zkSync's EraVM and Arbitrum's EVM, before a sweep is trusted |
| `zksync_uc1_probe.py` | Fact checks F1–F12 for the zkSync UC1 path (balances, chain ids, error texts, …); results in `results/zksync-uc1-facts-2026-10-01.md` |
| `zksync_footprint.sh` | zkSync's memory and CPU footprint, to decide whether it fits a Pi 4 (its header's Arbitrum remark is outdated: Arbitrum ran on the Pi 4) |
| `results/` | Raw output of these probes (`*.txt`), with summaries in `results/README.md` (Arbitrum) and `results/README-zksync.md` |

Two files in `results/` are notes from the cluster work, not probe output:
`zksync-pi-cpu-2026-09-30.md` and `zksync-uc1-facts-2026-10-01.md` (facts behind the
zkSync UC1 path, cited by the cluster spec).
