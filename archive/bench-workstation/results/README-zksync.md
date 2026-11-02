> **OUTDATED** — pre-cluster workstation probes (x86, 2026-09-08/09); not thesis results; the capacity probes on the Pi cluster are future work (cluster spec §7.1).

# zkSync Era throughput measurements — 2026-09-08/09

Same probe, same transaction shape and same host as the Arbitrum runs in
[`README.md`](README.md), so the two are comparable rather than merely adjacent.

| # | File | Question | Verdict |
|---|---|---|---|
| 0 | (pre-flight) | Does EraVM accept plain type-0 transfers? | **Yes** — probe reusable |
| A | `zksync-one-client-…txt` | Max throughput for ONE client | ~17.6 tx/s (host busy) |
| A2 | `zksync-one-client-nonce2500-…txt` | Same, nonce window 200→2500, quiet host | **~25–31 tx/s** |
| B | `zksync-node-ceiling-…txt` | Max throughput for ONE node | **~20.2 tx/s** |
| C | (footprint) | Does the stack fit a Raspberry Pi 4? | **Yes — 2 557 MB of ~3 700** |

## Headline: zkSync is far slower here, and far more expensive per transaction

| | Arbitrum Nitro | zkSync Era | ratio |
|---|---:|---:|---:|
| 1 client, 1 in flight | 4.13 tx/s | **0.64 tx/s** | 6.5× |
| 1 client, best | 157.5 tx/s | **17.6 tx/s** | 8.9× |
| 1 node, best | ~1 200 tx/s | **20.2 tx/s** | ~60× |
| host load at best | 5.5–7.3 | **45.7** | — |
| L2 block cadence | 250 ms | 1 000 ms | 4× |

**The node ceiling (20.2) is barely above the single-client ceiling (17.6).** One
account already saturates ~87 % of what the whole node can do. On Arbitrum the
gap was 157 → 1 200, a factor of 7.6. So on zkSync the bottleneck is the node,
not the account.

## ⚠️ Run-to-run variance is large — treat single runs with suspicion

The same cell (1 account, depth 64) measured **17.64 tx/s at host load 16.08** and
**30.57 tx/s at load 3.53** — a 74 % difference from host conditions alone, with
identical code and configuration.

**The quiet-host figures are the trustworthy ones.** Test A and Test B below were
taken on a loaded host and are therefore *lower bounds*, not ceilings. This is the
strongest argument in these results for repeating everything at n=3 on an
otherwise idle machine before anything enters the thesis.

## Test A2 — does the nonce window limit throughput? No.

`max_nonce_ahead` raised 200 → 2500 and the server restarted (verified live: a
transaction 300 nonces ahead was accepted, which 200 would have rejected).

```
depth   inflt    tps    e2e_mean    load
   64      64   30.57    1870 ms    3.53
  128     128   19.12    5794 ms   12.67
  256     256   25.57    4591 ms   15.66
  512     512   25.60    5865 ms   15.87   <- 512 in flight, zero nonce errors
```

512 in flight now runs cleanly and yields 25.60 tx/s against 25.57 at 256 — **no
gain**. The plateau is unchanged. The window was never the binding constraint:
saturation appears at depth 64, far inside even the 200 setting.

## Test A — one client

```
depth   inflt    tps    e2e_mean   e2e_p90    err
    8       8    3.29    2417 ms    3384 ms    63   (leftover mempool drain)
   32      32    8.99    3496 ms    6032 ms     0
   64      64   17.64    3356 ms    5009 ms     0   <- knee
  128     128   16.98    5675 ms    9509 ms     0
  192     192   16.05    5139 ms    8847 ms     0
  256     256   16.19    7258 ms   12703 ms   158   (nonce window, see below)
```

Beyond depth 64 throughput is flat-to-declining while latency more than doubles —
the same saturation signature as Arbitrum, at one ninth the level.

### A hard per-account cap with no Arbitrum equivalent — and mainnet is stricter

