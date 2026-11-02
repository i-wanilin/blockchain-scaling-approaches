"""Open = deposit credited on zkSync's L2; Close = withdrawal included; payments as for Arbitrum."""
import os
import unittest

from web3 import Web3

from measure import keys, zksync
from measure.tests.fake_w3 import FakeW3, fake_acct


class TestMinNonce(unittest.TestCase):
    """Task 10d: Close's nonce must never collide with a payment nonce the submitter
    already issued. See the cluster spec §8 step 3, "Close-nonce collision"."""

    def test_send_uses_min_nonce_when_rpc_pending_lags(self):
        w3, acct = FakeW3(pending_nonce=5, chain_id=zksync.L2_CHAIN_ID), fake_acct()
        r, tx = zksync._send(w3, acct, {"from": acct.address, "to": acct.address, "value": 0, "data": "0x"},
                             min_nonce=8)
        self.assertEqual(tx["nonce"], 8)
        self.assertEqual(acct.seen_nonces[-1], 8)

    def test_send_uses_rpc_pending_when_already_ahead_of_min_nonce(self):
        w3, acct = FakeW3(pending_nonce=10, chain_id=zksync.L2_CHAIN_ID), fake_acct()
        r, tx = zksync._send(w3, acct, {"from": acct.address, "to": acct.address, "value": 0, "data": "0x"},
                             min_nonce=8)
        self.assertEqual(tx["nonce"], 10)

    def test_send_without_min_nonce_keeps_old_behaviour(self):
        w3, acct = FakeW3(pending_nonce=3, chain_id=zksync.L2_CHAIN_ID), fake_acct()
        r, tx = zksync._send(w3, acct, {"from": acct.address, "to": acct.address, "value": 0, "data": "0x"})
        self.assertEqual(tx["nonce"], 3)

    def test_send_uses_fee_cap_multiplier(self):
        """Same ceiling as arbitrum.py (shared FEE_CAP_MULTIPLIER): the charged price is
        the base fee (zkSync ignores the priority fee), so the ceiling only needs
        headroom for base-fee rises, not a bare 2x."""
        w3, acct = FakeW3(pending_nonce=1, chain_id=zksync.L2_CHAIN_ID), fake_acct()
        r, tx = zksync._send(w3, acct, {"from": acct.address, "to": acct.address, "value": 0, "data": "0x"})
        self.assertEqual(tx["maxFeePerGas"], zksync.FEE_CAP_MULTIPLIER * w3.eth.gas_price + 1)

    def test_close_withdraw_uses_fee_cap_multiplier(self):
        w3, acct = FakeW3(pending_nonce=5, chain_id=zksync.L2_CHAIN_ID), fake_acct()
        c = zksync.close_withdraw(w3, acct, 100)
        self.assertEqual(acct.seen_txs[-1]["maxFeePerGas"], zksync.FEE_CAP_MULTIPLIER * w3.eth.gas_price + 1)

    def test_close_withdraw_forwards_min_nonce(self):
        w3, acct = FakeW3(pending_nonce=5, chain_id=zksync.L2_CHAIN_ID), fake_acct()
        c = zksync.close_withdraw(w3, acct, 100, min_nonce=9)
        self.assertEqual(c["nonce"], 9)

    def test_close_withdraw_uses_rpc_pending_when_already_ahead(self):
        w3, acct = FakeW3(pending_nonce=15, chain_id=zksync.L2_CHAIN_ID), fake_acct()
        c = zksync.close_withdraw(w3, acct, 100, min_nonce=9)
        self.assertEqual(c["nonce"], 15)

    def test_close_withdraw_without_min_nonce_keeps_old_behaviour(self):
        w3, acct = FakeW3(pending_nonce=5, chain_id=zksync.L2_CHAIN_ID), fake_acct()
        c = zksync.close_withdraw(w3, acct, 100)
        self.assertEqual(c["nonce"], 5)


class TestPure(unittest.TestCase):
    def test_http_url(self):
        self.assertEqual(zksync.http_url("ws://192.168.178.210:3051"), "http://192.168.178.210:3050")

    def test_selectors(self):
        self.assertEqual((zksync._REQUEST_DIRECT, zksync._BASE_COST, zksync._WITHDRAW),
                         ("0xd52471c1", "0x71623274", "0x51cff8d9"))

    def test_deposit_calldata_roundtrip(self):
        data = zksync.deposit_calldata(271, 5 * 10**17 + 123, "0x" + "11" * 20, 5 * 10**17, 300000)
        self.assertTrue(data.startswith("0xd52471c1"))
        # decoded by eth_abi back to the same tuple
        from eth_abi import decode
        (t,) = decode(["(uint256,uint256,address,uint256,bytes,uint256,uint256,bytes[],address)"],
                      bytes.fromhex(data[10:]))
        self.assertEqual(t[0], 271); self.assertEqual(t[3], 5 * 10**17); self.assertEqual(t[6], 800)

    def test_payment_gas_is_set(self):
        self.assertGreater(zksync.PAYMENT_GAS, 0)


@unittest.skipUnless(os.environ.get("ZK_L1") and os.environ.get("ZK_L2"), "needs the workstation zkSync stack")
class TestLifecycle(unittest.TestCase):
    def test_open_then_close_on_a_fresh_account(self):
        l1 = Web3(Web3.HTTPProvider(os.environ["ZK_L1"]))
        l2 = Web3(Web3.HTTPProvider(zksync.http_url(os.environ["ZK_L2"])))
        fresh = keys.account(191)                       # outside the run range
        start_l2 = l2.eth.get_balance(fresh.address)
        zksync.fund_on_l1(l1, zksync.funder(), fresh.address, 2 * 10**18)
        o = zksync.open_deposit(l1, l2, fresh, 5 * 10**17)
        self.assertGreaterEqual(o["l2_balance_wei"] - start_l2, 5 * 10**17)   # plus the base-cost refund
        self.assertLess(o["t_l1_confirmed"], o["t_l2_credited"])
        self.assertGreater(o["base_cost_wei"], 0)
        c = zksync.close_withdraw(l2, fresh, 10**17)
        self.assertEqual(c["status"], 1)
        self.assertGreater(c["l2_fee_wei"], 0)
