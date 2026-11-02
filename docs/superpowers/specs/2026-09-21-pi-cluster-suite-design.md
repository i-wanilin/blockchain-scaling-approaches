# Design spec — Raspberry Pi cluster suite (install, operate, record)

**Status:** executed — the suite was run and the measurements are finished
(2026-10-12); §1–§7 give the design and, where it differs, what was built ·
**Date:** 2026-09-21 · **Branch:** `v2`
**Commits and branches** named in this spec (the branch `v2`, short hashes such as
`708b090`) come from the history of the work and are not part of this copy.
**Parent spec:** `2026-09-03-l2-benchmark-design.md` (what is measured and why).
This spec covers *how the three systems get onto the Pis and how a run is driven
and recorded*. It changes no metric. It **does** redefine the UC2 workload
(§7.3): parent spec §3.2 is superseded there.
**Replaces:** the laptop-centred layout of the earlier `deploy/README.md` and the
`laptop_ip` variable of the July ansible spike (archived in
`archive/ansible-spike/`, never used on the `bath` cluster).

---

## 1. Goal

Prepare the whole suite before anything is installed on hardware:

1. **Install once.** One command puts everything a board needs onto it — Perun,
   Arbitrum Nitro, zkSync Era, the load generators and the recorder.
2. **Operate without reinstalling.** Start a stack, stop it, run an experiment,
   collect the recording.

**As built:** goal 2 was met (`deploy/cluster`, §5). Goal 1 was not: the boards
were set up by hand with one-off scripts and `deploy/cluster sync` copies the
measurement code (§3); the one-command installer is **future work (owner,
2026-10-11)**.

**Fixed constraints (decided, not open):**

- Infrastructure *and* users run on Raspberry Pis. The workstation drives the
  cluster over SSH (`deploy/cluster`) and receives results. It is never in the
  measured path.
- All boards are on Wi-Fi. Agreed with the supervisor; no Ethernet is available.
- Some Pi 3s are model 3B, which has 2.4 GHz only. **All eight boards were pinned to
  2.4 GHz** so that no pair of boards talks across the access point's band bridge.
  **Revised 2026-10-05/06 (owner): mixed Wi-Fi is the final setup** — `bath1` and
  `bath2` on the router's 5 GHz radio (`deploy/pin-5ghz.sh`), every other board on
  2.4 GHz (`bath0`'s 5 GHz link measured 13 Mbit/s). Pairs between the two groups
  cross the band bridge; this is accepted and recorded (§9 U4). Every experiment
  file names its 5 GHz boards (`WIFI_5GHZ_BOARDS`) and pre-flight checks them.
- Storage is SD card on every board.
- Nothing starts at boot. A board that is powered on and idle runs no stack.
- **UC1 pays in native ETH; UC2's game moves points, not money** (decided
  2026-09-24/25). Points are our own
  ERC-20 token with no value outside the game; fees are always paid in ETH by
  whoever sends the transaction; on the testbed all ETH is play money (§7.3).
  **No thesis measurement is recorded before the points token works on all three
  systems** (feasibility checks on the Pis may come first, §8).

## 2. Hardware and placement

| Name | Board | Job |
|---|---|---|
| `bath0` | Pi 4, 8 GB | The system under test, **one stack at a time**: the L1 (Anvil) for Perun, or the Arbitrum stack, or the zkSync stack. Each stack brings its own L1. |
| `bath1` | Pi 3B+, 1 GB | Time server (chrony) for the cluster. UC1: **the sensor** (§7.2). UC2: **player 0 and the host**, running the Perun hub (§7.3). |
| `bath2` | Pi 3B+, 1 GB | UC1: customer 1. UC2: player 1. |
| `bath4`…`bath7` | Pi 3B, 1 GB | UC1: customers 2–5 (`bath4`, `bath5`, `bath6`, `bath7`). UC2: `bath7`, `bath4`, `bath5`, `bath6` are players 2–5 (§7.3). |
| `bath3` | Pi 3B, 1 GB | **Dead since 2026-10-05** (board power fault, `deploy/FLEET.md`); idle in every final run by configuration (`IDLE_BOARDS="bath3"`), no data from it. |

**Final boards (2026-10-05/06):** `bath3` failed, so `bath7` replaced it as UC1
customer 5 and UC2 player 2. Every final run uses **seven boards**: `bath0` (Pi 4),
`bath1` and `bath2` (Pi 3B+), `bath4`…`bath7` (Pi 3B). `bath1` rebooted without
explanation three times: 2026-10-09/10 outside any run, 2026-10-11 at about 23:33
during a run (failed and replaced, §8), and 2026-10-12 at about 06:15 after the
series.

**Hardware change, 2026-09-29.** The cluster has **one** Pi 4, not two: the second
Pi 4 is defective (it reads no SD card, not even the bootloader recovery card;
the same cards boot in the other Pi 4). Its roles moved to a Pi 3B+ (`bath1`).
Consequences, stated in the thesis: there is **no spare** for the stack board, so
if `bath0` fails, zkSync cannot run at all; Perun's hub runs on a Pi 3B+, not on
the same board class as the rollups' sequencer; the one-node load comes from
several Pi 3s together; and at UC1's 10 ms window the sensor may be the limit
(recorded as a result). Eight boards in total, **no spares** (seven after
2026-10-05, above).

**Names say what a board is, not what it does** (decided 2026-09-25). A board keeps
its name `bath<N>` for the whole thesis; its role per experiment comes from the
experiment file (`CUSTOMERS=`, `PLAYERS=`, `IDLE_BOARDS=`; the stack is always
`bath0`, the sensor and hub `bath1`), so swapping a broken board means changing one
line there (as built; the plan was ansible inventory groups). The Pi 4 takes the
lowest number, then the Pi 3B+s. `deploy/FLEET.md` registers each board's model,
serial and Wi-Fi address, and every run's manifest records the model read from the
board. Each board and its SD card carry a sticker with the name.

**Why one infra board for all three stacks.** Measured footprints (parent repo,
`deploy/README.md` §5): zkSync 2 926 MB under load, Arbitrum 367 MB, Perun far
less. zkSync fits only on a Pi 4. Running all three on the *same* board makes the
comparison a same-hardware comparison.

**Capacity probe `one-node`: why several senders.** This concerns only the
capacity probe (§7.1; not run, future work, owner, 2026-10-11), which pushes transactions at the stack as fast as possible
to find its ceiling. A 1 GB Pi 3 may itself be the bottleneck as a sender, and
there is no second Pi 4. The ceiling must be limited by the node, not by the
senders, so several Pi 3s send together, and U6 (§9) checks which side is the
limit. The use cases are different: there the load starts at the data source —
in UC1 the sensor (`bath1`) generates the data whose arrival triggers the
customers' payments; in UC2 the players generate it themselves.

**OS:** Ubuntu Server 24.04.5 LTS, 64-bit, the same image on all eight boards
(decided 2026-09-27: the supervisor uses Ubuntu 24.04; the Pi 3 has a 64-bit CPU).
Flashed by hand with the Raspberry Pi Imager, preset with hostname, user
`i_wanilin`, SSH public key and the 2.4 GHz Wi-Fi network (settings in
`deploy/README.md` §2.1). Fixed addresses (`bath<N>` = `192.168.178.21<N>`) are set on
each board by `deploy/set-static-ip.sh`, not on the router.
Go/no-go on the Pi 3s' idle memory under Ubuntu: **passed** (§8 step 1), so
Raspberry Pi OS Lite, the fallback, is not needed. Done by hand on every board
(2026-09-29, `deploy/FLEET.md`, `deploy/README.md` §2; planned as the `common`
role): automatic updates and snap refreshes off, one kernel on every board (two
were found), and the throttle readout (`vcgencmd`).

**Arranged (2026-09-29).** SD cards: 128 GB in `bath0` (Docker images, chain
snapshots, PostgreSQL and RocksDB) and `bath1`, 32 GB in the others (`deploy/FLEET.md`).
One power supply per board. The Pi 4 needs a heatsink or fan — a bare Pi 4 under
sustained load throttles; throttling is recorded and reported, never silently
discarded (§9 U5).

## 3. Installation — as built (the installer is future work)

**Planned:** `cluster install`, an ansible installer extending the July spike's
`deploy/ansible/` tree, with roles `common`, `time-sync`, `docker`, `perun`,
`arbitrum`, `zksync`, `loadgen`, `recorder` and systemd units for every stack and
node. **Not built: future work (owner, 2026-10-11).** The spike tree is archived in
`archive/ansible-spike/` and was never run on the `bath` cluster (its `laptop_ip`
time source and `eth0` netem would not fit it).

**As built** (`deploy/README.md` §2–§5 is the runbook):

| What | Where | How |
|---|---|---|
| Base setup | all | by hand after flashing (§2), with one-off scripts: `set-static-ip.sh` (fixed address), `pin-24ghz.sh` / `pin-5ghz.sh` (Wi-Fi radio, §1), `wifi-powersave-off.sh` (`iw`, power save off and kept off), `wifi-pin-at-boot.sh` (rejoin the pinned radio at boot; `bath0`–`bath2`); automatic updates and snap refreshes off, one kernel, the user in the `video` group, `sudo` without a password (`deploy/FLEET.md`) |
| Time | all | by hand (2026-09-30): `bath1` serves time (chrony), all others follow only it |
| Stacks | `bath0` | by hand (2026-09-29/30): Docker with Compose and the pinned images, Anvil, our arm64 `zksync_server` with `liburing2`, the chain snapshots (§4); configuration files copied to `deploy/stack-config/` |
| Measurement code | `bath1`…`bath7` | `deploy/cluster sync` (§5): `measure/`, the recorder and board-facts scripts, the game (`tank-arena/arena`, `tank-arena/net`), the `perun-node` arm64 binary (cross-compiled on the workstation, hash checked after the copy), a Python venv from `measure/requirements.txt`; the GESL CSV to `bath1` only |

Nothing is a systemd unit: `deploy/cluster` starts every stack, node, sensor,
payment client and recorder for a run (`setsid nohup`, with a pattern check that
it started) and stops them at the end. Nothing starts at boot (§1).

**How each piece runs.** Everything with an official arm64 image at our pinned tag
runs in Docker, so the Pi runs the same release that the workstation footprints
were measured on. Verified against the registries on 2026-09-21:

| Image | arm64 published |
|---|---|
| `offchainlabs/nitro-node:v3.9.6-91bf578` | yes |
| `ethereum/client-go:v1.16.4` | yes |
| `redis:6.2.6` | yes |
| `postgres:18` | yes |
| `ghcr.io/paradigmxyz/reth:v1.8.2` | yes |

`zksync_server` has no official arm64 image; it runs natively from our own build
(`zksync-setup/ARM64.md`), started by `deploy/cluster up zksync`. Perun is a static
Go binary and also runs natively. No Pi 3 gets Docker: the rollup stacks run on
`bath0` only, and whether one fits on a 1 GB board is not examined.

**Keys.** The test accounts are derived from the public development mnemonic
(`measure/keys.py`: fixed indices for the funder, the sensor, the customers, the
`arena` account and the players). That is acceptable because every chain is local
to the cluster; no key has value anywhere else. The passwords and keys in the
stack configuration are the public development defaults
(`deploy/stack-config/README.md`). The recovered zkSync wallets stay out of the
repo.

The game needs no extra installation beyond `sync` (the spike's `player-node`
role is not used). Network-delay emulation (`netem`) is used in no run: it
belonged to the `-distant` runs, which are future work (§7.3). The Pis need no Go
toolchain: binaries are cross-compiled on the workstation.

## 4. Chain setup by snapshot

Setup tooling (`zkstack`, the Arbitrum deployment scripts) is known to work on
the x86 workstation and is unproven on arm64. Setup is installation, not
measurement, so it does not have to happen on a Pi.

- Each rollup chain is **initialised once on the workstation**. The resulting
  state — L1 data directory, the rollup's database (PostgreSQL and RocksDB for
  zkSync), configs — is packed as tarballs and copied to `bath0` (`~/arbitrum/*.tgz`,
  `~/snap/*.tgz`). **As built:** the tarballs are not in the repository (several
  GB); their `SHA256SUMS` and the stack configuration files are committed in
  `deploy/stack-config/`, and every run records the SHA-256 of those files
  (`stack-config.sha256`, §6), which match the committed copies byte for byte.
- `deploy/cluster up <stack>` **restores the snapshot before starting** (after
  checking the tarballs against `SHA256SUMS`), so every run begins from a
  byte-identical chain and "reset" is a file restore. The snapshots were put on
  `bath0` by hand (§3).
- **What a snapshot contains, and what it deliberately does not** (as built). It
  contains the chain state, the zkSync build outputs the server reads at run time
  (compiled contracts, `etc/`; found 2026-09-29), and **one funded funder account**
  on the L1. Everything else is done per run in the untimed setup: the funder
  funds the test accounts (one per sensor, customer or player; `measure/keys.py`)
  on the L1, and for UC2 the points token is **deployed fresh on the rollup** and
  minted (§7.3 *Run parameters*). The snapshot does **not** contain the users' ETH
  on the rollup: bridging the ETH that pays the rollup fees is the rollup's
  *Open*, which is part of Metric 1, so it happens on the Pi, timed, at the start
  of every run (§7.2, §7.3). Baking funded L2 accounts into the snapshot would
  silently skip that measurement.
- Perun needs no snapshot: every run starts a fresh Anvil, and `deploy/cluster
  up perun` deploys the Adjudicator and the ETH AssetHolder (`perun-node -mode
  uc1-deploy`, from the workstation, untimed). The deployer's nonces 0 and 1 give
  the same two addresses in every run, and `up perun` checks them (corrected
  2026-10-04: `-mode init` only creates the wire identity; §8 step 3).

**✅ Riskiest assumption in this spec — answered 2026-09-29.** Whether a chain
initialised on x86 starts cleanly from copied state on arm64. **It does, for both
rollups** (§8 step 1, U1): the x86 reth and geth data directories start unchanged
on `bath0`, PostgreSQL moves as a `pg_dump`, the zkSync RocksDB was copied along,
and both chains continue at the PC's last block and confirm transfers. One
addition to the snapshot: the zkSync server reads compiled contract files from
its working directory at run time, so they travel with it. Sustained workloads
on the restored chains: answered by the final series (15-minute UC1 runs on both
rollups, 2026-10-11/12, §8).

**Fallback if it fails:** run the setup tooling on the workstation *over the
network* against the L1 and database on `bath0`, then snapshot on the Pi itself.
Slower, still outside the measured path.

## 5. Control — `deploy/cluster`

One shell script (`deploy/cluster`; planned as a wrapper around the installer's
playbooks, as built it does everything over SSH itself). It runs on the
workstation (the PC, from WSL, which reaches the boards as `bath0`…`bath7` through
`~/.ssh/config`). No new daemon and nothing resident on the Pis: every process of
a run is started and stopped by the script (§3).

| Command | Does |
|---|---|
| `cluster sync` | copy the measurement code, the game and the `perun-node` binary to `bath1`…`bath7` and check their hashes; create or update each board's Python venv; the GESL CSV to `bath1` (§3) |
| `cluster status` | per board: kernel, radio, power save, throttle flags, clock offset and source, temperature, free disk, netem, which stack processes run |
| `cluster preflight` | the pre-flight checks below, on their own |
| `cluster idle-temps` | record every board's idle temperature for the cooling gate (stack down) |
| `cluster up perun\|arbitrum\|zksync` | restore snapshot, start the stack on `bath0`, wait until healthy. **Refuses if another stack is up.** |
| `cluster down` | stop whatever is up on `bath0`; remove any `netem` qdisc from every board (it does not re-check afterwards; the next run's pre-flight does) |
| `cluster run <experiment> [--reps N]` | per repetition: code check (every board runs exactly the workstation's code), `down`, cooling gate, `up`, clock-settle gate, pre-flight, untimed setup, start the recorders, run, stop them, collect every board's results and logs into the run folder, manifest, `down`. Roles come from the experiment file `deploy/experiments/<experiment>.env` (`CUSTOMERS=`, `PLAYERS=`, `IDLE_BOARDS=`, `WIFI_5GHZ_BOARDS=`, §2). A failed repetition writes `RUN_FAILED`, still collects what exists, brings the cluster down and stops the series |

There is no separate `cluster install` (future work, §3) and no `cluster
collect`: collection is the last step of `run`.

**Before pre-flight, the clock-settle gate** (2026-09-30, after `up`): after a heavy run a
board's offset to `bath1` can stay high for minutes, so `run` waits until every
board except `bath1` is below 5 ms on two consecutive polls, at most 600 s
(`clock-gate.txt` in the run folder).

**Pre-flight, every run, abort on failure** (`measure/preflight.py`). The boards in
`IDLE_BOARDS` (`bath3` in every final run) are left out of every check and every
step of the run:

- all boards reachable;
- **each card in its own board:** every board's Wi-Fi hardware address and serial
  match `deploy/FLEET.md` (cards were swapped unnoticed on 2026-09-29);
- **every board on its expected radio**: the 2.4 GHz radio (BSSID
  `02:00:00:00:00:01`), or the 5 GHz radio for the boards in `WIFI_5GHZ_BOARDS`
  (since 2026-10-05, §1): band steering moved dual-band boards before, and a run
  would not notice it otherwise;
- **one kernel on all boards** (unified by hand on 2026-09-29; a board that has
  drifted is a failed check, not a warning);
- clock offset to `bath1` at most 5 ms;
- Wi-Fi power save off;
- **power (revised 2026-09-29):** `bath0` and `bath1` must show no throttle flag,
  current or since boot (`get_throttled` = `0x0`, the soft temperature bit
  `0x80000` excepted); the since-boot bits stay set after a brief dip, the fix is a
  reboot of that board, which the abort message names. The other Pi 3s may be
  undervolted: the Pi 3Bs are undervolted under load on every supply tried
  (`deploy/FLEET.md`), so their flags are **recorded, not a reason to stop**. This
  is the start condition only; throttling *during* a run is recorded and reported,
  not aborted (§9 U5);
- **no `netem` qdisc on any board** (as built there is no exception: the
  `-distant` runs that would have used one are future work);
- at least 5 GB free disk;
- the expected stack and only that stack is up;
- **cooling gate** (decided 2026-09-30, revised the same day; checked before `up`,
  so the chain does not idle while the boards cool): every board within
  **3 °C of its own idle temperature** before the run starts, waiting at most
  `COOL_MAX_S` (1 200 s = 20 minutes by default, 2 700 s in the zkSync experiment
  files) and failing the run otherwise; the idle temperatures are recorded with
  `cluster idle-temps` (stack down, boards idle), the start temperatures in every
  run folder, so every run starts from the same thermal state and the run order
  does not matter. Idle temperatures were recorded twice (2026-09-30, kept as
  `results/pi/idle-temps-2026-09-30.txt`, and 2026-10-01); every final run used
  those of 2026-10-01 (`results/pi/idle-temps.txt`). A fixed 50 °C was the
  first choice, but the bare Pi 3Bs idle at 50.5–56.9 °C in the room (bath0 48.7 °C),
  so it would never open.

## 6. Recording

One folder per repetition on the workstation (as built):

```
results/pi/<uc1|uc2>/<date>-<time>-<stack>-<experiment>-rep<k>/
  manifest.json      git commit (and whether the tree was dirty), code hash,
                     image IDs with repository and tag (Docker stacks), board
                     facts per host at start and end: model, serial, Wi-Fi
                     hardware address, kernel, BSSID and frequency, power save,
                     throttle flags, clock offset and source, netem present
                     (yes/no), free disk, temperature, the perun-node hash, and
                     since 2026-10-11 also Wi-Fi signal and rx/tx link rate
                     (the final UC1 series has them, the UC2 series does not);
                     the experiment parameters (experiment.env); the recorder
                     summary per board (share of the run spent undervolted,
                     mean ARM clock, max temperature, BSSIDs seen, mean and max
                     CPU busy); idle and start temperatures; the GESL recording
                     (sig and SHA-256, checked against measure.gesl); the
                     SHA-256 of bath0's stack configuration (stack-config.sha256:
                     compose file, SHA256SUMS and nitro config, or for zkSync
                     compose file, SHA256SUMS, general.yaml and the server
                     binary); for UC2 the four game parameters, the config
                     digest, the game hash, the SHA-256 of the reference event
                     log and of every board's own event log
  run-start-commit.txt, code-hash.txt, experiment.env, clock-gate.txt,
  chrony-start/, chrony-end/, facts-start/, facts-end/, start-temps.txt (UC1
  also stream-start-temps.txt, UC2 match-start-temps.txt), summary.json (the
  analysis), RUN_FAILED if the repetition failed
  results/           raw output of the experiment, per host
  logs/              per host: the application logs of the run (sensor,
                     customer, payment client, game node, Perun nodes,
                     recorder), on bath0 the stack's logs (Anvil log, docker
                     logs, zkSync server log); journald is not collected
  recorder/          sampler time series, per host, every 5 s: SoC
                     temperature, throttle flags, ARM clock, memory used, load,
                     SD-card I/O, BSSID, frequency, signal, clock offset, and
                     since 2026-09-30 cpu_busy_pct (all cores, /proc/stat);
                     on bath0 in zkSync runs also the zk-sampler (server CPU
                     and RSS, L2 block, sealed L1 batch)
```

The recorder is **system-wide** per board; per-process resource figures exist
only for the zkSync server (zk-sampler). Per-process recording for every stack and
client is **future work (owner, 2026-10-11)**.

A figure in the thesis must be traceable to run folders. The n=3 rule of the
parent spec applies here: `--reps 3`, three complete folders per cell; a failed
repetition keeps its folder (with `RUN_FAILED`) and is replaced by a new one, so a
cell can have four folders (UC1 zkSync `w1000`, §8).

## 7. Experiments — separated by use case

The setup and operate layers (§3–§5) are use-case agnostic: a stack that is up
serves whichever workload is pointed at it. **Experiments are grouped by use case**,
because the thesis compares the three systems *per use case*.

### 7.1 Capacity probes (no use case)

**`baseline` was run** (2026-10-12, stacks down; results in §8). **`one-client`,
`one-node` and `footprint` are future work (owner, 2026-10-11):** not run. Without
them, what is known about each system's ceiling comes from the use cases: zkSync
sustained 4.0–4.9 payments per second (goodput) under UC1's 15-minute load of ETH
transfers with 5 accounts × 20 in flight, and U9 found more in flight does not
raise it — the chain's own limit **under that load** (n = 1); UC2's ~2-minute
burst of token transfers (6 accounts × 20 in flight) reached 7.5–8.9 per second,
with submitted → confirmed p50 ≈ 7 s against ≈ 22–23 s in UC1, and why the burst
confirms faster is not established. Arbitrum's UC1 `w10` goodput (≈ 370 per
second) is a lower bound, and Perun's total over many channels is inferred from
the per-channel rate (§7.3 *Payment ledger*).

| Experiment | Stack | Senders | Source |
|---|---|---|---|
| `baseline` (run 2026-10-12) | none | all seven boards | `deploy/wifi-baseline.sh`: per board, one at a time, the Wi-Fi link, ping RTT and TCP throughput (up and down) to `bath0` and to `bath1`, no chain running; `results/pi/baseline/`. The old `deploy/measure-baseline.sh` (laptop layout) is archived in `archive/deploy-legacy/`. Perun's `yield_timeout_ms` was **not** derived from it: the owner fixed 200 ms (2026-10-06), about 7 × the ≈ 30 ms update round trip on the Pis |
| `reth-idle-cpu` (run 2026-10-29) | zkSync's L1 only | none | `deploy/reth-idle-cpu.sh`: reth from the zkSync compose file alone on `bath0` (no server, no PostgreSQL, so no batches and no commitments), empty blocks; 5-min averages after 1 min warm-up: whole board 0.10 % idle, 2.47 % at 300 ms (reth's cgroup 7.35 % of one core), 0.89 % at 1 s (2.41 %); 3.34 and 1.00 blocks per s. Asked by the owner for the thesis's §8.7: the L1's own block-making is a small share of the 98.8 % CPU of the zkSync runs; `results/pi/reth-idle-cpu-2026-10-29.txt` |
| `one-client` (future work) | arbitrum, zksync | one Pi 3B, then one Pi 3B+ | `archive/bench-workstation/probe_ceiling.py` |
| `one-node` (future work) | arbitrum, zksync | several Pi 3s together | `archive/bench-workstation/probe_ceiling.py` |
| `footprint` (future work) | each | — | recorder, idle and under the one-node load |

