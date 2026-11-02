# Raspberry Pi cluster: as-built runbook

This file describes how the thesis cluster was **actually** set up and operated
for the final measurements (UC1 series 2026-10-11/12, UC2 series 2026-10-06).
Everything below was done by hand or with the scripts in this folder. The
installer the cluster spec once planned (§3, ansible) was **never built**; it is
future work. The old ansible spike lives in `archive/ansible-spike/` and was never
run on these boards.

- Design and results: [`docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md`](../docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md)
  (the *cluster spec*)
- Board register (serials, Wi-Fi addresses, power, faults): [`FLEET.md`](FLEET.md)
- Stack configuration as measured on `bath0`: [`stack-config/`](stack-config/)
- Run data: [`../results/pi/`](../results/pi/) (index: `results/pi/README.md`)

## Contents

1. [The cluster](#1-the-cluster)
2. [One-time setup of each board](#2-one-time-setup-of-each-board)
3. [Network and Wi-Fi](#3-network-and-wi-fi)
4. [Time](#4-time)
5. [The stacks on `bath0`](#5-the-stacks-on-bath0)
6. [Driving the cluster: `deploy/cluster`](#6-driving-the-cluster-deploycluster)
7. [Experiment files](#7-experiment-files)
8. [Results layout and analysis](#8-results-layout-and-analysis)
9. [Gotchas](#9-gotchas)
10. [Not done / future work](#10-not-done--future-work)

## 1. The cluster

Eight Raspberry Pis named `bath0`…`bath7` (a name says what the board is, not what
it does), all on Wi-Fi, all on SD cards. The **workstation** (a PC running WSL2
Ubuntu) flashes cards, drives the runs over SSH and collects the results. It is
never in the measured path.

| Board | Model | Role in the final runs |
|---|---|---|
| `bath0` | Pi 4 Model B, 8 GB (two heatsinks) | **System under test**, one stack at a time: Anvil (Perun), the Arbitrum Nitro dev stack, or the zkSync Era stack |
| `bath1` | Pi 3B+, 1 GB | chrony time server for the cluster. UC1: **sensor**. UC2: **player 0**, the host, and the **Perun hub** |
| `bath2` | Pi 3B+, 1 GB | UC1 customer; UC2 player 1 |
| `bath3` | Pi 3B, 1 GB | **Dead since 2026-10-05** (board power fault, [`FLEET.md`](FLEET.md)); idle in every final run (`IDLE_BOARDS="bath3"`) |
| `bath4`, `bath5`, `bath6` | Pi 3B, 1 GB | UC1 customers; UC2 players 3, 4, 5 |
| `bath7` | Pi 3B, 1 GB | Replaces `bath3`. UC1 customer; UC2 player 2 |

So UC1 has the sensor `bath1` and five customers `bath2 bath4 bath5 bath6 bath7`.
UC2 has six players `bath1 bath2 bath7 bath4 bath5 bath6` (p0…p5).

**Known hardware limitations (accepted by the owner, stated in the thesis):** the
Pi 3Bs are undervolted under load on every supply tried and then run at 600 MHz,
about half speed. Their throttle flags are recorded in every run, not a reason to
stop it. `bath0` and `bath1` have clean power. There is no spare Pi 4: the second
one is defective. `bath1` restarted five times between 2026-10-09 and 2026-10-12,
caused by a faulty power cable that was replaced on 2026-10-13 ([`FLEET.md`](FLEET.md)).
One restart, on 2026-10-11 at about 23:33, fell into a run, which was failed and
replaced (cluster spec §8 step 3).

## 2. One-time setup of each board

### 2.1 Flashing (Raspberry Pi Imager, the same settings on every card)

1. **Device:** *Raspberry Pi 4* for `bath0`, *Raspberry Pi 3* for the rest.
2. **OS:** *Other general-purpose OS* → *Ubuntu* → **Ubuntu Server 24.04.5 LTS
   (64-bit)**. The supervisor uses Ubuntu 24.04. Not 26.04 (too new for Docker's
   official support), nothing 32-bit (our software is 64-bit only), not Ubuntu Core.
3. **Customisation:**
   - **Hostname** `bath<N>`, one per card, matching the sticker on the card and
     board;
   - time zone `Europe/Berlin`;
   - **user** `i_wanilin`, the same on all boards;
   - **Wi-Fi:** the router's SSID and password (one network name on both bands),
     **Wi-Fi country `DE`**. A wrong or missing country can leave the radio off;
   - **SSH:** enabled, **public-key only**, with the **WSL** key of the
     workstation (`cat ~/.ssh/id_ed25519.pub` inside WSL). The Imager runs on
     Windows and offers the Windows key (`workstation`), which differs. Adding both is
     fine;
   - Raspberry Pi Connect off, telemetry off.

Ubuntu applies these settings during the first boot (cloud-init): up to about 7
minutes on the Pi 4, several on a Pi 3. Do not power the board off early.
Afterwards `ssh i_wanilin@<address>` must work from WSL without a password.

Idle memory on the Pi 3s under Ubuntu: 500–640 MB of 899 MB free right after first
boot. Ubuntu stayed.

### 2.2 Per-board scripts, run once by hand with sudo

Copy each script to the board and run it there, for example
`scp deploy/<script> bath<N>:~/ && ssh -t bath<N> 'sudo bash ~/<script> <args>'`.

| Script | Boards | What it does |
|---|---|---|
| [`set-static-ip.sh`](set-static-ip.sh) `192.168.178.21<N>` | all | Fixed address outside the router's DHCP range (§3). Refuses an address in use, keeps a backup, reverts by itself if the router is unreachable, logs to `journalctl -t set-static-ip`. |
| [`pin-24ghz.sh`](pin-24ghz.sh) `02:00:00:00:00:01` | `bath0`; on `bath1` and `bath2` also, before `pin-5ghz.sh` | Pins the router's 2.4 GHz radio: netplan `band` + `bssid`, and the driver option `roamoff=1` (`/etc/modprobe.d/brcmfmac-roamoff.conf`). Logs to `journalctl -t pin-24ghz`. |
| [`pin-5ghz.sh`](pin-5ghz.sh) `02:00:00:00:00:02` | `bath1`, `bath2` (since 2026-10-05) | Pins the router's 5 GHz radio, the mirror of `pin-24ghz.sh`. It relies on the `roamoff=1` that `pin-24ghz.sh` put in place, and reverts by itself if the board is not reachable on 5 GHz after 90 s. Logs to `journalctl -t pin-5ghz`. Undo with `pin-24ghz.sh`. |
| [`wifi-powersave-off.sh`](wifi-powersave-off.sh) | all | Installs `iw`, switches Wi-Fi power save off, and adds a service that switches it off again whenever `wlan0` appears. Before: a ping between two Pi 3s took up to 1.1 s; after: 20–36 ms. |
| [`wifi-pin-at-boot.sh`](wifi-pin-at-boot.sh) `[BSSID]` | installed on `bath0`, `bath1`, `bath2` (2026-09-30) | A boot-time service that rejoins the pinned radio if the Broadcom chip's first join went elsewhere. **Its default BSSID is the 2.4 GHz radio**; see §3 for what that means on `bath1`/`bath2`. |

### 2.3 Operating-system settings, set by hand on every board

- **Automatic updates off** (`unattended-upgrades`, `apt-daily`, `apt-daily-upgrade`)
  and **snap refreshes held**. An update cut off by a power cycle once left `bath1`'s
  package system half-installed (fixed with `sudo apt-get -f install`).
- **One kernel on all boards**, `6.8.0-1065-raspi`, held with `apt-mark hold`.
  Every manifest records the kernel.
- **`sudo` without a password** for `i_wanilin`
  (`/etc/sudoers.d/90-i_wanilin-nopasswd`, set by the owner), so that
  `deploy/cluster` can run setup steps without a prompt per board.
- `i_wanilin` is in the `video` group (`vcgencmd get_throttled` without sudo). On
  `bath0` it is also in the `docker` group.
- chrony as in §4.

Nothing starts at boot: an idle board runs no stack. `deploy/cluster sync`
(§6) creates the Python venv on each board (`~/venv`, installing `python3-venv` if
needed) and copies the code. Nothing else is installed on the Pi 3s.

## 3. Network and Wi-Fi

**Router:** FRITZ!Box 6660 Cable, `192.168.178.1`, **one SSID on both bands**.
Its DHCP range is `.20`–`.200`. The boards' fixed addresses lie outside it:
**`bath<N>` = `192.168.178.21<N>`** (`bath0` = `.210` … `bath7` = `.217`), set on
each board (§2.2), not on the router. The workstation's `~/.ssh/config` has one
`Host bath<N>` entry per board. WSL cannot resolve `.local` names, and the router's
`bath<N>.fritz.box` names lag behind card swaps, so neither is used.

**Final setup: mixed Wi-Fi** (owner, 2026-10-05/06; cluster spec §1, §9 U4):

| Radio | BSSID | Boards |
|---|---|---|
| 5 GHz | `02:00:00:00:00:02` | `bath1`, `bath2` (Pi 3B+), pinned with `pin-5ghz.sh` |
| 2.4 GHz, channel 11 (2462 MHz) | `02:00:00:00:00:01` | `bath0` (pinned with `pin-24ghz.sh`; its 5 GHz link measured only 13 Mbit/s) and the Pi 3Bs `bath4`…`bath7` (2.4 GHz only) |

Why: on one shared 2.4 GHz channel, the UC1 sensor's stream plus the payment
traffic drove `bath1`'s flows into a collapse. Moving the sensor (and `bath2`) to
5 GHz took their flows off the shared channel. Pairs between the two groups cross
the router's band bridge; this is accepted and recorded. Every experiment file
names its 5 GHz boards (`WIFI_5GHZ_BOARDS="bath1 bath2"`), and pre-flight checks
every board's BSSID before each run. Earlier series with all boards on 2.4 GHz
are archived (§7).

**Why pinning needs three measures.** The router practises *band steering*: a band
restriction alone was undone within 0.36 s on `bath0`. A netplan `bssid` is still
not enough on the Broadcom chip of the Pi 3B+/Pi 4, which roams inside its own
firmware. The driver option `roamoff=1` hands roaming back to Ubuntu's Wi-Fi
client. With band + BSSID + `roamoff=1` a pin holds while the board runs. At boot,
however, the chip may make its first join before the pin applies (seen on
2026-09-30). **If the router is replaced, every pinned board needs the new BSSID**,
or it will not connect.

**`wifi-pin-at-boot` on `bath1`/`bath2`.** The service was installed on 2026-09-30,
before the switch to 5 GHz, with the default (2.4 GHz) BSSID. On a board now pinned
to 5 GHz it finds the "wrong" BSSID after every boot. It then reloads the driver
and reapplies netplan up to three times; netplan brings the board back to 5 GHz
each time. Finally it logs a `WARNING … still not on 02:00:00:00:00:01` and exits
1. So the board ends on its correct 5 GHz radio, after about three driver reloads
(roughly 1–2 minutes of Wi-Fi flapping after boot). It is useless there and was
disabled on both on 2026-10-13 (`sudo systemctl disable wifi-pin-at-boot`; the other
way would be to reinstall it with the 5 GHz BSSID, `sudo bash wifi-pin-at-boot.sh
02:00:00:00:00:02`). Nothing links it to `bath1`'s reboots, which a faulty power
cable caused (`FLEET.md`). Pre-flight's BSSID check remains the guarantee
for every run.

**Wi-Fi baseline.** [`wifi-baseline.sh`](wifi-baseline.sh) (added 2026-10-12)
measures the bare network with no chain running: per board the link (BSSID,
frequency, signal, bit rates), the ping RTT distribution and TCP throughput to
`bath0` and `bath1`. Output goes to `results/pi/baseline/<date>-wifi-baseline/`.

## 4. Time

One board, **`bath1` (`192.168.178.211`), is the only time source** (chrony; set
up by hand on 2026-09-30). It is not the stack board, so a busy stack cannot disturb
the clock.

- `bath1`: `/etc/chrony/conf.d/cluster-server.conf` with `allow 192.168.178.0/24`
  and `local stratum 8` (it serves time even without internet). It keeps Ubuntu's
  internet pools as its own upstream.
- All others: the pool lines in `/etc/chrony/chrony.conf` commented out, and
  `/etc/chrony/sources.d/cluster.sources` naming `bath1` only.
  `systemd-timesyncd` is replaced by chrony. Check with `chronyc sources`: the only
  source, marked `^*`, is `192.168.178.211`.

**How the measurements depend on it.** No latency is computed across boards: every
payment timestamp (UC1 and UC2) is taken on the payer's own board, and Open/Close
are single-board durations (`measure/analyze_uc1.py`, `measure/analyze_uc2.py`).
Clock sync matters for lining up the recorder series and logs of different boards.
Each run has a **clock gate**: every board's offset to `bath1` must be below 5 ms on
two consecutive polls, waiting at most 600 s. Offsets at start and end are
recorded (`chrony-start/`, `chrony-end/`).

## 5. The stacks on `bath0`

All three stacks run on `bath0`, one at a time, each with its own L1, so the
comparison is same-hardware. The rollup chains were **initialised once on the x86
workstation** and copied to `bath0` as snapshots (cluster spec §4; x86 chain state
starts unchanged on arm64). Every run restores the snapshot, so it starts from a
byte-identical chain. Installed by hand on `bath0` (2026-09-29/30):

- **Base:** Docker 29.1.3 with Compose, `liburing2` (for `zksync_server`), `perf`.
- **Perun:** Anvil 1.7.1 (Foundry's official arm64 release) at
  `/home/i_wanilin/bin/anvil`. No snapshot: every run starts a fresh Anvil
  (`--chain-id 31337 --block-time 1`, port 8545), and `up perun` deploys the
  Adjudicator and the ETH AssetHolder from the workstation. The addresses are fixed
  by the deployer's nonces and checked. The Perun nodes on the Pi 3s are the
  `perun-node` binary (`perun/hub-demo`), cross-compiled and shipped by `sync`.
- **Arbitrum Nitro:** `~/arbitrum/docker-compose.yaml` with three services: L1
  `ethereum/client-go:v1.16.4`, `redis:6.2.6`, and the sequencer
  `offchainlabs/nitro-node:v3.9.6-91bf578` (official arm64 images). Five volume
  tarballs (`config`, `seqdata`, `l1data`, `l1keystore`, `tokenbridge-data`
  `.tgz`), checked against `~/arbitrum/SHA256SUMS`. Endpoints: L1 `:8545`, L2 HTTP
  `:8547`, L2 WS `:8548`.
- **zkSync Era:** the ecosystem under `~/zksync-ecosystem/` (`thesis_local/`
  with `docker-compose.yml` for reth `v1.8.2` (L1, `:8945`) and PostgreSQL 18;
  the chain in `thesis_local/chains/thesis_chain/` with `configs/general.yaml` and
  the RocksDB; `zksync-era/`, the server's working directory with the compiled
  contracts it reads at run time). The arm64 `zksync_server` is our own build at
  `~/zksync/bin/zksync_server` (SHA-256 `e77b1cc8…`, `zksync-setup/ARM64.md`),
  run natively. Snapshot in `~/snap` (`chain.tgz`, `reth-data.tgz`,
  `postgres-vol.tgz`, `SHA256SUMS`). One setting differs from the local-dev
  default: `block_commit_deadline_ms` = `ZK_COMMIT_DEADLINE_MS` (20 000 in every
  final run), written by `up` and recorded. Endpoints: L2 HTTP `:3050`, WS `:3051`.

The compose files, `general.yaml`, the Arbitrum sequencer config and both
`SHA256SUMS` are copied in [`stack-config/`](stack-config/). Each run records their
SHA-256 in `stack-config.sha256`. The zkSync ecosystem's original initialisation
(`zkstack`) is in [`../zksync-setup/`](../zksync-setup/). The snapshot tarballs
themselves (several GB) are not in the repository.

Memory on `bath0`: Arbitrum at most 1.65 GB under a 20-minute sustained run; zkSync
at most 2.8 GB. zkSync uses all four cores (98–100 % CPU in the measurements).
Neither stack fits a 1 GB Pi 3, which is why only `bath0` hosts stacks.

## 6. Driving the cluster: `deploy/cluster`

[`deploy/cluster`](cluster) is one bash script that runs on the workstation (WSL)
and reaches the boards as `ssh bath<N>`. **Workstation prerequisites:** the repo
venv `.venv` (Python 3.12, `measure/requirements.txt`; override with `LPY=`), Go
(builds `perun-node`), Foundry `cast` (`~/.foundry/bin/cast`, `CAST=`), Node.js with
`npm ci` in `deploy/zk-token/` (UC2 on zkSync), and the GESL recording at
`data/gesl-samples/sigId-6063.zip` (from gesl.ornl.gov, not redistributed). Details
are in the root [`README.md`](../README.md).

`IDLE_BOARDS` takes boards out of a command (space-separated; `bath0` and `bath1`
can never be idle). For `sync`, `status`, `preflight`, `down` and `idle-temps` it
comes from the environment: the final runs used `IDLE_BOARDS=bath3`, which is also
the current default. For `run` it comes from the experiment file only.

| Command | Does |
|---|---|
| `sync` | Copies `measure/`, the recorder and board-facts scripts and the game (`tank-arena/arena`, `tank-arena/net`) to `~/measure-code` on every active Pi 3, creates `~/venv` there and installs `measure/requirements.txt`. Copies the GESL CSV to `bath1:~/gesl` only and checks its SHA-256. Cross-compiles `perun-node` from the **committed** `perun/hub-demo` tree (refuses a dirty `perun/`; static, `CGO_ENABLED=0 -trimpath -buildvcs=false -ldflags "-s -w"`): arm64 to every board, hash-checked; amd64 kept in `deploy/dist/amd64/` for `up perun`. Records hash, Go toolchain, tree and commit in `deploy/dist/arm64/perun-node.build`. Prints the code and game hashes that every run checks. |
| `up arbitrum` \| `up zksync` \| `up perun` | Restores the snapshot (rollups) or starts a fresh Anvil and deploys the contracts (Perun), starts the stack on `bath0` and waits until L1, L2 HTTP and L2 WS answer (zkSync takes about 4 min). Refuses while another stack runs. No cooling gate (only `run` has one). |
| `down` | Stops every stack on `bath0` (zkSync server first, then its containers; the Arbitrum sequencer with 60 s grace; Anvil) and removes any `netem` delay on every board. |
| `status` | One line per board: kernel, BSSID and frequency, power save, throttle flags, chrony offset and source, temperature, free disk, `netem`, Docker state. |
| `preflight` | The pre-flight checks of a run, without a run. |
| `idle-temps` | Each board's idle temperature with the stacks down, written to `results/pi/idle-temps.txt`, the reference for the cooling gate. The file in use dates from 2026-10-01. |
| `run <experiment> [--reps N]` | Runs `deploy/experiments/<experiment>.env` N times; see below. |

**What one repetition does** (in this order; every wait has a deadline):

1. Kill leftovers on every board (processes found by their full deployed path and
   mode, never by substring). Check that every board runs the workstation's
   `measure/` code (and, for UC2, the game hash and the reference-log hash; for
   Perun, the `perun-node` hash and an unchanged `perun/`). Record the run-start
   commit. For UC1, check the GESL CSV on `bath1` against `measure.gesl`.
2. `down`, then the **cooling gate**: every board within 3 °C of its idle
   temperature (`COOL_MAX_S`, 1 200 s default, 2 700 s for zkSync).
3. `up <stack>` (**snapshot restore**), and hash the stack configuration.
4. **Clock gate** (§4), then **pre-flight**: all boards reachable; each card in
   its own board (Wi-Fi address and serial as in `FLEET.md`); each board on its
   expected radio (`WIFI_5GHZ_BOARDS`); one kernel; power save off; `bath0` and
   `bath1` without throttle flags; no `netem`; enough disk; only the expected
   stack up.
5. Funding on the L1 (untimed) and the deposit budget check. For UC2: the token
   deployment and mint (rollups: `measure.uc2_setup`; zkSync via
   `deploy/zk-token/deploy.js`), or the Perun contracts, cluster config and hub key.
6. Start the recorders and the payment clients (`measure.payclient`), which do the
   timed **Open** (rollups: bridge ETH; Perun: open channels). UC1: start the
   sensor (Perun: `perun-node -mode uc1-sensor` on `bath1`) and the customers, then
   stream for `DURATION_S`. UC2: start the Perun hub on `bath1` if needed, then
   `arena.node` on the six players at once, and wait for the match.
7. Drain (`DRAIN_S`), then the timed **Close** (Perun: settle on the L1; rollups:
   withdrawal submission).
8. Collect everything into `results/pi/<uc1|uc2>/<run id>/` (copies checked file
   by file; a short copy is listed in `COLLECT_INCOMPLETE`). For UC2: the points
   balances and the hash check of every board's game trace against the reference
   (`HASH_MISMATCH` fails the run). Then write `manifest.json`, gzip the big files,
   and `down`.

A failed repetition writes `RUN_FAILED` (containing the reason), still
collects what exists, brings the cluster down, and stops the series. The run ID is
`<date>-<time>-<stack>-<experiment>-rep<k>`.

**Other scripts in `deploy/`**, not part of a run:

| Script | Where | What it does |
|---|---|---|
| [`reth-idle-cpu.sh`](reth-idle-cpu.sh) | `bath0`, with no stack up | Measures the CPU of zkSync's L1 (reth `--dev`) alone, without the zkSync server: the idle board, then reth at 300 ms and at 1 s block time, 300 s each. Output of the run of 2026-10-29: `results/pi/reth-idle-cpu-2026-10-29.txt`. |
| [`uc2-local.sh`](uc2-local.sh) `perun\|arbitrum OUTDIR` | workstation | One whole UC2 match on the workstation: six loopback players, with the run-folder layout of `deploy/cluster`, so `measure.analyze_uc2` reads the result. The chain must already be up. A development check, not a measurement. |

## 7. Experiment files

An experiment file is a sourced bash file in `deploy/experiments/`. A run folder
keeps its own copy (`experiment.env`). **The final files (17):**

| Files | What |
|---|---|
| `uc1-perun5mx7-w{1000,100,10}.env` | UC1 on Perun: direct channels sensor ↔ customer, `UPDATE_TIMEOUT_S=10`, `CHALLENGE_S=10`, `PERSIST=1`, `STRICT_POLICY=1` |
| `uc1-arbitrum5mx7-w{1000,100,10}.env` | UC1 on Arbitrum, `RECEIPT_MODE=own` at every window (each client fetches only its own receipts) |
| `uc1-zksync5mx7-w{1000,100,10}.env` | UC1 on zkSync, `MAX_IN_FLIGHT=20`, `RECEIPT_TIMEOUT_S=1200`, `CLOSE_TIMEOUT_S=1800`, `ZK_COMMIT_DEADLINE_MS=20000`, `COOL_MAX_S=2700` |
| `uc1-{perun,arbitrum}5mx7-smoke.env` | 3-minute `w10` smokes before the final UC1 series |
| `uc2-{perun,arbitrum,zksync}.env` | UC2 series (3 repetitions each) |
| `uc2-{perun,arbitrum,zksync}-smoke.env` | One smoke match each (the same match; not a measurement) |

`5mx7` = 5 customers, mixed Wi-Fi, `bath7` in place of `bath3`. All UC1 files share
`CUSTOMERS=(bath2 bath4 bath5 bath6 bath7)`, `EXPECTED_CUSTOMERS=5`,
`IDLE_BOARDS="bath3"`, `WIFI_5GHZ_BOARDS="bath1 bath2"`, `DURATION_S=900`
(15 min), `PRICE_WEI=10^12`, `SENSOR_QUEUE_MAX=3600`, `SENSOR_WRITE_TIMEOUT_S=30`,
`MAX_QUEUE=10000` and `DRAIN_S=60`. `WINDOW_MS` is 1000, 100 or 10, which gives 5,
50 or 500 payments per second offered in total. `MAX_IN_FLIGHT` is 64 on Perun and
Arbitrum and 20 on zkSync.

UC2 files: `USE_CASE=uc2`, `PLAYERS=(bath1 bath2 bath7 bath4 bath5 bath6)` (p0
must be `bath1`), `N_PLAYERS=6`, `TICKRATE=20`, `GAME_TICKS=1200` (60 s),
`GAME_SEED=42`, `START_POINTS=10000`, the same `IDLE_BOARDS` and
`WIFI_5GHZ_BOARDS`. Perun: `YIELD_TIMEOUT_MS=200` (**fixed by the owner**, not
derived from a network baseline), `MAX_COLLISION_RETRIES=10`, `READY_WAIT_S=900`,
`COMPLETE_EXTRA_S=960`. Arbitrum: `RECEIPT_MODE=own`. zkSync: the same timeouts
as its UC1 files. A UC2 run refuses to start without
`tank-arena/reference/uc2-seed42.jsonl.sha256`.

**Superseded files** (the 6-customer and 5-customer series with `bath3`, all-2.4
GHz series, Wi-Fi diagnostics, a zkSync capacity probe) are in
`deploy/experiments/archive/`, each marked `# ARCHIVED` on its first line. Their
run folders stay in `results/pi/uc1/` and keep their own `experiment.env`. See
[`../archive/README.md`](../archive/README.md).

## 8. Results layout and analysis

One folder per repetition, `results/pi/<uc1|uc2>/<run id>/`:

```
manifest.json          commit, code/game/binary hashes, per board: model, serial, Wi-Fi
                       address, kernel, BSSID/frequency; experiment parameters; for UC2 the
                       game parameters and trace hashes
experiment.env         the experiment file as run
run-start-commit.txt, code-hash.txt, stack-config.sha256, gesl.json (UC1),
perun-node-build.txt, perun-node-sha256.txt (Perun)
clock-gate.txt, chrony-start/, chrony-end/, start-temps.txt, facts-start/, facts-end/
results/               raw output per board (payment ledgers, sensor/hub/game data)
logs/                  process and container logs
recorder/              CPU, memory, temperature, throttle flags over time, per board
summary.json           written by the analysis
RUN_FAILED, COLLECT_INCOMPLETE, HASH_MISMATCH   only when that happened
```

The board facts record signal and link rate only since 2026-10-11, so the UC2
series has none. Big files are gzip-compressed; the readers accept `X` or `X.gz`.

**Analysis** (repository root, the repo venv):

```sh
.venv/bin/python -m measure.analyze_uc1 RUN_DIR     # -> RUN_DIR/summary.json
.venv/bin/python -m measure.analyze_uc2 RUN_DIR     # -> RUN_DIR/summary.json
.venv/bin/python -m measure.aggregate RUN_DIR...    # mean ± sd, min/max, per-run values
```

`aggregate` refuses runs from different commits, code, configuration or analysis
commits, and incomplete runs. `--allow-dirty` and `--include-incomplete` relax
this; such runs are listed in the output. The thesis aggregates are
`results/pi/uc1/aggregate-*5mx7-*.txt` and `results/pi/uc2/aggregate-uc2-*.txt`.
The first line of each names its runs. Which run folders belong to which series:
`results/pi/README.md`.

## 9. Gotchas

Each of these cost time once.

- **Under-voltage looks like other faults.** Boards on weak supplies answered ping
  but hung on SSH, or dropped off the network. Read
  `cat /sys/devices/platform/soc/soc:firmware/get_throttled`: `0x0` is clean,
  `0x50005` means under-voltage and throttling now and earlier. Power banks run dry
  (`bath5`, 2026-09-30).
- **An SSH port that accepts and immediately hangs up**
  (`kex_exchange_identification: Connection reset by peer`) persisted on three Pi 3s
  after a card swap; one clean power cycle fixed it.
- **Cards move, boards stay.** Hostname, SSH identity and configuration live on the
  card. Identify boards by their Wi-Fi hardware address ([`FLEET.md`](FLEET.md));
  pre-flight checks it.
- **Kernels drift** under automatic updates (two versions within hours): keep
  updates off and the kernel held.
- **`iw` is not installed** on Ubuntu Server; `wifi-powersave-off.sh` installs it.
- **Unloading the Wi-Fi driver** needs `modprobe -r brcmfmac_wcc brcmfmac`;
  `brcmfmac` alone is refused as "in use".
- **Programs started over SSH must be fully detached** (`setsid … < /dev/null`,
  output to a file), or the SSH session waits for them.
- **A Pi 4 with no green LED**, even with the bootloader recovery card, while the
  same cards boot in another Pi 4, is a board fault (the second Pi 4).
- **A Pi 3B with a dark red power LED on a known-good supply** is a board fault too
  (`bath3`, 2026-10-05); reflashing the card does not help.

## 10. Not done / future work

- **The installer** (cluster spec §3, ansible roles) was never built; the boards
  were prepared by hand as above. The old spike tree is in `archive/ansible-spike/`.
- **Capacity probes** (§7.1: Arbitrum's and Perun's total ceilings), the
  `perun-on-arbitrum` variant (§7.4) and the `uc2-*-distant` runs with `netem` delay
  were not measured (owner, 2026-10-11).
- **Comparison with a one-host Docker emulation** of the network: the spike's
  emulation layer was not carried forward to the cluster work (it belonged to the
  exploratory spike). A netem-shaped
  loopback bridge is a poor model of a contended radio. Future work, not an open
  decision.
