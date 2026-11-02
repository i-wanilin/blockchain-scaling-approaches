# The early demo without a network (`local` mode)

Not used by the thesis measurements. Before the Raspberry Pi cluster existed, this mode
tested the hub on one computer: one program holds the hub (Ingrid) and all clients, and
their messages pass inside the program's memory (go-perun's `wire.LocalBus`), not over
a network. The program of the measurements is described in [`README.md`](README.md).

## What it does

```
        ledger channels (on-chain funding)        virtual channels (off-chain, via hub)
  Alice ──────┐                                   Alice ─ ─ ─ Bob
  Bob   ──────┼──► Ingrid (hub)        plus        Alice ─ ─ ─ Carol
  Carol ──────┘                                    Bob   ─ ─ ─ Carol
```

1. Deploys the Perun adjudicator + ETH asset holder.
2. Sets up one **hub** (Ingrid) and N **clients** inside the one program.
3. Each client opens **one ledger channel** to the hub (on-chain deposit).
4. Opens a **full mesh of virtual channels** between every client pair, routed
   through the hub — entirely **off-chain**.
5. Sends a burst of off-chain payments (throughput measurement).
6. Settles all virtual channels (off-chain) and all ledger channels (on-chain).
7. Prints per-phase **on-chain gas** and **off-chain payments/sec + latency**.

The hub (`Ingrid`) is just a normal go-perun client with an accept-policy — see
`hub.go`. The virtual-channel *protocol* (funding/defunding through the parents,
watching, settlement) is provided by go-perun; only the node wiring + accept-policy
is application code. This is single-hop (one intermediary); go-perun does not do
multi-hop routing.

## Requirements

- Foundry / **Anvil** (the local EVM).
- Go ≥ 1.23 (built here with 1.26).

## Running

Anvil **must use interval mining** 

```sh
# Terminal 1: start Anvil with a block time 
anvil --chain-id 31337 --block-time 1

# Terminal 2: run the demo
go run .                      # 3 clients, 100 payments (defaults)
go run . -clients 4 -payments 200
go run . -url ws://127.0.0.1:8545 -chainid 31337
go run . -asset points        # the same run on the points token
```

Flags: `-url`, `-chainid`, `-clients` (2..6), `-payments`, `-trace`, `-paced`,
`-asset`.

## Asset: ETH or points (`-asset`)

`-asset eth` (the default) is the original setup: channels hold native ETH in the
ETH asset holder. `-asset points` runs the same scenario on UC2's game currency,
our own ERC-20 with no value (cluster spec §7.3): the run deploys `PointsToken`
(`contracts/points/`, the same contract the rollups run; Go binding in
`pointstoken/`), mints 1 000 000 points to every account with an Anvil key (hub
and all clients) in one `mintBatch` transaction, and binds an ERC-20 asset holder
to the adjudicator (`asset.go`).

| | `eth` | `points` |
|---|---|---|
| amounts (`ledger_deposit`, `virtual_stake`) | whole ETH, sent as wei (× 10^18) | whole points, sent as they are (1 point = 1 token unit) |
| default ledger deposit / virtual stake | 100 / 10 ETH | 1000 / 150 points |
| burst payment, cross-payment | 0.001 ETH, 1 ETH | 1 point, 1 point |
| trace amount (fire 1, hit 5) | × 0.001 ETH | points, unscaled |
| ledger open, per depositing party | 1 tx: deposit | 2 txs: approve (token), deposit (asset holder) |

The approve and the deposit both happen inside go-perun's funding, so they are
part of the timed and gas-metered ledger open. The results table splits the two
on-chain phases by contract: `approve (token)` and `deposit (asset holder)` under
the open, `conclude (adjudicator)` and `withdraw (asset holder)` under the settle.
Measured with 3 clients (6 depositing parties): ETH open 274 878 gas / 6 txs;
points open 667 920 gas / 12 txs (approve 46 113 each, deposit 65 207 on average).
Setup is reported apart under `deploy contracts`: in points mode PointsToken
469 169 gas and its `mintBatch` to 7 accounts 219 503 gas (in distributed mode
Ingrid prints the same as `setup gas:`).

## Latency per lifecycle phase (timers)

Every run (default and `-trace`) additionally reports the per-channel wall-clock
latency of the four lifecycle phases of a channel, as mean/min/max across the
channels. The binary prints this block as "Sub-process latency", the project's earlier
name for it; the printed text stays, since changing it would change the measured
binary. The four phases:

- **ledger open** — channel proposal until funded/ready (on-chain: bound by the
  deposit transactions and Anvil's block time, so seconds-scale);
- **ledger close** — finalize-start until funds withdrawn (on-chain, seconds-scale);
- **virtual open** — proposal until both endpoints hold a ready channel
  (off-chain, milliseconds-scale);
- **virtual settle** — finalize-start until defunded back into the ledger
  channels (off-chain, milliseconds-scale).

## Trace replay (`-trace`)

Pre-cluster tool, not used by the thesis measurements: UC2 on the cluster pays
live from the running game (`DISTRIBUTED.md`, *UC2*), not from a replayed trace.
A tank-game trace's payment events can drive the payment phase here:

```sh
# generate a trace first: cd ../../tank-arena && python -m arena.sim --players 6 --out ../perun/hub-demo/trace.jsonl
go run . -clients 6 -trace trace.jsonl
```

(The `bench/trace-*.jsonl` files earlier examples named are not in this repository;
they belonged to the exploratory spike and were not carried forward.)

`-trace` replaces the synthetic burst of phase 3 with a replay of the JSONL
trace's payment events, in trace order. Mapping (player *i* → client *i*; the
player count is derived from the trace as max id + 1 and must equal `-clients`):