### 7.2 UC1 — IoT micropayments

Topology (parent spec §11.1, resolved 2026-09-25): **one sensor, many customers**.

| Board | UC1 role |
|---|---|
| `bath0` (Pi 4) | the stack under test |
| `bath1` (Pi 3B+) | **the sensor**: replays the recording and sends it — the load; receives the payments |
| `bath2` (Pi 3B+), `bath4`…`bath7` (Pi 3B) | **5 customers** (final series; `bath3` dead, §2): each receives the whole stream and pays the sensor |

**How a run works** (decided 2026-09-25):

- **Data.** The sensor replays one GESL recording at its real 120 rows per second and
  sends the **whole stream** to every customer over a **direct TCP connection** — one
  per customer, so connections and the sensor's upload grow linearly. One stream is
  about 68 KB/s (584 bytes × 120 rows); five are about 340 KB/s, roughly 2.7 Mbit/s.
  With the final mixed Wi-Fi (§1) the sensor `bath1` and customer `bath2` are on
  5 GHz, so four of the five streams cross the band bridge onto the shared 2.4 GHz
  channel.
- **Payments.** **Postpay:** after receiving a window, the customer pays for it. The
  latency measured is from the window's arrival to the payment's confirmation.
- **Window sweep** (parent spec §11.3): 1 s, 100 ms, 10 ms → 1, 10, 100 payments per
  second per customer; with 5 customers 5, 50, 500 per second are offered to the
  sensor. Each window size is
  one paced experiment. There is no unpaced run.
- **Money.** UC1 pays in **native ETH** — no token. Perun's launcher already paid in ETH.
- **Perun: no hub.** Each customer opens one **direct channel** to the sensor: 5
  channels, growing linearly (a star, unlike UC2's all-pairs game). The deposit covers
  the whole run plus a margin; longer operation would use make-before-break (parent
  spec §11.5). (Planned with 6 customers; from 2026-10-02 measured with 5, first
  `bath2`…`bath6`, in the final series `bath2`, `bath4`…`bath7`, §8 step 3.)
- **Perun: no payment retries** (decided 2026-10-01, Perun plan Q6). An update
  that times out cannot be retried safely: the sensor may already hold the signed
  state. It ends *unresolved* and marks the channel broken; every later payment on
  it *fails* ("channel broken"). A rejection fails only that payment. A broken
  channel is **not closed** in the run (the sidecar refuses; a non-cooperative
  dispute close is out of scope): the run records the Close error and is
  incomplete. Reconciliation against the final channel state shows the
  difference. This departs from §7.3's "retries … the same for all systems".
- **The rate is constant**, as a PMU is. UC2 provides the varying load.

**Who pays which fee.** All fees are paid in ETH by whoever sends the transaction.

| Moment | Perun (direct channels on the L1) | Arbitrum / zkSync |
|---|---|---|
| Start of each run | each customer opens a channel to the sensor and deposits ETH — **timed as Perun's *Open*** | each customer bridges ETH onto the rollup (the snapshot has none there, §4) — **timed as the rollup's *Open*** |
| During the run | **no fees**: a payment is a signed channel update | **one transaction per window**, paid by the customer; the sensor only receives |
| End of run | the customer finalizes the channel off-chain, sends `concludeFinal` (the *primary*) and withdraws its remainder; the sensor (*secondary*) withdraws its earnings and pays only its own withdrawal fee — **timed as *Close*, on the customer's board, from finalize to the confirmation of the customer's withdrawal** (decided 2026-10-01, Perun plan Q2) | nothing extra; *Close* is the withdrawal submission (parent spec §11.4) |

| Experiment (final series, 2026-10-11/12) | Stack | Window |
|---|---|---|
| `uc1-perun5mx7-w1000`, `-w100`, `-w10` | perun | 1 s, 100 ms, 10 ms |
| `uc1-arbitrum5mx7-w1000`, `-w100`, `-w10` | arbitrum | same |
| `uc1-zksync5mx7-w1000`, `-w100`, `-w10` | zksync | same |

Files `deploy/experiments/uc1-<stack>5mx7-w{1000,100,10}.env` (`5mx7`: 5
customers, mixed Wi-Fi, 7 boards); the earlier `uc1-<stack>-*` and `uc1-<stack>5-*`
files are in `deploy/experiments/archive/`. Settings that differ per system (all in
the experiment files): in flight per account 64 on Perun and Arbitrum, 20 on zkSync
(§7.3 *Payment ledger*); Arbitrum fetches only its own receipts
(`RECEIPT_MODE=own`) at every window; zkSync waits longer for receipts and for
Close (`RECEIPT_TIMEOUT_S=1200`, `CLOSE_TIMEOUT_S=1800`, against the defaults of
120 s and 300 s) and has the longer cooling cap (`COOL_MAX_S=2700`, §5).

**Data source and publication.** GESL, cited as *ORNL, LLNL. (2023). Grid Event
Signature Library. GESL. https://gesl.ornl.gov*. The raw files are behind a free
account and are **not published**; the repository records only the signature IDs and
the SHA-256 of each CSV, so anyone can download and verify the same data. (The ZIP
files are generated per download, so their hashes are not stable; the CSV hashes are.)

| Signature | Recording | CSV SHA-256 |
|---|---|---|
| 6006 | microPMU, 120 Hz, 269.6 s, oscillation 0.23 Hz | `a08d7c330d2641c2568c85192e4e2c1221bcfe7b5de645f20404d0525f86be72` |
| 6021 | microPMU, 120 Hz, 266.5 s, generator off, oscillation | `0198a44ce15611bf9febb884ac4cbf49250441277fba33442d11f91b4e2b5094` |
| 6062 | microPMU, 120 Hz, 309.3 s, generator on, oscillation 0.86 Hz | `bf59576f7754a6ad013b9155bca23ca15e60be9c43b2d982bf9c1a11b7dd707f` |
| 6063 | microPMU, 120 Hz, 974.9 s, generator on/off, frequency deviation | `87b2ad10cd5ba219a43f91d871635a142fa24e75016f2760108e297a6b6fd009` |

**Decided 2026-09-30:** recording **6063** for every system — the most complete
(52 measurement columns, 4 of which contain no data in the whole recording: phase-b
voltage angle and magnitude at `Hydro2Unit1` and `Hydro2Unit2`; the other 48 are
complete in all 116 988 rows. `measure/gesl.py` reports this as "92.31 % of cells
filled", which is 48 of 52 columns, not 92 % of the readings. The others: 6006 has 5
empty columns and the rest complete (90.38 %); 6021 has 17 empty columns, the whole
`Hydro1_Diesel_Line` point, and 4 missing cells (67.31 %); 6062 has 4 empty columns
and the rest 99.57 % complete (91.91 %). Wording corrected 2026-10-16, counts checked
2026-10-14) and
the only one long enough for the chosen run length (16.2 min; the others 4.4–5.2
min). **Each run replays 15 minutes**, so that heat and steady state show; that is
900, 9 000 and 90 000 payments per customer at the three windows. The price per
window is arbitrary on a test chain (10¹² wei); the deposit of 1 ETH per customer
covers the payments and fees with a wide margin — except zkSync at `w10`, whose
fees at 90 000 payments come to about 3.7 ETH: that file deposits 5 ETH and funds
6 ETH per customer.

**Decided in flight — outcomes** (final series, §8): the 10 ms window was run on
all three systems and every run completed, but no system keeps up with 500
payments per second there (all `w10` runs saturated: Perun ≈ 157 per second in
total, Arbitrum ≈ 370, zkSync ≈ 4.7). zkSync's per-account limit on unconfirmed
transactions: 20, mainnet's limit; more in flight does not raise zkSync's rate
(U9). Log volume on the SD cards: not a limit (pre-flight requires 5 GB free;
every run collected complete).

**New code (built):** a GESL reader and the sensor (replay + TCP fan-out), the
customer (receive, pay per window), a direct-channel mode in the Perun launcher, and
the pipelined rollup sender (`measure/submitter.py`, ETH transfers; UC2 reuses it
for token transfers), all on the payment ledger of §7.3.

**Discussed, not measured:** the sensor going offline (Perun stalls without the
sensor's co-signature, a rollup keeps accepting payments — parent spec §5.4 N1).

**Not in the suite either:** the prover, Kubernetes. Network-delay emulation
belonged only to the `uc2-*-distant` runs (future work).

### 7.3 UC2 — game

**The question UC2 answers:** how much of a real-time multiplayer game can each
Layer-2 carry, **without a central game server**?

**Scope (revised 2026-09-28): payment processing under an honest multiplayer
workload.** UC2 measures how each system processes the payments a real-time game
produces, when every player pays what the game says it owes. It does **not**
enforce the game's rules: a hit is paid by the victim voluntarily, so a losing
player could simply refuse, and on-chain balances prove that the players signed
them, not that they match the game. A game whose rules the chain enforces (for
example, hits verified by a contract) is a substantially larger project and is out
of scope; the thesis states this as a limitation.

#### The design

The real `tank-arena` game runs on six boards, one player per board (layout
below), in its
peer-to-peer lockstep mode (`arena/node.py`, topology `mesh`): every Pi connects
directly to every other Pi and sends its own tank's input each tick; every Pi
computes the same game from those inputs. No relay and no server; nobody hosts
the game itself.

The traffic is split by what it is:

| What | Rate (6 players, 20 Hz, 60 s, seed 42, **the rules before 2026-09-25** — the new weapon below raises the shot count) | Carried by |
|---|---|---|
| **Movement** — each tank's input, every tick | 20 per player per second | directly between the Pis (the game's own mesh) |
| **Shots and hits** — points move | 190 shots + 51 hits = 241 per match, about 4 per second | the Layer-2 under test |
| **Final score** — the balances at the end | 1 per match | the Layer-2 under test, at close |

When the game produces a shot or a hit, it hands the event to the payment client
on the same board and **carries on without waiting** (decided 2026-09-24). The
game never waits for a confirmation, so a slow system cannot change *which*
events happen. It can still change *when* they happen: the game and the payment
client share each board's CPU and the radio, so payment load can stretch the
match (see *Fairness*). That effect is measured, not assumed away.

**Next-shot deadline misses.** From the logs we know, per shot, when it was
fired, when its payment was confirmed and when the same player fired next. For
each system we report **the share of shot payments that miss the next-shot
deadline under the observed workload** — still unconfirmed when that player fired
again. This is a deadline metric only. It does **not** predict how a game that
waits for each payment would play: gating would change every later shot, the
interactions and the queueing, so no such outcome is claimed. The deadline is the
moment the player actually fired again (its next shot's generated time), as
`measure/analyze_uc2.py` implements it since 2026-10-12; earlier figures used that
shot's scheduled tick time.

Perun, Arbitrum and zkSync therefore do exactly the same job — move points and
record the final score — while movement travels the same way for all three. That
keeps the comparison like for like (parent spec §3.3).

#### Game rules for the measurement

The game's rules before 2026-09-25 produced only about 4 payments per second for
6 players (241 per 60 s match), because each tank fires at most once every 0.70 s, regains one shell
every 0.75 s, and the bots fire only when an opponent is exactly in line and in
range. Two changes (decided 2026-09-25) make the game shoot as people do:

**One trigger, AK-47 style.** There is no mode switch. A **tap** fires one
bullet; **holding** the trigger keeps firing at a fixed rate (about 10 rounds per
second). The weapon has a magazine (about 30 rounds) and a reload when it is
empty (about 2.5 s). Each bullet does less damage than the old shell, so one
burst does not end every fight. The game's per-tick input already carries
"trigger pressed: yes/no"; holding is "yes" on consecutive ticks, so no new
control is needed.

**Trigger-happy bots.** A bot fires when a hit is *plausible* — an opponent near
its line of fire, or about to drive into it — not only when it is certain, and
now and then takes a speculative shot with a low chance of hitting. It decides
how long to hold the trigger: a tap for a long shot, a burst when a hit looks
likely. All randomness comes from each bot's own seeded generator, so a seed
still reproduces the game exactly.

**Payments are unchanged in kind:** every bullet costs 1 point, every hit moves
points from the victim to the shooter.

**Frozen before any Pi measurement.** Fire rate, magazine size, reload time,
damage per bullet, hit payment and the bots' shooting behaviour are tuned **once**
in a simulation on the workstation, then frozen. Every run's manifest records
them with the seed, so the game cannot be adjusted after seeing results. The
resulting payment rate is a result of that tuning, not a target.

**Fixed, not tuned (decided 2026-10-06):** the weapon's numbers are the ones above —
**10 rounds per second** (one bullet every 2 ticks at 20 Hz), **30-round
magazine**, **2.5 s reload** (50 ticks, starting when the magazine is empty) —
and a shot costs 1 point, a hit moves 5. They set the points bound (*Perun
channel capacity*, below), so they are not part of the tuning. **Tuned:** damage
per bullet and the bots' shooting behaviour (how "plausible" is judged, how often
a speculative shot is taken, how long a burst is held). **Acceptance**, checked
in the simulation over seeds 1–20 (6 players, 20 Hz, 60 s) and for the measured
seed 42: the determinism tests stay green; every player fires in every match;
hits are 10–40 % of shots; there are kills in every match, but no player is
killed in the first 3 s (owner, 2026-10-06: a guard against a weapon so strong that the first burst after spawning ends the fight). The tuning record (values tried, the chosen set, the
statistics per seed) is committed with the frozen values. *Cost if wrong:* the
acceptance band is ours, not taken from a source; a different band only changes
the payment rate, which is reported as a result either way.

**Frozen (owner, 2026-10-06, Checkpoint 1):** damage per bullet is **6**, so a
tank with 160 hp dies after 27 hits. The bots fire when an opponent is within
**40** units of the line of fire (`plausible_lane`), or will enter it within 6
ticks (`lead_ticks`). Beyond 240 units (`long_range`) a bot taps; closer, it
holds a burst of 3–8 bullets. A speculative tap has a chance of 0.02 per tick
(`spec_p`). After every tap or burst the bot releases the trigger for 4 ticks
(`release_ticks`, 0.2 s). The digest of all game and bot parameters is
`777169e581c58faa94fbb1edce41823e038d00cf9bf5b06332755700b7e6d1d1`; the record
is `tank-arena/TUNING.md`. Seed 42 then gives 813 shots and 257 hits, that is
1 070 payments, or 17.8 payments/s (a result, not a target). The mean over
seeds 1–20 and 42: hits are 0.304 of shots, 7.7 kills per match, 18.0
payments/s.

#### Points, not money

Points are **our own ERC-20 token with no value outside the game**. This is a
deliberate choice against gambling: nobody stakes or wins money. The rules are
the ones `tank-arena/arena/world.py` already implements:

- every player starts a match with **10 000 points**, minted for free (revised
  2026-10-06 from 1 000: it is the smallest round number that covers the
  worst-case outflow of a 60 s match, see *Perun channel capacity*);
- **no balance goes below zero, on any system** (decided 2026-10-06): a Perun
  channel cannot hold a negative balance, so the game may not produce one either.
  The rule lives in the game, so it is the same for all three systems and the
  event logs stay identical: a tank whose balance is below the shot cost cannot
  fire, and a hit moves at most the victim's remaining balance. With 10 000
  points and a 60 s match neither case can occur (the worst case leaves at least
  1 264, *the bound* below); the rule exists so that a mistake in the bound shows up as a capped
  payment in the log, not as a payment no system can make;
- a **shot costs 1 point**, paid to the `arena` account (ammo);
- a **hit moves 5 points** from the victim to the shooter;
- **the final balances are the scoreboard**, recorded on-chain at close and signed
  by the players. Once recorded it cannot be altered, but it reflects the game only
  as far as the players paid honestly (see *Scope*).

The `arena` account is the hub on Perun and a fixed address on the rollups; it
only receives.

The token contract is a standard ERC-20 with a mint function. The backend we
already use ships one (`peruntoken`, deployed by `DeployPerunToken`). It is
deployed once per chain: on the L1 for Perun (the channels live there), and
**directly on each rollup** for Arbitrum and zkSync. Points are minted where they
are used, so they never need to be bridged from L1 to a rollup.

`--asset eth` remains as a **measurement-only** setting (plain ETH transfers
instead of points), meant to give the asset-type gas delta the parent spec §6 asks
for. It is not a game mode, and it was not run: the delta is taken from UC1's ETH
transfers against UC2's token transfers on the same rollups (*Run parameters*).

#### Who pays which fee

A token cannot pay a fee: every network accepts only ETH. **Fees are paid in ETH
by whoever sends the transaction.** On the testbed all ETH is play money from
pre-funded test accounts; the thesis reports fees in gas units.

| Moment | Perun (channels on the L1) | Arbitrum / zkSync |
|---|---|---|
| Start of each run (the snapshot has no ETH on the rollup, §4) | — | each player bridges some ETH onto the rollup to pay fees there — **timed as the rollup's *Open*** |
| Match start | each player: `approve` + deposit points into the channel with the hub; the hub deposits its side — **timed as Perun's *Open*** | 10 000 points per player are minted on the rollup (in the run's setup, before *Open*, untimed) |
| During the match | **no fees**: a shot or hit is a signed channel update | **one transaction per shot and per hit**, paid by the player whose points move |
| Match end | each player and the hub: close the channel, points return as the final score — **timed as *Close*** | nothing extra: the score is already on the rollup; *Close* is the withdrawal submission, the 7-day / proof delay comes from documentation (parent spec §11.4) |

This trade-off — Perun pays to open and close but nothing per shot; the rollups
pay nothing to open but a fee for every shot — is the cost result of UC2.

**Known wrinkle, kept on purpose in the first version:** a hit moves points
*from the victim*, so the victim sends that transaction and pays its fee. The fix
is a small game contract that the shooter calls to record a hit; it is noted,
not built.

#### What the boards run

| Board | UC2 role |
|---|---|
| `bath0` (Pi 4) | the stack under test: the L1 for Perun, or the whole rollup stack |
| `bath1` (Pi 3B+) | **player 0 and the host**: runs the Perun hub; also stays the time server |
| `bath2` (Pi 3B+) | player 1 |
| `bath7` (Pi 3B) | player 2 |
| `bath4`, `bath5`, `bath6` (Pi 3B) | players 3–5 |
| `bath3` (Pi 3B) | idle (dead since 2026-10-05, §2) |

**`bath7` replaces `bath3`** (owner, 2026-10-06): `bath3` went offline on
2026-10-05 ("no route to host"; a board power fault, so it is dead, §2). Both are
a Pi 3 Model B Rev 1.2 with the same kernel (`deploy/FLEET.md`), so `bath7` is
**player 2 for the whole UC2 suite**, and those runs are the final ones. `bath7`
had been taken out of UC1 on 2026-10-02 for a 7–11 Mbit/s link; on 2026-10-06 it
read −51 dBm, 72 Mbit/s out, 43 Mbit/s in (read by hand), so it is used, in UC2
and, in `bath3`'s place, in the final UC1 series (§7.2). The UC2 manifests record
no link rate (board facts carry it only since 2026-10-11, §6); every board's
signal is in the recorder series. UC2's per-board traffic (the 20 Hz inputs to
five peers and a few payments per second) is far below a UC1 customer's 68 KB/s
stream.

**The player with the best hardware hosts** (decided 2026-09-24), as players of
hosted multiplayer games commonly choose. The hub is only needed to open and close
channels; during play, points move directly between players, and the host cannot
steal points (channel security). Since the second Pi 4 failed (§2), the host is a
Pi 3B+ — slightly faster than the Pi 3Bs, but not the same board class as the
rollups' sequencer on `bath0`; the thesis states this.

**The players are not equal, and do not need to be.** The thesis compares
systems, not players. Two rules keep that comparison fair:

1. **Identical layout for all three systems.** Player 0 sits on `bath1` in the
   Perun, Arbitrum and zkSync runs alike; only in Perun does that board also run
   the hub, because the rollups have no host role.
2. **Host and guests are reported separately.** The host and the hub share
   `bath1`, so the channel messages between them cross no radio; the open and
   close transactions still travel over the radio to `bath0`. The headline figures use
   the five guests, who see what a normal player sees.

In lockstep every tick waits for all six inputs, so the game runs at the pace of
the slowest board; a faster host does not play faster.

| Experiment | Stack | Points move through |
|---|---|---|
| `uc2-perun` | perun | virtual channels through the hub on `bath1` |
| `uc2-arbitrum` | arbitrum | token transfers on the rollup |
| `uc2-zksync` | zksync | token transfers on the rollup |
| `uc2-perun-distant`, `uc2-arbitrum-distant`, `uc2-zksync-distant` (**future work**, owner, 2026-10-11) | each | as above, with added network delay on the player boards (see *Distant players*) |
| `uc2-movement-onchain` (**future work**, owner, 2026-10-11) | arbitrum, zksync | movement as transactions too, to show where each rollup breaks: by throughput Arbitrum on `bath0` should carry the 120 tx/s of movement (it carried ≈ 370 payments/s in UC1 `w10`, a lower bound; inferred), but not by latency (≥ 250 ms per block = about 5 game ticks late); zkSync (4–9 tx/s on the Pi, §7.1) has neither. Not possible on Perun through a hub (see below). |