```
nonce too high. allowed nonce range: 4903 - 5103, actual: 5125
```

| Setting | Value | Source |
|---|---:|---|
| Rust struct default | 5 | `core/lib/config/src/configs/api.rs:523` |
| **zkSync Era mainnet** | **20** | live mainnet error, maintainer-confirmed |
| This local chain | 200 | `etc/env/file_based/general.yaml:35` |

A real mainnet error reported by a developer reads `nonce too high. allowed nonce
range: 0 - 20`, and a maintainer confirms 20 is "the maximum queue length the
server allows" ([discussion #940](https://github.com/zkSync-Community-Hub/zksync-developers/discussions/940)).

**So this local chain is 10× MORE permissive than production.** The measurements
here flatter zkSync rather than penalise it.

On mainnet, one account may hold ~20 transactions in flight. To reach 120 tx/s
that requires 167 ms per transaction — *faster than the 1 000 ms block cadence*.
**One account cannot reach GESL's rate on zkSync Era mainnet at any hardware
budget.** Arbitrum imposes no such window.

It is enforced in `core/node/api_server/src/tx_sender/mod.rs` — **operator mempool
policy, not consensus.** A user cannot raise it; running your own node does not
help, since transactions still go through the sequencer's API.

### The real-world escape: native Account Abstraction

zkSync Era has native AA, so a smart-contract account can batch many payments into
one transaction. This is legitimate and fits zkSync well — but it is a **design
change, not a workaround**: it breaks one-payment-one-transaction, so per-payment
gas and latency stop being comparable across the three systems, and it collides
with the "one entity = one key, no aggregation" rule.

The finding worth reporting is the asymmetry: **zkSync pushes the design toward
batching; Arbitrum does not need it.**

### Sanity check against the public chain

| Measurement | TPS |
|---|---:|
| Mainnet peak daily-average (Dec 2023) | ~62 |
| AMM benchmark (Jun 2024) | 181 |
| Claimed theoretical capacity | ~2 000 |
| **This local single-host chain** | **25–31** |

Our figure is the same order as real observed mainnet usage, which is evidence the
harness is sound. Real zkSync would clear 120 tx/s at the *node* level; it still
would not from *one account*, and the observed ~62 would not cover UC1's
four-device 480 tx/s.

## Test B — one node

```
inflt    tps    e2e_mean   e2e_p90    load   err
   64   14.64    4110 ms    6910 ms   23.43    0
  128   16.74    6926 ms   11992 ms   31.77    0
  256   20.23   11002 ms   17379 ms   45.65    0   <- peak
  512   15.06   28114 ms   41440 ms   57.88    0
```

Peak ~20 tx/s at 256 in flight, then throughput falls while latency reaches 28 s.

⚠️ **Host load was 23–58 on 16 cores throughout, with only 2 generator
processes.** Most of that is the sequencer itself. This is therefore a
*deployment* ceiling on this hardware, not a protocol ceiling — but the same
caveat applied to Arbitrum, which reached ~1 200 tx/s on the same host at load
5.5. The comparison that survives is: **on identical hardware, Arbitrum sustained
~60× the throughput at ~⅛ the CPU load.**

## UC1 verdict (GESL Provider 11: 4 devices at 120 Hz)

| Requirement | Needed | Arbitrum | zkSync |
|---|---:|---:|---:|
| One device, per-sample | 120 tx/s | ✅ 157.5 | ❌ **17.6** (7× short) |
| All four devices | 480 tx/s | ✅ 995.9 | ❌ **20.2** (24× short) |

**Per-sample payment at GESL's native rate is not viable on zkSync Era in this
deployment.** UC1 on zkSync would require batching or windowed settlement — which
is a design change, not a tuning change, and it interacts with the "one entity =
one key, no aggregation" rule.

## Gas is not deterministic on zkSync

Five identical 1-wei transfers:

```
gasUsed: 127499, 83334, 127499, 83334, 127499
```

