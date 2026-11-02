"""zkSync Era lifecycle for UC1 (parent spec §5.1, §11.4), same interface as arbitrum.py.

Open  = bridge ETH from the L1 with Bridgehub.requestL2TransactionDirect (the call
        zksync-ethers' deposit makes for an ETH-based chain, verified in the UC1
        zkSync facts F16) and wait until the L2 balance shows it (timed).
        mintValue = amount + base cost; the unused part of the base cost is refunded
        to the same account, so the credit check is "delta >= amount".
Payment = one EIP-1559 ETH transfer, fixed generous gas limit (cluster spec §7.3:
        zkSync's gas varies with pubdata pricing, 83 334 / 127 499 for identical
        transfers). zkSync ignores the priority fee; the effective price is the base
        fee (F15). eth_sendRawTransaction answers after mempool admission, before
        sequencing (F10), so the pipelined submitter needs no nonce-gap handling in
        practice; the per-account bound of 20 in flight comes from the caller.
Close = withdrawal *submission* with L2BaseToken.withdraw(address) at 0x...800A,
        timed to its L2 receipt. Batch commit, proof and execution are documentation.
        zksync-ethers sends this as an EIP-712 type 0x71 tx; a type-2 withdraw(address)
        to 0x...800A was tested in Task 1 (F16) and is accepted, so type 2 is kept here
        (same as the plan's `_send`, same as Arbitrum's signer).
The L1 funder is the ecosystem deployer (the only rich account in the reth genesis
that zkstack does not use at run time); its key is the public zksync-era dev key.
Local dev chains only.
"""
from __future__ import annotations

import time

from eth_abi import encode
from eth_account import Account

from measure.arbitrum import FEE_CAP_MULTIPLIER  # shared constant; see arbitrum.py for the rationale

L1_CHAIN_ID, L2_CHAIN_ID = 9, 271
BRIDGEHUB = "0xE965F94Cd784d274E6970F603576265B17CfA3f4"          # snapshot contracts.yaml; F16
L2_BASE_TOKEN = "0x000000000000000000000000000000000000800A"      # F16
GAS_PER_PUBDATA = 800                                              # F16
PAYMENT_GAS = 400_000  # 2x the largest F15 estimate (175 690), rounded up for margin; w10 budget 90 000x(1e12+4e13 wei) ~= 3.7 ETH < 5 ETH deposit at gas price 1e8
DEPLOYER_KEY = "0x7726827caac94a7f9e1b160f7ea819f172f7b6f9d2a97f992c38edeab82d4110"
_REQUEST_DIRECT = "0xd52471c1"   # requestL2TransactionDirect((uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address))
_BASE_COST = "0x71623274"        # l2TransactionBaseCost(uint256,uint256,uint256,uint256)
_WITHDRAW = "0x51cff8d9"         # withdraw(address)
SUBMITTER_KW = {                 # texts from F12; defaults kept where zkSync uses the same words
    "accepted_texts": ("already known", "known transaction"),
    "retry_texts": ("nonce too high",),
    "low_texts": ("nonce too low",),
    "receipt_sweep_s": 10.0,     # newHeads + per-head receipts is the main path (F14)
    "receipt_mode": "own",       # per head: tx hashes, then only own receipts (task 10c: Wi-Fi bursts)
}

# Gas limit of one PointsToken.transfer (UC2), measured 2026-10-06 on the workstation chain
# (Task 6, uc2_setup --sample-transfers 20, p0 -> arena/p1..p5 round-robin, 1 point each):
# eth_estimateGas min 162 868 / median 162 868 / max 164 287 (the first one, to the empty arena
# slot); receipt gasUsed min 91 029 / median 136 199 / max 136 919. 2 x 164 287 = 328 574, rounded
# up to 10 000. Fee cap per transfer 330 000 x FEE_CAP_MULTIPLIER x 1e8 wei = 3.3e14 wei.
TOKEN_PAYMENT_GAS = 330_000


def http_url(ws_url: str) -> str:
    url = ws_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    return url.replace(":3051", ":3050", 1)


def funder():
    return Account.from_key(DEPLOYER_KEY)


def _send(w3, acct, tx, timeout=300, min_nonce=None):
    nonce = w3.eth.get_transaction_count(acct.address, "pending")
    if min_nonce is not None:
        nonce = max(nonce, min_nonce)                     # guard: a lagging read must not go below it
    tx = dict(tx, nonce=nonce, chainId=w3.eth.chain_id,
              maxFeePerGas=FEE_CAP_MULTIPLIER * w3.eth.gas_price + 1, maxPriorityFeePerGas=1, type=2)
    tx.setdefault("gas", int(w3.eth.estimate_gas({k: tx[k] for k in ("from", "to", "value", "data") if k in tx}) * 1.5))
    h = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    r = w3.eth.wait_for_transaction_receipt(h, timeout=timeout, poll_latency=0.05)
    return r, tx


