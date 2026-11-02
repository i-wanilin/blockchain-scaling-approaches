> **OUTDATED** — pre-cluster workstation probes (x86, 2026-09-08/09); not thesis results; the capacity probes on the Pi cluster are future work (cluster spec §7.1).

# Arbitrum throughput measurements — 2026-09-08

> **zkSync Era measured too** — see [`README-zksync.md`](README-zksync.md).
> Headline on identical hardware: one client **157.5 tx/s** (Arbitrum) vs
> **17.6 tx/s** (zkSync); one node **~1 200** (burst; see the correction below)
> vs **~20**. On a Raspberry Pi, the full Arbitrum stack needs 367 MB and fits
> even a 1 GB board; the full zkSync stack needs 2 926 MB and needs a 4 GB board
> (measured 2026-09-09; `deploy/README.md` §5 has the stacks as measured on the cluster).

Six runs in one afternoon. **Each corrects the one before**, and the corrections
are as much a result as the numbers: a client-side artifact survived from the
spike through two probes before it was caught.

| # | File | Question | Verdict |
|---|---|---|---|
| 1 | `arbitrum-ceiling-…txt` | Is the spike's 4 tx/s a node limit? | Data valid, **conclusion WITHDRAWN** |
| 2 | `arbitrum-accounts-…txt` | Where is the aggregate ceiling? | Linear to 251 tx/s, no knee |
| 3 | `arbitrum-single-account-…txt` | Can one account beat 4 tx/s? | **Yes — 113.8 tx/s** |
| 4 | `arbitrum-ceiling-mp-…txt` | Ceiling, GIL escaped | 982 tx/s, then base-fee rejections |
| 5 | `arbitrum-ceiling-mp2-…txt` | Ceiling, gas headroom | **~1,200 tx/s — knee found** |
| 6 | `arbitrum-one-device-120hz-…txt` | Can one device do GESL's 120 Hz? | **Yes — 157 tx/s** |

---

## ⚠️ CORRECTION 2026-09-09: 1,200 tx/s is a BURST, not a sustainable rate

Queried on our own chain:

```
ArbGasInfo.getGasAccountingParams()
  speedLimitPerSecond    7,000,000     <- identical to Arbitrum One (ArbOS 6+)
  gasPoolMax            32,000,000
  maxTxGasLimit         32,000,000     ArbOS version 95
```

**Our testnode enforces exactly the same 7 M gas/s target as Arbitrum One**, so
this transfers directly rather than being a local-config artefact.

| measured | gas/s | vs 7 M target |
|---|---:|---:|
| 995.94 tx/s (256 in flight) | 20.91 M | **2.99×** |
| 1 194.35 tx/s (peak) | 25.08 M | **3.58×** |

The target is not a hard cap — exceeding it raises the L2 base fee, EIP-1559
style, to curb demand. **So the "base fee rises under load" finding below was not
a curiosity: it was ArbOS's congestion control doing its job, and `--gas-mult 200`
was us overriding the price signal.** These rates are reachable in bursts (the
32 M gas pool allows ~4.5 s of them) but are not economically sustainable.

### Sustainable throughput at the 7 M target

| gas per payment | sustainable tx/s |
|---|---:|
| 21 000 — plain ETH transfer (**what we measured**) | **333** |
| 50 000 — simple contract call | 140 |
| 100 000 — ERC-20 / metered payment | 70 |

### The UC1 verdict changes

| | needed | gas/s | share of the WHOLE chain | verdict |
|---|---:|---:|---:|---|
| One device @120 Hz | 120 tx/s | 2.52 M | **36 %** | fits, but consumes a third of the chain |
| Four devices @120 Hz | 480 tx/s | 10.08 M | **144 %** | ❌ **exceeds the chain target** |

The earlier "✅ all four devices, 107 % headroom" verdict was measured against the
node's burst capability, not against the rate the chain is designed to sustain.
**Corrected: four devices at per-sample granularity do not fit Arbitrum One.**

Worse, the budget is unforgiving. For one device at 120 Hz:

```
100 % of the entire chain target ->  58,333 gas per payment
 36 % (what 21k gas costs)       ->  21,000 gas per payment
 10 % (a polite share)           ->   5,833 gas per payment
```

A plain ETH transfer already costs 21 000. **Any real payment — a contract call, a
channel update, an ERC-20 transfer — pushes a single 120 Hz device past a third of
Arbitrum One's entire sustainable capacity.** For context, Arbitrum One averages
~57 TPS of real traffic, so adding 480 tx/s would be roughly a 9× increase in the
chain's total load.

### What still stands

The *mechanism* is unaffected: throughput = concurrency ÷ submission latency, and
latency is flat below saturation. The node really does reach ~1 200 tx/s. What
changed is the interpretation — that figure is a burst ceiling obtained by
ignoring the fee signal, and the thesis must quote **333 tx/s** as the sustainable
plain-transfer rate.

## The answer (node mechanism — see the correction above for sustainability)

**Throughput = concurrency ÷ submission latency, until the node saturates near
1,200 tx/s.**

```
    4.13 tx/s  =    1 in flight  /  0.244 s
  113.80 tx/s  =   32 in flight  /  0.245 s
  995.94 tx/s  =  256 in flight  /  0.257 s
 1194.35 tx/s  = 1024 in flight  ->  SATURATED (latency now rising)
```

