# Arbitrum bridge — Open/Close sub-process measurements (nitro-testnode)

Date: 2026-07-19. Host: laptop (laptop), local nitro-testnode at
`~/projects/blockchain-scaling-approaches-mirror/nitro-testnode/`.

## Environment

- L1: geth `--dev --dev.period=1` (chain 1337, :8545) — 1 s auto-mined blocks.
- L2: nitro sequencer (chain 412346, :8547).
- Running compose services: **geth, sequencer, redis only.**
  **No additional services were started.** The sequencer's own config
  (`/config/sequencer_config.json` in the `config` volume) has
  `node.delayed-sequencer.enable: true` (and an embedded batch-poster,
  `max-delay: 30s`), so the sequencer itself reads the L1 delayed inbox —
  the standalone `poster`/`staker`/`validation_node` services are NOT
  needed for L1→L2 message delivery. Verified empirically: deposits were
  credited on L2 ~0.9 s after L1 inclusion with only the three services up.
- Contracts (from `docker compose run --rm --entrypoint sh scripts -c 'cat /config/deployment.json'`):
  - Inbox: `0xA520303d332b27763E3518000861EdBc9843ceCc`
  - Bridge: `0x6d2B5Db5290BE4Be47ACC104EB052eEFc1f7b1b1`
  - Sequencer inbox: `0x60FFA00eaC35597FAAb2b2B5926e5b0CddF5700c`
- Tooling: foundry `cast` (~/.foundry/bin/cast), `date +%s.%N` wall clock,
  0.1 s L2 balance polling. `LC_ALL=C` (locale comma-decimals silently
  truncate awk float math — one run was discarded for this).

Fresh accounts, funded on L1 with
`./test-node.bash script send-l1 --to address_0x<addr> --ethamount 2 --wait`:

- A `0x2B0Fc817F3C2f919C2F3E72a3bfaf76237c3ef76` (timing run discarded — locale bug; gas/tx-type data kept)
- B `0x90f73261cb4B6A8A4cEC375eaCA88BfE687FdF8B`
- C `0xeD6f4668FBFAc1578EC6D0d46A83eA091a185506`
- D `0xA13740C52dD494cC015fc8055c95e5ec6b6376D2`

## 1. Deposit L1→L2 (`Inbox.depositEth()`)

Signature verified empirically against the deployed inbox: the payable
no-argument variant `depositEth()` (selector `0x439370b1`) — `eth_call`
returns the delayed-message number. Command per run (fresh account each):

```
cast send 0xA520303d332b27763E3518000861EdBc9843ceCc "depositEth()" \
  --value 500000000000000000 --private-key $PK -r http://localhost:8545 --json
# then poll: cast balance $ADDR -r http://localhost:8547   (every 0.1 s)
```

Timing: t0 = just before `cast send`; t_L1 = receipt returned (tx mined on
L1); t_e2e = first non-zero L2 balance observed.

| run | acct | L1 gas used | t_L1 incl (s) | t_e2e L1-send→L2-credit (s) | L1 tx |
|-----|------|-------------|---------------|------------------------------|-------|
| 1 | B | 91 067 | 1.06 | 1.90 | `0x63a07238c6173d2814c63a465ceae4dbe0f561128c9e52320ccc8ef5ec99ad97` |
| 2 | C | 91 067 | 0.79 | 1.72 | `0x74dc4eba21cdd5f1a241dce4c60d7cc00aa56a2a387aac44339fe843ffefd91e` |
| 3 | D | 91 067 | 0.82 | 1.78 | `0x8739f147c0465b8b1b960abf83175b01aca725b4858b26ecd4d913b359257e87` |
| (A) | A | 91 067 | (discarded) | (discarded) | `0x5bca8737b78a11c5beac29625c75b383499f6ea11d5d72d407a8e5af40e01608` |

- **Mean L1 gas: 91 067** (identical in all 4 sends — fresh EOA, first
  deposit each, so fully comparable).
- **Mean L1 inclusion latency: 0.89 s** (dominated by the 1 s dev-mode
  block period).
