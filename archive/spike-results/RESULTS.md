# Benchmark results — measured 2026-07-18/19 on the laptop

Host: HP Pavilion x360, Intel i5-8250U (4C/8T, max 3.4 GHz), 16 GB RAM,
Linux Mint 22.3 (kernel 6.17), Foundry/Anvil 1.7.1.

Methodology notes:
- "Sequential closed-loop" = one in-flight tx at a time, send → receipt;
  measures per-tx latency, NOT the node's maximum throughput.
- Game-workload replay = the SAME deterministic trace
  (`trace-uc-c-6p-20hz-30s.jsonl`, seed 42, sha256 8faf1ab1…, 379 token
  transfers: 250 fire + 129 hit from 6 players / 30 s @ 20 Hz) replayed via
  `replay_bench.py` against each family. Fairness: identical workload, same
  closed-loop discipline.

## 1. Perun hub (go-perun v0.15.0 + perun-eth-backend v0.6.0, Anvil block-time 1 s)

Synthetic burst mode (`hub-demo`, off-chain virtual-channel payments):
- deploy contracts ~3,918,416 gas (2 txs); gas per ledger-channel open ≈ 91,620–91,626
- virtual-channel open + settle: 0 gas, 0 txs (fully off-chain)
- clients=2 (1 virt ch): 500 payments / 354.98 ms → 1409 payments/s, 0.710 ms/payment;
  open ledgers 183,240 gas (4 tx); settle 478,952 gas (6 tx)
- clients=3 (3 virt ch): 200 payments / 126.74 ms → 1578 payments/s, 0.634 ms/payment;
  open ledgers 274,878 gas (6 tx); settle 718,464 gas (9 tx)
- clients=4 (6 virt ch): 500 payments / 207.72 ms → 2407 payments/s, 0.415 ms/payment;
  open ledgers 366,480 gas (8 tx); settle 958,016 gas (12 tx)
- clients=6 (15 virt ch): 500 payments / 213.17 ms → 2346 payments/s, 0.426 ms/payment;
  open ledgers 549,732 gas (12 tx); settle 1,437,060 gas (18 tx)

Game-workload trace replay (`go run . -clients 6 -trace ../../bench/trace-uc-c-6p-20hz-30s.jsonl`;
fire = client→hub over ledger channel, hit = victim→shooter over virtual channel,
amounts scaled 0.001 ETH/game-unit, closed-loop):
- 379/379 ok (fire 250/0, hit 129/0), wall 199.06 ms → 1904 payments/s
- latency (all): mean 525 µs, median 492 µs, p10 367 µs, p90 641 µs, max 1.255 ms
- latency (fire/ledger): mean 578 µs, median 536 µs — ledger update ~150 µs slower
- latency (hit/virtual): mean 423 µs, median 400 µs
- on-chain: 0 gas for all 379 transfers; setup (6 ledger opens) 549,756 gas,
  settle 1,437,208 gas, deploy 3,918,416 gas

### Cross-family comparison — SAME 379-transfer game trace, closed-loop
- Perun:    mean 0.525 ms/transfer, 0 gas each          (1904/s)
- zkSync:   mean 111.7 ms/transfer, ~129,761 gas each   (9.0/s)
- Arbitrum: mean 249.3 ms/transfer, 21,000 gas each     (4.0/s)
- (L1 1s-block baseline, synthetic: mean 986 ms, 21,000 gas)

## 2. Arbitrum (nitro-testnode, nitro-node v3.9.6, local L1 geth + sequencer, chain 412346)