def fund_on_l1(l1, funder_acct, to, amount_wei):
    r, _ = _send(l1, funder_acct, {"from": funder_acct.address, "to": to, "value": amount_wei, "gas": 21000})
    if r["status"] != 1:
        raise RuntimeError("L1 funding reverted")


def deposit_calldata(chain_id, mint_value, to, l2_value, l2_gas_limit) -> str:
    args = encode(["(uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address)"],
                  [(chain_id, mint_value, to, l2_value, b"", l2_gas_limit, GAS_PER_PUBDATA, [], to)])
    return _REQUEST_DIRECT + args.hex()


def _l2_gas_limit(l2, acct, amount_wei) -> int:
    est = l2.provider.make_request("zks_estimateGasL1ToL2",
                                   [{"from": acct.address, "to": acct.address, "value": hex(amount_wei)}])
    return int(est["result"], 16)


def _base_cost(l1, gas_price, l2_gas_limit) -> int:
    data = _BASE_COST + encode(["uint256", "uint256", "uint256", "uint256"],
                               [L2_CHAIN_ID, gas_price, l2_gas_limit, GAS_PER_PUBDATA]).hex()
    return int(l1.eth.call({"to": BRIDGEHUB, "data": data}).hex(), 16)


def open_deposit(l1, l2, acct, amount_wei) -> dict:
    before = l2.eth.get_balance(acct.address)
    t_start = time.time()
    l2_gas = _l2_gas_limit(l2, acct, amount_wei)
    max_fee = FEE_CAP_MULTIPLIER * l1.eth.gas_price + 1  # the contract prices with tx.gasprice <= max_fee;
    # same formula _send below applies to this tx's own maxFeePerGas, so the base cost stays consistent
    # with what is actually signed (both read l1.eth.gas_price independently, as before; steady on a dev chain)
    base = _base_cost(l1, max_fee, l2_gas)
    mint = base + amount_wei
    r, _ = _send(l1, acct, {"from": acct.address, "to": BRIDGEHUB, "value": mint,
                             "data": deposit_calldata(L2_CHAIN_ID, mint, acct.address, amount_wei, l2_gas)})
    t_l1 = time.time()
    if r["status"] != 1:
        raise RuntimeError("requestL2TransactionDirect reverted")
    while True:
        bal = l2.eth.get_balance(acct.address)
        if bal - before >= amount_wei:
            break
        if time.time() - t_start > 600:
            raise TimeoutError("deposit not credited on the L2 within 600 s")
        time.sleep(0.05)
    return {"t_start": t_start, "t_l1_confirmed": t_l1, "t_l2_credited": time.time(),
            "l1_gas_used": r["gasUsed"], "l1_fee_wei": r["gasUsed"] * r["effectiveGasPrice"],
            "l2_balance_wei": bal, "mint_value_wei": mint, "base_cost_wei": base, "l2_gas_limit": l2_gas}


def estimate_payment_gas(l2, frm, to) -> int:
    if not PAYMENT_GAS:
        raise RuntimeError("zksync.PAYMENT_GAS not set (Task 3, from F15)")
    return PAYMENT_GAS


def payment_tx(to, amount_wei, gas, max_fee) -> dict:
    return {"to": to, "value": amount_wei, "gas": gas, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": 0}


def close_withdraw(l2, acct, amount_wei, timeout=300, min_nonce=None) -> dict:
    """min_nonce: a lower bound for Close's nonce. payclient passes the node's pending
    nonce read after its bounded admission wait (payclient.wait_for_admission): that
    is >= the submitter's next_nonce when the node admitted every sent payment (the
    task 10d race, debug-late-offbyone.md: Close must not reuse a payment's nonce),
    and the node's own value when some were not admitted by then, so it never leaves a
    nonce gap ("nonce too high"). Those may be dropped or, on zkSync, queued behind a
    missing nonce and mined right after Close; payclient's post-Close settle counts the
    latter (executed_after_close). _send's own pending read is later than the bound, so
    max() only guards against a lagging read and never raises the nonce above the node's."""
    t_start = time.time()
    data = _WITHDRAW + acct.address[2:].lower().rjust(64, "0")
    r, tx = _send(l2, acct, {"from": acct.address, "to": L2_BASE_TOKEN, "value": amount_wei, "data": data},
                  timeout=timeout, min_nonce=min_nonce)
    return {"t_start": t_start, "t_confirmed": time.time(), "status": r["status"], "nonce": tx["nonce"],
            "l2_gas_used": r["gasUsed"], "l2_fee_wei": r["gasUsed"] * r["effectiveGasPrice"]}
