#!/usr/bin/env python3
"""Fund N accounts derived from the anvil test mnemonic, from account 0.

The throughput probe needs many distinct senders: per-account throughput is
capped by the sequencer, so the aggregate ceiling can only be found by adding
accounts. The testnode's own `script send-l2` spawns a container per transfer,
which is far too slow for 64 accounts; this sends directly over JSON-RPC.

Local dev chains only -- this mnemonic is public.

Usage: fund_accounts.py <rpc_url> <n_accounts> [ether_each]
"""
import sys, os, time
from web3 import Web3
from eth_account import Account

Account.enable_unaudited_hdwallet_features()
MNEMONIC = "test test test test test test test test test test test junk"

RPC = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8547"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 64
EACH = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0


def acct(i):
    return Account.from_mnemonic(MNEMONIC, account_path=f"m/44'/60'/0'/0/{i}")


w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 120}))
if not w3.is_connected():
    sys.exit(f"cannot reach {RPC}")
chain, gp = w3.eth.chain_id, w3.eth.gas_price
# zkstack chains generate their own wallets, so the funder may not be
# mnemonic-derived. ZK_PK overrides; recipients are always mnemonic-derived.
_pk = os.environ.get("ZK_PK")
funder = Web3().eth.account.from_key(_pk) if _pk else acct(0)
bal = w3.eth.get_balance(funder.address)
print(f"funder {funder.address}  {w3.from_wei(bal,'ether'):.2f} ETH  chain={chain}")

need = []
for i in range(0 if _pk else 1, N):
    a = acct(i)
    if w3.eth.get_balance(a.address) < w3.to_wei(EACH * 0.5, "ether"):
        need.append((i, a.address))
print(f"{len(need)} of {N-1} accounts need funding ({EACH} ETH each)")
if not need:
    sys.exit(0)

required = w3.to_wei(EACH * len(need), "ether")
if bal < required:
    sys.exit(f"funder has {w3.from_wei(bal,'ether')} ETH, needs {w3.from_wei(required,'ether')}")

nonce = w3.eth.get_transaction_count(funder.address)
t0 = time.perf_counter()
hashes = []
for k, (i, addr) in enumerate(need):
    tx = {"to": addr, "value": w3.to_wei(EACH, "ether"),
          "gas": int(os.environ.get("FUND_GAS", "21000")),
          "gasPrice": gp, "nonce": nonce + k, "chainId": chain}
    hashes.append(w3.eth.send_raw_transaction(
        w3.eth.account.sign_transaction(tx, funder.key).raw_transaction))
    if (k + 1) % 16 == 0:
        print(f"  sent {k+1}/{len(need)}")
w3.eth.wait_for_transaction_receipt(hashes[-1], timeout=300)
print(f"funded {len(need)} accounts in {time.perf_counter()-t0:.1f}s")

low = [i for i in range(N) if w3.eth.get_balance(acct(i).address) == 0]
print("all funded" if not low else f"STILL EMPTY: {low}")