#### Distant players (`uc2-<stack>-distant`, future work)

**Not built and not run (future work, owner, 2026-10-11)**; the design is kept
for the thesis's future-work section. The same UC2 run, with artificial network delay on the six player boards
(`bath1`, `bath2`, `bath7`, `bath4`…`bath6`) through the `netem` role: **20 ms ± 5 ms added to each
board's outgoing traffic** (normal distribution). Delay applies on the way out
of each board, so two players see about **40 ms** round trip between them, and a
player sees about **20 ms** to the stack on `bath0` — on top of the real Wi-Fi.
That is roughly players in different cities of one country. The profile is
fixed before measurement and recorded in the manifest; it is cleared after the
run (netem does not survive a reboot either).

It shows two effects the single-room runs cannot:

- **Lockstep:** every tick waits for the slowest input, so the game's own tick
  lag grows with the delay.
- **Payments:** Perun's point transfers travel directly between players, so each
  one now crosses the player-to-player delay; a rollup transfer crosses only the
  player-to-stack delay.

#### Limitations: one Wi-Fi network is the best case

All boards share one Wi-Fi network, so every board can reach every other
directly and round trips are a few milliseconds. On the internet, two things
change, and the thesis states both:

1. **Reachability.** Home routers use NAT: devices share one public address, and
   incoming connections are blocked. Two players behind their own routers cannot
   simply connect to each other. The standard remedy is *hole punching* through a
   small rendezvous server, which works for most home connections but often fails
   behind mobile networks (carrier-grade NAT); those pairs fall back to a *relay
   server*. Video calls in the browser (WebRTC) and peer-to-peer games work this
   way, so real "peer-to-peer" usually means "direct where possible, via a relay
   where not", with a small server to find each other.
   - This affects **the movement traffic and Perun alike**: during play, Perun's
     points move directly between the two players, not through the hub.
   - It does **not** affect the rollups: a player only connects outward to the
     sequencer, a public server.
   - go-perun ships a transport built on libp2p, which includes hole punching and
     relay fallback; our launcher uses plain TCP, which works only where every
     machine can reach every other. NAT traversal is not built or measured here.
2. **Distance.** Round trips between cities are often 20–100 ms, against a 50 ms
   tick. The `-distant` runs (future work) would measure one point of this; real lockstep games hide
   it with a fixed input delay or rollback, which this game does not implement.

#### Fairness: the same game on all three systems

The game is deterministic from `(seed, players, tickrate, duration)`. Every Pi
writes its event log. Before the first UC2 run, one Pi generates the **reference
event log** from the four parameters (on arm64, the architecture every player
runs; amd64/arm64 float equality is untested and therefore not relied on). After
each run every board's log hash is compared with the reference. Match → all three
systems saw the identical **logical** events. Mismatch → the run is flagged and not
used.

**Identical events are not identical load.** The same 1 000 events over 60 s and
over 90 s are different loads. Because the game, the payment client and the radio
share each board, payment processing can slow the lockstep indirectly. So every
event carries **five timestamps**: the intended tick time, when the game generated
it, when it entered the payment queue, when it was submitted, and when it was
confirmed. Each run reports its achieved match duration and tick lag per system.
The stretch is a **measured application effect**, reported with the results, not a
confound claimed to be eliminated.

#### Payment ledger: when payments and the game diverge

Both use cases need the same machinery between the event source (game or sensor)
and the Layer-2. The spec requires it before any code is written:

- **Event ownership and stable IDs.** Each payment event belongs to exactly one
  sending board and carries an ID derived from the event (match, tick, player,
  sequence), stable across retries.
- **No duplicates.** A retry reuses the ID; a payment is never made twice.
- **Bounded queue** per board, with a fixed maximum length; an event that cannot be
  queued is recorded as failed, not dropped silently.
- **Retries** with a fixed policy (count and back-off), the same for all systems.
  Exception: Perun retries no payment, because a timed-out update may already be
  signed by the receiver (§7.2, decided 2026-10-01).
- **Pipelined submission, never one-at-a-time.** A sender that waits for each
  confirmation measures its own round trip, not the system: at about 250 ms per
  Arbitrum block it would cap a player at ~4 payments per second (the spike's
  "~4 tx/s" artefact), far below the automatic weapon's rate. Rollup senders
  therefore submit without waiting, keep nonces in order per account, and hold a
  **bounded number in flight per account**. The bound follows what the real network
  allows: for zkSync at most **20** (mainnet's per-account limit; the local chain's
  2 500 is for the capacity probes only); Arbitrum 64. The bound is recorded with
  every run (experiment file); the in-flight count at any moment follows from the
  per-payment submitted and confirmed timestamps. Perun allows one update in flight
  per channel by protocol (the payment client may hand up to 64 to its node, which
  sends them one at a time). **On the Pi cluster this is the bottleneck at UC1's
  10 ms window** (corrected 2026-10-04): an update takes about 30 ms there
  (confirmation p50 ≈ 30 ms in the final `w10` series), so in the final UC1 series
  (`w10`, measured) one channel carried ≈ 28–31 updates per second to a Pi 3B
  customer on 2.4 GHz and ≈ 40 per second to `bath2` (Pi 3B+, 5 GHz). On
  the workstation over loopback an update takes 0.82 ms (p50, p99 1.21 ms;
  measured 2026-10-03), so the limit is the boards' CPU and Wi-Fi, not the
  protocol alone (inferred from the two).
- **Drain deadline.** After the match (or the recording) ends, the queue gets a
  fixed time to drain; whatever is still pending then is *unresolved*.
- **Outcome per payment**, exactly one of: *generated*, *queued*, *submitted*,
  *succeeded*, *failed*, *unresolved*. **Succeeded** means included with a success
  status — a mined transaction can still revert, so a receipt alone is not proof.
  For Perun it means the update is signed by both parties.
- **Reconciliation.** At the end, the succeeded payments must add up to the final
  balances — the game's scoreboard (UC2) or the windows received (UC1). Any
  difference is reported.

**Perun channel capacity (UC2).** A player's points are split across its channels:
one ledger channel with the hub (which also receives the ammo payments to `arena`)
and five virtual channels, one per opponent. A channel can run dry while the
player still has points in total — for example, when one opponent hits the same
player again and again. Channels are **not** sized from the trace, which a real
player could not know. Every channel is funded from a **worst-case bound derived
from the game rules** — the most points one match can move across that channel —
and the bound is disclosed. Points have no value, so generous funding costs nothing
real. A payment that still finds its channel empty is recorded as *failed*.

**The bound (decided 2026-10-06).** Let *S* be the most bullets one tank can fire
in a match: the trigger held from the first tick to the last, never killed. With
the fixed weapon (one bullet every 2 ticks, 30 per magazine, 50 ticks reload) a
magazine and its reload take 108 ticks (29 gaps of 2 plus the 50-tick reload,
which starts at the last bullet because timers run before firing), so a 1 200-tick
match allows 11 full magazines (the last ends at tick 1 188) plus 6 more bullets:
**S = 336**. A test in `tank-arena` computes *S*
from the frozen implementation, and the run setup takes it from there, not from
this text. With *n* = 6 players:

| Channel | Funded side | Worst case | Points |
|---|---|---|---|
| virtual channel A–B | each player's side | every bullet B fires hits A: 5 × *S* | **1 680** |
| ledger channel player–hub | the player | ammo 1 × *S* plus its five virtual stakes 5 × 1 680, all from one deposit of the player's whole balance | 336 + 8 400 = 8 736 ≤ **10 000** |
| ledger channel player–hub | the hub | the hub mirrors each virtual channel's opposite side in that ledger channel: 5 × 1 680; it never pays ammo | **8 400** |

The player deposits all 10 000 points, so the scoreboard at close is its whole
balance; the hub deposits 8 400 into each of the six ledger channels (50 400,
minted to the hub's account). In the worst case a player still ends with 10 000 − 8 736 = 1 264 points. The condition **start balance ≥ (1 + 5 × (n − 1)) × S**
is checked by the run setup; if a later change to the weapon or the match breaks
it, the start balance is raised, never the channels sized from a trace. On the
rollups there are no channels: each player holds its 10 000 points in one
account, and the same bound says no transfer can find the balance short.

#### Run parameters (decided 2026-10-06)

Everything a UC2 run needs, so the suite can be built and run without further
decisions. "Owner" marks the author's decisions; the rest are rulings made on the
author's behalf, each with what it costs if wrong. Where UC1 already settled a question,
UC2 takes the same answer, so the two use cases differ only where the workload does.

| Item | Value | Source / cost if wrong |
|---|---|---|
| Match | 6 players, 20 Hz, **1 200 ticks (60 s)**, seed **42**, topology `mesh` | owner |
| Repetitions | **3 per system, the same seed** — one reference event log; the spread between reps is the system's own | owner |
| Experiments | `uc2-perun`, `uc2-arbitrum`, `uc2-zksync`, files `deploy/experiments/uc2-<stack>.env`, plus one `uc2-<stack>-smoke` match each before its series (not a measurement) | as UC1's smoke runs |
| Layout | `bath1` player 0 + hub, `bath2` 1, `bath7` 2, `bath4`–`bath6` 3–5 (`PLAYERS=`), `bath3` idle (`IDLE_BOARDS=`, dead); `bath0` the stack | owner |
| Wi-Fi | mixed: `WIFI_5GHZ_BOARDS="bath1 bath2"` | owner |
| Queue per board | `MAX_QUEUE=10000` | as UC1; a 60 s match produces at most 336 + 336 × 5 events per player, so it never fills |
| In flight | Arbitrum 64, zkSync 20 per account, Perun 1 per channel (protocol; `MAX_IN_FLIGHT=64` bounds what the payment client hands its node) | as UC1 |
| Fees | fee cap 10 × base fee; zkSync a fixed gas limit of **330 000**: 2 × the largest `eth_estimateGas` (164 287) for a points `transfer` in a 20-transfer sample on the workstation chain (2026-10-06), rounded up (`measure/zksync.py`; planned: from the smoke match); Arbitrum the estimate plus the UC1 margin | as UC1 (`measure/arbitrum.py`, `measure/zksync.py`) |
| Receipts | `RECEIPT_MODE=own`: each player fetches only its own receipts | as the final UC1 series |
| Drain | `DRAIN_S=60` after the last tick, as in UC1: rows still queued at the deadline are never sent (*unresolved*); payments already in flight (at most `MAX_IN_FLIGHT` per account) may execute after it and are counted as *late* (`late_executed`) from the nonces (rollups) or the channel (Perun) | as UC1. Predicted: zkSync's backlog would mostly show as unresolved. Measured: 11.5 % unresolved (6.4–18.8 % per run); the burst confirmed at 7.5–8.9 per second, faster than UC1's sustained rate (§7.1) — a result |
| Retries | rollups: the UC1 policy; **Perun: none** — an update that times out (10 s) is *unresolved* and breaks that channel (§7.2, Q6) | as UC1 |
| Collisions | both ends of a virtual channel pay (hits go both ways): the existing asymmetric policy (`perun/hub-demo/update_policy.go`); yield 200 ms, at most 10 retries (owner, 2026-10-06) | built and tested 2026-09-21 |
| Lockstep stall | no tick for **30 s** → the match aborts, the run is failed | the UC1 sensor's stall tolerance; a longer stall is a lost run, not latency |
| Game → payment client | each board's game writes its shots and hits to the local payment client over a **Unix socket**, as the UC1 customer (`measure/payclient.py`); the game never waits; the payment client must be listening before tick 0 or the run aborts | ruling; cost: none expected, the UC1 path is proven |
| Event ID | `(match, tick, player, seq)`; *seq* counts that player's payment events within the tick | §7.3 *Payment ledger* |
| Hashed event log | logical events only, indexed by tick, **no wall-clock**; the five timestamps go to the payment ledger, keyed by event ID | needed for the hash to match across systems |
| Reference log | generated once on `bath2` (arm64) with the in-process simulation and the frozen config; committed with its SHA-256; every board's log is compared after every run, a mismatch flags the run | §7.3 *Fairness* |
| Setup (untimed) | Perun: the points token on the L1, 10 000 per player and 50 400 to the hub. Rollups: the token deployed on the rollup for the run, `mintBatch` 10 000 per player, the `arena` account a fixed address that only receives | ruling: a fresh token per run instead of one in the snapshot; cost: none, setup is not timed |
| *Open* | Perun: per player `approve` + deposit, the hub's deposit, ledger channels, then the 15 virtual channels off-chain (timed separately). Rollups: per player an ETH bridge deposit of 1 ETH for fees, as UC1 | §7.3 *Who pays which fee* |
| *Close* | Perun: virtual channels settle into the ledger channels, then per player `concludeFinal` + withdrawal, timed from finalize to the confirmed withdrawal; the hub withdraws last (as UC1 Q2). As built, the timed Perun *Close* also contains the waits at the close barriers (every player done, virtual channels settled, every ledger channel including the hub's settled), so it is longer than one player's own settlement (§8). Rollups: the withdrawal submission | as UC1 |
| Not in the suite | `-distant`, `uc2-movement-onchain`, `perun-on-arbitrum` (future work, owner, 2026-10-11); `--asset eth` runs: the asset-type gas delta is taken from UC1's ETH transfers against UC2's token transfers on the same rollups | ruling; cost if wrong: one extra run per rollup |

**Reported per system** (headline: the five guests; the host `bath1` separately):
payment latency, generated → confirmed and submitted → confirmed, p50 and p99,
for shots and hits separately and together; outcome shares (*succeeded*,
*failed*, *unresolved*, *late*); goodput; the **next-shot deadline miss share**
(shots only; each player's last shot has no next shot and is left out);
**match stretch** (achieved duration ÷ 60 s); **tick lag** per board (actual
tick time − intended tick time, p50, p99, max); the event-log hash check;
reconciliation of succeeded payments against the final balances; *Open* and
*Close* time and gas; gas per shot and per hit transfer; CPU and throttle per
board. Fees in gas units. `measure/analyze_uc2.py` produces this, as
`analyze_uc1.py` does for UC1.

#### New code

State on 2026-10-06: **exists** = built for UC1 or earlier and reused; **new** =
still to build then. **All of it was built and tested before the UC2 series
(2026-10-06, §8 step 6).**

- **new — Weapon and bots** in `tank-arena` (rules above), with the determinism
  tests kept green; the **no-negative-balance rule** in `arena/world.py`; a test
  that computes *S* from the frozen config; the tuning record.
- **new — Game → payment bridge.** `arena/node.py` emits each shot and hit to the
  local payment client over the Unix socket instead of only writing it to the
  log; the hashed log without wall-clock; the 30 s stall abort.
- **exists — Payment ledger** (above, `measure/ledger.py`), shared by UC1 and UC2;
  **new:** the UC2 event ID and the game's timestamps (intended tick time,
  generated) feeding its five timestamps.
- **new — Perun live mode.** `hub-demo -mode client` today replays a trace file;
  it gains a live input (events from the local game): ammo through the ledger
  channel to the hub, hits through the virtual channel to the shooter. The
  launcher's funding becomes asymmetric (*the bound*, above: player deposit
  10 000, hub deposit 8 400 per ledger channel, virtual stake 1 680); today it
  deposits the same amount on both sides and caps virtual stakes at the ledger
  deposit.
- **new — Rollup sender for points.** The UC1 sender (`measure/submitter.py`,
  pipelined, nonces in order) with the same live input and one ERC-20
  `transfer` per event, the token deployed and minted in the run setup.
- **exists — Points token** on all three systems (§8 step 2).
- **new —** a test of Perun's mesh under **paced, live** input (the existing mesh
  test replays closed-loop).
