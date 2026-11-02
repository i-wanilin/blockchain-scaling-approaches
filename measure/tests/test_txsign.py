"""The direct coincurve signer produces the same bytes as eth_account."""
import unittest

from measure import keys
from measure.txsign import Eip1559Signer

TO = keys.account(100).address


class TestTxSign(unittest.TestCase):
    def check(self, **extra):
        acct = keys.account(101)
        signer = Eip1559Signer(acct.key)
        for nonce in (0, 1, 5, 127, 128, 300, 70000):
            for value in (0, 1, 10**18):
                tx = {"to": TO, "value": value, "gas": 21000, "maxFeePerGas": 2 * 10**8,
                      "maxPriorityFeePerGas": 0, "nonce": nonce, "chainId": 412346, **extra}
                ref = acct.sign_transaction(dict(tx, type=2))
                raw, h = signer.sign(tx)
                self.assertEqual(raw, "0x" + ref.raw_transaction.hex().removeprefix("0x"), (nonce, value))
                self.assertEqual(h, "0x" + ref.hash.hex().removeprefix("0x"))

    def test_transfer_matches_eth_account(self):
        self.check()

    def test_calldata_matches_eth_account(self):
        self.check(data=bytes.fromhex("a9059cbb" + "00" * 31 + "01" + "00" * 30 + "ff00"))

    def test_bad_input_raises(self):
        signer = Eip1559Signer(keys.account(101).key)
        with self.assertRaises(KeyError):
            signer.sign({"to": TO, "nonce": 0, "chainId": 1})
        with self.assertRaises(ValueError):
            signer.sign({"to": "0x1234", "nonce": 0, "chainId": 1, "gas": 1, "maxFeePerGas": 1,
                         "maxPriorityFeePerGas": 0})


if __name__ == "__main__":
    unittest.main()