- **fire** `{from: i, to: "arena", amount: 1}` — player *i* pays the **arena**,
  which is mapped to the **hub** (Ingrid): a direct update on client *i*'s
  **ledger channel**, whose counterparty is the hub. No special machinery: the
  hub is an ordinary go-perun client and already accepts payment updates.
- **hit** `{from: victim, to: shooter, amount: 5}` — the victim pays the shooter
  over their pairwise **virtual channel** (the mesh channel between the two).
- Amounts are game units scaled by `traceUnit` (0.001 ETH/unit), preserving the
  1:5 fire/hit ratio while never exhausting a channel's stake.

Replay is **closed-loop** — one payment in flight, the next starts only after
the previous channel update completed — mirroring the receipt-waited EVM replay
methodology. The results block reports transfers, ok/fail, wall time, rate, and
per-payment latency (mean/median/p10/p90/max), split by fire vs. hit. Other
event types in the trace (`move`) carry no payment and are ignored.

## Paced replay (`-paced`, only with `-trace`)

Pre-cluster tool, not used by the thesis measurements. The thesis's own runs are paced
too, but differently: UC1 runs one paced experiment per window (spec §7.2) and UC2 pays
live from the running game. Without `-paced`, the trace replay above waits for each
payment before sending the next, the one-payment-at-a-time method the thesis rejects
(cluster spec §7.3, "Pipelined submission").

```sh
go run . -clients 6 -trace trace.jsonl -paced
```

Answers the real-time question — *can Perun keep up with a 20 Hz game?* —
by replaying **open-loop**: each payment is dispatched at its recorded trace
timestamp `t_ms` (relative to replay start), so payments overlap if the previous
one hasn't completed. Concurrency respects go-perun's rule that updates on one
channel must be sequential: one FIFO queue + worker goroutine per underlying
channel (keyed by channel ID, so both directions of a virtual channel share a
queue); different channels update in parallel.

Reported per payment: **lateness** = completion − scheduled time. The block
prints mean/median/p90/max lateness, the **deadline-miss rate** against the
50 ms tick budget (completion later than scheduled + 50 ms), the **max backlog**
(peak dispatched-but-uncompleted payments), and whether the whole replay
finished within the trace duration + 10%.

## Sample results (in-process LocalBus, workstation)

These are `local`-mode numbers from the workstation: all nodes in one process on
`wire.LocalBus`, against a local Anvil. They are **not cluster results**; the
off-chain rate has no network in it. The thesis results (UC1, UC2 on the Pi
cluster) are in the cluster spec §8 and under `results/pi/`.

3 clients (3 ledger channels, 3 virtual channels):

```
phase (on-chain)                            gas      txs
deploy contracts                        3918416        2
open 3 ledger channels                   274866        6
open 3 virtual channels                       0        0  <- headline
settle virtual channels                       0        0
settle ledger channels                   718384        9
gas / ledger-channel open                 91622
Off-chain: 100 payments in 50.7ms => 1972 payments/s, 0.507 ms/payment
```

4 clients (4 ledger channels, **6** virtual channels):

```
open 4 ledger channels                   366492        8
open 6 virtual channels                       0        0  <- headline
gas / ledger-channel open                 91623
Off-chain: 200 payments in 102.3ms => 1955 payments/s, 0.512 ms/payment
```

### The scaling result, in one sentence
On-chain gas grows **O(N)** with the number of participants (one ~91.6k-gas ledger
channel each), while the **O(N²)** pairwise virtual channels — and their settlement —
cost **zero gas**. Off-chain payment throughput (~2000 tx/s here, in-process) is independent of N.

## Friction encountered (kept as methodology notes)

1. **Anvil instamine deadlocks go-perun.** With Anvil's default mining (one block
   per transaction), go-perun's funder/settler waits for `txFinalityDepth` (1)
   *additional* confirmation block that never arrives, because no further tx is
   submitted. The funding/settlement phase hangs. **Fix: run Anvil with
   `--block-time 1`** (interval mining) so confirmations always advance. (The
   upstream perun example sidesteps this by using a Hardhat node that mines every
   5 s.)
2. **Hub must accept ledger updates.** The intermediary's update handler can be a
   no-op *only* if the intermediary itself initiates the channel finalization
   (as the go-perun unit test does). Here clients finalize their own ledger
   channels before settlement, so the hub's update handler must `Accept` them,
   otherwise the finalize hangs. Virtual-channel funding/defunding is handled
   internally by go-perun and never reaches the user update handler.

## Demo simplifications (vs. a real deployment)

- **In-process bus + direct references.** All nodes share a `wire.LocalBus`, and the
  payer reads the payee's ledger-channel ID directly. In a real network the hub
  would advertise that ID, and nodes would talk over libp2p/TCP. The on-chain gas
  numbers are unaffected; only the off-chain transport differs (and would add real
  network latency to the payments/sec figure).
- **Single-hub star, no fees.** All clients connect through one intermediary. A
  single hub is sufficient whenever it can reach every participant, which is the case
  for the targeted use cases, so no multi-hop routing is involved. Charging a hub fee
  is an economic concern that does not affect the measured gas or throughput and is
  left out of scope.

## Running the tests

`hub_test.go` exercises the same scenario with assertions (mesh size, zero-gas
virtual channels, on-chain ledger gas, exact payment balances), once per asset
(`TestHubManyToMany`, `TestHubManyToManyPoints`). It needs a local
Anvil with **interval mining**; without one it is skipped (never hangs).

```sh
anvil --chain-id 31337 --block-time 1     # in another terminal
go test -v ./...                          # or: ANVIL_URL=ws://host:port go test -v ./...
```
