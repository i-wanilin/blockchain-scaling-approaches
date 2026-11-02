"""UC2 points on the rollups: one ERC-20 PointsToken.transfer per payment event.

The game names recipients logically ("arena", "p<k>"); the payment client maps
them to addresses from a recipients file and pays points (token units, carried
in the event's amount_wei field) with transfer(to, amount) on the token
contract. Calldata is built by hand, as in arbitrum.py, so no ABI or contract
object sits on the hot path.
"""
from __future__ import annotations

import json

from web3 import Web3

TRANSFER = "0xa9059cbb"          # transfer(address,uint256)
BALANCE_OF = "0x70a08231"        # balanceOf(address)


def _word_addr(addr: str) -> str:
    return addr[2:].lower().rjust(64, "0")


def transfer_data(to_addr: str, amount: int) -> str:
    return TRANSFER + _word_addr(to_addr) + format(amount, "x").rjust(64, "0")


def balance_of_data(addr: str) -> str:
    return BALANCE_OF + _word_addr(addr)


def load_recipients(path: str, n_players: int) -> dict:
    """Logical name -> checksummed address. A file that lacks "arena" or any of
    p0..p<n_players-1> is refused: a missing recipient would fail payments one
    by one in the middle of a run instead of up front."""
    with open(path) as f:
        raw = json.load(f)
    need = ["arena"] + [f"p{k}" for k in range(n_players)]
    missing = [n for n in need if n not in raw]
    if missing:
        raise ValueError(f"recipients file {path} lacks: {', '.join(missing)}")
    return {n: Web3.to_checksum_address(a) for n, a in raw.items()}


def token_tx(token: str, recipients: dict, ev, gas: int, max_fee: int) -> dict:
    """The transfer for one event. An unknown ev.to raises KeyError; pay_loop
    records that as a failed row ("submit failed"), never a silent skip."""
    return {"to": token, "value": 0, "data": transfer_data(recipients[ev.to], ev.amount_wei),
            "gas": gas, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": 0}


def token_gas(system_module, l2, frm: str, token: str, to_addr: str) -> int:
    """Gas limit for one transfer. zkSync: the constant measured for it (a
    missing one is a failure, as with PAYMENT_GAS); Arbitrum: the node's
    estimate with the same 1.5x margin as estimate_payment_gas."""
    if hasattr(system_module, "TOKEN_PAYMENT_GAS"):
        if not system_module.TOKEN_PAYMENT_GAS:
            raise RuntimeError("zksync.TOKEN_PAYMENT_GAS not set")
        return system_module.TOKEN_PAYMENT_GAS
    return int(l2.eth.estimate_gas({"from": frm, "to": token, "data": transfer_data(to_addr, 1)}) * 1.5)
