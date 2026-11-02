# Perun phase 2 — sub-process latency timers + paced (open-loop) trace replay

Measured 2026-07-19 on the HP Pavilion x360 laptop (Linux Mint, Go 1.26.1).
Chain: dedicated Anvil `--port 8555 --chain-id 31337 --block-time 1`
(port 8545 left untouched — occupied by the Arbitrum testnode).
Code: `perun/hub-demo` (go-perun v0.15.0, perun-eth-backend v0.6.0).
Trace: `bench/trace-uc-c-6p-20hz-30s.jsonl` — UC-C, 6 players, 20 Hz, 30 s;
379 payment events = 250 fire (client→hub, ledger channel) + 129 hit
(victim→shooter, virtual channel).

All numbers below are copied verbatim from the program's results blocks.

## Run 1 — default mode, `-clients 6` (open/close sub-process latency)

```
Topology: 1 hub + 6 clients; 6 ledger channels (star), 15 virtual channels (mesh)
phase (on-chain)                        gas    txs
deploy contracts                    3918416      2
open ledger channels                 549744     12
open virtual channels                     0      0  <- headline
settle virtual channels                   0      0
settle ledger channels              1437072     18
gas / ledger-channel open             91624
Sub-process latency (wall clock, per channel):
  ledger open   (propose->funded, on-chain)      n=6  mean=1.0248s min=1.0182s max=1.0389s
  ledger close  (finalize->withdrawn, on-chain)  n=6  mean=2.1558s min=2.0284s max=2.7637s
  virtual open  (propose->ready, off-chain)      n=15 mean=11.4ms min=4.5ms max=41.7ms
  virtual settle (finalize->defunded, off-chain) n=15 mean=8.3ms min=5.5ms max=12ms
Off-chain: 100 payments in 146.29ms => 684 payments/s, 1.463 ms/payment
```

Reading: ledger open/close are bound by Anvil's 1 s block time (open ≈ 1 block
for the two deposits; close ≈ 2 blocks for conclude + withdraw), while the
off-chain virtual-channel open and settle are milliseconds — proving the
open/close asymmetry the state-channel family claims.

## Run 2 — closed-loop trace replay, `-clients 6 -trace ../../bench/trace-uc-c-6p-20hz-30s.jsonl`

```
Sub-process latency (wall clock, per channel):
  ledger open   (propose->funded, on-chain)      n=6  mean=1.0321s min=1.0132s max=1.0597s
  ledger close  (finalize->withdrawn, on-chain)  n=6  mean=2.0361s min=2.0328s max=2.0468s
  virtual open  (propose->ready, off-chain)      n=15 mean=7.1ms min=3.2ms max=13.2ms
  virtual settle (finalize->defunded, off-chain) n=15 mean=8.9ms min=6.7ms max=14.4ms
Trace replay: ../../bench/trace-uc-c-6p-20hz-30s.jsonl
  transfers: 379 total = 250 fire (client->hub, ledger) + 129 hit (victim->shooter, virtual)
  ok/fail:   379 / 0 (fire 250/0, hit 129/0)
  wall time: 642.079ms => 590 payments/s (closed-loop, one in flight)
  latency (all)   mean=1.693ms median=1.45ms p10=1.087ms p90=1.896ms max=13.205ms
  latency (fire)  mean=1.82ms median=1.541ms p10=1.341ms p90=2.077ms max=8.665ms
  latency (hit)   mean=1.447ms median=1.17ms p10=1.002ms p90=1.401ms max=13.205ms
```

## Run 3 — paced (open-loop) replay, same trace, `-paced`

Each payment dispatched at its trace timestamp t_ms; one FIFO worker per
channel (per-channel ordering preserved, cross-channel concurrency allowed).
Deadline budget = one 20 Hz tick = 50 ms.

```
Sub-process latency (wall clock, per channel):
  ledger open   (propose->funded, on-chain)      n=6  mean=1.0322s min=1.0212s max=1.0649s
  ledger close  (finalize->withdrawn, on-chain)  n=6  mean=2.0342s min=2.0268s max=2.0408s
  virtual open  (propose->ready, off-chain)      n=15 mean=7.1ms min=3.6ms max=12.1ms
  virtual settle (finalize->defunded, off-chain) n=15 mean=8.7ms min=6.4ms max=16.6ms
Paced trace replay (open-loop at trace timestamps): ../../bench/trace-uc-c-6p-20hz-30s.jsonl
  transfers: 379 total = 250 fire (client->hub, ledger) + 129 hit (victim->shooter, virtual)
  ok/fail:   379 / 0 (fire 250/0, hit 129/0)
  wall time: 29.952s for a 29.95s trace => finished within +10%: true (13 payments/s)
  lateness (completion - scheduled): mean=2.997ms median=2.291ms p90=5.482ms max=19.635ms
  deadline misses (> 50ms after schedule): 0 / 379 = 0.00%
  max backlog: 6 payment(s) dispatched but not yet completed
  latency (all)   mean=2.132ms median=1.455ms p10=1.103ms p90=3.84ms max=18.842ms
  latency (fire)  mean=2.452ms median=1.601ms p10=1.315ms p90=4.407ms max=18.842ms
  latency (hit)   mean=1.513ms median=1.156ms p10=1.025ms p90=2.096ms max=9.502ms
```

## Headline answers

- Can Perun keep up with a 20 Hz game in real time? **Yes.** 0.00% deadline
  misses against the 50 ms tick budget; max lateness 19.6 ms (max is the
  simultaneous 6-player fire volley at a tick boundary — peak backlog 6, i.e.
  one payment per player in flight); replay finished in 29.952 s for a 29.95 s
  trace (well within +10%).
- Open/close cost is entirely a ledger-channel (on-chain) phenomenon:
  ~1.0 s open / ~2.0-2.2 s close per ledger channel at 1 s block time, vs.
  ~7-11 ms open / ~8-9 ms settle for virtual channels (off-chain, gas-free).

## Verification

- `go vet ./...` — clean.
- `ANVIL_URL=ws://127.0.0.1:8555 go test -count=1 ./...` — PASS
  (TestHubManyToMany, 11.56 s; tests already support the `ANVIL_URL` override,
  default `ws://127.0.0.1:8545` behavior unchanged).
