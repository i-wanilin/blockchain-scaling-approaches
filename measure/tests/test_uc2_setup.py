"""UC2 rollup setup: recipients, the committed EraVM bytecode, and (on a local
stack) the deploy + mintBatch path end to end."""
import hashlib
import json
import os
import tempfile
import unittest

from web3 import Web3

from measure import keys, points, uc2_setup


def _check_stack(test, stack, l1, l2_http):
    with tempfile.TemporaryDirectory() as d:
        uc2_setup.setup(stack, l1, l2_http, players=6, points=10000, fund_wei=10**18, out=d)
        with open(os.path.join(d, "token.json")) as f:
            tok = json.load(f)
        test.assertEqual(tok["stack"], stack)
        test.assertEqual(tok["points"], 10000)
        test.assertEqual(tok["deployer"], keys.account(keys.TOKEN_DEPLOYER).address)
        rec = points.load_recipients(os.path.join(d, "recipients.json"), 6)
        l2 = Web3(Web3.HTTPProvider(l2_http))
        for k in range(6):
            test.assertEqual(uc2_setup.balance(l2, tok["token"], rec[f"p{k}"]), 10000)
        test.assertEqual(uc2_setup.balance(l2, tok["token"], rec["arena"]), 0)
        print(stack, "TOKEN", json.dumps(tok))


class SetupTest(unittest.TestCase):
    def test_recipients_follow_the_account_indices(self):  # arena=110, p_k=111+k
        rec = uc2_setup.recipients(6)
        self.assertEqual(list(rec), ["arena", "p0", "p1", "p2", "p3", "p4", "p5"])
        self.assertEqual(rec["arena"], keys.account(110).address)
        for k in range(6):
            self.assertEqual(rec[f"p{k}"], keys.account(111 + k).address)

    def test_recipients_file_is_accepted_by_the_payment_client(self):
        with tempfile.TemporaryDirectory() as d:
            p = uc2_setup.write_recipients(d, 6)
            self.assertEqual(points.load_recipients(p, 6), uc2_setup.recipients(6))

    def test_zbin_hash_matches(self):                        # sha256 of the committed zbin == .sha256 file
        with open(uc2_setup.ZBIN, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        with open(uc2_setup.ZBIN + ".sha256") as f:
            self.assertEqual(f.read().strip(), digest)
        self.assertEqual(uc2_setup.check_zbin(), digest)

    def test_zbin_hash_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            zbin = os.path.join(d, "x.zbin")
            with open(zbin, "w") as f:
                f.write("00")
            with open(zbin + ".sha256", "w") as f:
                f.write("0" * 64 + "\n")
            with self.assertRaisesRegex(RuntimeError, "sha256"):
                uc2_setup.check_zbin(zbin)

    def test_mint_batch_calldata(self):
        a, b = "0x" + "aa" * 20, "0x" + "bb" * 20
        d = uc2_setup.mint_batch_data([a, b], 10000)
        self.assertEqual(d[:10], uc2_setup.MINT_BATCH)
        self.assertEqual(uc2_setup.MINT_BATCH, "0x" + Web3.keccak(text="mintBatch(address[],uint256)")[:4].hex().removeprefix("0x"))
        words = [d[10 + 64 * i: 10 + 64 * (i + 1)] for i in range((len(d) - 10) // 64)]
        self.assertEqual([int(w, 16) for w in words[:3]], [0x40, 10000, 2])   # offset, amount, length
        self.assertEqual(words[3], "0" * 24 + "aa" * 20)
        self.assertEqual(words[4], "0" * 24 + "bb" * 20)

    @unittest.skipUnless(os.environ.get("ARB_L1") and os.environ.get("ARB_L2_HTTP"), "needs the workstation Arbitrum stack")
    def test_setup_against_local_arbitrum(self):             # token.json written, 6 balances == 10000, arena 0
        _check_stack(self, "arbitrum", os.environ["ARB_L1"], os.environ["ARB_L2_HTTP"])

    @unittest.skipUnless(os.environ.get("ZK_L1") and os.environ.get("ZK_L2_HTTP"), "needs the workstation zkSync stack")
    def test_setup_against_local_zksync(self):               # the same
        _check_stack(self, "zksync", os.environ["ZK_L1"], os.environ["ZK_L2_HTTP"])


if __name__ == "__main__":
    unittest.main()
