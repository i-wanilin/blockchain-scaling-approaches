# archive/

Material that is no longer part of the measurement path, kept for the record.
Nothing in `deploy/cluster`, `measure/` or `tank-arena/` reads anything here;
the scripts are kept as they were and may not run from their new location.
Commit hashes and branch names in these files (`main`, `v2`) come from the
history of the work and are not part of this copy.

| Path | What it is | Why archived | Replaced by |
|---|---|---|---|
| `ansible-spike/` | The July 2026 spike's ansible tree (inventory for `pi1`..`pi6`, `laptop_ip`; roles common, golang, netem, player-node, time-sync) | Never run on the bath cluster; its time-sync role would break bath1's time server (see its README) | Boards set up by hand (`deploy/README.md`); the installer of cluster spec §3 is future work |
| `deploy-legacy/measure-baseline.sh` | Network/clock baseline of the old six-Pi laptop layout (hosts from `ansible/inventory.ini`) | Built for the spike layout, never adapted to the bath cluster | `deploy/cluster preflight` / `status` and `deploy/board-facts.sh` |
| `deploy-legacy/build-arm64.sh` | Cross-compile of the hub demo and download of arm64 foundry binaries for the spike | Unversioned build, no provenance record | `build_perun` in `deploy/cluster` (run by `deploy/cluster sync`: committed `perun/` tree, hash and toolchain recorded) |
| `workstation-setup/Makefile`, `workstation-setup/setup.sh` | Rebuilt the June 2026 WSL workstation environment: anvil-zksync, nitro-testnode, go-perun / perun-eth-backend / perun-examples clones at pinned versions | That single-machine environment is not what was measured; the thesis ran on the Pi cluster | The cluster stacks on bath0 (`deploy/README.md`, `zksync-setup/`) and the repo venv from `measure/requirements.txt` |
| `history/report-2026-10-13.md` | Status report of 2026-10-13: the setup, both use cases step by step, the results at that date (formerly in `docs/`) | Superseded by the final analysis | `analysis/data/results.csv`, the cluster spec |
| `history/STATUS-2026-06-23-workstation-install.md` | The June 2026 status log of the workstation install | Describes the superseded workstation environment (it was the root `STATUS.md`) | The top-level `README.md` and `results/pi/README.md` |
| `plans/` | The four executed implementation plans (UC1 Arbitrum, UC1 Perun, UC1 zkSync measurement paths; UC2 game on three systems) | Executed; kept as the record of how the code was built | The code itself and the cluster spec |
| `bench-workstation/` (formerly `bench/`) | The September 2026 workstation probes: the throughput-ceiling probe, zkSync checks, and their raw output; two notes from the cluster work (`results/zksync-pi-cpu-2026-09-30.md`, `results/zksync-uc1-facts-2026-10-01.md`) cited by the cluster spec | x86 workstation numbers, not thesis results; the Pi capacity probes are future work | `results/pi/` (index `results/pi/README.md`); cluster spec §7.1, §11 |
| `spike-results/` (moved from the top level 2026-10-17) | The June–August 2026 exploratory spike's measurements and logs | Its own README calls it archived: real numbers that informed decisions and the method chapter, not thesis results | `results/pi/`, the cluster spec §8 |
| `../deploy/experiments/archive/` | Superseded UC1 experiment files (6-customer and 5-customer series with bath3, Wi-Fi diagnostics, a zkSync capacity probe), each used by committed runs; each has an `# ARCHIVED` first line. Three diagnostic files (`diag-arbitrum5-w10-oldcode`, `diag-arbitrum5-w10-oldsensor`, `diag-zksync-w1000-no7`) survive only as `experiment.env` in their run folders | bath3 died on 2026-10-05; the final series changed board set and Wi-Fi setup | The 17 final files in `deploy/experiments/` (`uc1-*5mx7-*`, `uc2-*`); run folders keep their own `experiment.env` copies |
