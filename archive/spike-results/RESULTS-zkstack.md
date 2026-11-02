# zkSync full chain (zkstack) — install + bridge measurements, 2026-07-20

Goal: measure the deposit/withdraw sub-process that anvil-zksync cannot
(no L1 bridge), and test whether the laptop can run the real stack at all.

## Setup that finally worked

- rustup (stable + auto-pulled nightly-2025-03-19), zkstackup → zkstack CLI 0.2.1,
  foundry-zksync, ecosystem `thesis_local` at ~/zksync-ecosystem/ (OUTSIDE the repo),
  chain `thesis_chain` (id 271, era-VM, rollup mode, no proofs), L1 = reth docker
  on port **8945** (8545 is the Arbitrum testnode — the whole point of most failures
  below), postgres in docker, `zksync_server` built from source (release, 184 MB,
  needs `libclang-dev`; ~4 GB target dir; built overnight on the i5-8250U).
- Full working sequence encoded in ~/zksync-ecosystem/clean-init.sh.

## Deployment-complexity findings (thesis data — metric "deployment complexity")

10 failed/partial attempts before the clean pass. Distinct failure classes:
1. zkstack prompts crash with NotConnected when run without a TTY (pty + auto-Enter needed).
2. `--dev` silently OVERRIDES an explicit `--l1-rpc-url` with the compiled-in
   default http://127.0.0.1:8545 (defaults.rs) — registration sprayed at the wrong chain.
3. "Distributing eth" fails with a spurious "insufficient funds" on a custom-port
   L1; bypass: `wallet_creation: InFile` + manual pre-funding of all wallets.
4. Re-running `ecosystem init` after a partial deploy hits `Ownable: caller is not
   the owner` / `BridgeHubAlreadyRegistered` — the flow is not idempotent.
5. Half-crashed init leaves chain contracts.yaml with zeroed/missing fields
   (schema requires ~40 fields incl. flattened CTM section).
6. Priority-op id mismatch panic in eth_watch when init generations mix
   (queue ids 0–5 from an old attempt outside the 50k-block default scan window).
7. `zksync_server` build needs libclang (bindgen) — not in zkstack's prerequisites list.
Contrast: Perun = single Go binary; Arbitrum testnode = one docker script;
anvil-zksync = one static binary. The full zkSync stack is by far the heaviest
deployment of the four (hours + 4 GB build + docker L1 + postgres, 11 attempts).

## Measurements (laptop, i5-8250U, chain 271, no proofs, reth L1 1s blocks)

### Deposit L1→L2 (zksync-ethers `wallet.deposit`, ETH, 3 runs)
- L1 gas: 288,831 / 283,756 / 341,335  (mean 304,641)
- e2e latency L1 send → L2 balance credited: 4.64 / 4.50 / 4.56 s (mean 4.57 s)
  (L1 inclusion ≈ 4.5 s of that; L2 credit follows within ~0.1 s)

### Withdrawal initiation L2→L1 (`wallet.withdraw`, ETH, 3 runs)
- L2 gas: 236,540 / 115,536 / 235,823 (mean 195,966; bimodal — cf. Arbitrum's
  SendMerkleUpdate gas spread)
- receipt latency: 1.94 / 1.84 / 2.40 s (mean 2.06 s)
- Full L1 finalization requires batch commit + proof + execute — not measured
  (no-proofs dev chain; protocol-bound, documented).

### Sequential transfer latency (evm_bench.py, 100 native transfers)
- mean 4,130.5 ms  median 4,079.6  p10 2,710  p90 5,367  min 191  max 14,605
- 0.2 tx/s closed-loop; gas/transfer mode 128,186
- **KEY FINDING: anvil-zksync (same laptop): 25.7 ms mean — the emulator
  understates real-sequencer latency by ~160×.** The real state-keeper's batch
  sealing dominates. Directly supports the environment-realism axis: emulator
  numbers are lower bounds, not predictions.

### Resource footprint
- server RAM ~0.2–3% of 16 GB (idle–load), CPU bursts to ~620% (6 cores) during
  the bench; laptop stays usable. Disk: ~4 GB target/ + ~2 GB toolchains + images.
- Verdict: the laptop CAN run the full stack; a Pi 4 (4 GB, arm64) cannot
  realistically build it and would struggle to run it — placement guidance in
  deploy/README.md stands (full zkSync belongs on the workstation).
