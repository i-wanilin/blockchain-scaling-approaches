# Distributed Perun hub benchmark

> **Layout note (2026-10-12):** the sections from *Safety boundary* to *Scope* describe
> the **pre-cluster** `hub` / `client` / `preflight` / `init` launcher (verified on the
> cluster on 2026-09-29, cluster spec §8 step 1), which the thesis measurements do not
> use. The measured modes are in **[UC1](#uc1-direct-channels-no-hub)** and
> **[UC2](#uc2-hub-and-virtual-channels-on-the-cluster)** below; `deploy/cluster`
> starts them.

This launcher runs Ingrid (the hub) and up to six clients (`alice` … `frank`) as
independent processes connected by authenticated TCP. The control API performs discovery and lifecycle barriers;
Perun payment updates travel directly between Alice and Bob.

## Safety boundary

This is a private-LAN Anvil experiment. It uses deterministic public Anvil
development accounts. Never expose the chain to an untrusted network, reuse
these keys with valuable funds, or describe the ephemeral self-signed TLS layer
as production key infrastructure.

`.state/`, `results/`, and `config/cluster.local.json` are generated locally and
ignored by Git. Do not copy `.state/` between nodes.

## Deployment on the Pi cluster

The launcher runs on the Raspberry Pi cluster described in the cluster spec
(`docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md` §2, §7.3) and
`deploy/README.md`. Boards are `bath0`…`bath7` at `192.168.178.21<N>`:

| Role | Board | Endpoint |
|---|---|---|
| Anvil (the L1) | `bath0` (Pi 4) | `ws://192.168.178.210:8545` |
| Ingrid (the hub, the hosting player) | `bath1` (Pi 3B+) | `192.168.178.211:9000`, control `:8080` |
| Clients | `bath2`… (Pi 3B+, Pi 3B) | `192.168.178.21<N>:9000` |

Verified on 2026-09-29 with Anvil on `bath0`, Ingrid on `bath1`, Alice on `bath2`
and Bob on `bath3` (cluster spec §8 step 1). Use `config/cluster.example.json` as
the template and keep an identical completed `cluster.local.json` on all
boards. No source-code changes are made per user. The earlier three-laptop
beta is kept under *Legacy* at the end.

Important configuration fields:

- `startup_timeout_seconds`: how long a client may wait for the other clients;
  default 300 seconds.
- `operation_timeout_seconds`: lifecycle-operation deadline; default 180
  seconds.
- `result_dir`: local JSON/log output directory.
- `keep_hub_running`: `false` makes Ingrid exit automatically after every
  client has settled and saved its result.
- `disable_strict_payment_policy`: leave `false` for the beta. The default
  application policy rejects updates that mint value, increase the actor's
  balance, or decrease a counterparty's balance.

## One-time network setup

Use only a trusted private Wi-Fi network (the cluster's, cluster spec §1).

Ingrid accepts TCP 8545 (Anvil), 8080 (control), and 9000 (Perun). Each client
accepts TCP 9000. Port 9000 is an authenticated protocol endpoint: do not probe
it with `nc` or `Test-NetConnection`, because a raw connection produces an
expected authentication EOF in Ingrid's log. Use the preflight command, which
checks the HTTP manifest and chain instead.

## Build

The Pis get a static arm64 binary, cross-compiled on the workstation:

```bash
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -ldflags "-s -w" \
  -o ../../deploy/dist/arm64/perun-node .
```

Copy it to `bin/perun-node` on each board. For a native build on Linux:
`./scripts/build.sh`.

## Fresh run

Stop Ingrid before Anvil. Then clear generated state on every node:

```bash
./scripts/reset-state.sh
```

Resetting removes `.state/` and preserves both the shared config and previous
`results/`. Use `--clear-results` only when intentionally discarding old
evidence.

## Start order

Processes started over SSH must be fully detached (`setsid … < /dev/null`, output
to a file), or the SSH session waits for them (`deploy/README.md` §9).

### 1. Anvil on `bath0`

Foundry v1.7.1, the official arm64 build:

```bash
anvil --host 0.0.0.0 --chain-id 31337 --block-time 1
```

### 2. Ingrid on `bath1`

The script validates the chain, endpoint, and free listener ports, preserves a
timestamped log, and returns the actual program exit status.

```bash
./scripts/run-hub.sh
```

### 3. The clients

The configurable startup barrier removes the former 90-second rush. Start the
clients while Ingrid is running; any client may wait safely for the others.

```bash
./scripts/run-client.sh bob 0        # on bath3
./scripts/run-client.sh alice 100    # on bath2
```

### Trace workloads

The primary client can replay a recorded JSONL trace instead of the synthetic
burst, using the same traces as the single-process battery. (Pre-cluster tool; the
`bench/trace-ucb-1s-10hz-60s-tank.jsonl` named below is not in this repository; it belonged to the
exploratory spike and was not carried forward. Any trace from `python -m arena.sim` in `tank-arena/`
works the same way.)

```bash
./scripts/run-client.sh bob 0
./bin/perun-node -mode client -node alice -config config/cluster.local.json \
  -trace ../../bench/trace-ucb-1s-10hz-60s-tank.jsonl          # closed-loop
./bin/perun-node -mode client -node alice -config config/cluster.local.json \
  -trace ../../bench/trace-ucb-1s-10hz-60s-tank.jsonl -paced   # open-loop
```

`-paced` dispatches each payment at its trace timestamp and additionally
reports lateness, deadline misses against the 50 ms budget, and peak backlog.
Both modes write a `trace` block into the result JSON; a burst run omits it.

Every client is started with the **same** `-trace` and replays only the payments
it makes itself, so together the cluster replays the whole trace. A trace is
loaded and validated **before** any chain or peer work and is rejected if it
needs more players than the cluster defines. The six-player UC2 trace therefore
needs six clients (`alice` … `frank`, the names that have Anvil keys).

## Asset

`"asset"` in the cluster config picks what the channels hold: `"eth"` (the
default when the field is absent) or `"points"`, our ERC-20 with no value
(cluster spec §7.3). `-asset` on the command line overrides the config field;
every process must run the same asset, and a client refuses to join a hub that
deployed the other one.

- `eth`: `ledger_deposit` and `virtual_stake` are whole ETH (sent as wei);
  defaults 100 and 10.
- `points`: Ingrid deploys the points token (`PointsToken`,
  `contracts/points/`), mints 1 000 000 points to every name with an Anvil key
  in one `mintBatch`, and deploys an ERC-20 asset holder bound to the adjudicator;
  the manifest carries the token address. `ledger_deposit` and `virtual_stake`
  are whole points, sent as they are (1 point = 1 token unit); defaults 1000 and
  150. Fractions are rejected. A burst payment moves 1 point, a trace `fire`
  1 point and a `hit` 5 points.

Opening a ledger channel on points costs each party two transactions, approve
and deposit, both inside the timed ledger open. Each client's result JSON has
`asset`, `ledger_open_gas` and `ledger_settle_gas` (its own transactions only,
split `by_contract`: `token` = approve, `asset holder` = deposit or withdraw,
`adjudicator` = conclude); the log prints the same as `client gas:`, and Ingrid
prints its own share as `hub gas:`. The deployer's setup cost (contract
creations, and in points mode the one `mintBatch`) is printed by Ingrid as
`setup gas:` and is not part of Open.

## Many-to-many operation

**Mesh.** Every pair of clients gets one virtual channel. The client with the
lower index in `clients` opens it, which also makes it index 0 of that channel.
`virtual_stake × (clients − 1)` must fit in `ledger_deposit`; the loader checks.

**Simultaneous proposals.** A go-perun channel allows one update in flight. If
both ends propose at once, neither answers. The two ends therefore have fixed
roles: index 0 waits `update_timeout_seconds` (default 10); index 1 gives way
after `yield_timeout_ms` (default 1000), lets index 0's update through, and
re-proposes. Equal timeouts are unsafe — tried, the channel was dead within five
payments — so the loader refuses a long timeout under 4× the yield.

⚠️ **Set `yield_timeout_ms` well above the worst normal round trip of your
link** (about 10× the measured p99). If an ordinary round trip ever exceeds it,
the yielder abandons an update its peer accepted; the run then fails loudly
with "channel is desynchronised" rather than continuing on bad state.

Each result JSON reports `collisions` (how often this node gave way),
`payment_latency_clean` (burst workload: samples that did not give way) and
`virtual_final` (version and balances of every channel once all paying has
stopped). The two ends of a channel must report identical `virtual_final`
entries; compare them after every run.

**Settle order.** go-perun's hub waits a fixed 10 s for the matching settlement
proposal from the other end of a virtual channel, so the mesh is settled in
round-robin rounds of disjoint pairs, with a hub barrier between rounds.
`virtual_settle_ms` is the sum of the settle calls and excludes barrier waits.

**Start instant.** Once every client holds its full mesh the hub publishes one
start time 1.5 s ahead; all clients begin paying then, by their own clocks. A
paced replay is therefore only as aligned as the nodes' clock sync.

The completed lifecycle is:

1. register every client;
2. open and fund each client–Ingrid ledger;
3. open the full mesh of virtual channels, then wait for the common start;
4. execute and measure payments, then wait until every client has stopped;
5. record `virtual_final`, finalize (by each channel's opener) and defund the
   virtual channels cooperatively, round by round;
6. finalize and settle every ledger channel with Ingrid;
7. write a JSON result on each client;
8. acknowledge saved results and let Ingrid stop cleanly.

Anvil remains running so repeated RPC inspection is possible. Stop it only
after Ingrid exits.

## Result evidence

Each client writes:

- `results/logs/<timestamp>-<node>.log`
- `results/<run-id>-<node>.json`

Both JSON files are also uploaded to Ingrid before it exits, so the hub's
`results/` directory is the central structured evidence bundle. Alice's JSON
contains payment count, total burst time, throughput, mean,
p50/p90/p99/max payment latency, virtual-settle time, ledger-settle time, OS,
architecture, hostname, commit, and the hub-generated run ID.

A successful run requires:

- exit code zero from both clients and Ingrid;
- `completed: true` in both JSON files;
- all requested payments present;
- no `level=error`, timeout, or `sub-channels present` message;
- matching run IDs on Alice and Bob.

## Automated validation

Normal tests:

```bash
go test -count=1 ./...
go vet ./...
```

Full independent-process lifecycle against a running Anvil:

```bash
RUN_DISTRIBUTED_TEST=1 go test -count=1 -run TestDistributedThreeProcess -v
```

Every distributed scenario — three processes, head-on collision, six-process
mesh — also runs on points: `-run 'Points$'` selects those variants.

The integration test requires graceful hub/client exit, result files for both
clients, and no error-level lifecycle logs.

## Scope

Included: real TCP transport, persistent identities/channel state, registered
peer admission, full cooperative channel lifecycle, structured latency output,
preflight checks, and Linux/Windows operation.

Not included: internet NAT traversal, production key custody, multi-hop route
selection, fees, general wallet UX, or production denial-of-service defenses.
Policy-enabled application checks should be benchmarked separately from this
baseline because client-side update inspection—not Ingrid—is in the direct
payment path.

## UC1: direct channels (no hub)

UC1 (pay per data window, cluster spec §7.2) uses the same binary in three other
modes, which never read `cluster.local.json`. There is no hub: each customer opens
one ledger channel straight to the sensor, deposits (the sensor deposits 0), pays
one signed update per window and closes cooperatively at the end. `deploy/cluster`
starts everything (`up perun`, `run uc1-perun5mx7-*`, see `deploy/README.md` §6 and
§7); the final UC1 series (2026-10-11/12) is in cluster spec §8 step 3.

| Mode | Where | What it does |
|---|---|---|
| `-mode uc1-deploy` | workstation, against Anvil on `bath0` | deploys the Adjudicator and the ETH AssetHolder, writes `contracts.json`; the addresses are the same on every fresh Anvil and pinned in `uc1.go` |
| `-mode uc1-sensor` | `bath1` | accepts one channel per admitted customer (`-customers`, `-allow`), accepts payments under the payment policy (`-strict`, on by default), settles as secondary, writes `sensor-result.json` |
| `-mode uc1-customer` | each customer board | the sidecar of `measure.payclient --system perun`; with `-probe-rate`/`-probe-seconds` a Go-only probe |

Ports: Anvil `ws://192.168.178.210:8545` (chain 31337); the sensor's Perun wire
`192.168.178.211:9000` and its control HTTP `:8090` (`/v1/manifest`,
`/v1/register`, `/v1/status`; 8090, not the hub's 8080, so a stray UC2 hub never
answers); each customer's wire on `192.168.178.21<N>:9000`.

**Sidecar protocol:** JSON lines on stdin/stdout, one reply per request in request
order, logs on stderr; requests `init`, `open`, `pay`, `state`, `quiesce`, `close`,
`exit`. The reference is the header of `uc1_customer.go`; the Python side is
`measure/perun.py`. A payment is never retried: an update timeout leaves it
*unresolved* and breaks the channel, and a broken channel is not closed.
Close: the customer finalizes and is primary on chain (`concludeFinal`, then its
withdrawal); the sensor withdraws its earnings as secondary.

Tests against a local Anvil (`anvil --chain-id 31337 --block-time 1`):

```bash
ANVIL_URL=ws://127.0.0.1:8545 go test -count=1 -run 'TestUC1.*' -v -timeout 15m
```

## UC2: hub and virtual channels on the cluster

UC2 (the six-player tank game, cluster spec §7.3) uses the hub. Every player opens
one ledger channel to the hub on `bath1` and, through it, one virtual channel with
each of the five others. A shot pays 1 point to the hub in the ledger channel (the
arena's ammo); a hit pays 5 points to the shooter in the virtual channel with that
player. The asset is an ERC-20 `PointsToken`, not ETH. The amounts come from the
frozen game's bound (`python -m arena.bound` in `tank-arena`): each player deposits
its 10 000 points, each virtual channel holds 1 680 per side, and the hub deposits
8 400 per ledger channel to back the far side of that player's five virtual
channels. `deploy/cluster run uc2-perun` starts everything (`deploy/README.md`).

| Mode | Where | What it does |
|---|---|---|
| `-mode uc2-deploy` | workstation, against Anvil on `bath0` | deploys the Adjudicator, the ERC-20 AssetHolder and `PointsToken`, mints points to the players (one `mintBatch`) and to the hub, writes `contracts.json` |
| `-mode uc2-hub` | `bath1` | `-mode hub` on those contracts, the hub's key from a file, the payment policy on (a player may only lower its own balance) |
| `-mode uc2-player` | each player board | the sidecar of `measure.payclient` for UC2 (`uc2_player.go`), same JSON-lines protocol as UC1 (`init`, `open`, `pay`, `state`, `quiesce`, `close`, `exit`); `pay` takes `"to": "hub"` or `"p<k>"` |

Accounts (public Anvil mnemonic): 110 the hub, 111–116 players 0–5. Ports: the hub's
wire `192.168.178.211:9000` and its control HTTP `:8091`; each player's sidecar wire
`:9001` (player 0 shares `bath1` with the hub); the game mesh `:9100`.

Every channel has its own FIFO, one update in flight. A collision on a virtual channel
(both ends paying at once after a head-on hit) is resolved by the channel index
(`update_policy.go`): index 0 has priority, the other end yields (200 ms) and retries,
at most 10 times. A pay is never retried after an update timeout (10 s): the payment is
*unresolved*, the channel is broken, and a broken channel is not closed. Close: the
payments-done barrier, then the virtual channels are settled, then the ledger channel.

Tests against a local Anvil: `uc2_test.go` (config, policy, deploy) and
`uc2_integration_test.go` (six paced live players, head-on hits, a stopped peer; its
header has the command). The 2026-10-06 series (3 runs, 1070 payments each, all
succeeded, no broken channel, latency p50 ≈ 39 ms for guests and 18 ms for the host)
is in cluster spec §8 step 6.

## Legacy: the three-laptop beta (July 2026)

Before the Pi cluster, the launcher was validated on three laptops over home
Wi-Fi. Kept for the record; the scripts it names (`*.ps1`) still exist.

| Role | Machine | Endpoint |
|---|---|---|
| Ingrid + native Anvil | Windows hub laptop | `192.168.178.22:9000` |
| Alice | ASUS G16, native Windows executable | `192.168.178.35:9000` |
| Bob | HP x360, Linux Mint | `192.168.178.27:9000` |

On Windows, the PowerShell equivalents were `.\scripts\build.ps1`,
`.\scripts\reset-state.ps1 [-ClearResults]`, `.\scripts\run-anvil.ps1` (Anvil on
Ingrid's machine), `.\scripts\run-hub.ps1` and
`.\scripts\run-client.ps1 -Node alice -Payments 379`. Native Windows binaries are preferred on Windows. A native `.exe` launched
from WSL still uses the Windows network stack, but PowerShell and Bash syntax
must not be mixed.
The
repository enforces LF endings for `.sh` files and CRLF for `.ps1` files.

### Beta protocol

Run one unrecorded 10-payment pilot first. Then conduct five fresh 379-payment
runs, resetting Anvil and all node state between trials. Record one
representative run on video, but treat the timestamped raw logs and JSON files
as the primary evidence.

The main beta keeps strict payment policy enabled. Afterward, an optional
same-hardware A/B campaign can set `disable_strict_payment_policy` to `true`
for five more runs. Result JSON records the selected profile, and the summary
script reports the two medians separately.

For every trial also retain 50–100 pings between each pair of laptops, Wi-Fi
SSID/band/signal/link rate, power mode, and any packet loss. Report the median
across trials; do not select only the fastest run.

Capture that metadata before the campaign:

```powershell
.\scripts\capture-environment.ps1 -Peers 192.168.178.27,192.168.178.35
```

```bash
./scripts/capture-environment.sh 192.168.178.22 192.168.178.35
```

After five runs, summarize Ingrid's central JSON directory (the script
`bench/summarize_perun_lan.py` and its output are not in this repository; they belonged to the
exploratory spike and were not carried forward):

```bash
python bench/summarize_perun_lan.py perun/hub-demo/results \
  --output bench/RESULTS-perun-lan-beta.md
```
