# A Use-Case-Driven Comparison of Layer-2 Scaling Approaches for IoT Micropayments and On-Chain Gaming

Code, configuration and results of the bachelor's thesis of Ivan Ilin, Technische
Universität Berlin, Open Distributed Systems (ODS), 2026.

## Summary

Ethereum processes around 15 to 30 transactions per second and charges a fee for each of
them. For devices in the Internet of Things (IoT) and for games that pay small amounts many
times per second, the main chain is too slow and too expensive. Layer-2 protocols move such payments
off the main chain. The thesis compares one system from each of the main Layer-2 families
on the same two use cases:

- **Perun**, a state channel (go-perun, with Anvil as its L1);
- **Arbitrum Nitro**, an optimistic rollup;
- **zkSync Era**, a zero-knowledge (ZK) rollup, run without its prover.

The use cases:

- **UC1, IoT micropayments.** A sensor streams a real power-grid recording from the Grid
  Event Signature Library (GESL) to five customers for 15 minutes. Each customer pays once
  per window of 1000 ms, 100 ms or 10 ms, so the sensor receives 5, 50 or 500 payments per
  second in total.
- **UC2, on-chain gaming.** Six players pay for every shot and every hit in a deterministic
  60-second tank match: 1070 payments per match. The host (player p0) runs on the same
  board as Perun's hub; the other five players are the guests.

All runs took place on a cluster of Raspberry Pis connected by Wi-Fi. One Raspberry Pi 4
(`bath0`) runs the system under test with its own local L1, one system at a time. Six
Raspberry Pi 3 boards (`bath1`, `bath2`, `bath4` to `bath7`) act as the sensor, the
customers and the players.
A workstation starts the runs and collects the results; it is never in the measured path.
Each configuration ran three times.

We measure the latency of each lifecycle phase (Open, payment, Close), the throughput
(payments confirmed per second), the payment deadlines missed, and the gas used.

## Results

The numbers below are means of the three runs, rounded, from
[`analysis/data/results.csv`](analysis/data/results.csv), the table the thesis quotes. They
come from local development stacks on small boards: they show how the three approaches
compare on hardware of this size, not how much the public networks can carry.

**UC1, IoT micropayments**

- Perun confirmed payments fastest of the three systems. Its median payment latency was 40 ms with 1000 ms
  windows and 35 ms with 100 ms windows; Arbitrum's median was 92 ms and 244 ms at the same
  windows.
- At 50 payments per second (100 ms windows), Perun missed the 100 ms payment deadline
  for 0.5 % of the payments. Arbitrum, held back by its block cadence, missed it for 97 %.
- Perun needed no gas per payment. An Arbitrum payment used 21,000 gas.
- Opening Perun's channel to the sensor took 1.3 s; Arbitrum's Open took 1.6 s. When
  Perun's channel closed, the money was back on the L1 after 2.6 s. On a rollup, a
  withdrawal still waits for the challenge period (Arbitrum) or the security delay
  (zkSync).
- Under overload, at 500 payments per second, neither Perun nor Arbitrum kept up.
  Arbitrum delivered 79 % of the payments, limited by the client boards. Perun delivered
  34 %, because each channel processes one update at a time.
- zkSync kept the Pi 4 fully loaded (99 % CPU) and confirmed 4.0 to 4.9 payments per
  second at every window. This is a result about the zkSync server on small hardware, not
  about ZK rollups in general.

**UC2, on-chain gaming**

- Perun and Arbitrum confirmed all 1070 payments of every match. zkSync confirmed 947 on
  average.
- The median latency of a guest's shot payment was 39 ms on Perun, 195 ms on Arbitrum and
  26 s on zkSync.
- Perun's Open through the hub took 14 s per player; Arbitrum's Open took 1.7 s. On Perun this Open
  is repeated whenever the players change.

**Which system for which use case.** Perun pays off when the partners are fixed and the
payment deadlines are tight, as in UC1. Otherwise Arbitrum is the easier default: it needs no fixed
partners, and anyone can join and pay anyone.