Sequential closed-loop, 100 ETH transfers (sequencer RPC :8547):
- 4.1 tx/s; latency ms: mean 237.0, median 242.4, p10 236.6, p90 244.8,
  min 5.0, max 247.3 (≈ the testnode sequencer's ~250 ms block cadence)
- gas/transfer: 21,000 (exact)

Game-workload replay (379 transfers, 7 accounts):
- 379/379 ok, wall 94.49 s, 4.0 tx/s
- latency ms: mean 249.3, median 250.5, p10 249.0, p90 252.0, max 258.2
- gas/transfer: 21,000 (exact)

## 3. zkSync Era (anvil-zksync v0.6.11, default config, chain 260)

Sequential closed-loop, 100 ETH transfers (single hot account):
- 29.6 tx/s; latency ms: mean 25.7, median 25.0, p10 24.9, p90 28.1,
  min 24.7, max 31.5
- gas/transfer: 129,845 (mode; min 129,845 max 132,187) — zkSync gas units

Game-workload replay (379 transfers, 7 accounts):
- 379/379 ok, wall 42.35 s, 9.0 tx/s
- latency ms: mean 111.7, median 111.7, p10 111.3, p90 112.0, max 115.2
- gas/transfer: mode 129,761 (min 129,642 max 132,103)
- NOTE: higher latency than the single-account run (111.7 vs 25.7 ms mean) —
  multi-account rotation exercises a colder path in anvil-zksync; investigate
  (account-touch/first-write costs vs sealing behavior) before quoting a
  single "zkSync latency" figure.

## 4. Anvil L1 baseline (block-time 1 s, chain 31337)

Sequential closed-loop, 100 ETH transfers:
- 1.0 tx/s (block-interval-bound); latency ms: mean 986.2, median 988.3,
  p10 984.4, p90 1008.4, min 323.2, max 1011.8
- gas/transfer: 21,000 (exact)

## Reproduce

```sh
# venv (once): python3 -m venv bench/.venv && bench/.venv/bin/pip install web3
anvil --chain-id 31337 --block-time 1 &            # L1 / Perun runs
(cd perun/hub-demo && go run . -clients 6 -payments 500)
(cd zksync && ./anvil-zksync &)                     # zkSync runs
bench/.venv/bin/python bench/evm_bench.py http://127.0.0.1:8011 100 zksync
bench/.venv/bin/python bench/replay_bench.py bench/trace-uc-c-6p-20hz-30s.jsonl http://127.0.0.1:8011 zksync
(cd nitro-testnode && ./test-node.bash --init --detach)  # Arbitrum (heavy)
bench/.venv/bin/python bench/evm_bench.py http://127.0.0.1:8547 100 arbitrum
```

## Phase 2 (2026-07-19) — see also RESULTS-perun-phase2.md, RESULTS-arb-bridge.md, RESULTS-ucab.md, ../emulation/RESULTS-netem.md, results-ucab-runs.log

### Paced (open-loop) replay at native cadence — deadline keeping
UC-C game 6p @20 Hz (12.7 pay/s offered, 50 ms budget):
- Perun: 0/379 misses, mean lateness 3.0 ms, max 19.6 ms, backlog ≤ 6
- zkSync: 46/379 = 12.1% misses, mean 29.4 ms, max 80.6 ms; 12.6 tx/s sustained
- Arbitrum: 362/379 = 95.5% misses, mean 210 ms, max 1.03 s; 12.6 tx/s sustained
UC-A platooning 4v @10 Hz (40.1 pay/s offered, 100 ms budget):
- Perun 0/2400 misses (lateness 1.3 ms) | zkSync 0/2400 (41.7 ms) | Arbitrum 2400/2400 missed, achieved 8.0 tx/s
UC-B PMU 1 sensor @10 Hz (10 pay/s offered):
- Perun 0/600 (1.1 ms) | zkSync 0/600 (19.5 ms) | Arbitrum 597/600 missed, achieved 4.0 tx/s
KEY FINDING: Arbitrum nitro-testnode accepts ~1 tx per account per ~250 ms block
under in-order nonces → per-payer cap ≈ 4 tx/s regardless of offered rate.

### Sub-process wall-clock (Perun, n=6/15)
ledger open 1.02 s mean | ledger close 2.16 s | virtual open 11.4 ms | virtual settle 8.3 ms

### Arbitrum bridge (Inbox.depositEth / ArbSys.withdrawEth)
deposit: 91,067 L1 gas, 1.80 s mean to L2 credit (3 runs) | withdraw init: 70,410 gas mean, 0.12 s
deposit lands as type-0x64 direct-mint system tx (not a retryable)

### ERC-20 transfer gas (minimal token, steady-state)
L1 Anvil 35,047 | Arbitrum 35,047 | zkSync 209,492 (--evm-interpreter mode; native-ETH 129,761)

### Netem (tier 2, closed-loop UC-C, medians ms)
zkSync: baseline 112.5 → wifi 127.2 → lte 173.9 (≈3 shaped crossings/tx; wifi bimodal → quote medians)
Arbitrum: 250.6 → 250.7 → 250.3 (cadence-dominated; only tails widen)
Perun × netem: not measurable (in-process bus) → Pi cluster tier 3
