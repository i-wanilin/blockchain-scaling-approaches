# Why zkSync is slow on the Pi 4: hardware, not our build — 2026-09-30

(Updated the same night with a Cortex-A72 build and thread tests; see the last section.)

**Question.** On `bath0` (Raspberry Pi 4) zkSync carried only about 4 transfers
per second and confirmed a transfer in 16.7 s (median) during the sustained run,
against 20–30 per second and well under a second on the workstation. Is that the
Pi's hardware, our arm64 build of `zksync_server` (built under QEMU in 5 h), or
the configuration?

**Answer.** Hardware. About 80 % of the Pi's CPU time goes into KZG commitment
arithmetic on the BLS12-381 curve, which zkSync computes for every Rollup batch.
That code is generic Rust on both architectures (no assembly on either), and the
Pi does it 17× slower than the workstation — what 4 slower cores against 16
threads predict. The build is fine; a faster or native build produces the same
code. Configuration cannot remove the work: it stays with calldata instead of
blobs and with the older blob format.

## Setup

- Workstation: Intel i7-10700K (8 cores, 16 threads), x86 `zksync_server` built
  from the pinned commit `ff5f519b`. Pi: `bath0`, Cortex-A72 (4 cores), with the
  heatsinks, our arm64 binary from the same commit (SHA-256 `e77b1cc8…`).
- Same chain state (the snapshot), same configuration, batch window
  `block_commit_deadline_ms` set to 20 000 on both for the comparison.
- Load: 2 ETH self-transfers per second for 120 s, one account
  (`paced_sender.py`, feasibility tool, not in the repo).
- CPU of `zksync_server` from `top` every 5 s; a 30 s `perf` profile on the Pi
  (`perf 6.8.12`, 199 Hz, call graphs).

## Results

**Plain CPU speed** (`openssl speed sha256`, one core, large blocks): workstation
510 MB/s, Pi 169 MB/s — the workstation is about 3× faster per core, and has 16
threads against 4.

**Same load, same settings:**

| | Workstation | Pi 4 |
|---|---|---|
| Confirmation, median | 0.6 s | 1.3–1.4 s in the 120 s tests; 16.7 s in the 20-minute run |
| `zksync_server` CPU | 85 % of one core | 160–190 % (up to 270 % while catching up) |
| Commitment generation per step | 3.7 s median | 30–76 s |

**Where the Pi's CPU goes** (perf, while the server was working off the
commitment backlog left by the 20-minute run): 80 % in KZG / BLS12-381 field
arithmetic (`zksync_ff` `Fq::square`, `zksync_pairing` G1, `zksync_kzg`
`multiscalar_mul`, via rayon), 0 % in RocksDB, the rest spread thinly. The SD card
is not involved.

**The 16.7 s median of the 20-minute run** came from that backlog, not from the
batch window: each batch took longer to commit than the next one took to arrive,
so commitment work accumulated and starved everything else, including the
sealing of the small L2 blocks that carry receipts. With the same 20 s window the
workstation confirms in 0.6 s.

**Configuration tests on the Pi** (2 tx/s, 120 s each; `bath0` restored afterwards):

| Setting | Confirmation median / p99 | Server CPU | Commitment per step |
|---|---|---|---|
| Blobs, Fusaka format (as configured) | see above | up to 270 % | 38–76 s (3 batches per step) |
| (a) Blobs, pre-Fusaka format | 1.4 s / 21 s | 186 % | 40–62 s (1 batch per step) |
| (b) Calldata instead of blobs | 1.3 s / 15 s | 164 % | 30–62 s (1–2 batches per step) |

Neither setting removes the KZG work: in this zkSync version the commitment of a
Rollup batch includes the blob commitments however the data is posted. Only
Validium mode would drop it, and that is a different system, not zkSync as a
rollup.

**Why not the build.** The field library `zksync_ff` has hand-written assembly
only for 4-limb (256-bit) fields and only on `x86_64`
(`zksync_ff_derive-0.32.11/src/asm/impls_4.rs`). BLS12-381 is a 6-limb field, and
`zksync_kzg` does not enable the feature, so the workstation runs the same
generic Rust as the Pi. QEMU affected only how long the build took (5 h 04 min
against 13 min natively), not the code it produced.

## What it means

- zkSync on Pi-class hardware is CPU-bound by batch commitments. The 20 s batch
  window keeps batches full; it is not the cause of the slow confirmations.
- The finding for UC1 stands (cluster spec §8): zkSync on the Pi carries about
  4 transfers per second, UC1 at the 1 s window needs 6.
- Both remaining ideas were tried overnight (below): neither helps.

## Overnight: a Cortex-A72 build and fewer commitment threads

**Question.** Can a build tuned for the Pi 4's processor, or fewer parallel
commitment jobs, make zkSync on `bath0` faster?