- **new — Running and analysis:** the UC2 roles in `deploy/cluster` (players,
  hub, reference-log check, collection of every board's event log), the
  experiment files, `measure/analyze_uc2.py`.

#### Considered and rejected (2026-09-24)

1. **All game data inside Perun, through the hub.** A feasibility check on the
   workstation (throwaway code, not in the repo) put the game's input frames and
   the shot payments into a virtual channel with an app: 60 s at 20 Hz in both
   directions, 2 400 updates, median 0.9 ms, clean cooperative close. But when a
   player vanished, the on-chain dispute **reverted: "cannot have app"**. Perun's
   Ethereum adjudicator forbids apps in virtual channels (`registerSingle`), while
   go-perun v0.15 does not check this off-chain. Such a channel is safe only while
   everyone is honest and online. Rejected.
2. **All game data inside Perun, in direct channels between players.** The same
   check with a direct (ledger) channel passed the dispute: the remaining player
   got 15 ETH back minus 0.00028 ETH gas in 13 s. But every pair of players needs
   its own on-chain channel: n(n−1)/2 = 15 for 6 players, 190 for 20, against n
   with a hub. (The hub design also has one channel per pair — 15 virtual
   channels for 6 players — but those open off-chain through the hub, with no
   transaction and no fee. What grows with the number of opponents there is the
   stake each player locks from its single deposit.) It also needs per-player time slots: in lockstep both ends of a
   channel send at the same instant, and with one update in flight per channel
   the second player was starved until its ticks were shifted by half a tick.
   Rejected on scalability; reported in the thesis as an analysis with these
   numbers.
3. **The game waits for each payment** (pay before you fire). A slow system would
   then play a different game, and the event logs would no longer match. Its
   effect is derived from the logs instead (see *The design*).
4. **The hub on one of the Pi 3 players.** The strongest "no server" story, but
   that board would carry a player, a payment client and the hub. Replaced by the
   best-hardware host on `bath1`.
5. **Money at stake** — entry fees and prizes, casino-style. Rejected by the
   project owner: the thesis does not build a gambling game.
6. **Pay money during the match and refund it at the end.** On Perun the refund is
   free; on the rollups it needs extra transactions that exist only because of
   the design, which would bias the cost comparison. Replaced by points without
   value.
7. **A relay for movement** (`arena` relay topology). The relay is a server.
   Rejected in favour of the peer-to-peer mesh.
8. **Several weapon profiles swept from slow to fast.** Replaced by one weapon and
   one bot behaviour, fixed before measurement: simpler, and closer to how people
   play.
9. **Movement through Perun, coded into tiny payment amounts** (to get around the
   hub's no-app rule). Technically possible, but each input must reach every
   opponent's channel — 240 channel updates per second for 4 players, 600 for 6 —
   with lockstep collisions in every channel, and it protects nothing beyond plain
   signed messages. Dropped by the project owner.
10. **Replaying a payment trace file** (the previous version of this section).
   Replaced by the live game, so that movement traffic, game CPU and payments
   share the Pis and the radio as they would in reality; the event-log hash check
   keeps the workload identical.

### 7.4 Perun on Arbitrum (`perun-on-arbitrum`, future work)

**Not part of the thesis measurements (owner, 2026-10-11):** a sound idea that
could be measured, named in the thesis as future work; not built.

Perun's on-chain part — the Adjudicator and the Asset Holder — is ordinary
Ethereum contracts, and Arbitrum runs Ethereum contracts unchanged. Deployed on the
Arbitrum stack on `bath0` instead of on Anvil, a channel's **open and close become
Arbitrum transactions** (cheap, blocks about every 250 ms), while payments inside
the channel stay off-chain and free. The launcher needs only a different chain
address and chain ID.

**Experiment:** deploy Perun's contracts on the Arbitrum stack; run UC1 at one
window size (and possibly one UC2 match); record gas and latency of open and
close. Payment latency does not change, because payments never touch the chain.
It feeds the cost scenarios of parent spec §5.2 ("Perun on the base chain" vs
"Perun on Arbitrum").

**Caveats the thesis states:** Perun then inherits Arbitrum's security — a party
whose dispute transaction the sequencer refuses can force it in only after about a
day, so for full safety Perun's dispute window on Arbitrum must be longer than
that (our short test windows are for measurement only); and moving funds from
Arbitrum back to the base chain still takes Arbitrum's 7-day withdrawal period.
**Perun on zkSync is out:** zkSync does not run Ethereum contracts unchanged, and
whether Perun's contracts survive zkSync's own compiler is unknown.

## 8. Order of work, verification, and first contact

**Riskiest first (revised 2026-09-28).** The questions that could change the
thesis most are on hardware, so they are answered first. **Rule:** no thesis
measurement is recorded before the points token works on all three systems;
feasibility checks on the Pis are not measurements.

1. **Pi feasibility**, by hand, no installer yet:
   - ✅ go/no-go on the Pi 3s' idle memory under Ubuntu — **go** (2026-09-29:
     500–640 MB of 899 MB free minutes after first boot, with snaps and automatic
     updates still active);
   - **power, first check:** on 2026-09-29 four Pi 3Bs reported under-voltage and
     throttling (`get_throttled` = `0x50005`); after their supplies were replaced,
     all eight read `0x0` — **at idle only**; under load the Pi 3Bs remain
     undervolted (next item);
   - ⚠️ **power and speed of the Pi 3s** (2026-09-29): under full load the five Pi 3Bs
     (`bath3`…`bath7`) are undervolted in nearly every sample and run at 600 MHz —
     a fixed hashing job took 14.1 s, against about 7 s expected at their normal
     1.2 GHz, so **about half speed**. Several supplies and cables were tried with
     the same result; both Pi 3B+ boards run at full speed (`bath1` clean, `bath2`
     borderline). Accepted: no hardware is bought. The 3Bs are consistently slow
     devices, identical for all three systems; `bath0` and `bath1` have clean power.
     Details: `deploy/FLEET.md`; consequences: §5, §6, U6 and *Limitations* below;
   - ✅ **Wi-Fi power saving off** on all eight boards (2026-09-29,
     `deploy/wifi-powersave-off.sh`): a ping between two Pi 3s took up to 1.1 s
     before, 20–36 ms after;
   - ✅ **automatic updates off** on all eight boards (2026-09-29), after an update
     interrupted by a power cut had left `bath1`'s package system half-installed
     (repaired). `sudo` needs no password on the boards, so such steps run from the
     workstation;
   - ✅ **heat and cooling:** bare, `bath0` reached 81 °C within 90 s of full CPU load
     and ran the zkSync sustained run at the soft temperature limit (below). No
     hardware is bought (the boards are on loan from the university); on
     2026-09-29 the **two heatsinks of the defective Pi 4 were moved onto `bath0`**:
     about 56 °C under the Arbitrum sustained run, limit never reached. From then
     on `bath0` runs with them; runs made before are marked bare;
   - ✅ **`bath0` prepared by hand** (2026-09-29): automatic updates and snap
     refreshes off, Docker 29.1.3 with Compose, `liburing2`; the user is in the
     `docker` and `video` groups, so `get_throttled` needs no `sudo`;
   - ✅ **the arm64 `zksync_server` starts on `bath0`** (2026-09-29): SHA-256 as in
     U2, all libraries resolved, `--version` → `31.5.0-non-semver-compat`. This
     proves the binary runs on the board and OS, not yet that it runs a chain;
   - ✅ **zkSync starts on `bath0` from state made on the PC** (2026-09-29, U1 for
     zkSync). The recovered chain was run on the PC (one transfer confirmed), stopped
     and packed: PostgreSQL as `pg_dump` (427 MB), reth's data directory copied raw
     (15 MB packed), the chain directory with its RocksDB, and the zkSync build
     outputs (compiled contracts and `etc/`, 65 MB packed), 553 MB in all,
     copied in about 1.5 minutes. On `bath0`, the x86 reth data started as is and kept mining; `pg_restore`
     took 2 min 13 s; the arm64 server answered after 6 s at the PC's last block
     and confirmed a transfer (block 2712, 1.7 s round trip from the PC). Idle with
     the stack up: 60 °C, 1.2 GB used. **Finding for §4:** the server reads the
     compiled contract files from its working directory at run time, so a snapshot
     must carry them. Not yet done: a sustained run (U3), which waits for cooling;
     and a snapshot from a fresh init with the points token;
   - ⚠️ **zkSync batch window.** With the chain's local-development setting
     `block_commit_deadline_ms: 2000`, every L1 batch on `bath0` closed after a
     single transfer: preparing the next batch (catching up the RocksDB copy on
     the SD card) takes 6–10 s on the Pi, longer than the 2 s window, so the chain
     managed about one transfer every 5 s. With **20 000 ms** (changed on `bath0`'s
     copy only; the original is kept as `general.yaml.orig-2000ms`) batches fill
     (17 to 149 transfers each). **Open decision** for the thesis setup;
   - ⚠️ **zkSync capacity on the Pi 4: about 4 transfers per second**, all four cores
     busy (batches about every 35 s, up to about 150 transfers each), against
     20–30 on the workstation. At 10 per second, 234 of 600 were still
     unconfirmed after 2 minutes (all arrived later). **Recorded as a finding**:
     UC1 at the 1 s window already needs 6 payments per second, so zkSync on
     Pi-class hardware cannot keep up with UC1; this is reported, not engineered
     around;
   - ✅ **zkSync on `bath0`, sustained** (2026-09-29, bare, 20 s window): 2 transfers
     per second for 20 minutes from `bath1`, **2 400 of 2 400 confirmed**, none
     lost; confirmation median 16.7 s, p99 51 s. **Cause (investigated 2026-09-30,
     `archive/bench-workstation/results/zksync-pi-cpu-2026-09-30.md`): not the batch window but the
     CPU** — about 80 % of the Pi's CPU goes into KZG / BLS12-381 commitment
     arithmetic for every batch (17× slower than the workstation, same generic
     code on both; our build is fine), so a commitment backlog starves block
     sealing. The workstation confirms in 0.6 s with the same 20 s window.
     Tried the same night, neither helps (same report): a build with
     `-C target-cpu=cortex-a72` commits batches exactly as fast (29.3 s per batch)
     and its confirmation times lie within the run-to-run spread;
     `commitment_generator.max_parallelism` is not wired into the main server, and
     fewer KZG threads (`RAYON_NUM_THREADS`) make things worse. The official build
     stays. Run-to-run spread at the same setting is large (confirmation medians
     32–52 s), so zkSync figures on the Pi need repetitions and a reported spread;
     81 °C on average, soft temperature limit active in 56 % of samples, clock
     1761 MHz on average (full: 1800); no undervoltage; memory at most 2.8 GB;
     SD card 1.2 GB written (about 1 MB/s), busy 13 % of the time;
   - ✅ **Arbitrum starts on `bath0` from state made on the PC** (2026-09-29, U1 for
     Arbitrum). The PC's stack (L1 geth, redis, sequencer) was started, confirmed a
     transfer, and was stopped cleanly (its previous stop had been a hard kill); its
     five data volumes were packed raw (46 MB). On `bath0` a compose file with only
     those three services, the sequencer on the official
     `offchainlabs/nitro-node:v3.9.6-91bf578` arm64 image, started in 9 s at the PC's
     last block, posted batches to its L1 and confirmed a transfer (block 2983).
     Memory with the stack up: sequencer 159 MB, geth 132 MB, redis 2 MB; 60 °C,
     `0x0`. Not yet done: a sustained run;
   - ✅ **Arbitrum on `bath0`, sustained** (2026-09-29, with heatsinks): 10 transfers
     per second for 20 minutes from `bath1`, spread over 16 accounts, **12 000 of
     12 000 confirmed**; confirmation median 277 ms, p99 402 ms. 57 °C on average,
     limit never active; memory at most 1.65 GB; SD card 2.4 GB written (about
     2 MB/s), busy 8 %. 10 per second is the rate chosen for this endurance run,
     not a ceiling: the feasibility sender submits one transfer per account at a
     time (the sequencer rejects out-of-order nonces and answers only once a
     transfer is sequenced), which gave about 14 per second over 16 accounts. The
     ceiling on the Pi is for the capacity probes (§7.1);
   - ✅ **time server and one kernel** (2026-09-30): chrony on all eight, `bath1` the
     only source (offsets 0.1 µs–1.7 ms right after setup); all boards on kernel
     `6.8.0-1065-raspi`, held;
   - ⚠️ **band after reboot** (2026-09-30): all three dual-band boards were found on
     5 GHz — the pin does not survive a boot reliably. **The sustained runs and the
     overnight zkSync tests therefore had `bath0` on 5 GHz**, the Pi 3Bs on 2.4 GHz
     (feasibility only, no thesis data affected). Fixed with a boot check
     (`deploy/wifi-pin-at-boot.sh`); the pre-flight radio check (§5) stays the
     guarantee;
   - ✅ **Perun across four boards** (2026-09-29), in the UC2 layout: Anvil 1.7.1
     (official arm64 build) on `bath0`, the hub on `bath1`, Alice on `bath2`, Bob on
     `bath3`, the launcher cross-compiled (static arm64, commit `c2a0f8c`). Ledger
     channels opened and funded, one virtual channel, 100 payments from Alice
     without failure (p50 26 ms, p99 46 ms, about 37 per second one at a time), both
     ends reporting the identical final state (version 100), every channel settled,
     all processes exit 0. Findings for the installer and `cluster run`:
     - **Wi-Fi power save** is presumably on (a `bath1`→`bath2` ping took up to
       1.1 s; `iw` is not installed, so it could not be read). The `common` role
       must install `iw`, switch power save off and assert it — this matters for
       `yield_timeout_ms` (1 s);
     - the hub's first dial to `bath0` failed with *no route to host* before the
       boards had found each other on the Wi-Fi; a retry worked. Pre-flight should
       ping every pair before starting;
     - clocks are synced to the internet only, last corrections 27–60 ms; the
       `time-sync` role (`bath1` as source) is needed before paced runs;
     - processes started over SSH must be fully detached (`setsid`, input from
       `/dev/null`), or the SSH session waits for them.
   - ⚠️ **Perun UC1 feasibility probe** (2026-10-03, plan
     `2026-10-01-uc1-perun-measurement-path` Task 2; Go only, no Python, no
     recorder): Anvil 1.7.1 on `bath0` (`--block-time 1`), `perun-node -mode
     uc1-sensor -customers 5` on `bath1` (account 100), one direct ledger channel
     from each of `bath2`…`bath6` (accounts 101…105, `bath7` out), each customer a
     `uc1-customer` probe paying open-loop for 60 s through the one-update-in-flight
     queue; commit `917f3f4`, static arm64 build with go1.26.4 (SHA-256
     `1b6c2650…`). All four runs: every payment confirmed, **no update timed
     out**, customer and sensor agree on the final version and balances of every
     channel, every channel settled, sensor exit 0 (open 0 gas, close 5 tx /
     ≈214 000 gas). Update latency = submit → confirmed at the customer:

     | Run | Offered per customer / at sensor | Achieved per customer | Update p50 / p99 / max (ms) | `bath1` sensor CPU (board busy) |
     |---|---|---|---|---|
     | p1 | 1 / 5 per s | 1.0 | 29–32 / 52–125 / 125 | 8 % (8 %) |
     | p10 | 10 / 50 per s | 10.0 | 25–29 / 61–78 / 103–266 | 60 % (24 %) |
     | p100 | 100 / 500 per s | **29.6–33.4** (sum 154) | 28–32 / 71–82 / 280–968 | 148 %, max 194 % (53 %) |
     | p100, persistence off | 100 / 500 per s | 30.2–34.3 (sum 157) | 27–31 / 71–80 / 193–389 | 137 %, max 166 % (50 %) |

     **The limit at 100 per second is the channel, not the sensor or the radio.**
     The update round trip is about 30 ms at every load (1 to 100 per second), and
     each channel's achieved rate equals 1 / its mean update latency (stop-and-wait,
     one update in flight). All five channels flatten at the same ceiling, while
     `bath1` still has CPU headroom (148 % of 400 %) and the radio is not
     collapsed (ping `bath1`→customers p50 10 ms instead of 6.5 ms, no loss). The
     measured figure is the ≈30 ms round trip; its split is only estimated: CPU
     share ÷ achieved rate gives roughly 10 ms of sensor CPU and 13–15 ms of
     customer CPU per update (derived, not measured per update; the CPU work may
     overlap the network wait), the rest the Wi-Fi round trip. That most of it is
     computation on the Pi 3s fits the 3B+ `bath2` reaching 33/s against about
     30/s on the half-speed 3Bs. In strict mode the queue grows without
     bound: the 6 000 payments of a 60 s run take 180–200 s, end-to-end p99
     118–142 s. **Q3 consequence:** one update per window holds at the 100 ms window
     (10 per second, no backlog, end-to-end p99 = update p99 < 100 ms), and cannot
     hold at the 10 ms window on these boards (about 3 windows per update at best).
     **Q4:** persistence costs little: ≈1.2 kB written per update (36 MB in the
     p100 run, ≈200 kB/s), ≈10 % of the sensor's CPU, no latency difference.
     `bath1` reached 60 °C in the p100 runs and set the soft temperature limit flag
     (`0x80008`, sticky `0x80000` after); the 3Bs `bath3`, `bath4`, `bath6`
     showed under-voltage (`0x50005`) as before. Report and raw data:
     a working note, not kept;
2. ✅ **In parallel, on the workstation: the points token** (done 2026-09-29).
   One contract, `contracts/points/PointsToken.sol` (ERC-20 with one minter and
   `mintBatch`, 0 decimals), compiled with solc 0.8.30 for the EVM and with
   zksolc 1.5.18 (`--codegen evmla`, same code path as solc) for zkSync, whose EVM
   emulator is off. Perun launcher: `"asset": "points"` deploys it, mints with one
   `mintBatch` and funds channels through the ERC-20 asset holder; the full suite
   passes (31 tests, including three-process, head-on and six-process mesh on
   points). Local Arbitrum and zkSync: deployed, `mintBatch` of 3 × 1 000 and one
   5-point transfer confirmed, balances 995 / 1 005 / 1 000. Opening a Perun
   channel on points needs a `ws://` chain URL (the depositor waits for the
   `Approval` event).
