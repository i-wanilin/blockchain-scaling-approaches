# measure/arbitrum.py
"""Arbitrum lifecycle for UC1 (parent spec §5.1, §11.4).

Open  = bridge ETH from the L1 with Inbox.depositEth and wait until the L2
        balance shows it (timed; on 2026-09-30 the workstation copy credited in
        1.9 s).
Close = withdrawal *submission* with ArbSys.withdrawEth on the L2 (timed). The
        7-day challenge period that follows is documentation, reported apart.
Funding the fresh account on the L1 before Open is setup, not measured.
"""
from __future__ import annotations

import time

INBOX = "0xA520303d332b27763E3518000861EdBc9843ceCc"
ARBSYS = "0x0000000000000000000000000000000000000064"
L1_CHAIN_ID, L2_CHAIN_ID = 1337, 412346
_DEPOSIT = "0x439370b1"          # depositEth()
_WITHDRAW = "0x25e16063"         # withdrawEth(address)

# Admission ceiling for maxFeePerGas, shared by arbitrum.py and zksync.py (zksync.py
# imports this constant rather than redefining it: one number, one rationale). Every
# payment and Close/withdraw here is signed with maxPriorityFeePerGas effectively 0
# (arbitrum/zksync payment_tx: tip 0; _send below: tip 1 wei, L1 only needs a nonzero
# tip), so the charged price is always the base fee -- this constant only decides
# admission (whether a tx is accepted into a block), never what is paid. It only needs
# headroom for the base fee rising during a run. On 2026-10-04, run
# 20261004-223042-arbitrum-diag-arbitrum5own-w10-5ghz-mixed-rep1 showed Arbitrum's L2
# base fee (ArbOS congestion pricing) rising 0.100 -> 0.2006 gwei at ~384 payments/s; a
# fixed 2x cap taken once at the start of the run was no longer enough partway through
# a 15-minute run, and 78 012 payments plus one Close were rejected with "max fee per
# gas less than block base fee". 10x gives headroom for a rise like that over a full run.
# Balance check (the cap must stay much smaller than the deposit, even though it is
# never actually paid): gas_limit * FEE_CAP_MULTIPLIER * gas_price, at a 1 gwei gas
# price -- Arbitrum payment 31_500 gas * 10 * 1e9 ~= 3.15e14 wei vs a ~1 ETH deposit;
# zkSync payment 400_000 gas * 10 * 1e9 ~= 4e15 wei vs a >= 1 ETH deposit. Both leave
# many orders of magnitude of margin.
FEE_CAP_MULTIPLIER = 10


def http_url(ws_url: str) -> str:
    """HTTP endpoint for an L2 WebSocket URL: ws->http, wss->https, port 8548->8547."""
    url = ws_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    return url.replace(":8548", ":8547", 1)


def _send(w3, acct, tx, min_nonce=None):
    nonce = w3.eth.get_transaction_count(acct.address, "pending")
    if min_nonce is not None:
        nonce = max(nonce, min_nonce)                     # guard: a lagging read must not go below it
    tx = dict(tx, nonce=nonce, chainId=w3.eth.chain_id,
              maxFeePerGas=FEE_CAP_MULTIPLIER * w3.eth.gas_price + 1, maxPriorityFeePerGas=1,
              type=2)  # L1 geth rejects a zero tip; this _send also does the L2 Close (ArbSys.withdrawEth)
    tx.setdefault("gas", int(w3.eth.estimate_gas({k: tx[k] for k in ("from", "to", "value", "data") if k in tx}) * 1.5))
    h = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    return w3.eth.wait_for_transaction_receipt(h, timeout=300, poll_latency=0.05)


def fund_on_l1(l1, funder, to, amount_wei):
    r = _send(l1, funder, {"from": funder.address, "to": to, "value": amount_wei, "gas": 21000})
    if r["status"] != 1:
        raise RuntimeError("L1 funding reverted")


def open_deposit(l1, l2, acct, amount_wei) -> dict:
    before = l2.eth.get_balance(acct.address)
    t_start = time.time()
    r = _send(l1, acct, {"from": acct.address, "to": INBOX, "value": amount_wei, "data": _DEPOSIT})
    t_l1 = time.time()
    if r["status"] != 1:
        raise RuntimeError("depositEth reverted")
    while True:
        bal = l2.eth.get_balance(acct.address)
        if bal - before >= amount_wei:
            break
        if time.time() - t_start > 600:
            raise TimeoutError("deposit not credited on the L2 within 600 s")
        time.sleep(0.05)
    return {"t_start": t_start, "t_l1_confirmed": t_l1, "t_l2_credited": time.time(),
            "l1_gas_used": r["gasUsed"], "l1_fee_wei": r["gasUsed"] * r["effectiveGasPrice"],
            "l2_balance_wei": bal}


def estimate_payment_gas(l2, frm, to) -> int:
    return int(l2.eth.estimate_gas({"from": frm, "to": to, "value": 1}) * 1.5)


def payment_tx(to, amount_wei, gas, max_fee) -> dict:
    return {"to": to, "value": amount_wei, "gas": gas, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": 0}


def close_withdraw(l2, acct, amount_wei, min_nonce=None) -> dict:
    """min_nonce: same lower bound as zksync.close_withdraw -- the node's pending nonce
    after payclient's bounded admission wait. Arbitrum's _send does the same independent
    RPC "pending" read as zkSync's, so the same rule applies: >= the submitter's next nonce
    when every sent payment was admitted, the node's own value (no gap) when some were
    dropped (run 20261003-024642-arbitrum-uc1-arbitrum5-w10-rep3 failed with "nonce too
    high" when Close was given the submitter's nonce instead)."""
    t_start = time.time()
    data = _WITHDRAW + acct.address[2:].lower().rjust(64, "0")
    r = _send(l2, acct, {"from": acct.address, "to": ARBSYS, "value": amount_wei, "data": data},
              min_nonce=min_nonce)
    return {"t_start": t_start, "t_confirmed": time.time(), "status": r["status"],
            "l2_gas_used": r["gasUsed"], "l2_fee_wei": r["gasUsed"] * r["effectiveGasPrice"]}
