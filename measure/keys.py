"""Deterministic test accounts from the public development mnemonic.

Index 0 funds the L1; 100 is the UC1 sensor, 101..106 are customers 1..6.
UC2: 110 is the arena, 111..116 are players 0..5, 119 deploys the PointsToken.
Local dev chains only -- this mnemonic is public.
"""
from eth_account import Account

Account.enable_unaudited_hdwallet_features()
MNEMONIC = "test test test test test test test test test test test junk"
FUNDER, SENSOR = 0, 100
ARENA, UC2_PLAYER0, TOKEN_DEPLOYER = 110, 111, 119


def customer_index(n: int) -> int:
    """Customer n (1-based) -> account index."""
    return SENSOR + n


def player_index(k: int) -> int:
    """UC2 player k (0-based) -> account index."""
    return UC2_PLAYER0 + k


def account(index: int):
    return Account.from_mnemonic(MNEMONIC, account_path=f"m/44'/60'/0'/0/{index}")