3. **One complete measurement path**, end to end on real boards: one use case, one
   system, with the payment ledger, the recorder, the manifest and collection.
   Only then the others.
   - ✅ **Built and run: UC1 on Arbitrum** (2026-09-30, `bath0` the stack, `bath1`
     the sensor, `bath2`…`bath7` six customers). The path: the `measure/` package
     (UC1 sensor and customer on the GESL time column, local payment client with
     Open, ledger, drain and Close, pre-flight checks from §5, run manifest,
     `measure.analyze_uc1`, `measure.aggregate`, `measure.compress`);
     `deploy/cluster` (`sync`, `idle-temps`, `up`, `status`, `preflight`, `run`,
     `down`); the cooling gate, the clock gate, the per-board recorder, collection
     to `results/pi/uc1/<run>/`, gzip of the big run files. Idle temperatures
     (median of 3, stack down, 2026-09-30 12:22 UTC, `results/pi/idle-temps.txt`):
     49.6 °C (`bath0`) to 58.0 °C (`bath5`).
   - ✅ **Result: nine runs, three windows × three repetitions**, 15 minutes each,
     6 customers, `results/pi/uc1/` (aggregate files `aggregate-arbitrum-w*.txt`,
     mean ± spread over the repetitions). Summaries and aggregates were regenerated
     on 2026-09-30 at analysis commit `a7ac8ad`, which adds goodput, the
     admitted/dropped shares, confirmation time per customer, receive lag and the
     `saturated` flag (final review); the latency, miss, Open/Close and fee figures
     did not change. Every figure below is from those files.
     - `w1000` — `20260930-153419-…-w1000-rep1`, `20260930-155906-…-w1000-rep2`,
       `20260930-162341-…-w1000-rep3`: all 5 400 payments of every run succeed,
       goodput 6.005 per second (the offered 6), p50 0.1045 s, p99 0.3809 s,
       confirmation p50 0.0987 s, deadline misses 0 %, Open median 1.712 s, Close
       median 0.2876 s, fees 0.01134 ETH;
     - `w100` — `20260930-174958-…-w100-rep1`, `20260930-181612-…-w100-rep2`,
       `20260930-184138-…-w100-rep3`: all 54 000 succeed, goodput 60.0 per second
       (the offered 60), p50 0.2335 s, p99 0.4227 s **at the sequencer's 250 ms
       block cadence** (Nitro's default `execution.sequencer.max-block-speed`, not
       set in the snapshot's `sequencer_config.json`; parent spec §5.1), deadline
       misses 99.31 %, Open 1.693 s, Close 0.2102 s, fees 0.1134 ETH;
     - `w10` — `20260930-190714-…-w10-rep1`, `20260930-193644-…-w10-rep2`,
       `20260930-200751-…-w10-rep3`: **saturated** — the payment clients' queues
       (`MAX_QUEUE=10000` per customer) refused payments in every run, so the
       headline is goodput, not latency. Rep1 is **incomplete** (see below), so
       two lines. Complete runs only (rep2, rep3, n=2, `aggregate-arbitrum-w10.txt`):
       goodput 248.5 per second (241.3 and 255.6), 44.21 % of the 540 000
       offered payments succeed (231 883 and 245 572; 42.94 and 45.48 %), 50.01 % are dropped by a full
       queue (51.37 and 48.64 %), confirmation p50 1.154 s (1.106 and 1.202 s),
       Open 1.633 s, Close 0.2153 s, fees 0.5013 ETH. With `--include-incomplete`
       (n=3, `aggregate-arbitrum-w10-incl-incomplete.txt`; rep1: 244 317 of its
       514 746 ledger rows succeed): goodput 250.5 per second, 45.29 % succeed, 49.16 % dropped, confirmation p50 1.088 s, Open
       1.833 s, Close 0.2191 s, fees 0.5052 ETH. Latency of the payments that
       succeeded, **set by `MAX_QUEUE=10000` and `DRAIN_S=60`, not by Arbitrum**
       (below): p50 135.3 s, p99 610.8 s (n=2); p50 137.5 s, p99 593.7 s (n=3);
       deadline misses 100 %.
   - **Findings** (corrected after the final review, 2026-09-30).
     (a) `w1000`: all 5 400 payments of every run succeed; no deadline misses in
     the three series runs. Blocks carrying payments: 2 227–2 268 per run, about
     2.4 payments each.
     (b) `w100`: all 54 000 succeed, but 99.31 % (99.24–99.42 %) are confirmed after
     their 100 ms deadline. This is the **block cadence**, not a capacity limit: the
     sequencer seals a block at most every 250 ms (Nitro default
     `max-block-speed`); the runs had 3 586–3 592 blocks carrying payments in
     900 s (0.251 s per payment-carrying block, about 15 payments each), and the six customers
     pay in lockstep, because the sensor sends each row to all of them at once.
     Latency p50 0.2335 s ≈ one cadence step, so a 100 ms deadline is met only by
     the few payments that arrive just before a block is sealed.
     (c) `w10`: **no Arbitrum capacity ceiling was measured.** The offered load was
     600 payments per second (6 × 100). The per-customer table below shows that
     goodput follows the customer's board, not the sequencer: `c1` (`bath2`, the
     Pi 3B+) paid all 90 000 of its payments in every run at 95.9–99.9 per second
     (it paid everything, but queued up to ~10 s behind), confirmation p50 0.53–0.58 s; the five
     Pi 3Bs managed 17.2–48.1 per second each, confirmation p50 1.21–2.48 s.
     `bath0` was busy too, so a second limit there is not ruled out: load1 over
     the stream averaged 2.95–3.43 of 4 cores (peaks 4.19–4.74; recorder CSVs).
     The limit in these runs is the Python
     payment client on the half-speed Pi 3Bs (U6), made heavier because every
     client fetches and parses each block's full receipt list
     (`eth_getBlockReceipts`), i.e. all ~250 payments per second of all six
     customers, not only its own. The pooled goodput of 241.3–255.6 per second is
     therefore a **lower bound** for Arbitrum on the Pi 4, not its ceiling; the
     ceiling is for the §7.1 capacity probes (`one-client` on a 3B and a 3B+,
     `one-node`), **future work (owner, 2026-10-11)**. Withdrawn: the earlier "saturates
     at ~255 payments/s" and "258–273 per second over the 900 s" (900 s was the
     wrong denominator; goodput is now divided by the confirmation span, first to
     last confirmation, about 961 s including the 60 s drain).
     **The `w10` latency is the client's queue, not Arbitrum:** a payment admitted
     behind up to `MAX_QUEUE=10000` others waits about MAX_QUEUE ÷ its client's
     service rate (for example 10 000 ÷ 36.9 per second ≈ 271 s against a
     queueing p50 of 248 s for `c2` in rep2), and only the admitted payments that
     succeed are in the percentiles (survivorship: 48.63–52.53 % admitted, 42.94–47.46 %
     succeeded). Confirmation time (submit to receipt) stays at 0.96–1.20 s
     (median).
     **Reconciliation:** the ledger differs from the sensor's received total by
     exactly 320 payments (320 000 000 000 000 wei) in every `w10` run: 64 per
     saturated customer (`MAX_IN_FLIGHT=64`) were submitted but unresolved at the
     drain deadline and executed later. The summary now reports this as
     `explained_wei` = 320 000 000 000 000 and `ok_explained` true; the strict
     `ok` stays false. **Fees:** those 320 payments have no receipt in the ledger,
     so their fees are not in the `w10` fee figures (`fees_unpriced_submitted_rows`
     = 320); at 21 000 gas × 0.1 gwei each that is about 0.000672 ETH per run,
     0.13–0.14 % of the figure.
     **Receive lag** (t_received on the customer − t_sent on `bath1`, per row of
     `rows_received.csv`; crosses two boards, clock offsets < 5 ms by the clock
     gate): at `w1000` and `w100` negligible (pooled p50 6.7–12.0 ms, p99
     56.9–79.4 ms); at `w10` pooled p50 0.36–0.40 s, p99 2.53–6.74 s, max
     7.2–27.8 s, per customer up to p99 26.0 s and max 27.8 s (`bath6`, rep1).
     `t_intended` is taken when the customer reads the row, so this lag is **not**
     in the payment latency: at `w10` a payment's window began up to that much
     earlier than its `t_intended`.
     **Per customer at `w10`** (`MAX_QUEUE=10000`, `DRAIN_S=60`; values rep1 / rep2
     / rep3; rep1's `c5` stopped at 64 746 windows when the sensor dropped it):

     | customer | board | model | succeeded (of 90 000) | goodput /s | confirmation p50 s | receive-lag p99 s |
     |---|---|---|---|---|---|---|
     | c1 | `bath2` | Pi 3 B+ | 90 000 / 90 000 / 90 000 | 99.9 / 98.1 / 95.9 | 0.53 / 0.56 / 0.58 | 0.3 / 0.3 / 0.3 |
     | c2 | `bath3` | Pi 3 B | 46 179 / 35 460 / 42 110 | 48.1 / 36.9 / 43.9 | 1.21 / 1.51 / 1.33 | 0.8 / 1.5 / 0.8 |
     | c3 | `bath4` | Pi 3 B | 28 019 / 19 165 / 20 776 | 29.2 / 20.0 / 21.6 | 1.35 / 1.98 / 2.48 | 5.5 / 16.0 / 3.5 |
     | c4 | `bath5` | Pi 3 B | 39 006 / 30 135 / 39 386 | 40.7 / 31.4 / 41.0 | 1.37 / 1.79 / 1.50 | 1.6 / 1.9 / 0.8 |
     | c5 | `bath6` | Pi 3 B | 15 292 / 16 517 / 24 382 | 21.0 / 17.2 / 25.4 | 1.98 / 2.24 / 1.98 | 26.0 / 6.2 / 4.3 |
     | c6 | `bath7` | Pi 3 B | 25 821 / 40 606 / 28 918 | 26.9 / 42.3 / 30.1 | 1.94 / 1.33 / 1.70 | 3.3 / 1.7 / 2.6 |

     Source: `per_customer` in each run's `summary.json` (goodput = succeeded ÷
     the customer's own confirmation span; model from `facts-start/`).
   - **Provenance of the nine runs.**
     - *Code:* the manifests of the `w1000` series record commit `1ebc0c0`, those
       of the `w100` and `w10` series `c5fa7c8` (the HEAD when each manifest was
       written, at the end of the run; the trees were clean). Between the two only
       `deploy/cluster` changed (`0b4b883`, `45a39fd`, `539f2ab`); the `measure/`
       code hash is `1d1cc70fa45e97da` in all nine. From now on `deploy/cluster`
       writes `run-start-commit.txt` at run start and the manifest prefers it.
     - *GESL recording:* all runs replayed **sigId 6063** (`GESL_SIG` default at
       every run commit). `sync` checked the CSV on `bath1` against the one in
       `data/gesl-samples/sigId-6063.zip`, whose SHA-256 `87b2ad10…` equals
       `measure.gesl.KNOWN_SHA256[6063]`; every 15-minute sensor log reports
       108 000 rows (900 s × 120 Hz). From now on each run checks the CSV against
       `KNOWN_SHA256` (the run fails on a mismatch) and writes `gesl.json`.
     - *Stack configuration:* the snapshot's. Before every run `up` checked each
       tarball against `~/arbitrum/SHA256SUMS` (`config.tgz` `0eb21958…`, which
       holds `sequencer_config.json` `939ab077…`, no `max-block-speed` set);
       `docker-compose.yaml` (`e43dac36…`, services on
       `offchainlabs/nitro-node:v3.9.6-91bf578`) was last changed 2026-09-30 14:08
       (ports 8545 and 8548 opened to the LAN), before the smoke run. From now on
       each run records these three hashes in `stack-config.sha256`.
   - **Extra and aborted runs** (all kept in `results/pi/uc1/`; the ones below are not in the aggregates, except rep1 of `w10` in the include-incomplete file):
     - `20260930-190714-…-w10-rep1`: **incomplete** (marked so by the analysis,
       commit `3405ce8`). The sensor dropped customer 5 (`bath6`, a Pi 3B) at
       seq 78 044, about 650 s in, because that customer's queue exceeded 240
       frames while its payment client was saturated; its ledger has 64 746 of
       90 000 windows. Kept and excluded from the complete-only aggregate. The drop
       is itself a finding: a Pi 3B customer cannot keep up with the 120 Hz stream
       while its payment client is saturated at the 10 ms window. The owner chose
       to rerun the whole `w10` series (one rerun alone cannot be aggregated with
       runs from another commit); see the 2026-10-01 rerun below.
     - **`w10` rerun 2026-10-01** (`20261001-091555-…-w10-rep1`, `20261001-094524-…-w10-rep2`,
       `20261001-101435-…-w10-rep3`, commit `8287e84`): all three **incomplete**,
       not the `w10` headline (that stays the n=2 aggregate above).
       In every run the sensor dropped customer 6 (`bath7`, Pi 3B) about 12 s in
       (seq 1 481–1 670). `bath7` received the stream too slowly from the first
       second while its CPU was about 6 % busy. It was **undervolted**: `get_throttled`
       `0x50005` at run start and again at idle afterwards, ARM clock 600 MHz, `wlan0` rx bitrate
       11 Mbit/s against 65 on `bath6` at a similar signal (on 2026-09-30 it started at `0x50000`).
       Its power supply or cable has to be checked at the cluster. In rep3, in addition, all six
       customers were dropped one after another between about 750 and 771 s,
       with no change in signal, access point or CPU on the boards; most likely a Wi-Fi
       stall of over 2 s, not proven. Kept as a finding. Aggregate with
       `--include-incomplete` (effectively five customers): latency p50 147 ± 12 s,
       confirmation p50 0.92 s, succeeded share 0.60
       (`aggregate-arbitrum-w10-20261001-incl-incomplete.txt`). **Rerun `w10` once
       `bath7`'s power is fixed.** *Superseded:* Arbitrum `w10` was measured in the
       final series (mixed Wi-Fi, own receipts; `bath7` kept up), "Final UC1 series" below.
     - `20260930-142408-…-smoke-rep1`: the 60 s smoke run, 6 × 60 = 360 payments,
       all succeeded, p50 0.107 s;
     - `20260930-145330-…-w1000-rep1`: the first 15-minute dry run, 5 400 of 5 400,
       p50 0.110 s, p99 0.405 s, two deadline misses (0.037 %); kept as the dry
       run, the series was repeated;
     - `20260930-165534-…-w10-rep1`: a complete `w10` run at commit `1ebc0c0`
       (229 844 succeeded, goodput 239.0 per second over its confirmation span; saturated like the series), before the fixes below; not aggregated,
       the series was redone with the fixed pipeline;
     - `20260930-164817-…-w100-rep1` (`RUN_FAILED`: stage "payment clients" failed,
       ssh exit 255, a connect timeout) and `20260930-172822-…-w10-rep2`
       (`RUN_FAILED`: pre-flight failed, `bath5` clock offset 6.394 ms).
   - **Fixes the pipeline needed** (commits `0b4b883`, `45a39fd`, `539f2ab`): ssh and
     scp retry on connect errors (background checks run `pgrep` before relaunching,
     so a retry cannot start a second recorder); a clock-settle gate before
     pre-flight (ignores unsynchronised chrony, needs 2 good polls, uses real
     elapsed time; its reading is saved per run as `clock-gate.txt`); collection
     skips a dead host after one failure. The `w100` and `w10` series ran with
     these, the `w1000` series and the dry run before.
   - **Owner's checkpoint decisions:** customers sign directly with `coincurve`; after a permanent rejection the client
     resyncs its nonce; ports 8545 and 8548 on `bath0` are opened to the LAN (2026-10-01: the L1's HTTP API on 8545 cut to `eth,net,web3` and `corsdomain=*` removed; compose `22391604…`, backup `docker-compose.yaml.pre-harden-20261001`); the cooling gate compares each board
     with its own idle temperature (`idle-temps.txt`), not a fixed limit; run data
     is gzip-compressed in git (readers accept `X` or `X.gz`).
   - ✅ **Built: UC1 on zkSync** (2026-10-01/02; plan
     `archive/plans/2026-10-01-uc1-zksync-measurement-path.md`, facts
     `archive/bench-workstation/results/zksync-uc1-facts-2026-10-01.md`; merged into `v2` as `bc62981`).
     The same layout and `measure/` path as Arbitrum, with zkSync's Open (Bridgehub
     deposit, timed until credited on L2), payments, and Close (a type-2
     `withdraw(address)`, timed to its L2 receipt); `deploy/cluster up zksync` (one
     stack at a time), pre-flight and manifest per stack; and in
     `measure.analyze_uc1` the offered rate, the backlog, and the payments executed
     after the drain deadline (`late_executed`, counted exactly as Close nonce −
     start nonce per account; reconciliation `ok_with_late`).
     - *Owner's answers:* Q1 A (all three windows × 3, as for Arbitrum; if a window
       saturates, that is the result), Q2 a (`block_commit_deadline_ms` 20 000,
       everything else as in the snapshot), Q3 a (60 s drain plus exact late
       accounting), Q4 a (the 3 °C cooling rule; `COOL_MAX_S` 2 700 for zkSync,
       idle temperatures re-measured on 2026-10-01, the old file kept as
       `idle-temps-2026-09-30.txt`), Q5 a (`THERMAL_STICKY_OK` stays empty: after
       the smoke run `bath0` showed only `0x80000`, which pre-flight already
       accepts), Q6 a (raw PostgreSQL volume), Q7 a with the hardening below.
     - *`bath0` prepared* (2026-10-01): reth's 8945 opened to the LAN with
       `--http.api eth,net,web3` and without `--http.corsdomain "*"` (PostgreSQL
       stays on localhost; compose backup `.orig-localhost`); `pg_restore` once
       (135.3 s), then the volume packed raw as `postgres-vol.tgz` (746 068 393
       bytes, `908b408f…`), which `up zksync` restores in every run: 237–241 s to a
       healthy server.
     - *Client settings:* `PAYMENT_GAS` 400 000 (2× the largest estimate, 175 690,
       rounded up), `MAX_IN_FLIGHT=20` (§7.3, mainnet's per-account limit;
       Arbitrum 64), `RECEIPT_TIMEOUT_S=1200` (longer than stream + drain, so no
       slot is freed before an outcome), `CLOSE_TIMEOUT_S=1800`; `MAX_QUEUE=10000`
       and `DRAIN_S=60` as for Arbitrum.
     - *Smoke run* `20261001-143418-…-smoke-rep1` (commit `4ecac71`, 6 customers):
       360 of 360 succeeded, p50 2.86 s, p99 20.99 s, goodput 5.764 per second of 6
       offered, backlog max 107, late 0, reconciliation `ok`; Open 2.24–5.16 s,
       Close 2.29–3.68 s; `bath0` at most 71.5 °C, flags `0x80000` only.
   - ⚠️ **Dry runs and diagnostics, 2026-10-01/02: the sensor → customer stream
     collapsed.** Every 15-minute dry run lost the stream to all customers at once,
     well before 900 s. All runs `w1000`, kept in `results/pi/uc1/`, not
     aggregated; "windows" is the number of the 900 windows each customer received.

     | run | system | code | customers | what was tested | stream |
     |---|---|---|---|---|---|
     | `20261001-150825` | zkSync | `64091bf` | 6 | first dry run (sensor queue 240 frames, write timeout 5 s) | ended at 95–101 windows |
     | `20261001-162538` | zkSync | `ec945eb` | 6 | stall tolerance 3 600 frames / 30 s | ended at 109–116 |
     | `20261001-210821` | zkSync | `bf27c81` | 6 | + write coalescing | ended at 111–127 |
     | `20261001-212733` | Arbitrum | `bf27c81` | 6 | control, the worktree's code | ended at 129–133 |
     | `20261001-214540` | Arbitrum | `ab0c2d1` | 6 | control, the morning's code (main checkout) | ended at 881–885 |
     | `20261001-222300`, `20261001-230143` | Arbitrum | `7404471` | 6 | bisect: the code before the sensor changes, at night | complete, 108 000 rows to every customer (temporary worktree; committed to `results/pi/uc1/` later, `a0b0a47`) |
     | `20261001-233750` | zkSync | `bf27c81` | 6 | as `210821`, at night | ended at 438–444 |
     | `20261002-105057` | zkSync | `bf27c81` | 5 | `bath7` out | ended at 813–816 |
     | `20261002-124719` | zkSync | `67e3c49` | 6 | own receipts | ended at 539–542 |
     | `20261002-135127` | zkSync | `4bdc4a9` | 5 | `bath7` out + own receipts + Close fix | **complete**, receive lag max 0.37 s |

     Up to each collapse the payments were clean (every submitted payment
     accounted for, except the Close-nonce cases below).
     **Measured** (on `162538`; working note, not kept):
     the sensor emitted every row on time (at most 3.9 ms late), and for the first
     ~35 s of the collapse its queue wait stayed 0 while the delay grew below the
     application, in `bath1`'s kernel send buffers (up to ~30 s per connection).
     All six customers fell to the same ~18 rows per second in the same second;
     none advertised a zero TCP window, and their CPUs became less busy. Over the
     same access point → customer hops, `bath0`'s receipts reached c1–c5 within
     0.1–0.4 s of each other at the normal block cadence while `bath1`'s rows ran
     4–50 s late. So the bottleneck was `bath1`'s six flows (its TCP send path and
     Wi-Fi uplink), not the sensor process, the customers or the downlink. zkSync
     stalls block production for ~15–20 s about every 30 s and then seals catch-up
     blocks of 27–46 payments; every payment client fetched each block's full
     receipt list (`eth_getBlockReceipts`), and the delivery dips line up with
     these bursts to the second, the collapse starting in the third.
     `bath1` sends one TCP segment per row (asyncio sets `TCP_NODELAY`; 682 B per
     packet on average since boot). The access point sent to `bath7` at 7.2–11
     Mbit/s at −50 dBm, to the other boards at 39–72. The Arbitrum controls
     collapsed too in the evening, so the cause is not the zkSync code, while two
     Arbitrum runs at `7404471` at night were clean; the collapses on 2026-10-01
     came at irregular clock times, from morning to night.
     **Inferred, not measured:** that the per-row stream alone takes about 60–65 %
     of the 2.4 GHz channel's airtime (textbook frame overheads); that a catch-up
     block's receipts, fetched whole by six clients, make a burst of about 0.8 MB
     for a 45-payment block (receipt size assumed 2–4 KB, not measured); why the
     flows stay slow instead of recovering (TCP back-off or TCP Small Queues on the `brcmfmac`
     driver; not separable without a capture); that the channel's condition
     differs by time of day. Neither leaving `bath7` out (`105057`) nor own
     receipts (`124719`) was enough alone in single runs; both together carried
     `135127` and 8 of the 9 series runs. Which of the two matters more is not
     separated (n = 1 each).
   - **Fixes and owner decisions** (2026-10-01/02):
     - *Sensor stall tolerance* (`4a91287`, `ec945eb`): `SENSOR_QUEUE_MAX=3600`
       (30 s at 120 rows/s) and `SENSOR_WRITE_TIMEOUT_S=30`, set only in the env
       files; the defaults (240 frames, 5 s) and the 6-customer Arbitrum files are
       unchanged. It turns a short stall into latency; it cannot ride out the
       collapse (`162538` dropped its customers at about 60 s of lag).
     - *Write coalescing when a customer is behind* (`b768fb2`, `bf27c81`): the
       sensor writes all of that customer's queued frames (at most 512) in one
       write; one frame per write while it keeps up, so steady-state traffic and
       `t_sent` are unchanged. It is in the sensor code for every stack (the
       2026-09-30 Arbitrum series ran without it). Alone it did not prevent the
       collapse (`210821`).
     - *zkSync payment clients fetch only their own receipts* (`67e3c49`,
       `08aa6d6`): per new head, the head's transaction hash list, then only the
       client's own receipts. The detection moment is the same (+1.4 ms median
       `t_confirmed` on the workstation); Arbitrum keeps block receipts; the path
       used is recorded in `lifecycle.json`.
     - *Close-nonce collision* (`cc73b36`, `4bdc4a9`; working note, not kept):
       Close took its nonce from the node's pending count, which could lag the
       nonces the payment client had already sent. One payment then shared Close's
       nonce and never executed, and later ones executed after Close, outside the
       late accounting (c3 in `233750`: 68 against 66 late; in `105057`: 99 against
       98). Close's nonce is now the larger of the node's count and the client's
       next nonce (Arbitrum the same). A bug fix, not a method change.
     - *5 customers* (owner, 2026-10-02): `bath7` out — undervolted (`0x50005`,
       600 MHz) with a 7–11 Mbit/s link. Env files `uc1-zksync5-*.env` (`89ce5a3`);
       the 6-customer files stay for after a network fix. **Arbitrum is rerun with
       the same 5 customers and sensor settings** (`uc1-arbitrum5-*.env`,
       `98f31d1`), so that the two are comparable (done 2026-10-03, below).
   - ✅ **Result: zkSync, 5 customers, nine runs** (2026-10-02, 14:27–20:35, all
     at commit `89ce5a3`, `measure/` code hash `069f13b855e29e20`; aggregates
     `aggregate-zksync5-w*.txt`, mean ± sd (n); every figure below is from those
     files and the runs' `summary.json`). Offered: 5, 50 and 500 payments per
     second.
     - `w1000` — `20261002-142707-…-w1000-rep1`, `20261002-150312-…-w1000-rep2`,
       `20261002-154304-…-w1000-rep3`: all complete, no queue drops, yet not all
       payments succeed: goodput 4.173 ± 0.064 per second (3) of the offered 5,
       succeeded share 0.8699 ± 0.015 (3) (the rest unresolved at the drain
       deadline), payment latency p50 56.83 ± 7.4 s, p99 180.6 ± 26 s,
       confirmation p50 21.17 ± 1.1 s, deadline misses 98.28 %, backlog max
       681 / 961 / 856, Open 2.537 ± 0.08 s, Close 41.38 ± 10 s, fees
       0.03465 ± 0.00058 ETH;
     - `w100` — `20261002-162354-…-w100-rep1`, `20261002-170448-…-w100-rep2`,
       `20261002-174650-…-w100-rep3`: all complete, not saturated by the flag (no
       queue drops; backlog max 41 000 / 40 870 / 40 844, below 5 × `MAX_QUEUE`):
       goodput 4.594 ± 0.049 per second (3) of 50, succeeded share
       0.09681 ± 0.00082, p50 321.8 ± 14 s, p99 864.7 ± 8.6 s, confirmation p50
       23.58 ± 0.53 s, deadline misses 100 %, Open 2.527 ± 0.17 s, Close
       41.56 ± 9.4 s, fees 0.03852 ± 0.00032 ETH;
     - `w10` — `20261002-190524-…-w10-rep2`, `20261002-195041-…-w10-rep3`
       (complete, n = 2): **saturated**, 0.8797 ± 0.0002 of the offered payments
       dropped by full queues, backlog max 50 000 (= 5 × `MAX_QUEUE`), goodput
       4.617 ± 0.1 per second (2) of 500, succeeded share 0.009696 ± 0.00031,
       p50 373.7 ± 40 s and p99 940.7 ± 9.1 s (set by `MAX_QUEUE=10000` and
       `DRAIN_S=60`), confirmation p50 23.67 ± 0.92 s, Open 2.566 ± 0.11 s, Close
       19.17 ± 1.9 s, fees 0.03858 ± 0.0012 ETH.
       `20261002-182911-…-w10-rep1` is **incomplete**: the stream to all five
       customers collapsed at about 396 s (39 592–39 715 of 90 000 windows; receive
       lag p99 28.88 s, max 45.84 s); its payments up to then are clean (2 802
       succeeded, late 100, `ok_with_late` true). Kept as a finding: with `bath7`
       out and own receipts the collapse is rarer, not gone. With
       `--include-incomplete` (n = 3, `aggregate-zksync5-w10-incl-incomplete.txt`):
       goodput 4.934 ± 0.55 per second (rep1 5.566 over its shorter confirmation
       span), p50 304.8 ± 1.2e+02 s, confirmation p50 20.76 ± 5.1 s.
     - *In all nine runs:* `late_executed_total` 100 = 5 customers × 20 in
       flight, submitted before the drain deadline and executed after it; the
       strict reconciliation `ok` is false, `ok_explained` and `ok_with_late` are
       true. Close waits behind the 0–20 payments still pending on its account.
       `bath0`: CPU 98.5–99.7 % busy (stream mean), at most 81.3–82.3 °C (complete
       runs; rep1 of `w10` 79.8 °C), mean 76.9–77.6 °C, clock 1 800 MHz in all but at
       most 2 samples per run (1 775 MHz), flags `0x80000` (soft limit
       occurred since boot) in every sample and `0x80008` (soft limit active) in
       one sample each of `w100` rep1 and `w10` rep3; no undervoltage on `bath0`
       or `bath1`. Customers (`bath2`…`bath6`) 10.4–22.4 % CPU busy; receive lag
       in the complete runs p99 0.046–0.063 s, max at most 0.57 s.
   - **Finding: zkSync on the Pi 4 carries about 4.1–4.7 payments per second,
     whatever is offered** (goodput 4.116–4.688 over the eight complete runs at 5,
     50 and 500 offered), with `bath0`'s CPU saturated; this matches the
     feasibility runs (§8 step 1, about 4 per second). Confirmation time stays at
     21–24 s (median), so the payment latency is set by the backlog in front of
     the chain: p50 57 s at `w1000`, 322 s at `w100`, 374 s at `w10`, where the
     full queues bound it. Even the 1 s window (5 per second) is beyond capacity.
     The `w10` rep1 collapse is a separate network finding (above).
   - **Comparability caveats** (cf. the Arbitrum5 result below):
     - 5 customers here, 6 in the 2026-09-30 Arbitrum series: offered 5/50/500
       against 6/60/600 per second. Compare by offered rate and by shares, not by
       totals.
     - Sensor settings: zkSync5 ran with 3 600 frames / 30 s and write coalescing,
       the 2026-09-30 Arbitrum series with 240 frames / 5 s and without
       coalescing. While a customer keeps up, coalescing writes the same frames,
       and the receive lag is similar (zkSync5 p99 0.046–0.063 s; Arbitrum `w1000`
       and `w100` 56.9–79.4 ms).
     - In flight: 20 per account on zkSync (§7.3), 64 on Arbitrum. With
       5 × 20 = 100 in flight and a confirmation median of 21–24 s, the bound by
       itself allows about 100 ÷ 21–24 s ≈ 4.2–4.7 payments per second, the same
       as the measured goodput. The chain is the likelier limit (`bath0`'s CPU at
       99 %, and the feasibility runs without this bound also reached about 4 per
       second), but these runs alone do not separate the two (U9; *answered
       2026-10-04: the chain under this load, n = 1*).
     - Receipts: zkSync clients fetch only their own receipts, Arbitrum's whole
       blocks. Close on zkSync includes waiting behind up to 20 pending payments,
       so its Close figures are not Arbitrum's.
     - Provenance: the final-review fixes made after the series (`08aa6d6`,
       `576e57c`, `b30e0c5`, `ea2520c`, `48c4e12`) leave every summary key
       unchanged (regression over 35 run folders); the own-receipt fallback that
       `08aa6d6` narrows never fired in the nine runs.
   - ⚠️ **Result: Arbitrum, 5 customers, nine runs** (`uc1-arbitrum5-w{1000,100,10}`
     × 3, env/deploy commit `98f31d1`, analysis at `37dd9c7`; aggregates
     `aggregate-arbitrum5-w*.txt`, mean ± sd (n); every figure below is from
     those files and the runs' `summary.json`). Offered: 5, 50 and 500
     payments per second — same customers (`bath7` out) and sensor settings
     (3 600 frames / 30 s, write coalescing) as zkSync5.
     - `w1000` — `20261002-232754-…-w1000-rep1`, `20261002-235523-…-w1000-rep2`,
       `20261003-002236-…-w1000-rep3`: all complete, no drops, every payment
       succeeded: goodput 5.005 ± 0.00099 per second (3) of the offered 5,
       succeeded share 1 ± 0 (3), payment latency p50 0.09701 ± 0.00066 s,
       p99 0.361 ± 0.011 s, confirmation p50 0.09148 ± 0.00062 s, deadline
       misses 0.0148 ± 0.026 %, backlog max 5 / 7 / 5, Open 1.704 ± 0.23 s,
       Close 0.2971 ± 0.032 s, fees 0.00945 ± 0 ETH.
     - `w100` — `20261003-004938-…-w100-rep1`, `20261003-011916-…-w100-rep2`,
       `20261003-014845-…-w100-rep3`: all complete, no drops, every payment
       succeeded: goodput 50.00 ± 0.0048 per second (3) of 50, succeeded share
       1 ± 0 (3), payment latency p50 0.2308 ± 0.00041 s, p99 0.4672 ± 0.028 s,
       confirmation p50 0.2248 ± 0.0004 s, deadline misses 98.68 ± 0.056 %
       (the 100 ms deadline is tighter than Arbitrum's own block cadence),
       backlog max 41 / 55 / 58, Open 1.694 ± 0.21 s, Close 0.1752 ± 0.0018 s,
       fees 0.0945 ± 0 ETH.
     - `w10` — no `aggregate-arbitrum5-w10.txt`: 0 of 3 runs complete.
       `20261003-021821-…-w10-rep1` and `20261003-023225-…-w10-rep2`: the
       sensor stream to all five customers collapsed within the stream phase
       (windows received before the collapse, of 90 000 expected: rep1
       4 031–6 145 ≈ 40–61 s; rep2 4 914–7 728 ≈ 49–77 s; receive lag p99
       125.5 s / 117.2 s, max 126.1 s / 119.4 s — the stalled stream, not
       Arbitrum). Payments up to the collapse are accounted: rep1 succeeded
       share 0.8125 of 25 551 offered (within its shorter span), late
       executed 128; rep2 0.7786 of 32 578 offered, late executed 192; both
       `ok_with_late` true. `20261003-024642-…-w10-rep3` collapsed the same
       way (windows 5 977–7 513 ≈ 60–75 s) and then **`RUN_FAILED`** at Close:
       `nonce too high: tx 3906 state 3895` on `bath4` (customer c3) — the
       Close-nonce bug (Close took the submitter's next nonce although sent
       payments had not yet been admitted), fixed afterward in `b9c6b58`,
       `7876f5f`, `be5f991` (Close now waits up to 30 s for the node to admit
       sent payments and never closes above its pending nonce; payments that
       execute after Close are counted).
     - *Diagnostics* (2026-10-03, after the fix, to separate the `w10`
       collapse from the code): `20261003-070041-…-oldsensor-rep1` (sensor
       defaults 240 frames / 5 s, commit `a0b0a47` dirty) collapsed at about
       20–135 s per customer (windows 1 971–13 485 of 90 000; receive lag p99
       24.9 s, max 28.5 s); `20261003-071701-…-oldcode-rep1` (the code of
       `8287e84`, which streamed `w10` cleanly for 900 s on 2026-09-30 and
       2026-10-01 morning) collapsed at about 58–67 s (windows 5 845–6 688;
       receive lag p99 45.8 s, max 49.2 s). Both collapsed where the same (or
       older) code ran clean before: the radio environment changed, not the
       code. Arbitrum `w10` with 5 customers is therefore **not measured**;
       the 6-customer `w10` series of 2026-09-30 (n = 2, complete) remains the
       Arbitrum `w10` reference, with the caveat of 6 vs 5 customers.
   - **Finding: at 5 customers Arbitrum carries all offered load at the 1 s
     and 100 ms windows** (goodput ≈ offered, succeeded share 1, backlog in
     the tens at most), with latency set by the ~250 ms block cadence, not by
     a queue or an in-flight bound — unlike zkSync, whose goodput sits at the
     same ~4.1–4.7 per second regardless of window (§8 step 3 above). The
     10 ms window could not be measured on either stack in this series (*it was in the
     Final UC1 series, mixed Wi-Fi, below*): it
     collapses the shared Wi-Fi link before either chain's capacity is
     reached (diagnostics above; the two clean night controls
     `20261001-222300`/`20261001-230143` at `7404471` and the 6-customer
     2026-09-30 series show the same code streaming `w1000`/`w10` cleanly
     when the channel is not degraded).
   - **zkSync5 ↔ Arbitrum5 comparison, 5 customers, same sensor settings:**

     | window | offered/s | goodput zkSync | goodput Arbitrum | latency p50 zkSync | latency p50 Arbitrum | confirmation p50 zkSync | confirmation p50 Arbitrum | deadline miss zkSync | deadline miss Arbitrum |
     |---|---|---|---|---|---|---|---|---|---|
     | `w1000` (1 s) | 5 | 4.173 ± 0.064 | 5.005 ± 0.001 | 56.83 ± 7.4 s | 0.097 ± 0.0007 s | 21.17 ± 1.1 s | 0.0915 ± 0.0006 s | 98.28 % | 0.015 ± 0.026 % |
     | `w100` (100 ms) | 50 | 4.594 ± 0.049 | 50.00 ± 0.005 | 321.8 ± 14 s | 0.231 ± 0.0004 s | 23.58 ± 0.53 s | 0.225 ± 0.0004 s | 100 % | 98.68 ± 0.056 % |
     | `w10` (10 ms) | 500 | 4.617 ± 0.1 (n=2, saturated) | not measured — Wi-Fi collapse (0/3, diagnostics above) | 373.7 ± 40 s | — | 23.67 ± 0.92 s | — | 100 % | — |

     zkSync's ~4–4.6 per second ceiling, flat across all three offered rates,
     may be the 20-in-flight-per-account bound rather than the chain itself
     (U9); Arbitrum carries all offered load at the 1 s and 100 ms windows,
     and its latency is set by the 250 ms block cadence, not by a backlog.
   - ✅ **Built and run: UC1 on Perun, 5 customers** (2026-10-03/04, plan
     `2026-10-01-uc1-perun-measurement-path`, branch `perun-uc1`). The path:
     `bath0` runs a fresh Anvil 1.7.1 per run (`--block-time 1`, chain 31337);
     `deploy/cluster up perun` deploys the Adjudicator and the ETH AssetHolder
     from the workstation (`perun-node -mode uc1-deploy`, untimed; the same two
     addresses in every run, checked); `bath1` runs the unchanged
     `measure/sensor.py` and beside it `perun-node -mode uc1-sensor`; each
     customer (`bath2`…`bath6`) runs the unchanged `measure/customer.py` and
     `measure.payclient --system perun`, which drives a Go sidecar
     (`perun-node -mode uc1-customer`) over JSON lines on stdin/stdout. Each
     customer opens one direct ledger channel to the sensor, deposits 1 ETH (the
     sensor 0), pays one signed update per window (10¹² wei) and closes
     cooperatively. `deploy/cluster` gained `up perun`, a Perun branch in `run`,
     a three-way one-stack refusal and a SHA-256 check of the `perun-node`
     binary on every board before every run.
     - *Owner answers* (Q1–Q7 of the plan, all the recommended option): the
       Python payment client keeps the ledger and drives the Go sidecar (Q1);
       Close = the customer finalizes off-chain, sends `concludeFinal` as
       primary and withdraws, the sensor withdraws its earnings as secondary,
       timed on the customer from finalize to its own withdrawal's confirmation
       (Q2, §7.2); strict, one signed update per window, no coalescing at
       `w10`, so a shortfall is a result (Q3, confirmed at Checkpoint 1 with
       the probe, §8 step 1); channel state persisted to the SD card at both ends
       (Q4); accounts 100 (sensor) and 101…105 from the public mnemonic, as on
       Arbitrum (Q5); no payment retries (Q6, §7.2); the sensor's payment
       policy on (Q7).
     - *Rulings during the build:* R-P1 **5 customers** (`bath2`…`bath6`,
       `bath7` out, sensor `-customers 5`), comparable with zkSync5 and
       Arbitrum5; R-P2 the same sensor settings as those (3 600 frames / 30 s,
       write coalescing); R-P6 **a broken channel is not closed** — the sidecar
       refuses, a non-cooperative dispute close is out of scope; the run then
       records the Close error and `complete: false` (no channel broke in any
       run below); R-P7 the sensor's close gas is reported as a total and a
       per-channel mean (go-perun's settle exposes no receipts).
     - *Facts from the workstation* (Task 1, measured): Open is one customer
       transaction, 45 801 gas; the sensor sends none. Close is three
       transactions per channel: the customer's `concludeFinal` ≈170 900 gas
       and withdrawal ≈42 800 gas, the sensor's withdrawal ≈42 800 gas. Payments
       cost 0 gas. Contract deployment 3 918 416 gas in two transactions. Over
       loopback an update takes p50 0.82 ms, p99 1.21 ms (1 000 per second, one
       channel, persistence on).
     - *Smoke and dry run:* `20261003-220029-…-smoke-rep1` (60 s, at
       `655da59`): 5/5 complete, 300/300 succeeded, three-way reconciliation ok
       (succeeded = sensor's channel balance = customer's view; sensor's L1
       change + its close fee = Σ earnings), Open 1.29–1.37 s, Close
       2.60–2.82 s, payment latency p50 53.5 ms, p99 157 ms. The Arbitrum
       regression smoke `20261003-214919` passed at the same commit (6/6,
       360/360). Dry run `20261003-221734-…-w1000-rep1` (900 s, at
       `996034d`): 4 500/4 500 succeeded, 0 deadline misses, reconciliation
       ok, p50 53.4 ms, p99 129 ms, Open 1.28–1.40 s, Close 2.49–2.80 s,
       `bath1` 20 % CPU busy, at most 54.8 °C.
     - *Fault injection* (the Perun failure path of `finish_run` had not run):
       `20261003-224714-…-smoke-rep1` got SIGINT about 30 s into the stream and
       ignored it (bash ignores SIGINT in background jobs) — it ran to a clean
       end, so it is not a test of the failure path. `20261003-225906-…-smoke-rep1`
       got SIGTERM instead: `RUN_FAILED` "interrupted", 31 of 60 windows per
       customer, 4 lifecycles `complete: false` and one complete (c2, Close
       2.67 s), all five channels still settled on chain (the sidecars close on
       shutdown), summary `complete: false`, no process left on any board. The
       failure path works.
     - *The series* (`uc1-perun-w{1000,100,10}` × 3, 2026-10-03 23:13 to
       2026-10-04 morning, all at run-start commit `ad8a18d`, clean; `measure/`
       code hash `46925c39002513fc`; `perun-node` arm64 SHA-256 `1afb81aa…`,
       go1.26.4; analysis at `ad8a18d`, re-analysed at `47df96e` for the late
       keys only, see *Late executions* below). No code changed between the smoke
       (`655da59`) and the series outside `results/`: the pipeline needed no
       fixes. Of the nine series runs, six are complete; three are excluded,
       and two of them were rerun once at the same commit:
       - `20261004-010845-…-w100-rep2`, **excluded**: the sensor stream to all
         five customers collapsed at about 739–747 s (7 388–7 465 of 9 000
         windows; receive lag p99 20.6 s, max 41.1 s) — the same signature as
         the zkSync5 and Arbitrum5 collapses (U4). Its payments up to then are
         clean (37 104 succeeded, reconciliation ok).
       - `20261004-024531-…-w10-rep2`, **excluded**: the same collapse at about
         375–377 s (37 514–37 700 of 90 000 windows; receive lag p99 26.4 s, max
         35.6 s); the payments up to then show the same late executions as the
         complete `w10` runs (26–57 per channel, `ok_explained` true).
       - `20261004-031153-…-w10-rep3`, **`RUN_FAILED`** before any payment:
         `bath2` was unreachable ("No route to host") at the code check. It was
         off the network for about 15 min (from about 03:12 to 03:27) and came
         back without a reboot (up 3 days, throttle flags `0x50000`, i.e.
         undervoltage and throttling since boot, not at that moment).
       - Reruns: `20261004-033023-…-w100-rep1` and `20261004-040042-…-w10-rep1`
         (the folder names count from rep1 again), both complete.
     - *Results* (aggregates `aggregate-perun-w*.txt`, mean ± sd (n); every
       figure below is from those files and the runs' `summary.json`).
       Offered: 5, 50 and 500 payments per second.
       - `w1000` — `20261003-231312-…-w1000-rep1`, `20261003-234156-…-w1000-rep2`,
         `20261004-000953-…-w1000-rep3`: all complete, every payment succeeded:
         goodput 5.006 ± 0.00011 per second (3) of 5, succeeded share 1 ± 0
         (3), payment latency p50 0.05301 ± 0.0019 s, p99 0.1576 ± 0.016 s,
         confirmation p50 0.0494 ± 0.002 s, deadline misses 0.0074 ± 0.013 %,
         backlog max 6 / 5 / 5, Open 1.323 ± 0.029 s, Close 2.629 ± 0.062 s (of
         which finalize 0.02971 ± 0.0019 s), payment fees 0.
       - `w100` — `20261004-003806-…-w100-rep1`, `20261004-013711-…-w100-rep3`,
         `20261004-033023-…-w100-rep1` (rerun): all complete, every payment
         succeeded: goodput 50.01 ± 0.00048 per second (3) of 50, succeeded
         share 1 ± 0 (3), p50 0.0485 ± 0.00064 s, p99 0.1791 ± 0.03 s,
         confirmation p50 0.04455 ± 0.00074 s, deadline misses 3.907 ± 0.89 %
         (payments slower than the 100 ms window), backlog max 59 / 39 / 16,
         Open 1.354 ± 0.011 s, Close 2.489 ± 0.039 s (finalize
         0.03027 ± 0.0018 s).
       - `w10` — `20261004-020746-…-w10-rep1`, `20261004-040042-…-w10-rep1`
         (rerun), complete, n = 2: **saturated**. Goodput 108.5 ± 0.87 per
         second (2) of 500, succeeded share 0.2314 ± 0.0018, dropped by full
         queues 0.6772 ± 0.0017 (all 305 292 / 304 210 failures are queue-full),
         unresolved at the drain deadline 9.1 % (41 155 / 41 069), backlog max
         50 000 (= 5 × `MAX_QUEUE`), p50 385.2 ± 1.7 s and p99 499 ± 6.4 s (set
         by `MAX_QUEUE=10000` and `DRAIN_S=60`), confirmation p50
         0.04226 ± 0.00072 s — an update is as fast as at `w1000`; the latency
         is the queue in front of it. Open 1.312 ± 0.00084 s, Close
         2.493 ± 0.071 s.
       - *Gas, every run:* Open 45 801 gas (customer); payments 0; Close
         ≈213 750 gas for the customer (median 213 776 in `020746`) and
         214 116 gas for the sensor over five channels (42 823 per channel, same
         run). ETH fees on Anvil are an artefact of its base fee (parent §5.2)
         and are not reported.
       - *Boards:* `bath0` (Anvil) 1.6–1.7 % CPU busy, at most 45.2 °C — the
         chain does no work during the stream. `bath1` (sensor) 20 / 35 / 55 %
         busy at `w1000` / `w100` / `w10`, at most 54.8 / 59.1 / 61.8 °C, no
         undervoltage. Customers 10–17 / 15–24 / 25–38 % busy; the Pi 3Bs
         `bath3`, `bath4`, `bath6` undervolted in 31–96 % of the samples
         (`bath2`, the 3B+, and `bath5` never). Receive lag p99 0.055–0.088 s
         in every complete run.
     - **Finding: Perun carries all offered load at the 1 s and 100 ms
       windows, with a payment latency of about 50 ms (p50) — and stops at about
       108 payments per second at the 10 ms window, ≈ 21.7 per channel**
       (measured; per customer 20.5–24.1, the 3B+ `bath2` highest). The cause is
       the one-update-in-flight channel, not the sensor or the chain: the probe
       (§8 step 1) measured about 30 ms per update on these boards and a
       ceiling of 29.6–33.4 updates per second per channel, while `bath1` was
       55 % busy and Anvil idle here. That the full pipeline reaches only about
       70 % of the probe's rate (21.7 against ≈ 30–31 per second) is
       measured; that the gap is the Python payment client, the sidecar hop
       and the sensor's stream on the same boards is inferred, not separated.
       Since the limit is per channel, the aggregate should grow with the
       number of customers until `bath1` saturates (inferred; not measured
       beyond 5 channels).
     - **Late executions at `w10`** (measured, both complete runs): at the drain
       deadline 64 payments per channel (= `MAX_IN_FLIGHT`) had been handed to
       the sidecar and were marked *unresolved*; 23–56 of them per
       channel (`020746`: 29 / 51 / 51 / 30 / 51; `040042`: 23 / 56 / 55 / 28 /
       56) were still executed by the sidecar before the quiesce took effect.
       So the strict reconciliation `ok` is **false** — the sensor holds
       212 × 10¹² and 218 × 10¹² wei more than the succeeded rows — and the
       difference equals exactly the sum of `unresolved_in_final_state`;
       `ok_explained` is true (0 ≤ difference ≤ the submitted-but-unresolved
       amount). The customer's and the sensor's channel views agree on every
       channel, and the sensor's L1 balance change plus its close fee equals
       its final channel balances (`l1_matches_final_balances` true).
       A channel has no nonces, but every Perun payment is `PRICE_WEI`, so the
       late count is exact from the channel: `late_executed` per customer =
       `unresolved_in_final_state`, `late_executed_total` **212** (`020746`)
       and **218** (`040042`), mean 215 ± 4.2 (n = 2), `late_executed_wei`
       212 × 10¹² / 218 × 10¹², and `ok_with_late` **true** in both runs (the
       sensor's balance = succeeded + late on every channel, the views agree,
       and the L1 adds up). At `w1000` and `w100` the late count is 0 and
       `ok_with_late` true in every run (re-analysed at `47df96e`, final
       review I2; until then these keys were null and the aggregate showed
       `n=0`). *Cause* (final review I1): mostly the order of operations in
       the Python client, not the channel. At the deadline it first marked
       about 8 000 pending rows unresolved (line-buffered writes on a Pi 3)
       and cancelled its trackers, and only then sent `quiesce`, 0.7–2.1 s
       after the deadline; the sidecar's queue (at most `MAX_IN_FLIGHT` = 64)
       kept running meanwhile. Per channel *dropped at quiesce* + *late* = 64,
       and the late count grows with that gap. Only the one update in flight
       and replies already in the pipe are inherent. From `d00a6bd` on,
       `quiesce` is sent at the deadline itself, before the rows are marked
       unresolved (pay_loop's `at_deadline` hook; Arbitrum and zkSync
       unchanged); the committed runs predate it. Their numbers stay valid:
       the late payments are honestly *unresolved* (never counted as
       succeeded, so goodput and latency are conservative by at most 57
       payments per channel in 900 s) and the reconciliation accounts for
       every wei.
     - *Collection gap in three `w10` folders* (final review I3):
       `20261004-020746-…-w10-rep1`, `20261004-024531-…-w10-rep2` and
       `20261004-040042-…-w10-rep1` lost part of `bath2`'s (c1's) raw files
       when the 300 s `pc/` copy timed out silently: `c1/ledger.log.jsonl.gz`
       is truncated (it ends mid-line) and `c1/sidecar.log` is missing. c1's
       `ledger.csv` (all 90 000 rows), `lifecycle.json` and the other four
       customers are complete, so no number is affected: the analysis reads
       `ledger.csv`. Each folder has a `NOTE.txt`. `deploy/cluster` now gives
       the copy a size-scaled timeout and writes `COLLECT_INCOMPLETE` when a
       copy fails or the sizes differ.
   - **Three systems, 5 customers, same sensor settings** (*superseded by the Final
     UC1 series below: `bath3` died, the network changed to mixed Wi-Fi*; aggregates
     `aggregate-perun-w*.txt`, `aggregate-arbitrum5-w*.txt`,
     `aggregate-zksync5-w*.txt`; mean ± sd, n = 3 unless noted). Perun's Close
     is a real on-chain close, confirmed; the rollups' Close is the submission
     of a withdrawal, whose finalisation is not measured (parent §11.4) — so
     they stand in separate columns:

     | window | system | latency p50 | latency p99 | goodput / offered per s | deadline miss | Open | Close: on-chain close confirmed | Close: withdrawal submitted |
     |---|---|---|---|---|---|---|---|---|
     | `w1000` | Perun | 0.05301 ± 0.0019 s | 0.1576 ± 0.016 s | 5.006 ± 0.0001 / 5 | 0.0074 ± 0.013 % | 1.323 ± 0.029 s | 2.629 ± 0.062 s | — |
     | `w1000` | Arbitrum | 0.09701 ± 0.00066 s | 0.361 ± 0.011 s | 5.005 ± 0.001 / 5 | 0.0148 ± 0.026 % | 1.704 ± 0.23 s | — | 0.2971 ± 0.032 s |
     | `w1000` | zkSync | 56.83 ± 7.4 s | 180.6 ± 26 s | 4.173 ± 0.064 / 5 | 98.28 % | 2.537 ± 0.08 s | — | 41.38 ± 10 s |
     | `w100` | Perun | 0.0485 ± 0.00064 s | 0.1791 ± 0.03 s | 50.01 ± 0.0005 / 50 | 3.907 ± 0.89 % | 1.354 ± 0.011 s | 2.489 ± 0.039 s | — |
     | `w100` | Arbitrum | 0.2308 ± 0.00041 s | 0.4672 ± 0.028 s | 50.00 ± 0.005 / 50 | 98.68 ± 0.056 % | 1.694 ± 0.21 s | — | 0.1752 ± 0.0018 s |
     | `w100` | zkSync | 321.8 ± 14 s | 864.7 ± 8.6 s | 4.594 ± 0.049 / 50 | 100 % | 2.527 ± 0.17 s | — | 41.56 ± 9.4 s |
     | `w10` | Perun (n = 2, saturated) | 385.2 ± 1.7 s | 499 ± 6.4 s | 108.5 ± 0.87 / 500 | 100 % | 1.312 ± 0.0008 s | 2.493 ± 0.071 s | — |
     | `w10` | Arbitrum | not measured — Wi-Fi collapse (0/3) | | | | | | |
     | `w10` | zkSync (n = 2, saturated) | 373.7 ± 40 s | 940.7 ± 9.1 s | 4.617 ± 0.1 / 500 | 100 % | 2.566 ± 0.11 s | — | 19.17 ± 1.9 s |

     At 1 s and 100 ms, Perun and Arbitrum both carry the whole offered load;
     Perun's latency (≈ 50 ms, an off-chain round trip between two Pi 3s) is
     about half of Arbitrum's at `w1000` and a fifth at `w100`, where
     Arbitrum's 250 ms block cadence sets the floor. zkSync stays at about 4–5
     per second at every window. At 10 ms, Perun carries about 23 times
     zkSync's goodput but only about 22 % of the offered rate (succeeded share
     23 %); there the saturated latencies of both are set by the queue bound,
     not by the system. At the edges, Perun's Open (1.3 s) is the fastest of
     the three, and its Close (2.5–2.6 s) is a complete settlement on the L1, which the
     rollups' sub-second (Arbitrum) or 19–42 s (zkSync) withdrawal
     *submission* is not. *Superseded by the Final UC1 series below, which measured
     Arbitrum `w10` and answers U9; kept as the record of the all-2.4 GHz network.*
     **Caveats (then):** zkSync's ceiling may be its
     20-in-flight bound per account rather than the chain (U9); Arbitrum `w10`
     is not measurable on today's Wi-Fi (above; the 6-customer 2026-09-30
     series is the only Arbitrum `w10` reference); Perun's `w10` figure is a
     per-channel limit of about 30 ms per update on the Pi 3Bs (§8 step 1), so
     it says how much one Pi 3 customer can sign, not how much Perun can carry
     in total; Perun runs on a bare L1 here (`perun-on-arbitrum`, §7.4, is
     postponed).
   **Final UC1 series (2026-10-11/12, `5mx7`) — the thesis numbers; supersedes the
   table above.** The same UC1 setup on the final board set and network: customers
   `bath2`, `bath4`…`bath7` (`bath7` replaces `bath3`, whose board died — red power
   LED dark on a known-good supply; `bath7` is a Pi 3B like it; the same boards as
   the UC2 series), mixed Wi-Fi (`bath1` and `bath2` on 5 GHz, all others on
   2.4 GHz), `bath3` idle (`deploy/experiments/uc1-*5mx7-*.env`, commit `7ae751b`).
   Two 3-minute `w10` smokes first (`20261011-133026`, `-134345`): `bath7` kept up on
   both (on Arbitrum `bath7` paid 18 000 of 18 000, its stream receive lag p99 0.11 s;
   `bath6` lost 647 there, as at every Arbitrum `w10`). Then 9 cells ×
   3 runs, all **complete**; one zkSync `w1000` run (`20261011-230326`) failed because
   `bath1` (sensor, time server) rebooted on its own 12 minutes into the stream (no
   shutdown in its journal) and was replaced by `20261012-033531`. A faulty power cable
   caused this restart and four more outside the series (five in 2026-10-09…12; cable
   replaced 2026-10-13, `deploy/FLEET.md`); the failed run
   keeps its folder and a `complete: false` summary. The 7 runs of the interrupted
   5 October series (with `bath3`) are not used and are not in the repository. Aggregates `results/pi/uc1/aggregate-{perun,arbitrum,zksync}5mx7-w*.txt`
   (summaries re-analysed at `708b090`, 2026-10-12; every figure below was unchanged
   by it, the lower-bound column is new); mean ± sd, n = 3 (no ± where the sd is
   below the last digit shown):

   | window | system | latency p50 (succeeded) | latency p50, all payments (lower bound) | latency p99 (succeeded) | goodput / offered per s | succeeded | deadline miss | Open | Close: on-chain close confirmed | Close: withdrawal submitted |
   |---|---|---|---|---|---|---|---|---|---|---|
   | `w1000` | Perun | 0.0395 ± 0.0022 s | = | 0.0756 ± 0.0059 s | 5.005 / 5 | 100 % | 0 % | 1.324 ± 0.009 s | 2.580 ± 0.021 s | — |
   | `w1000` | Arbitrum | 0.0920 ± 0.0032 s | = | 0.358 ± 0.006 s | 5.005 / 5 | 100 % | 0.04 ± 0.06 % | 1.552 ± 0.13 s | — | 0.397 ± 0.013 s |
   | `w1000` | zkSync | 68.3 ± 6.2 s | ≥ 76.2 ± 7.3 s | 182 ± 15 s | 4.12 ± 0.09 / 5 | 85.7 ± 1.7 % | 98.5 % | 2.615 ± 0.13 s | — | 22.8 ± 15 s |
   | `w100` | Perun | 0.0350 ± 0.0001 s | = | 0.0777 ± 0.021 s | 50.00 / 50 | 100 % | 0.49 ± 0.46 % | 1.344 ± 0.015 s | 2.543 ± 0.017 s | — |
   | `w100` | Arbitrum | 0.244 ± 0.016 s | = | 56.4 (rep1) / 0.41 / 0.43 s | 49.99 / 50 | 100 % | 97.4 ± 0.6 % | 1.824 ± 0.23 s | — | 0.222 ± 0.040 s |
   | `w100` | zkSync | 323 ± 11 s | ≥ 444 ± 7 s | 859 ± 8.4 s | 4.66 ± 0.08 / 50 | 9.7 ± 0.1 % | 100 % | 2.507 ± 0.16 s | — | 35.3 ± 2.0 s |
   | `w10` | Perun (saturated) | 252 ± 0.8 s | ≥ 327 ± 3.4 s | 357 ± 8.9 s | 157.5 ± 1.4 / 500 | 33.6 ± 0.3 % | 100 % | 1.324 ± 0.008 s | 2.445 ± 0.017 s | — |
   | `w10` | Arbitrum (saturated) | 102 ± 18 s | ≥ 125 ± 8.1 s | 169 ± 13 s | 369.6 ± 5.5 / 500 | 78.8 ± 1.2 % | 100 % | 1.924 ± 0.14 s | — | 0.194 ± 0.022 s |
   | `w10` | zkSync (saturated) | 369 ± 13 s | ≥ 498 ± 6.7 s | 942 ± 6.7 s | 4.73 ± 0.15 / 500 | 1.0 % | 100 % | 2.664 ± 0.18 s | — | 26.9 ± 13 s |

   **How to read the latency columns.** Latency (window arrival on the customer to
   confirmation) covers the **succeeded** payments only. Where payments did not
   succeed, that is a survivor statistic and understates the latency: at `w10` the
   three systems are saturated (the client's bounded queue refused payments, so the
   succeeded latencies are set by `MAX_QUEUE` = 10 000 and the 60 s drain, not by the
   system), and zkSync leaves 14 % (`w1000`) and 90 % (`w100`) unresolved without
   filling the queue. The lower-bound column counts every payment; one that never
   succeeded counts as its customer's last confirmation minus its arrival (it was
   still unconfirmed then), so the true median is at least that (`payment_latency_
   lower_bound_s`, analysis since `708b090`); "=" where every payment succeeded. The
   system's own confirmation time (submission to confirmation, p50) is unaffected by
   the queue: Perun 0.030–0.036 s, Arbitrum 0.086 / 0.238 / 0.732 s, zkSync 22.2 /
   22.8 / 22.9 s at `w1000` / `w100` / `w10`. Latency starts when the customer
   receives the window's last row, so the sensor-to-customer stream delay is not in
   it (receive lag, below). Goodput divides n succeeded payments by the span from the
   first to the last confirmation, which holds n − 1 intervals: a system that pays
   exactly the offered rate shows it raised by n/(n − 1) — hence 5.005 of 5.

   Fees: Perun 0 per payment (Open/Close gas only); Arbitrum 0.00945 ETH per 4 500
   payments at `w1000` (21 000 gas each), 1.25 ± 0.07 ETH over the `w10` run (256
   payments per run in flight at the drain deadline are not priced); zkSync
   0.033–0.041 ETH per run (local test-chain prices; the gas figures carry over,
   the ETH figures do not). **Reconciliation:** `ok_with_late` holds in all 27 runs.
   Payments given up at the drain deadline that executed anyway: 5 per run on Perun
   `w10` (212–218 in the older series, before the quiesce fix `d00a6bd`), 256 per run
   on Arbitrum `w10` (64 = `MAX_IN_FLIGHT` on each of the four saturated Pi 3B
   customers; `bath2` none), 100 per run on zkSync at every window (20 per customer).

   **Arbitrum ran with own receipts at every window** (`RECEIPT_MODE=own`: each
   client fetches only its own receipts, as zkSync does), not only at `w10`; the
   older "whole blocks" comparability caveat (step 3 above) does not apply to these
   numbers.

   **Undervoltage, by system.** The Pi 3B customers ran undervolted (600 MHz) for a
   share of every run that grows with the load and so differs by system — `bath4`
   0.91–0.93 on Perun `w10`, 0.74–0.84 on Arbitrum `w10`, 0.41–0.43 on zkSync `w10`;
   `bath7` 0.94–0.98 / 0.86–0.94 / 0.56–0.73; `bath6` 0.93–0.96 / 0.85–0.91 /
   0.46–0.57; `bath5` 0.46–0.48 / 0.24–0.30 / 0.09–0.14 (at `w1000` 0.04–0.68 across
   boards and systems; per run in each summary's `diagnostics.recorder`). `bath0`,
   `bath1` and `bath2` were never undervolted. Where the customer boards are the limit
   — Perun and Arbitrum at `w10` — their figures are per-board lower bounds measured
   on half-speed boards, and the boards were slowed more under the heavier systems.
   `bath0` ran at 2 % CPU on Perun, 16 / 29 / 86 % on Arbitrum (`w1000` / `w100` /
   `w10`) and 99–100 % on zkSync at every window.

   **What changed against the table above:** Arbitrum `w10` is now measured (the
   mixed Wi-Fi and own-receipt mode removed the collapse): 370 payments per second,
   79 % of the offered 500. `bath2` (Pi 3B+) paid all its 90 000 at 99.7–100 per s,
   the Pi 3Bs 63–78 per s, so the Pi 3B payment clients are the visible limit (U6);
   `bath0` was at ≈ 86 % CPU, so a stack limit near this rate is not ruled out — a
   lower bound for Arbitrum; its ceiling needs the capacity probes (future work).
   Perun's latency fell from ≈ 50 to 35–40 ms and its `w10` goodput rose from 108.5
   to 157.5 per second; the sensor on 5 GHz is the likely reason (inferred: the
   board set and the quiesce code changed too). Its `w10` limit stays the per-channel
   update rate: ≈ 28–31 per second on a Pi 3B, ≈ 40 on `bath2` (3B+, 5 GHz). zkSync
   is unchanged: 4.1–4.7 per second at every window, the chain's ceiling on the Pi 4
   **under this sustained 15-minute load** (U9; UC2's two-minute burst reached
   7.5–8.9 per second, §8 step 6). **Outliers, kept:** Arbitrum
   `w100` rep1 (`20261011-190947`) had a stall — payment p99 56.4 s and stream receive
   lag p99 3.0 s against 0.41–0.43 s and 0.03–0.05 s in the other two runs; Arbitrum
   `w10` receive lag p99 0.44–6.5 s (stalls on the shared 2.4 GHz channel under
   ~370 transactions per second). **Not measured, future work (owner, 2026-10-11):**
   the capacity probes (§7.1: Arbitrum's and Perun's total ceilings) and
   `perun-on-arbitrum` (§7.4).

   **Wi-Fi baseline (2026-10-12; §7.1 `baseline`).** The bare network under the
   measurements, with no chain, client or game running: `deploy/wifi-baseline.sh`, per
   board one at a time, 300 pings 0.1 s apart and a 10 s TCP transfer each way (a Python
   sender/receiver over ssh) to `bath0` (the stack) and to `bath1` (sensor / hub).
   Run `results/pi/baseline/20261012-171149-wifi-baseline/`; `bath1` rebooted during it
   (the fifth restart, caused by a faulty power cable, `deploy/FLEET.md`), so the `bath4` → `bath1` pair was
   measured again in `…/20261012-173455-wifi-baseline/` (a first full run the same
   afternoon is void: a script fault set the transfer to 0 s, and `bath1` rebooted in it).
   n = 1 per pair; measured:

   | from → to | band (from) | RTT p50 / p99 / max (ms) | TCP up / down (Mbit/s) |
   |---|---|---|---|
   | `bath2` → `bath1` | 5 GHz → 5 GHz | 9.8 / 12.0 / 12.7 | 76.8 / 74.1 |
   | `bath1`, `bath2` → `bath0` | 5 GHz → 2.4 GHz | 10.1–10.3 / 19.3–24.8 / 34–44 | 39.6–42.9 / 31.2–32.8 |
   | `bath0` → `bath1` | 2.4 GHz → 5 GHz | 9.9 / 17.8 / 20.8 | 31.9 / 39.9 |
   | Pi 3Bs (`bath4`–`bath7`) → `bath0` | 2.4 GHz → 2.4 GHz | 9.3–10.6 / 20–31 / 29–51 | 1.1–5.4 / 6.3–10.0 |
   | Pi 3Bs (`bath4`–`bath7`) → `bath1` | 2.4 GHz → 5 GHz | 9.2–10.1 / 16–32 / 24–66 | 1.9–12.7 / 7.2–11.1 |

   No packet was lost on any valid pair. Every round trip has a floor of ≈ 10 ms
   (p50 9.2–10.3 ms on every pair, both bands): the floor under every latency above —
   Perun's ≈ 35 ms per payment holds about three such round trips (inferred). The Pi 3Bs
   on 2.4 GHz move only a few Mbit/s, least to `bath0` on the same channel (each packet
   crosses the air twice); a UC1 customer's stream needs ≈ 0.55 Mbit/s and its payments
   far less, so bandwidth is not the limit, but the shared 2.4 GHz channel is where the
   stalls of §9 U4 arise (inferred). The throughput figures come from a Python sender on
   undervolted boards and are what such a client can push, not the radio's capacity.

4. Installer checks before it touches all eight boards (*not reached: the installer is
   future work, §3*): `ansible-playbook
   --syntax-check` and `ansible-lint`; the rendered `cluster.local.json` loaded
   through the real `loadClusterConfig` in a Go test; a dry run against an arm64
   container (catches package names, paths, templates and unit files; proves
   nothing about performance or SD cards).
5. **Postponed until step 3 works:** the installer on all eight boards, weapon and
   bot tuning, `uc2-movement-onchain`, the `-distant` runs, and
   `perun-on-arbitrum`. *Outcome:* weapon and bot tuning were done (UC2, step 6);
   the rest is future work (owner, 2026-10-11; §11).
6. **UC2, next (step 3 done for UC1 on all three systems; decided 2026-10-06).**
   Built with the cluster as it is (`deploy/cluster`, boards prepared by hand);
   the installer stays postponed. Order, riskiest first, workstation before Pis:
   (a) weapon, bots, no-negative rule and *S* in `tank-arena`, tuned and frozen
   (workstation, simulation only); (b) the game → payment bridge and the
   live inputs for Perun and the rollup sender, tested on the workstation with
   six local processes per system; (c) the reference log on `bath2`, then one
   smoke match per system on the cluster (answers U7, U8); (d) the series:
   3 × `uc2-perun`, 3 × `uc2-arbitrum`, 3 × `uc2-zksync` (§7.3 *Run parameters*);
   (e) `analyze_uc2.py` and the results in this section. UC1's interrupted
   mixed-Wi-Fi series (`bath3` offline 2026-10-05; the 6 Perun runs at `w1000` and
   `w100` were complete) was *superseded*: `bath3` died, and the final series ran
   with `bath7` instead (`5mx7`, step 3 above, 2026-10-11/12).
   - ✅ **UC2 smoke matches** (2026-10-06, plan `2026-10-06-uc2-game-on-three-systems`,
     Task 14; `results/pi/uc2/*-smoke-rep1`). One 60 s match per system on the
     cluster: players `bath1 bath2 bath7 bath4 bath5 bath6`, `bath3` idle, mixed
     Wi-Fi, Perun yield 200 ms. All three are complete and hash-identical to the
     reference on all six boards (SHA-256 `bc1d6874…`, measured). These are smoke
     matches, n = 1 each, not the series. The same 1070 payments (813 fire, 257 hit,
     17.8 per second) were offered in each. Measured, from each `summary.json`
     (re-analysed at `708b090`, 2026-10-12: only the next-shot row changed, see the
     series below):

     | | Perun | Arbitrum | zkSync |
     |---|---|---|---|
     | payments succeeded | 1070 | 1070 | 1045 (host 25 unresolved, 20 late) |
     | latency p50 / p99, guests (ms) | 37 / 404 | 201 / 345 | 14 680 / 40 707 |
     | latency p50 / p99, host (ms) | 19 / 67 | 188 / 325 | 39 617 / 63 915 |
     | next-shot miss share, guests / host | 0.0035 / 0 | 0.640 / 0.671 | 0.993 / 1.0 |
     | tick lag p99 / max, pooled (ms) | 90 / 140 | 96 / 150 | 85 / 313 |
     | stretch | 1.00053 | 1.00074 | 1.00054 |
     | Open / Close (s per player) | 4.2–26.9 total (ledger 1.5–2.6), 6.7–7.0 | 1.2–2.1, 0.17–0.38 | 2.5–2.9, 8.6–27.6 |
     | gas per shot (median Open / Close) | 0 (103 684 / 222 459) | 34 398 (91 067 / 71 830) | 85 549 (291 390 / 178 247) |
     | reconciliation | ok | ok | `ok` false, `ok_with_late` true |

     - **zkSync's latency row covers the share that succeeded** (1045 of 1070, 97.7 %; the
       25 unresolved, 2.3 %, are excluded). Their latency is at least drain deadline −
       `t_generated`, so the p50 and p99 understate the tail (survivorship, as UC1's
       caveat above).
     - **zkSync's host** lost 25 of its 292 payments to the 60 s drain deadline
       (`drain deadline`); 20 of them executed on-chain afterwards (40 points), which
       explains every difference in the balances. Guests: 778 of 778. The host sends
       292 payments against about 130 per guest with the same 20 in flight; at tens of
       seconds of latency its queue outlasts the drain (inferred, not a code fault).
     - **`bath0` ran at 98 % CPU** (mean, max 99.9 %) on zkSync; on Perun 2 %, on
       Arbitrum 21 %. Player CPU stayed below 45 % (max) on every board.
     - **Tick-lag max of about 300 ms** on every board at once, seen only on zkSync
       (the others stayed below 150 ms); p99 was normal. Cause not established (a
       shared stall on the channel is a guess).
     - **`bath4`, `bath6`, `bath7` were undervolted at 600 MHz** for 25–75 % of each
       match (`0x50005`). This is the known Pi 3B limitation (see below), the same
       for all systems; the game still kept up.
     - **One gap:** the manifest records no Wi-Fi link rate, although §6 says it does.
       `bath7` was read by hand between Perun and Arbitrum: 2462 MHz, −52 dBm, rx 65 /
       tx 72 Mbit/s (not the 11 Mbit/s of 2026-10-01). Closed 2026-10-11: the board facts
       now record `signal_dbm`, `rx_bitrate_mbit`, `tx_bitrate_mbit` (`iw dev wlan0 link`)
       at run start and end; runs before that date have none.
   - ✅ **UC2 series** (2026-10-06, plan `2026-10-06-uc2-game-on-three-systems`,
     Task 15; `results/pi/uc2/*-uc2-{perun,arbitrum,zksync}-rep{1,2,3}`, aggregates
     `results/pi/uc2/aggregate-uc2-<system>.txt`). 3 × `uc2-perun`, 3 × `uc2-arbitrum`,
     3 × `uc2-zksync`, run back to back in one tmux session, 19:01–21:12, all nine from commit
     `caa1a6d` (code hash `997f22fb…` on every board). Same setup as the smoke matches:
     players `bath1 bath2 bath7 bath4 bath5 bath6`, `bath3` idle, mixed Wi-Fi (`bath1`,
     `bath2` on 5 GHz), Perun yield 200 ms, seed 42, `DRAIN_S=60`, 64 in flight on Perun and
     Arbitrum, 20 on zkSync. **No repetition failed and none was rerun.**

     **Re-analysed 2026-10-12 (`708b090`).** The next-shot deadline is now the moment the
     player actually fired again, as §7.3 defines it; until then the code used the next
     shot's *scheduled* tick time while the latency starts at the actual firing time, so
     the game's own tick lag (p50 ≈ 25 ms) counted as payment lateness. Old → new, guests:
     Perun 0.049 → 0.0018, Arbitrum 0.691 → 0.606, zkSync unchanged; every other figure is
     unchanged.

     **Run status (measured, from each `summary.json`).** All nine are `complete`, not
     `saturated`, and `hash_ok` on all six boards (SHA-256 `bc1d6874…` = the reference).
     Each offered the same 1070 payments (813 fire, 257 hit).

     | run | succeeded / unresolved | reconciliation |
     |---|---|---|
     | `190156-perun-rep1`, `191510-perun-rep2`, `192808-perun-rep3` | 1070 / 0 each | `ok`; no broken channel; every channel's mirror and net match the ledger |
     | `194109-arbitrum-rep1`, `195224-arbitrum-rep2`, `200332-arbitrum-rep3` | 1070 / 0 each | `ok` |
     | `201456-zksync-rep1` | 869 / 201 (host 122, guests 79) | `ok` false, `ok_with_late` true (57 late, 117 points) |
     | `203125-zksync-rep2` | 1002 / 68 (host 63, guests 5) | `ok` false, `ok_with_late` true (25 late, 69 points) |
     | `205212-zksync-rep3` | 971 / 99 (host 67, guests 32) | `ok` false, `ok_with_late` true (40 late, 76 points) |

     Every zkSync unresolved payment has `error = drain deadline`; the late ones executed
     on-chain afterwards and explain every balance difference.

     **The three systems side by side**, n = 3 each, mean ± sample standard deviation over
     the runs. Latency is generated → confirmed. "Host" is p0 on `bath1`; "guests" are the
     other five.

     | | Perun | Arbitrum | zkSync |
     |---|---|---|---|
     | latency p50, guests fire / hit (ms) | 38.6 ± 0.5 / 38.9 ± 1.0 | 195 ± 5 / 207 ± 6 | 26 170 ± 4 540 / 20 570 ± 6 440 |
     | latency p99, guests fire / hit (ms) | 82 ± 9 / 315 ± 259 | 444 ± 94 / 526 ± 298 | 69 430 ± 4 920 / 70 330 ± 4 840 |
     | latency p50, host fire / hit (ms) | 17.9 ± 0.1 / 32.5 ± 1.3 | 177 ± 3 / 196 ± 15 | 40 240 ± 3 630 / 60 070 ± 10 270 |
     | latency p99, host all (ms) | 58 ± 1 | 451 ± 240 | 78 590 ± 8 240 |
     | next-shot miss share, guests / host | 0.0018 / 0 | 0.606 ± 0.011 / 0.629 ± 0.004 | 0.996 ± 0.002 / 1.000 ± 0 |
     | latency p50, guests, all payments (lower bound, ms) | = | = | ≥ 26 360 ± 5 680 |
     | goodput (payments/s; offered 17.8 over the match duration) | 17.92 ± 0.001 | 17.89 ± 0.005 | 8.30 ± 0.72 |
     | succeeded / unresolved / late (of 1070) | 1070 / 0 / 0 | 1070 / 0 / 0 | 947 ± 70 / 123 ± 70 / 41 ± 16 |
     | tick lag pooled p50 / p99 / max (ms) | 24.9 ± 0.3 / 80 ± 7 / 162 ± 46 | 23.6 ± 1.3 / 109 ± 64 / 175 ± 101 | 21.6 ± 0.4 / 56 ± 11 / 108 ± 55 |
     | stretch | 1.00050 ± 0.00022 | 1.00054 ± 0.00022 | 1.00060 ± 0.00033 |
     | Open per player, median (s) | 14.0 ± 1.8 total (ledger 1.4–2.5, virtual 0.6–3.1) | 1.67 ± 0.03 | 2.51 ± 0.14 |
     | Close per player, median (s) | 7.14 ± 0.03 | 0.33 ± 0.02 | 19.3 ± 7.0 (range 2.0–39.7) |
     | gas, Open / Close (median per player) | 103 684 / 222 448 ± 10 | 91 067 / 71 830 | 291 390 / 145 449 ± 28 591 |
     | gas per shot (fire = hit) | 0 (off-chain) | 34 398 | 85 549 |
     | CPU mean / mean of per-run max %, `bath0` (Pi 4, the stack) | 1.9 / 2.1 | 20.0 / 32 | 97.9 / 99.9 |
     | CPU mean / mean of per-run max %, `bath1` (Pi 3B+, host) | 21.8 / 35 | 14.7 / 22 | 13.7 / 24 |
     | CPU mean / mean of per-run max %, other players (`bath2` 3B+; `bath4`–`bath7` 3B) | 18.5 / 40 | 17.2 / 42 | 16.2 / 37 |
     | undervolted share `bath4` / `bath6` / `bath7` | 0.58 / 0.61 / 0.56 | 0.25 / 0.44 / 0.67 | 0.31 / 0.39 / 0.64 |

     Goodput is succeeded payments over the confirmation span (first to last confirmation);
     the offered 17.8 per second is over the match duration, so Perun's 17.92 can exceed it.
     The span differs by system: ≈ 60 s on Perun and Arbitrum, up to ≈ 116 s on zkSync
     (it includes the drain), so zkSync's 8.3 per second is a rate over a longer window,
     not over the match. It is still about twice UC1's sustained 4.1–4.7: here a zkSync
     payment took ≈ 7 s from submission to confirmation (p50; ≈ 22 s in UC1), with up to
     6 × 20 in flight. Why the two-minute burst on a freshly restored chain confirms faster
     than UC1's 15-minute load is not established; U9's ceiling is stated for the latter.
     **zkSync's latency rows cover only the payments that succeeded** (per run 81.2–93.6 %
     of the 1070; 68–201 unresolved, 6.4–18.8 %). The unresolved ones were still queued or
     in flight at the drain deadline: 57 / 25 / 40 of them had been submitted and executed
     on-chain later (the late ones); the rest were never sent. Counting each with its
     payer's last confirmation − `t_generated` (a lower bound) gives the guests' p50
     **≥ 26.4 s** (per run 32.5 / 25.4 / 21.2 s against 27.2 / 25.2 / 20.1 s succeeded
     only; `latency_lower_bound_ms`); rep1's p99 cannot be bounded usefully (10 % of the
     guests' payments unresolved). Perun and Arbitrum lost none (1070 of 1070).

     **Perun's hit-latency tail follows channel collisions.** When both ends of a virtual
     channel propose an update at once (a head-on hit), one side gives way and retries
     (yield 200 ms, up to 10 tries). The guests' hit p99 per run, 599 / 93 / 251 ms, tracks
     the collisions counted at close, 5 / 0 / 3 (smoke: 9 collisions, 878 ms). The count
     is per player, not per payment, so the tail cannot be split exactly (future work:
     record the give-way per payment).

     Perun's "Open total" is the whole lifecycle step per player, including the barriers
     until every ledger channel and the full virtual mesh are open; the ledger and virtual
     Opens themselves take 0.6–3.1 s. Perun's Close (≈ 7 s) is likewise the whole
     coordinated step: the barriers (payments done, snapshot, close the virtual channels,
     five settle rounds, all virtual channels settled, every ledger settled, the hub's
     included) plus the player's own work — its virtual settles take 0.25–1.5 s and its
     ledger settle ≈ 3 s (2.55–3.34 s; the virtual range corrected 2026-10-16 from
     0.4–1.5 s, `close_virtual_settle_s` in `analysis/data/results.csv`); the sd of 0.03 s shows the barriers set the figure. It ends in a
     complete settlement on the L1; the rollups' Close is the withdrawal submission.
     Token setup on the rollups (untimed): Arbitrum deploy 469 169 gas + mintBatch 194 414,
     zkSync 722 671 + 135 429.

     **Series next to the smoke matches** (n = 3 vs n = 1, same commit for the game, same
     offered load):

     | | Perun series / smoke | Arbitrum series / smoke | zkSync series / smoke |
     |---|---|---|---|
     | succeeded | 1070 / 1070 | 1070 / 1070 | 947 ± 70 / 1045 |
     | latency p50 guests / host (ms) | 38.7 / 18.3 vs 37 / 19 | 198 / 180 vs 201 / 188 | 24 180 / 40 690 vs 14 680 / 39 617 |
     | next-shot miss guests | 0.0018 vs 0.0035 | 0.606 vs 0.640 | 0.996 vs 0.993 |
     | tick lag p99 / max (ms) | 80 / 162 vs 90 / 140 | 109 / 175 vs 96 / 150 | 56 / 108 vs 85 / 313 |
     | `bath0` CPU mean % | 1.9 vs 1.9 | 20.0 vs 21.4 | 97.9 vs 98.2 |

     **Measured:** everything in the tables (from the run folders: the payment ledgers, the
     game's ticks and node results, the chain receipts and balances, the recorder).
     **Inferred, not measured:**
     - zkSync is the bottleneck on `bath0` (CPU 98 %, as in UC1, U9): payments wait tens
       of seconds, so the payers' queues outlast the 60 s drain. Unlike the smoke match,
       guests lost payments too (rep1 79, rep2 5, rep3 32), and the loss varies from run to run
       (68–201). Late executions are at most 20 per player (p0 20 in every run, p4 5–20,
       p2 17 once): the payments still in flight (bound 20) at the deadline, which executed.
     - Next-shot miss (measured: a shot misses if its payment did not succeed before the
       same player actually fired again): Perun's ≈ 40 ms beats the next shot in all but
       ≈ 0.2 %; Arbitrum's ≈ 200 ms (its 250 ms block cadence sets the floor, as in UC1)
       misses about 61 %; zkSync misses all. The bots' bursts fire every 0.1 s, so any latency above
       ≈ 100 ms misses inside a burst (inferred).
     - The game itself is not disturbed by the payments on any system: stretch ≤ 1.001 in all
       nine runs, pooled tick-lag p99 48–183 ms per run. The highest tick-lag max of the
       series (290 ms, p99 183 ms) was Arbitrum rep2; the about 300 ms max seen on every
       board in the zkSync smoke match did not recur (zkSync series max 69–171 ms), so it was
       not zkSync-specific (cause still not established; a shared Wi-Fi stall is a guess).
     - **`bath4`, `bath6`, `bath7` were undervolted and at 600 MHz** for 9–75 % of each match
       (per-run share in the run folders; means in the table), on all three systems;
       `bath1`, `bath2`, `bath5` never (in UC1, `bath5` was too). Same caveat as the smoke item and the Pi 3B
       limitation below: their absolute numbers are a lower bound. In UC2 the players are
       not the limit of any system (no queue filled, Perun and Arbitrum paid all 1070), so
       the comparison between the systems is unaffected here, unlike UC1 at `w10`. The game
       kept up (player CPU max ≤ 45 %).
     - **No Wi-Fi link rate in these manifests** (smoke item above); not added during the
       series, because no code was to change. Added 2026-10-11 for later runs.

## 9. Known unknowns

| # | Unknown | When it is answered |
|---|---|---|
| U1 | x86-initialised chain state starts on arm64 (§4) | **Yes, for zkSync and Arbitrum** (2026-09-29, §8 step 1): a copy of the PC's state starts on `bath0` and confirms transfers. Sustained runs: answered by the 15-minute UC1 series on both rollups (U3, §8 step 3) |
| U2 | ~~The arm64 `zksync_server` binary is lost~~ — **resolved 2026-09-28:** the binary from the 8 September build was found (185 065 688 bytes, not distributed; SHA-256 `e77b1cc838a0bb459509b5f7067b02ebdb318758f14061e08339a2740b2ad8a6`). It runs the chain on the Pi: every zkSync run (UC1, UC2) used it (`images.zk_bin_sha256` in each manifest). | §8 step 1, on `bath0` |
| U3 | SD-card I/O under PostgreSQL and RocksDB | **Answered for the sustained loads** (2026-09-29, §8 step 1): zkSync about 1 MB/s written, SD busy 13 %; Arbitrum about 2 MB/s, busy 8 %. The card is not the bottleneck at these rates |
| U4 | Eight boards on one 2.4 GHz channel: contention under the one-node load — **partly answered 2026-10-01/02** (§8 step 3, zkSync dry runs): the channel is a shared bottleneck. The UC1 sensor's one-segment-per-row stream (an estimated 60–65 % of the airtime) plus the payment clients' receipt bursts drove `bath1`'s flows into a slow state that did not recover, on both systems; a slow station (`bath7`, 7–11 Mbit/s) adds to it. Mitigated (write coalescing, own receipts, 5 customers), not removed: `w10` rep1 of the zkSync5 series still collapsed, and on 2026-10-03 all three Arbitrum5 `w10` reps collapsed too, with old-sensor and old-code diagnostics collapsing where the same or older code had run `w10` clean on 2026-09-30/10-01 morning — the channel's condition varies over time independent of the stack or recent code, and the 5-customer suite's 10 ms window is not measured on either chain. On Perun (2026-10-04) the stream collapsed in 2 of 9 series runs (`w100` at about 745 s, `w10` at about 375 s), although Perun's payments are two small wire messages per update and fetch no receipts: the receipt bursts are not needed for the collapse (inferred, n = 2) | `baseline` experiment, idle and under load; the receive lag of every UC1 run; a capture on `bath1` to settle why the flows stay slow **2026-10-04:** Arbitrum w10 with `RECEIPT_MODE=own` (each client fetches only its own receipts; `uc1-arbitrum5own-w10`, run `20261004-124323`) streamed ~350–380 s instead of 40–75 s (229 payments/s, confirmation p50 0.85 s), then collapsed too: receipt traffic was a large share, but 500 payments/s plus the stream still exceed the 2.4 GHz channel. Remaining options: bath0/bath1 on 5 GHz or Ethernet (then rerun all systems), or report Arbitrum w10 as not measurable on this network. **Answered for the thesis 2026-10-11/12 (owner: mixed Wi-Fi):** `bath1` and `bath2` on 5 GHz, the rest on 2.4 GHz (`bath0`'s 5 GHz link measured 13 Mbit/s), own receipts on Arbitrum: all 27 runs of the final UC1 series complete, Arbitrum `w10` measured. Stalls remain (Arbitrum `w100` rep1, `w10` receive lag p99 up to 6.5 s), stated with the results. Bare network: the Wi-Fi baseline (§8) |
| U5 | Thermal throttling of a Pi 4 under sustained sequencer load — **bare: soft limit active most of the time at about 2 % clock loss (zkSync run); with the heatsinks: never reached (Arbitrum run)** | recorder samples the throttle flag throughout every run. A throttled run is flagged and reported, never silently discarded; if a stack throttles repeatedly under the specified cooling, **that is a result** (the stack does not sustain on this hardware). |
| U6 | Whether a 1 GB Pi 3 can drive its share of load (Python sender) — **sharper since 2026-09-29:** the Pi 3Bs run at about half speed (§8 step 1). **First evidence 2026-09-30** (§8 step 3, finding (c)): at the 10 ms UC1 window the Pi 3B+ customer paid all 100 payments/s, queued up to ~10 s behind, the Pi 3Bs reached 17–48 each. At zkSync's rates (about 1 per second per customer, 2026-10-02) the customers were 10.4–22.4 % CPU busy, not the limit | `one-client` from one Pi 3 vs. `one-node` from several; if adding senders raises the ceiling, the senders were the limit. Run `one-client` on a 3B+ and on a 3B to show the difference. **Final series (2026-10-11/12):** at `w10` the Pi 3B+ (`bath2`) paid 99.7–100 per s on Arbitrum and ≈ 40 on Perun, the Pi 3Bs 63–78 and ≈ 28–31 — the clients are the visible limit there; the `one-client`/`one-node` probes that would separate client and stack are **future work** (owner, 2026-10-11) |
| U7 | Whether a Pi 3 keeps the game's 20 Hz lockstep while also running the payment client | recorder CPU samples and the game's own tick lag, first UC2 run — **answered 2026-10-06 (smoke matches, n = 1 per system, §8 step 6): yes.** Lockstep held at 20 Hz with the payment client beside it: stretch at most 1.00074, player CPU below 45 % (max), on all three systems. Three boards ran at 600 MHz (undervolted) and still kept up. **Confirmed by the series (2026-10-06, n = 3 per system, §8 step 6):** stretch at most 1.00097 in all nine runs, player CPU max 45 % |
| U8 | Whether six-player lockstep at 20 Hz survives the shared Wi-Fi (mixed: `bath1`, `bath2` on 5 GHz, the other players on one 2.4 GHz channel) | `baseline` gives the RTT; the first UC2 run shows tick lag — **answered 2026-10-06 (smoke matches, n = 1 per system): yes, on one shared Wi-Fi channel.** Pooled tick lag p99 about 85–96 ms (per board 79–110 ms) at 20 Hz, no stalled match; max below 150 ms on Perun and Arbitrum, about 300 ms on zkSync (cause not established). One match per system, not the series. **Confirmed by the series (n = 3 per system):** pooled tick-lag p99 48–183 ms per run, max 69–290 ms (highest Arbitrum rep2), no stalled match; the ≈ 300 ms max of the zkSync smoke match did not recur |
| U9 | Whether zkSync's UC1 goodput on the Pi 4 (about 4.1–4.7 per second) is the chain's limit or the 20-in-flight bound per account (100 in flight ÷ 21–24 s confirmation gives the same number; §8 step 3) | the §7.1 `one-node` probe on zkSync with the in-flight depth swept; `bath0`'s CPU at 99 % and the feasibility runs without the bound point to the chain. **Answered 2026-10-04: the chain.** Capacity probe `20261004-102456-…-probe-zksync5-w100-if200-rep1` (`deploy/experiments/archive/probe-zksync5-w100-if200.env`: the w100 setup with 200 in flight per account instead of 20; not a UC1 cell): goodput **3.08 per second** (series with 20: 4.594 ± 0.049, n=3), confirmation p50 **297 s** (series 23.58 s), server CPU 249 % average (peak 301 %), complete, `ok_with_late` true. Ten times the in-flight depth gives no more, rather less throughput (1 000 in flight ÷ 297 s ≈ 3.4 per second, Little's law); the extra transactions only queue in the mempool. The ~4–4.6 per second in §8 step 3 is zkSync's own ceiling on the Pi 4 under UC1's sustained load, not the client bound. One run, n = 1. **Qualified 2026-10-12:** the final UC1 series confirms 4.1–4.7 per second; UC2's two-minute token burst reached 7.5–8.9 per second with a submission-to-confirmation p50 of ≈ 7 s (≈ 22 s in UC1); why the burst confirms faster is not established (§8 step 6) |

**Limitation for the thesis (2026-09-29, revised 2026-10-12): marginal power on the
Pi 3Bs.** The Pi 3Bs in the final runs (`bath4`–`bath7`) run undervolted at about half
speed under load (accepted by the owner; no new supplies). Their absolute numbers
(customers, players, senders) are therefore a lower bound for that board. Where the
boards are not the limit — UC1 at `w1000` and `w100`, all of UC2 — the comparison
between the systems is not affected: all three ran on the same boards with the same
supplies. Where they are the limit — Perun and Arbitrum at UC1 `w10` — it is: the
share of time undervolted grows with the load and so differs by system (e.g. `bath4`
0.91–0.93 on Perun, 0.74–0.84 on Arbitrum, 0.41–0.43 on zkSync), so those `w10`
figures are per-board lower bounds taken at different board speeds (§8 step 3, *Final
UC1 series*). Every run records each board's time spent undervolted and its clock (§6).

## 10. Documentation changes

Section numbers of `deploy/README.md` below are in the numbering of that date.

**Done (2026-09-29):** `deploy/README.md` (boards, 2.4 GHz, addresses, time
source on `bath1`, PC as the only workstation), `deploy/FLEET.md`, and
`perun/hub-demo/DISTRIBUTED.md` (the Pi cluster layout; the three-laptop beta
moved to a *Legacy* section).

**Done (2026-09-30):** `deploy/README.md` §4 (the `cluster` commands and the
analysis commands), with the first complete measurement path (§8 step 3).

**Done (2026-10-02):** `deploy/README.md` §4: `up zksync`, `down` for both stacks,
and the experiment-file keys added for zkSync and the 5-customer runs.

**Done (2026-10-04):** `deploy/README.md` §4 (`up perun`, the Perun experiment
keys), `deploy/FLEET.md` (Anvil's path on `bath0`) and
`perun/hub-demo/DISTRIBUTED.md` (*UC1: direct channels*), with UC1 on Perun
(§8 step 3).

**Archived (2026-10-12), not updated:** the spike's ansible tree
(`archive/ansible-spike/`), `measure-baseline.sh` and `build-arm64.sh`
(`archive/deploy-legacy/`), the June workstation `Makefile`/`setup.sh`
(`archive/workstation-setup/`); see `archive/README.md`.

**Done (2026-10-12, after a repository audit):** a new root `README.md`; `deploy/README.md`
rewritten as the as-built runbook and `deploy/FLEET.md` updated (`bath3` dead, `bath1`
reboots, mixed Wi-Fi); `results/pi/README.md` (final vs superseded data); the stack
configuration of `bath0` in `deploy/stack-config/`; superseded experiment files in
`deploy/experiments/archive/`; component READMEs corrected or marked *OUTDATED*;
STATUS and the plans archived; the parent spec marked superseded where
this spec wins; §1–§7 of this spec rewritten as built.

## 11. Future work and known limitations

Collected for the thesis (owner, 2026-10-11/12). None of it changes a measured number;
each item is either not measured or a property of the code as measured.

**Not measured (future work):**
- **Capacity probes** (§7.1 `one-client`, `one-node`, `footprint`): Arbitrum's ceiling
  on the Pi 4 (UC1's ≈ 370 per second is a lower bound), Perun's total over many
  channels (only the per-channel rate is measured), and why zkSync's UC2 burst
  (7.5–8.9 per second) exceeds its sustained UC1 rate (4.1–4.7).
- **Perun on Arbitrum** (§7.4): channel Open and Close as Arbitrum transactions.
- **`-distant` runs** (network-delay emulation) and **`uc2-movement-onchain`** (§7.3).
- **The one-command installer** (§3): the boards were set up by hand.
- **Per-process resource recording** (CPU and memory per container, Anvil,
  `perun-node`); the recorder is system-wide, only the zkSync server is sampled per
  process.
- **Give-way per payment on Perun:** collisions are counted per player, so the UC2 hit
  tail cannot be split exactly (§8 step 6).
- **A sensor-referenced UC1 latency:** latency starts when the customer receives the
  window, so stream delay is outside it (receive lag is reported separately).

**Known limitations of the code as measured** (none was triggered in the final runs;
fixing the Go items would change the measured `perun-node` binary, so they are stated,
not fixed):
- `perun-node` hub and players never read their handler-error queue (64 slots): errors
  are not logged, and after 64 a channel would stall. No channel broke in any run.
- The gas meter skips an RPC error silently; the measured gas is stable across runs
  (e.g. UC2 Open 103 684 identical, Close 222 448 ± 10).
- The UC2 hub admits ledger channels by balance shape and amounts only (not challenge
  duration, asset or app, unlike the UC1 sensor), and its control HTTP API has no
  authentication — acceptable on a closed local cluster.
- No dispute close: a broken channel is not closed, and a failed cooperative close does
  not fall back to an on-chain dispute (§7.2, out of scope).
- Board power: the Pi 3Bs run undervolted (§9); `bath1` restarted five times in
  2026-10-09…12, caused by a faulty power cable, replaced on 2026-10-13 (one run
  replaced; `deploy/FLEET.md`).

**Data posting to the L1: Arbitrum calldata, zkSync blobs (checked 2026-10-26).** Not
a changed number, but an asymmetry the thesis states.
- **Arbitrum posted every batch as calldata.** The sequencer log of all ten UC1 runs
  of 2026-10-11 (smoke and the nine final runs, 30–262 batches each) shows only
  `BatchPoster: batch sent … numBlobs=0`. `deploy/stack-config/arbitrum/sequencer_config.json`
  does not switch blob posting on (no `post-4844-blobs`; it sets `disable-blob-reader`).
- **zkSync posted blobs.** `deploy/stack-config/zksync/general.yaml` sets
  `pubdata_sending_mode: BLOBS`, and the L1 (reth) logged 31–40 blocks with `blobs=1`
  in each of the nine final UC1 runs. Its KZG commitments are computed per batch however
  the data is posted (CPU report of 2026-09-30, §8).
- **Blob posting cannot be switched on in our Arbitrum stack as it is** (tried on the
  cluster 2026-10-26; afterwards the stack was taken down, temporary files removed,
  nothing in the repository changed). The L1, geth v1.16.4, is on the Cancun fork and
  takes blobs only in the old format. With `post-4844-blobs` and `ignore-blob-price` on,
  Nitro v3.9.6 built a blob transaction and geth refused it:
  `error posting batch err="cell proof is not supported yet"`. Nitro v3.9.6 sends blobs
  only in the newer format of Ethereum's Fusaka upgrade (Osaka fork) and has no setting
  for the old one; while blobs were on, no batch was posted at all. The L1 cannot be
  moved to Osaka: with `--override.osaka`, geth stops with
  `Fatal: invalid chain configuration: missing entry for fork "osaka" in blobSchedule`.
  The fork list was fixed when the chain was created on the PC, and geth has no override
  for the fork in between (Prague).
- **Options:**

  | Option | What it takes | Comparable to the measured runs? |
  |---|---|---|
  | A. New Arbitrum chain with Prague and Osaka in its genesis | create the chain on the PC with a changed fork schedule in nitro-testnode's config, take a new snapshot, copy it to `bath0`, test, then run; about half a day to a day, unknown problems possible | same images and settings, but a different chain and L1 fork than the 12 measured Arbitrum runs |
  | B. Older Nitro version that still sends old-format blobs | swap the sequencer image | no: a different Arbitrum version than measured; not recommended |
  | C. No run; state it in the thesis | a paragraph | — |

  **Decided: C.** No new run; the thesis states the asymmetry (Chapter 6: all Arbitrum
  runs posted calldata).
- **Why the payment numbers are probably unaffected:** an Arbitrum payment counts as
  confirmed at the sequencer's receipt; batch posting runs in the background (a batch
  about every 30 s at 5 and 50 per second). `bath0` was at 15.8 / 29.4 / 85.9 % CPU at
  5 / 50 / 500 per second (`cpu_bath0_mean`, `results.csv`). The one plausible effect is
  the KZG work for blobs on `bath0` at 500 per second, where it was already at about
  86 %: UC1's ≈ 370 per second is a lower bound already, so this sharpens that caveat
  rather than changing the comparison. The costs are not affected: the thesis adds the
  data part of the fee that Arbitrum One charged, read from its receipts (thesis §5).
- **`bath1` undervoltage (seen 2026-10-26):** its throttled flags read `0x50000`,
  undervoltage at some point since its last boot; it was clean before. For any future
  run: reboot `bath1` first and check its replacement cable. Existing data is not
  affected.