**Answer.** No. The tuned build computes the commitments exactly as fast as the
generic one (29.3 s per batch either way), and its confirmation times lie within
the run-to-run spread. Fewer commitment threads make things worse. The existing
binary stays.

### What was built

The official pipeline again (Matter Labs' `docker/server-v2/Dockerfile`, the same
cached `zksync-build-base` image, commit `ff5f519b`, `--features=rocksdb/io-uring`),
with one addition: `ENV RUSTFLAGS="-C target-cpu=cortex-a72"`, confirmed in the
image's environment. Built under QEMU on the workstation in 2 h 56 min, first
attempt. Result: a binary `zksync_server.a72` (not distributed), 186 904 576
bytes, SHA-256 `ba35cf80045ee202baaaf58da2a5fa7e08c3bde631d4f530944cb407f2b4dbf0`,
`--version` 31.5.0-non-semver-compat, runs on `bath0` with all libraries.

### How it was tested

Every run: `bath0` with the heatsinks, the same chain, blobs in the Fusaka format,
20 s batch window; server restarted, commitment backlog empty before the load;
2 ETH self-transfers per second for 10 minutes from `bath1`, one account,
submitted in nonce order (the sender version made for Arbitrum, so absolute
confirmation times are not comparable with the 20-minute run of 2026-09-29, which
submitted without waiting); server CPU sampled every 5 s.

### Results

| Configuration | Runs | Confirmation median per run | p99 per run | Commitment per batch | Server CPU |
|---|---|---|---|---|---|
| Generic binary (current) | 4 | 32, 39, 46, 52 s | 106–151 s | 29.1–29.3 s | 240–245 % |
| Cortex-A72 binary | 5 | 16, 19, 25, 36, 54 s | 66–144 s | 29.3–29.5 s | 246 % |
| Generic, KZG pool 2 threads | 1 | 62 s | 132 s | 30.6 s | 223 % |
| Generic, KZG pool 1 thread | 1 | 91 s | 164 s | 41.2 s | 193 % |

All runs confirmed 1 200 of 1 200 transfers. The commitment backlog at the end was
4–6 batches every time, cleared within 90–120 s.

- **The tuned build does not speed up the expensive part.** Commitment time is the
  same to within 1 %. The field arithmetic is plain 64-bit multiplication, which
  the generic arm64 build already emits; the Cortex-A72 has no special
  instructions for it.
- **Its confirmation times are not significantly better.** The medians overlap
  (16–54 s against 32–52 s); a rank test gives U = 5 for 5 against 4 runs, where
  significance at 5 % needs U ≤ 1. Four of the five a72 runs also ran in one
  server process (see *Mistakes*), so they are less independent than the generic
  ones.
- **`commitment_generator.max_parallelism` does nothing on the main server** in
  this version: it is wired into the external node only
  (`core/bin/external_node/src/node_builder.rs`); the main server always uses
  cores − 1 = 3 (`CommitmentGenerator::default_parallelism`). The three generic
  runs with 10, 2 and 1 were therefore three repetitions of the same setting —
  which gave the run-to-run spread above.
- **Fewer KZG threads make it worse.** `RAYON_NUM_THREADS` (verified in the
  process environment) limits the pool the KZG code runs on: commitments get
  slower and confirmations do not improve. The CPU freed from KZG does not go to
  confirmations; under load the sender's submission lag reached 70–100 s, so the
  API accepting transfers is at least as much a bottleneck as block sealing.
- **Heat:** even with the heatsinks, `bath0` reached 83.7–84.2 °C in every
  zkSync run (the soft limit is 85 °C); the Arbitrum run, a lighter load, stayed
  at 57 °C. All configurations ran under the same thermal condition.
- **Spread is large.** The same configuration gave medians from 32 to 52 s. A
  thesis figure for zkSync on the Pi needs several repetitions and should report
  the spread.

### Mistakes during the night, and what they cost

- The first a72 runs: the test script looked the server up by the exact name
  `zksync_server`, which does not match `zksync_server.a72`. So the first a72
  server was never stopped, the later restarts failed on the busy port, and runs
  a72-r2, a72-r3 and the intended control all ran on that one a72 process, without
  CPU samples. Their confirmation and commitment data are valid a72 data (from
  the server's full log); the control and a CPU-sampled a72 run were repeated
  after the lookup was fixed (by full path).

### What it means

- Keep the existing binary: it is the official build, and the tuned one brings
  nothing measurable. `zksync_server.a72` and the 15.4 GB build image
  (`zksync-server-a72:builder`) can be deleted.
- zkSync's limit on the Pi is the hardware: KZG commitments at about 29 s per
  batch, and a CPU that is saturated under load. Nothing on the build or
  configuration side tried here moves it.