The same operation costs **53 % more** on alternating transactions, because EraVM
prices L1 pubdata into L2 gas and that cost amortises differently across batches.
Arbitrum charged a flat 21 000 every time.

**This matters for Metric 2.** The proposal makes gas units the primary cost
figure precisely because they are deterministic. On zkSync they are not, so a
single gas number is meaningless without the distribution and the batch position.

## Test C — footprint (the Raspberry Pi question)

**Everything runs on the Pi cluster, L1 included** — there is no workstation in
the measured path. An earlier version of this table excluded the L1 as
"workstation, not the Pi"; that contradicted the deployment plan and is corrected.

| Component | idle | under sustained load |
|---|---:|---:|
| `zksync_server` | 1 398 MB | 1 209 MB |
| PostgreSQL | 752 MB | **1 348 MB** |
| reth (L1) | 309 MB | 369 MB |
| **Full stack on the Pi** | **2 459 MB** | **2 926 MB** |

Measured against Arbitrum's full stack on the same host and the same load:

| full stack, all on-Pi | idle | under load |
|---|---:|---:|
| **Arbitrum** (sequencer + redis + geth) | **284 MB** | **367 MB** |
| **zkSync** (server + PostgreSQL + reth) | 2 459 MB | 2 926 MB |

**zkSync's full stack is ~8× heavier.**

| board | usable RAM | Arbitrum | zkSync |
|---|---:|---|---|
| Pi 3B+ / Pi 4 1 GB | ~900 MB | ✅ 41 % | ❌ 325 % |
| Pi 4 2 GB | ~1 800 MB | ✅ 20 % | ❌ 163 % |
| Pi 4 4 GB | ~3 700 MB | ✅ 10 % | ✅ 79 % |
| Pi 4 / 5 8 GB | ~7 800 MB | ✅ 5 % | ✅ 38 % |

**A whole Arbitrum stack fits a 1 GB Pi 3B+. zkSync needs a 4 GB board or
better**, or must be split across Pis — which puts PostgreSQL behind the Wi-Fi
link and into the §5 timing budget.

The driver is `zksync_server` (1.2 GB) plus its **mandatory PostgreSQL** (1.35 GB,
grown 80 % under a short load). Arbitrum needs no database — only a 4 MB redis.

⚠️ This is a freshly-initialised chain, so RocksDB and the Merkle tree are nearly
empty; a chain with real history needs re-measuring. And these are RSS figures
from the x86 workstation — RAM is settled, SD-card I/O is not.

## Method corrections made during these runs

1. **`--procs 8` invalidated the first Test B.** It drove host load to 115/16
   cores and produced 4.12 tx/s at 64 in flight — *below* Test A's 17.6 at the
   same concurrency, which is impossible if the node were the constraint. On
   Arbitrum `--procs 8` was the fix (280 → 1 200 tx/s); here it is the fault. The
   load column is what caught it. Re-run at `--procs 2` behind a load gate.
2. **Funding.** Accounts were sized from *observed* gas (~1.3e-5 ETH/tx), but the
   node reserves `gasLimit × gasPrice` up front. Test A ran dry mid-sweep.
3. **`zkstack dev rich-account` is not a bulk funding route.** 1.0 ETH sent on L1
   credited **0.000188 ETH** on L2; the rest went to the priority-op base cost.

## Conditions

zkSync Era `ff5f519b1`, contracts `101ab952b`, zkstack `v0.2.1-ff5f519b1`,
chain 271, EraVM, Rollup, **NoProofs** (`SKIP_EVERY_PROOF` — no prover, so this
measures the sequencer only), L1 = reth v1.8.2 `--dev.block-time 300ms`,
PostgreSQL 18. Sequencer RPC `:3050`. Host workstation, i7-10700K
(16 threads), WSL2, 15 GB RAM. Plain ETH transfers.

Single runs, not n=3 — the headline figures should be repeated at n=3 before they
enter the thesis, as with Arbitrum.
