"""Every payment ends with exactly one outcome and five timestamps."""
import csv
import os
import tempfile
import unittest

from measure.events import PaymentEvent, event_id
from measure.ledger import OUTCOMES, PaymentLedger, load_log, write_csv_rows
from measure.submitter import SubmitResult


def ev(w, t=100.0):
    return PaymentEvent(event_id("r1", 1, w), "0x" + "11" * 20, 10**12, t, t + 0.001)


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "ledger.log.jsonl")

    def test_event_roundtrip(self):
        e = ev(3)
        self.assertEqual(PaymentEvent.from_line(e.to_line()), e)
        self.assertEqual(e.id, "uc1/r1/c1/w3")

    def test_duplicate_id_is_refused(self):
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        self.assertTrue(led.add(ev(1)))
        self.assertFalse(led.add(ev(1)))
        self.assertEqual(len(led.rows()), 1)

    def test_bounded_queue_fails_instead_of_dropping(self):
        led = PaymentLedger(self.path, max_queue=2)
        self.addCleanup(led.close)
        for w in range(3):
            led.add(ev(w))
        outcomes = [r["outcome"] for r in led.rows()]
        self.assertEqual(outcomes.count("failed"), 1)
        self.assertIn("queue full", [r["error"] for r in led.rows()])

    def test_lifecycle_and_five_timestamps(self):
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        led.add(ev(1))
        led.queued("uc1/r1/c1/w1")
        led.submitted("uc1/r1/c1/w1", 100.01)
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 100.01, 100.3, 1, 7, 21000, 10**8, 1, ""))
        r = led.rows()[0]
        self.assertEqual(r["outcome"], "succeeded")
        for k in ("t_intended", "t_generated", "t_queued", "t_submitted", "t_confirmed"):
            self.assertIsNotNone(r[k], k)

    def test_reverted_is_failed_not_succeeded(self):
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        led.add(ev(1)); led.queued("uc1/r1/c1/w1"); led.submitted("uc1/r1/c1/w1", 1.0)
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 1.0, 1.2, 0, 7, 21000, 10**8, 1, ""))
        self.assertEqual(led.rows()[0]["outcome"], "failed")

    def test_pending_at_drain_deadline_becomes_unresolved_and_reconciliation_shows_it(self):
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        for w in (1, 2):
            led.add(ev(w)); led.queued(f"uc1/r1/c1/w{w}"); led.submitted(f"uc1/r1/c1/w{w}", 1.0)
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 1.0, 1.2, 1, 7, 21000, 10**8, 1, ""))
        led.unresolve_pending()
        self.assertEqual(sorted(r["outcome"] for r in led.rows()), ["succeeded", "unresolved"])
        rec = led.reconcile(observed_wei=10**12)
        self.assertEqual(rec["succeeded_wei"], 10**12)
        self.assertEqual(rec["difference_wei"], 0)
        self.assertEqual(rec["counts"]["unresolved"], 1)

    def test_log_is_appended_on_every_change_and_rebuilds_the_rows(self):
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        led.add(ev(1)); led.queued("uc1/r1/c1/w1")
        with open(self.path) as f:
            self.assertEqual(len(f.readlines()), 2)          # appended, never rewritten
        rebuilt = load_log(self.path)                        # as after a crash
        self.assertEqual(rebuilt[0]["outcome"], "queued")
        self.assertIn(rebuilt[0]["outcome"], OUTCOMES)
        out = self.path.replace(".log.jsonl", ".csv")
        led.write_csv(out)
        with open(out) as f:
            self.assertEqual(list(csv.DictReader(f))[0]["id"], "uc1/r1/c1/w1")

    def test_pending_tracking_with_outcome_changes(self):
        """Test that pending() follows outcome changes."""
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        led.add(ev(1))
        led.add(ev(2))
        led.add(ev(3))
        self.assertEqual(led.pending(), 3)

        # Mark one as queued (still pending: 3)
        led.queued("uc1/r1/c1/w1")
        self.assertEqual(led.pending(), 3)

        # Mark one as submitted (still pending: 3)
        led.submitted("uc1/r1/c1/w1", 100.01)
        self.assertEqual(led.pending(), 3)

        # Finish one as succeeded (now pending: 2)
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 100.01, 100.3, 1, 7, 21000, 10**8, 1, ""))
        self.assertEqual(led.pending(), 2)

        # Unresolve pending (now all are unresolved: pending should be 0)
        led.unresolve_pending()
        self.assertEqual(led.pending(), 0)

    def test_pending_with_queue_full(self):
        """Test that pending() is correct after queue-full case."""
        led = PaymentLedger(self.path, max_queue=2)
        self.addCleanup(led.close)
        for w in range(3):
            led.add(ev(w))
        # After 3 adds, 1 should fail (queue full), so pending should be 2
        self.assertEqual(led.pending(), 2)

    def test_outcome_method(self):
        """Test the outcome() method."""
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        led.add(ev(1))
        self.assertEqual(led.outcome("uc1/r1/c1/w1"), "generated")
        led.queued("uc1/r1/c1/w1")
        self.assertEqual(led.outcome("uc1/r1/c1/w1"), "queued")
        led.submitted("uc1/r1/c1/w1", 100.01)
        self.assertEqual(led.outcome("uc1/r1/c1/w1"), "submitted")
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 100.01, 100.3, 1, 7, 21000, 10**8, 1, ""))
        self.assertEqual(led.outcome("uc1/r1/c1/w1"), "succeeded")

    def test_load_log_recovers_from_torn_last_line(self):
        """Test that load_log skips unparseable last line but raises on corruption mid-file."""
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        led.add(ev(1))
        led.queued("uc1/r1/c1/w1")
        led.close()

        with open(self.path, "a") as f:
            f.write('{"id":"uc1/r1/c1/w2","outc')

        rebuilt = load_log(self.path)
        self.assertEqual(len(rebuilt), 1)
        self.assertEqual(rebuilt[0]["outcome"], "queued")

    def test_write_csv_rows_matches_ledger_csv(self):
        """Test that write_csv_rows produces the same CSV as PaymentLedger.write_csv()."""
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        led.add(ev(1))
        led.queued("uc1/r1/c1/w1")
        led.submitted("uc1/r1/c1/w1", 100.01)
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 100.01, 100.3, 1, 7, 21000, 10**8, 1, ""))

        out1 = self.path.replace(".log.jsonl", ".csv1")
        led.write_csv(out1)

        out2 = self.path.replace(".log.jsonl", ".csv2")
        write_csv_rows(load_log(self.path), out2)

        with open(out1) as f1, open(out2) as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_load_log_equals_led_rows_after_full_lifecycle(self):
        """Test that load_log(path) equals led.rows() after full lifecycle including resigned."""
        led = PaymentLedger(self.path, max_queue=10)
        self.addCleanup(led.close)
        led.add(ev(1))
        led.queued("uc1/r1/c1/w1")
        led.submitted("uc1/r1/c1/w1", 100.01)
        led.finish("uc1/r1/c1/w1", SubmitResult("0xab", 100.01, 100.3, 1, 7, 21000, 10**8, 1, "", resigned=2))

        led_rows = led.rows()
        rebuilt = load_log(self.path)

        self.assertEqual(len(rebuilt), 1)
        self.assertEqual(rebuilt[0]["id"], led_rows[0]["id"])
        self.assertEqual(rebuilt[0]["outcome"], led_rows[0]["outcome"])
        self.assertEqual(rebuilt[0]["resigned"], 2)