`eth_sendRawTransaction` is **synchronous** — it blocks until the transaction is
sealed. `--execution.sequencer.max-block-speed` defaults to **250 ms**, described
upstream as *"minimum delay between blocks (sets a maximum speed of block
production)"*, and is not overridden in this deployment.

So the 250 ms is a **floor on latency, not a cap on throughput.** The spike's
"4 tx/s" was one client holding one request open — a property of the client, not
of the account, not of Arbitrum.

## Verdict for UC1 (GESL Provider 11: 4 devices at 120 Hz)

| Requirement | Needed | Measured | Headroom |
|---|---|---|---|
| One device, per-sample | 120 tx/s | **157.5 tx/s** @ 48 in flight | 31 % |
| All four devices | 480 tx/s | **995.9 tx/s** @ 256 in flight | 107 % |
| Latency at that load | — | **253 ms** (unchanged from idle) | — |

**Per-sample payment at full GESL rate is within the NODE's burst capability**, and
it does not require extra accounts — so the "one entity = one key, no aggregation"
rule is not violated at the node level.

⚠️ **But see the correction at the top:** measured against Arbitrum's sustainable
7 M gas/s target, four devices at 120 Hz need 144 % of the whole chain. The node
can burst it; the chain is not designed to sustain it.

## The ceiling, and how we know it is real

| in-flight | 64 | 128 | 256 | 512 | 1024 | 2048 |
|---|---:|---:|---:|---:|---:|---:|
| tx/s | 254.9 | 507.1 | 995.9 | 1128.9 | **1194.4** | 1117.7 |
| e2e mean ms | 251 | 251 | 253 | 413 | 653 | 580 |
| load (16 cores) | 2.6 | 6.5 | 5.5 | 6.7 | 7.3 | 9.0 |
| errors | 0 | 0 | 0 | 0 | 0 | 0 |

Throughput plateaus and then **declines** while latency **climbs** — the classic
saturation signature. Below the knee, latency is flat at ~251 ms across a 4×
throughput range, which is well-behaved. 25,600 transactions, zero errors.

⚠️ Caveat: generator and node share this host, and load reached 9 of 16 cores at
the top. Some of the plateau may be host contention rather than the sequencer
alone. The knee is real; its exact position is a system property of this host.

## Side finding: gas price is load-dependent

Run 4 failed above 256 in-flight with:

```
max fee per gas less than block base fee: maxFeePerGas: 100000000, baseFee: …
```

Arbitrum's **L2 base fee rises under load**, and transactions pre-signed with the
idle gas price became underpriced mid-run. Fixed here with `--gas-mult 200`
(free ETH, and price is not what this probe measures).

**This matters for Metric 2:** gas *units* are deterministic, but gas *price* is
not constant under load. Any euro figure must state the load conditions it was
measured under.

## Reading the latency columns

```
  sign it                        <- before the timer (pre-signed, so ECDSA
                                    cost cannot inflate the result)
1 call eth_sendRawTransaction    <- TIMER STARTS
2 sequencer queues it
3 sequencer seals a block        <- at most every max-block-speed (250 ms),
                                    and ONLY if something is queued
4 the send call returns          <- it was BLOCKED for steps 2-3
5 poll eth_getTransactionReceipt
6 receipt in hand                <- TIMER STOPS
```

| Column | Covers | Meaning |
|---|---|---|
| `sub_ms` | 1→4 | How long the send call blocks — one block interval when unsaturated |
| `e2e_mean` / `e2e_p90` | 1→6 | Submission plus fetching the receipt |
| `inflt` | — | A × D, transactions outstanding at once. **The independent variable** |
| `tps` | — | **Confirmed in a block** per second |
| `load` | — | 1-min load average at end of cell, 16-core host. Plateau + low load ⇒ node bound. Plateau + ~1 core per process ⇒ generator bound |

`sub_ms ≈ e2e_mean` below saturation because the send already waited for
sealing; almost all latency is step 3.

## Two probe defects, both found and fixed

1. **One thread per ACCOUNT.** Since the send blocks, that thread serialised its
   own sends — the `depth` axis never varied real concurrency. Produced the
   withdrawn "4 tx/s is a per-account limit". Fixed: one thread per in-flight slot.
2. **Threads only.** CPython's GIL capped the *generator* near 280 tx/s while load
   sat at ~1 core of 16. Fixed: `--procs`, accounts partitioned across processes
   so nonces cannot race.

Both are why run 1's conclusion is marked WITHDRAWN in place rather than deleted —
how the artifact propagated is worth citing in the methodology chapter.

## Conditions

Arbitrum Nitro via `nitro-testnode` @ `62f6cae`, node image `v3.9.6-91bf578`,
contracts `v3.1.0`, token-bridge `v1.2.5`, chain 412346, sequencer `:8547`.
Host workstation, i7-10700K (16 threads), WSL2, 15 GB RAM.
Plain ETH transfers, 21 000 gas — **not ERC-20**.
The testnode's own traffic generators were **stopped** before every run; left
running they add ~2 tx/s *and drive block production*.

## Scope

A local development deployment, not Arbitrum One. **What transfers is the
mechanism — throughput = concurrency ÷ submission latency — and the fact that
latency is flat below saturation. Not the absolute rate.** These are probes
(30–200 tx per cell), not n=3 measurement runs; the headline figures should be
re-run at n=3 before they enter the thesis.