- **Mean end-to-end latency: 1.80 s** (L1 send → L2 balance credited);
  L2-side delay after L1 inclusion ≈ 0.84–0.96 s, i.e. roughly one more
  L1 block for the delayed sequencer to pick the message up.
- effectiveGasPrice varied per run (8 wei → 1.5 gwei) — geth dev-mode
  basefee oscillation; gas *used* is the meaningful cost figure here.

### L2-side mechanics observed (Q3)

Each deposit appears on L2 as a **type `0x64` (100) `ArbitrumDepositTx`** —
a system tx minting the ETH directly to the destination, NOT a retryable
ticket (plain `depositEth()` skips the retryable path; retryables are only
used by `createRetryableTicket`/token bridging). The L2 tx's `from` is the
**address-aliased L1 sender** (L1 addr + `0x1111...1111`), e.g. deposit
from B `0x90f7…dF8B` arrived as from `0xa2083261…687ff09c`.
Example L2 deposit txs:

- B: `0x30079e291fbe171442005cc67a523e6e91540eb224ec370a57989307572e20a9`
- C: `0x0ba92d8936aff884688c6970782bbe3f47ad6aa3331c5c681455ff629e0853a7`
- D: `0x725c57392b6af65430dac8b0f50f6a3f16281cc26eb2d1ac9cabb12dab3b666d`

## 2. Withdrawal initiation L2→L1 (`ArbSys.withdrawEth(address)`)

From the L2 balance created by the deposits. Command per run:

```
cast send 0x0000000000000000000000000000000000000064 "withdrawEth(address)" \
  $ADDR --value 100000000000000000 --private-key $PK -r http://localhost:8547 --json
```

| run | acct | L2 gas used | receipt latency (s) | L2 tx |
|-----|------|-------------|---------------------|-------|
| 1 | B | 67 571 | 0.12 | `0x4024d32ff5e31696d7fb5f7743560ae51564edf65932685b011f81a3d14fd865` |
| 2 | C | 75 288 | 0.10 | `0x5615b5aa5974869e829a288d3e60b435b2e73c97d3ee31df1059c06dbe71594f` |
| 3 | D | 68 371 | 0.13 | `0x08c857f66ff0a7226b1dd121f3c6b8656cddaccc37ccc28854218fc94e6574aa` |

- **Mean L2 gas: 70 410; mean receipt latency: 0.12 s** (sequencer soft
  confirmation — instant relative to block cadence).
- All are ordinary **type `0x2` (EIP-1559)** L2 txs; `gasUsedForL1 = 0` on
  this local chain. Each emits ArbSys **`L2ToL1Tx`** (topic0
  `0x3e7aafa7…74fc`); run 2 additionally emitted **`SendMerkleUpdate`**
  (topic0 `0xe9e13da3…8b95`) — the outbox Merkle accumulator grew a level,
  which explains its ~7.7 k extra gas. Withdrawal-initiation gas is thus
  slightly state-dependent (67.6–75.3 k here).

### Why initiation only (scope note)

`withdrawEth` merely *records* the L2→L1 message in the outbox Merkle tree.
Funds become claimable on L1 only after (a) a validator/staker posts and
**confirms an assertion** covering this L2 block — the `staker`/
`validator` services are not running here — and (b) the **challenge
period** elapses (≈7 days on mainnet; BoLD confirm period on this
testnode), then (c) someone calls `Outbox.executeTransaction` with a
Merkle proof. That tail is days-scale by protocol design, not a
performance property of the node software, so it is out of scope for this
latency benchmark; it is reported qualitatively in the thesis text instead.

## Summary for the thesis table

- Open (L1→L2 deposit): **91 067 L1 gas**, **≈1.8 s** end-to-end on a 1 s
  L1 block time (≈2 L1 blocks: 1 for inclusion + ~1 for delayed-inbox
  pickup by the sequencer).
- Close initiation (L2→L1 `withdrawEth`): **≈70 400 L2 gas** (67.6–75.3 k),
  **≈0.12 s** to sequencer receipt.
- Close finalization: challenge period + outbox execution, protocol-bound
  (days), not measured.
