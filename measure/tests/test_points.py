import json, os, tempfile, unittest

from measure import keys, points
from measure.events import PaymentEvent


class PointsTest(unittest.TestCase):
    def test_transfer_calldata(self):
        d = points.transfer_data("0x00000000000000000000000000000000000000aa", 5)
        self.assertEqual(d, "0xa9059cbb" + "0" * 62 + "aa" + "0" * 63 + "5")

    def test_token_tx_maps_logical_recipient(self):
        rec = {"arena": "0x" + "11" * 20, **{f"p{k}": "0x" + f"{k + 2:02x}" * 20 for k in range(6)}}
        tx = points.token_tx("0x" + "ee" * 20, rec, PaymentEvent("i", "p3", 5, 0.0, 0.0), 90000, 7)
        self.assertEqual(tx["to"], "0x" + "ee" * 20)
        self.assertEqual(tx["value"], 0)
        self.assertTrue(tx["data"].endswith("5"))
        self.assertIn("05" * 20, tx["data"])

    def test_unknown_recipient_raises(self):
        with self.assertRaises(KeyError):
            points.token_tx("0x" + "ee" * 20, {"arena": "0x" + "11" * 20}, PaymentEvent("i", "p9", 5, 0, 0), 1, 1)

    def test_recipients_file_must_be_complete(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.json")
            json.dump({"arena": "0x" + "11" * 20, "p0": "0x" + "22" * 20}, open(p, "w"))
            self.assertEqual(set(points.load_recipients(p, n_players=1)), {"arena", "p0"})
            with self.assertRaises(ValueError):
                points.load_recipients(p, n_players=2)

    def test_uc2_account_indices(self):
        self.assertEqual((keys.ARENA, keys.player_index(0), keys.player_index(5), keys.TOKEN_DEPLOYER), (110, 111, 116, 119))

    def test_balance_of_calldata(self):
        self.assertEqual(points.balance_of_data("0x00000000000000000000000000000000000000aa"),
                         "0x70a08231" + "0" * 62 + "aa")

    def test_token_gas(self):
        import types
        from measure import zksync
        l2 = types.SimpleNamespace(eth=types.SimpleNamespace(estimate_gas=lambda tx: 40000))
        self.assertEqual(points.token_gas(types.SimpleNamespace(), l2, "0x1", "0x2", "0x3"), 60000)
        self.assertEqual(points.token_gas(zksync, l2, "0x1", "0x2", "0x3"), zksync.TOKEN_PAYMENT_GAS)
        from unittest import mock
        with mock.patch.object(zksync, "TOKEN_PAYMENT_GAS", None):
            with self.assertRaisesRegex(RuntimeError, "TOKEN_PAYMENT_GAS not set"):
                points.token_gas(zksync, l2, "0x1", "0x2", "0x3")