The design of the measurements and every decision behind them are recorded in the cluster
spec,
[`docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md`](docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md).
Its parent spec,
[`2026-09-03-l2-benchmark-design.md`](docs/superpowers/specs/2026-09-03-l2-benchmark-design.md),
framed the metrics; the final list is [`analysis/metrics.md`](analysis/metrics.md).

## Repository map

| Path | What it holds |
|---|---|
| [`measure/`](measure/) | Python code that runs on the boards and on the workstation: the UC1 sensor and customers, the payment client (`payclient`) with the Arbitrum, zkSync and Perun back ends, the UC2 setup, the recorder side, and the analysis of each run (`analyze_uc1`, `analyze_uc2`, `aggregate`). Tests in `measure/tests/`. |
| [`deploy/`](deploy/) | The cluster driver [`deploy/cluster`](deploy/cluster), the experiment files `deploy/experiments/*.env` (superseded ones in `deploy/experiments/archive/`), one-off board scripts (static IP, Wi-Fi pinning, power saving), the recorder, the zkSync token deployer `deploy/zk-token/`, and the stack configuration copied from `bath0` (`deploy/stack-config/`). Runbook: [`deploy/README.md`](deploy/README.md); board register: [`deploy/FLEET.md`](deploy/FLEET.md). |
| [`perun/hub-demo/`](perun/hub-demo/) | `perun-node`, the Go program built on go-perun: the UC1 sensor and customer sidecars, the UC2 hub and players (ledger channels and virtual channels), and the contract deployment. |
| [`tank-arena/`](tank-arena/) | The UC2 game: a deterministic lockstep tank match (`arena/`, `net/`), its fixed reference log (`reference/`), the viewer and the figures (`viz/`, `watch.sh`). |
| [`contracts/points/`](contracts/points/) | `PointsToken`, the ERC-20 token for the UC2 points on the rollups, with its EraVM build. |
| [`zksync-setup/`](zksync-setup/) | How the zkSync Era ecosystem was set up (`zkstack`), and notes on the arm64 build of `zksync_server` ([`ARM64.md`](zksync-setup/ARM64.md)). |
| [`results/pi/`](results/pi/) | Every run folder and the aggregates: the final series (`uc1/*5mx7*` with `aggregate-*5mx7-*.txt`, and `uc2/` with `aggregate-uc2-*.txt`), the earlier series (smoke tests, diagnostics, the 2.4 GHz runs that broke off), and the Wi-Fi baseline (`baseline/`). Index: [`results/pi/README.md`](results/pi/README.md). |
| [`analysis/`](analysis/) | The result numbers and figures of the thesis. Three scripts write the three tables the thesis reads: `results_table.py` turns the 36 final runs (27 for UC1, 9 for UC2) into [`analysis/data/results.csv`](analysis/data/results.csv) (metric list: [`analysis/metrics.md`](analysis/metrics.md)); `cost_eur.py` converts the measured gas into euros at reference prices of the public networks (`analysis/data/cost_eur.csv`; the prices were collected by `reference_prices.py`); `stall.py` breaks down the stalled Arbitrum run (`analysis/data/stall.csv`). The figure scripts write `analysis/figures/`: `figures.py` (F1 to F4), `figures_timeline.py` (F5), `figures_prices.py` (F6), `figures_persecond.py` (F7), `figures_deadline.py` (F8), `figures_breakeven.py` (F10) and `figures_match.py` (F11). Further frames of the UC2 match, not used in the thesis, are in `analysis/figures/uc2-match/` (from the game's viewer, `tank-arena/viz`). Tests in `analysis/tests/`. |
| [`docs/`](docs/) | The design specs (`docs/superpowers/specs/`) and the thesis proposal (`docs/proposal.pdf`). |
| [`archive/`](archive/) | Material no longer in the measurement path: the unused ansible spike, legacy deploy scripts, the June workstation setup (`Makefile`, `setup.sh`) and its status log, the executed implementation plans, the June to August 2026 spike results ([`archive/spike-results/`](archive/spike-results/), not thesis results) and the September workstation probes (`archive/bench-workstation/`). See [`archive/README.md`](archive/README.md). |

## Reproduce

### (a) Re-analyse the recorded data (no hardware needed)

Requirements: Python 3.12 and the repository's virtual environment. Run the commands
from the root of the repository, in this order.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r measure/requirements.txt -r analysis/requirements.txt

# the three runs of one configuration -> their summary.json
# (overwrites the committed files; compare with git diff)
for d in results/pi/uc1/*-perun5mx7-w1000-rep*; do .venv/bin/python -m measure.analyze_uc1 "$d"; done
for d in results/pi/uc2/*-uc2-perun-rep*; do .venv/bin/python -m measure.analyze_uc2 "$d"; done

# three repetitions -> mean ± sd (compare with results/pi/uc1/aggregate-perun5mx7-w1000.txt
# and results/pi/uc2/aggregate-uc2-perun.txt)
.venv/bin/python -m measure.aggregate results/pi/uc1/*-perun5mx7-w1000-rep*
.venv/bin/python -m measure.aggregate results/pi/uc2/*-uc2-perun-rep*

# restore the committed summaries: the table needs all 36 from one analysis commit
git checkout -- results

# the 36 final runs -> analysis/data/results.csv (with --verify: about 15 to 20 minutes)
.venv/bin/python analysis/results_table.py --verify
# gas in euros -> analysis/data/cost_eur.csv (and reference_prices_monthly.csv for F6)
.venv/bin/python analysis/cost_eur.py
# the stalled Arbitrum run -> analysis/data/stall.csv
.venv/bin/python analysis/stall.py

# the figures -> analysis/figures/
.venv/bin/python analysis/figures.py            # F1 to F4, from results.csv
.venv/bin/python analysis/figures_timeline.py   # F5, from the run folders
.venv/bin/python analysis/figures_prices.py     # F6, from reference_prices_monthly.csv
.venv/bin/python analysis/figures_persecond.py  # F7, from the run folders
.venv/bin/python analysis/figures_deadline.py   # F8, from the run folders
.venv/bin/python analysis/figures_breakeven.py  # F10, from cost_eur.csv
.venv/bin/python analysis/figures_match.py      # F11, needs pygame (below)
```

A re-analysed `summary.json` should differ from the committed one in one field only,
`analysis_commit` (the commit the analysis ran at). `aggregate` refuses summaries from
different analysis commits, so re-analyse all three repetitions before aggregating, as
above. The committed `aggregate-*.txt` files carry one extra first line, a hand-written
comment that names the runs used; the rest matches the output of `aggregate`. One zkSync
`w1000` run (`20261011-230326`) failed and was replaced, so pass the three run folders of
that configuration explicitly.

`results_table.py --verify` re-runs the analysis on a copy of every final run folder and
needs the committed summaries in `results/` (hence the `git checkout` above). Large raw
files are stored compressed with gzip; the readers accept both `X` and `X.gz`. The figures
need matplotlib (`analysis/requirements.txt`); F11 also needs pygame, at the version the
game's viewer uses: `.venv/bin/pip install pygame==2.6.1`.

### (b) Re-run on a Raspberry Pi cluster

**Workstation** (Linux or WSL2; the runs were driven from WSL2 Ubuntu 24.04):

- the `.venv` above. `deploy/cluster` uses it for every step on the workstation
  (override with `LPY=`);
- Go 1.23 or later (`perun/hub-demo/go.mod`). `deploy/cluster sync` cross-compiles
  `perun-node` for arm64 and amd64. The final runs used go1.26.4; each run records the
  toolchain and the hash of the binary;
- Foundry's `cast` at `~/.foundry/bin/cast` (override with `CAST=`);
- Node.js and `npm ci` in `deploy/zk-token/`, for deploying the UC2 points token on zkSync
  (zksync-ethers 6);
- `ssh` access to `bath0` to `bath7` through `~/.ssh/config`, and `scp`, `tar`,
  `sha256sum`, `unzip`, `curl`, `git`;
- the GESL recording `sigId-6063`, downloaded from <https://gesl.ornl.gov> (free account,
  not redistributed) to `data/gesl-samples/sigId-6063.zip`. `measure/gesl.py` checks the
  hash of its CSV.

**Boards.** One Raspberry Pi 4 (8 GB) for the stacks and six Raspberry Pi 3 or 3B+
boards (`bath1`, `bath2`, `bath4` to `bath7`). The cluster scripts also name a seventh
board, `bath3`, which is set idle (`IDLE_BOARDS=bath3` and in every final experiment
file). The cluster was set up by hand; there is no installer. Flash the cards with
Raspberry Pi Imager, run the one-off scripts on each board, and install the stacks on
`bath0`. [`deploy/README.md`](deploy/README.md) describes every step, and
[`deploy/stack-config/`](deploy/stack-config/) holds the stack configuration.

**Runs.**

```sh
IDLE_BOARDS=bath3 deploy/cluster sync           # code, game, venv, perun-node, GESL CSV to the boards
deploy/cluster preflight                        # optional: the checks every run makes
deploy/cluster run uc1-perun5mx7-w1000 --reps 3 # one experiment file, three repetitions
deploy/cluster run uc2-arbitrum --reps 3
```

Each repetition runs end to end and writes `results/pi/<uc1|uc2>/<run id>/`, which is then
analysed as in (a).

## Tests

```sh
.venv/bin/python -m unittest discover -s measure/tests -t .
(cd tank-arena && ../.venv/bin/python -m unittest discover -s tests -t .)
.venv/bin/python -m unittest discover -s analysis/tests   # the result table against the final runs and the spec
(cd perun/hub-demo && go test ./...)   # unit tests; the integration tests skip themselves
```

The Go integration tests need a local Anvil (Foundry) and are switched on explicitly:

```sh
anvil --chain-id 31337 --block-time 1     # in a second terminal
(cd perun/hub-demo && RUN_DISTRIBUTED_TEST=1 ANVIL_URL=ws://127.0.0.1:8545 go test ./...)
```

To watch the UC2 match: `tank-arena/watch.sh` (pygame in its own virtual environment; see
the header of the script). To draw the frames in `analysis/figures/uc2-match/` again:
`cd tank-arena && <viewer-python> -m viz.match --figures ../analysis/figures/uc2-match`.

## Data and this copy

Almost all of the repository is raw run data in `results/pi/`: compressed payment records (`ledger.csv.gz`), logs and recorder
series for every run, so that every number in the thesis can be traced to its run folder.

The public copy, <https://github.com/i-wanilin/blockchain-scaling-approaches>, is a
snapshot of the tag that the thesis cites, without the history. It leaves out the Docker
logs of the stacks (`logs/bath0-docker.log` and `.log.gz` in the run folders, 0.8 GB),
which no analysis reads. In the recorded data, the name and the hardware addresses of the
Wi-Fi network and the host names of the author's computers are replaced with placeholders;
nothing else is changed. The public copy is about 1.8 GB.

The code is the final state of the analysis: run again on the raw data of every final run,
it gives the same summaries and the same result table (`results_table.py --verify`).
Commit hashes and branch names in the documentation come from the history of the work and
are not part of this copy.

## Licence

- **Code** (everything that runs: `measure/`, `deploy/`, `perun/hub-demo/`, `tank-arena/`,
  `contracts/`, `analysis/*.py` and the scripts): the [MIT licence](LICENSE).
- **Results, figures and documentation** (`results/`, `analysis/data/`,
  `analysis/figures/`, `docs/`, the Markdown files):
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) ([`LICENSE-data`](LICENSE-data)).
  Use them freely, citing the thesis (below).
- Third-party components keep their own licences. The frameworks the code builds on
  (go-perun, Arbitrum Nitro, zkSync Era, Foundry) are cloned separately and are not part
  of this repository. The GESL data is not included either; it is cited as *ORNL, LLNL
  (2023). Grid Event Signature Library. https://gesl.ornl.gov*.

## Citation

```bibtex
@thesis{ilin2026l2,
  author = {Ivan Ilin},
  title  = {A Use-Case-Driven Comparison of Layer-2 Scaling Approaches for IoT
            Micropayments and On-Chain Gaming},
  type   = {Bachelor's thesis},
  school = {Technische Universit{\"a}t Berlin, Open Distributed Systems},
  year   = {2026}
}
```
