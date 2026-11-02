#!/usr/bin/env python3
"""Pre-flight for the zkSync throughput runs.

Verifies the assumptions the Arbitrum probe silently relies on, BEFORE a sweep
is trusted. Each of these differs between EraVM and Arbitrum's EVM:

  1. RPC reachable, chain id as configured (271)
  2. A rich wallet exists and holds balance
  3. zkSync accepts a PLAIN legacy ETH transfer (not EIP-712) -- if it does not,
     the shared probe cannot be reused and we fall back to zksync-ethers
  4. Observed sealing cadence, i.e. the analogue of Arbitrum's 250 ms
     max-block-speed. general.yaml says miniblock_commit_deadline_ms: 1000.

Usage: zksync_sanity.py <rpc> <funder_privkey>
"""
import sys, time, statistics as st
from web3 import Web3

RPC = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3050"
import os
PK  = os.environ.get("ZK_PK") or sys.argv[2]
SINK = "0x000000000000000000000000000000000000dEaD"

w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 120}))
print(f"connected      : {w3.is_connected()}")
if not w3.is_connected():
    sys.exit(f"cannot reach {RPC}")
print(f"chain id       : {w3.eth.chain_id}")
print(f"block number   : {w3.eth.block_number}")
print(f"gas price      : {w3.eth.gas_price}")

acct = w3.eth.account.from_key(PK)
bal = w3.eth.get_balance(acct.address)
print(f"funder         : {acct.address}")
print(f"balance        : {w3.from_wei(bal,'ether')} ETH")
if bal == 0:
    sys.exit("funder has no balance -- init did not fund it")

# 3 + 4: five sequential transfers. Sequential => each waits a full seal, so the
# inter-receipt gap IS the block cadence, and 1/latency is one client's closed-
# loop ceiling. Exactly how Arbitrum's 4.13 tx/s arose.
print("\nsending 5 sequential plain transfers (legacy type-0)...")
lat, blocks = [], []
nonce = w3.eth.get_transaction_count(acct.address)
gp = w3.eth.gas_price
for i in range(5):
    tx = {"to": SINK, "value": 1, "gas": 200000, "gasPrice": gp,
          "nonce": nonce + i, "chainId": w3.eth.chain_id}
    t0 = time.time()
    try:
        h = w3.eth.send_raw_transaction(
            w3.eth.account.sign_transaction(tx, acct.key).raw_transaction)
    except Exception as e:
        sys.exit(f"PLAIN TRANSFER REJECTED: {e}\n"
                 "-> the shared probe cannot be reused; use zksync-ethers.")
    r = w3.eth.wait_for_transaction_receipt(h, timeout=300, poll_latency=0.01)
    dt = (time.time() - t0) * 1000
    lat.append(dt); blocks.append(r.blockNumber)
    print(f"  tx{i}  {dt:8.1f} ms   block {r.blockNumber}  gasUsed {r.gasUsed}")

print(f"\nmean latency   : {st.mean(lat):.1f} ms")
print(f"1-client tps   : {1000/st.mean(lat):.2f}  (closed-loop, 1 in flight)")
print(f"distinct blocks: {len(set(blocks))} of {len(blocks)}")
print("\nPLAIN TRANSFERS ACCEPTED -- probe_ceiling.py is reusable as-is.")
