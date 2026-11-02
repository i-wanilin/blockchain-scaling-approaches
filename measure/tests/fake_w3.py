# measure/tests/fake_w3.py
"""Shared fakes for zksync._send / arbitrum._send unit tests (task 10d: Close's nonce
must never collide with a payment nonce the submitter already issued). A minimal
w3.eth stand-in with a configurable "pending" nonce read, independent of whatever
nonce the caller wants to force with min_nonce, and a signing-account spy that
records the nonce it was asked to sign. No network, no node.

Not itself a test module (no test_ prefix): imported by measure/tests/test_zksync.py
and measure/tests/test_arbitrum.py.
"""
from eth_account import Account


class FakeEth:
    def __init__(self, pending_nonce, chain_id, gas_price=10**8):
        self.pending_nonce, self.chain_id, self.gas_price = pending_nonce, chain_id, gas_price
        self.sent = []

    def get_transaction_count(self, addr, block):
        assert block == "pending"
        return self.pending_nonce

    def estimate_gas(self, tx):
        return 21000

    def send_raw_transaction(self, raw):
        self.sent.append(raw)
        return b"\x11" * 32

    def wait_for_transaction_receipt(self, h, timeout=300, poll_latency=0.05):
        return {"status": 1, "gasUsed": 21000, "effectiveGasPrice": self.gas_price}


class FakeW3:
    def __init__(self, pending_nonce, chain_id):
        self.eth = FakeEth(pending_nonce, chain_id)


class SpyAcct:
    """Wraps a real (offline-signing) account and records the nonce it was asked to sign."""

    def __init__(self, acct):
        self._acct, self.address = acct, acct.address
        self.seen_nonces = []
        self.seen_txs = []       # full tx dict, e.g. for asserting maxFeePerGas (FEE_CAP_MULTIPLIER)

    def sign_transaction(self, tx):
        self.seen_nonces.append(tx["nonce"])
        self.seen_txs.append(tx)
        return self._acct.sign_transaction(tx)


def fake_acct():
    return SpyAcct(Account.create())
