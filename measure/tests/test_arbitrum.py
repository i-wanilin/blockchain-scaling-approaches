# measure/tests/test_arbitrum.py
"""Open = deposit credited on the L2; Close = withdrawal submitted and included."""
import os
import unittest

from web3 import Web3

from measure import arbitrum, keys
from measure.tests.fake_w3 import FakeW3, fake_acct


class TestMinNonce(unittest.TestCase):
    """Task 10d: defensive plumbing (debug-late-offbyone.md pt. 4) -- Arbitrum's _send
    does the same independent RPC "pending" read as zkSync's, over a different
    connection than the submitter's WebSocket, so give it the same optional lower
    bound. max() keeps Arbitrum's behaviour identical when there is no collision."""

    def test_send_uses_min_nonce_when_rpc_pending_lags(self):
        w3, acct = FakeW3(pending_nonce=5, chain_id=arbitrum.L2_CHAIN_ID), fake_acct()
        arbitrum._send(w3, acct, {"from": acct.address, "to": acct.address, "value": 0, "data": "0x"},
                       min_nonce=8)
        self.assertEqual(acct.seen_nonces[-1], 8)

    def test_send_uses_rpc_pending_when_already_ahead_of_min_nonce(self):
        w3, acct = FakeW3(pending_nonce=10, chain_id=arbitrum.L2_CHAIN_ID), fake_acct()
        arbitrum._send(w3, acct, {"from": acct.address, "to": acct.address, "value": 0, "data": "0x"},
                       min_nonce=8)
        self.assertEqual(acct.seen_nonces[-1], 10)

    def test_send_without_min_nonce_keeps_old_behaviour(self):
        w3, acct = FakeW3(pending_nonce=3, chain_id=arbitrum.L2_CHAIN_ID), fake_acct()
        arbitrum._send(w3, acct, {"from": acct.address, "to": acct.address, "value": 0, "data": "0x"})
        self.assertEqual(acct.seen_nonces[-1], 3)

    def test_send_uses_fee_cap_multiplier(self):
        """The ceiling is FEE_CAP_MULTIPLIER * gas_price + 1, not a bare 2x: the charged
        price is the base fee (tip 0 on payments, tip 1 wei here only because L1 geth
        rejects a zero tip), so the ceiling just needs headroom for base-fee rises."""
        w3, acct = FakeW3(pending_nonce=1, chain_id=arbitrum.L2_CHAIN_ID), fake_acct()
        arbitrum._send(w3, acct, {"from": acct.address, "to": acct.address, "value": 0, "data": "0x"})
        self.assertEqual(acct.seen_txs[-1]["maxFeePerGas"], arbitrum.FEE_CAP_MULTIPLIER * w3.eth.gas_price + 1)

    def test_close_withdraw_uses_fee_cap_multiplier(self):
        w3, acct = FakeW3(pending_nonce=5, chain_id=arbitrum.L2_CHAIN_ID), fake_acct()
        arbitrum.close_withdraw(w3, acct, 100)
        self.assertEqual(acct.seen_txs[-1]["maxFeePerGas"], arbitrum.FEE_CAP_MULTIPLIER * w3.eth.gas_price + 1)

    def test_close_withdraw_forwards_min_nonce(self):
        w3, acct = FakeW3(pending_nonce=5, chain_id=arbitrum.L2_CHAIN_ID), fake_acct()
        arbitrum.close_withdraw(w3, acct, 100, min_nonce=9)
        self.assertEqual(acct.seen_nonces[-1], 9)

    def test_close_withdraw_uses_rpc_pending_when_already_ahead(self):
        w3, acct = FakeW3(pending_nonce=15, chain_id=arbitrum.L2_CHAIN_ID), fake_acct()
        arbitrum.close_withdraw(w3, acct, 100, min_nonce=9)
        self.assertEqual(acct.seen_nonces[-1], 15)

    def test_close_withdraw_without_min_nonce_keeps_old_behaviour(self):
        w3, acct = FakeW3(pending_nonce=5, chain_id=arbitrum.L2_CHAIN_ID), fake_acct()
        arbitrum.close_withdraw(w3, acct, 100)
        self.assertEqual(acct.seen_nonces[-1], 5)


class TestHttpUrl(unittest.TestCase):
    def test_ws_port_8548_maps_to_http_8547(self):
        self.assertEqual(arbitrum.http_url("ws://127.0.0.1:8548"), "http://127.0.0.1:8547")

    def test_wss_and_other_ports_unchanged(self):
        self.assertEqual(arbitrum.http_url("wss://host:443/x"), "https://host:443/x")
        self.assertEqual(arbitrum.http_url("ws://host:9000"), "http://host:9000")
        self.assertEqual(arbitrum.http_url("http://host:8548"), "http://host:8547")


@unittest.skipUnless(os.environ.get("ARB_L1") and os.environ.get("ARB_L2"), "needs the workstation Arbitrum stack")
class TestLifecycle(unittest.TestCase):
    def test_open_then_close_on_a_fresh_account(self):
        l1 = Web3(Web3.HTTPProvider(os.environ["ARB_L1"]))
        l2 = Web3(Web3.HTTPProvider(arbitrum.http_url(os.environ["ARB_L2"])))
        fresh = keys.account(190)                       # outside the run range
        start_l2 = l2.eth.get_balance(fresh.address)
        arbitrum.fund_on_l1(l1, keys.account(keys.FUNDER), fresh.address, 10**18)
        o = arbitrum.open_deposit(l1, l2, fresh, 5 * 10**17)
        self.assertEqual(o["l2_balance_wei"] - start_l2, 5 * 10**17)
        self.assertLess(o["t_l1_confirmed"], o["t_l2_credited"])
        self.assertGreater(o["l1_gas_used"], 0)
        print("OPEN", o["t_l2_credited"] - o["t_start"], o["l1_gas_used"], o["l1_fee_wei"])
        c = arbitrum.close_withdraw(l2, fresh, 10**17)
        print("CLOSE", c["t_confirmed"] - c["t_start"], c["l2_gas_used"], c["l2_fee_wei"])
        self.assertEqual(c["status"], 1)
        self.assertGreater(c["l2_fee_wei"], 0)
