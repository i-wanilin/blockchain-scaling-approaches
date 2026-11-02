"""Compression must be invisible to the analysis: readers accept X or X.gz,
compress is lossless and idempotent, and analyze() gives the same summary."""
import gzip
import json
import os
import shutil
import tempfile
import unittest

from measure import analyze_uc1, compress
from measure.fileio import exists, open_text
from measure.ledger import load_log

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "uc1_smoke_c1")



class Readers(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def test_plain_and_gz(self):
        p = os.path.join(self.d, "a.csv")
        open(p, "w").write("x,y\n1,2\n")
        self.assertTrue(exists(p))
        self.assertEqual(open_text(p).read(), "x,y\n1,2\n")
        with gzip.open(p + ".gz", "wt") as f:
            f.write("x,y\n3,4\n")
        os.remove(p)
        self.assertTrue(exists(p))
        self.assertEqual(open_text(p).read(), "x,y\n3,4\n")

    def test_missing_names_both(self):
        p = os.path.join(self.d, "nope.csv")
        self.assertFalse(exists(p))
        with self.assertRaises(FileNotFoundError) as cm:
            open_text(p)
        self.assertIn("nope.csv.gz", str(cm.exception))
        self.assertIn("nope.csv", str(cm.exception))

    def test_load_log_gz_with_torn_last_line(self):
        p = os.path.join(self.d, "ledger.log.jsonl")
        with gzip.open(p + ".gz", "wt") as f:
            f.write('{"id": "a", "outcome": "queued"}\n{"id": "a", "outcome": "succeeded"}\n{"id": "b", "outc')
        rows = load_log(p)
        self.assertEqual([(r["id"], r["outcome"]) for r in rows], [("a", "succeeded")])

    def test_load_log_gz_corrupt_middle_line_raises(self):
        p = os.path.join(self.d, "l.jsonl")
        with gzip.open(p + ".gz", "wt") as f:
            f.write('{"id": "a"}\n{bad\n{"id": "b"}\n')
        with self.assertRaises(ValueError):
            load_log(p)


class Compress(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.run_dir = os.path.join(self.d, "run")
        shutil.copytree(FIXTURE, self.run_dir)
        open(os.path.join(self.run_dir, "experiment.env"), "w").write("WINDOW_MS=1000\nEXPECTED_CUSTOMERS=1\n")
        r = os.path.join(self.run_dir, "results")
        open(os.path.join(r, "rows_sent.csv"), "w").write("t_sent\n1000.0\n1005.5\n")
        open(os.path.join(r, "c1", "ledger.log.jsonl"), "w").write('{"id": "x"}\n')
        open(os.path.join(r, "c1", "rows_received.csv"), "w").write("a\n1\n")
        os.makedirs(os.path.join(self.run_dir, "logs"), exist_ok=True)
        for f in ("bath0-docker.log", "bath0-zksync-server.log"):
            open(os.path.join(self.run_dir, "logs", f), "w").write("2026-10-01T00:00:00Z some log line\n" * 50)
        open(os.path.join(self.run_dir, "logs", "bath1-sensor.log"), "w").write("{}\n")
        self.plain = {p: open(p, "rb").read() for p in compress.targets(self.run_dir)}

    def test_lossless_idempotent_and_leaves_others(self):
        self.assertEqual(len(self.plain), 6)
        n, _, _ = compress.compress_run(self.run_dir)
        self.assertEqual(n, 6)
        for p, data in self.plain.items():
            self.assertFalse(os.path.exists(p))
            self.assertEqual(gzip.open(p + ".gz", "rb").read(), data)
        gz1 = {p: open(p + ".gz", "rb").read() for p in self.plain}
        self.assertEqual(compress.compress_run(self.run_dir)[0], 0)
        self.assertEqual(gz1, {p: open(p + ".gz", "rb").read() for p in self.plain})
        self.assertTrue(os.path.exists(os.path.join(self.run_dir, "results", "c1", "lifecycle.json")))
        self.assertTrue(os.path.exists(os.path.join(self.run_dir, "results", "sensor-balance.json")))
        self.assertTrue(os.path.exists(os.path.join(self.run_dir, "logs", "bath1-sensor.log")))   # read by analyze

    def test_big_logs_only_when_present(self):
        os.remove(os.path.join(self.run_dir, "logs", "bath0-zksync-server.log"))   # an Arbitrum run has none
        names = [os.path.relpath(p, self.run_dir) for p in compress.targets(self.run_dir)]
        self.assertIn(os.path.join("logs", "bath0-docker.log"), names)
        self.assertNotIn(os.path.join("logs", "bath0-zksync-server.log"), names)
        self.assertNotIn(os.path.join("logs", "bath1-sensor.log"), names)

    def test_reproducible(self):
        d2 = os.path.join(self.d, "run2")
        shutil.copytree(self.run_dir, d2)
        compress.compress_run(self.run_dir)
        compress.compress_run(d2)
        for p in self.plain:
            q = p.replace(self.run_dir, d2)
            self.assertEqual(open(p + ".gz", "rb").read(), open(q + ".gz", "rb").read())

    def test_analyze_identical_after_compression(self):
        before = analyze_uc1.analyze(self.run_dir)
        compress.compress_run(self.run_dir)
        after = analyze_uc1.analyze(self.run_dir)
        self.assertEqual(json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
