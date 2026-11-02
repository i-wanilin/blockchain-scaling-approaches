"""GESL CSVs: verified by hash, rows yielded with their own time column."""
import glob
import os
import tempfile
import unittest
import zipfile

from measure import gesl

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def write(text):
    p = os.path.join(tempfile.mkdtemp(), "x.csv")
    with open(p, "w") as f:
        f.write(text)
    return p


class TestGesl(unittest.TestCase):
    def test_rows_yield_time_and_raw_line(self):
        p = write("Time,a,b\n0,1,2\n0.00833333333333333,3,NaN\n")
        self.assertEqual(list(gesl.rows(p)), [(0.0, "0,1,2"), (0.00833333333333333, "0.00833333333333333,3,NaN")])

    def test_completeness_counts_nan_and_empty(self):
        # Time column is not a measurement cell, so only a, b columns count
        self.assertAlmostEqual(gesl.completeness(write("Time,a,b\n0,1,NaN\n1,,2\n")), 2 / 4)

    def test_pick_prefers_complete_then_longer(self):
        a = write("Time,a\n0,1\n1,NaN\n")
        b = write("Time,a\n0,1\n1,2\n")
        c = write("Time,a\n0,1\n1,2\n2,3\n")
        self.assertEqual(gesl.pick_recording({1: a, 2: b}), 2)
        self.assertEqual(gesl.pick_recording({2: b, 3: c}), 3)

    @unittest.skipUnless(glob.glob(os.path.join(REPO, "data/gesl-samples/sigId-*.zip")), "GESL data not present")
    def test_real_csv_hashes_match_the_spec(self):
        for sig, sha in gesl.KNOWN_SHA256.items():
            z = os.path.join(REPO, f"data/gesl-samples/sigId-{sig}.zip")
            out = tempfile.mkdtemp()
            with zipfile.ZipFile(z) as zf:
                name = f"sigId-{sig}-PMU.csv"
                zf.extract(name, out)
            self.assertEqual(gesl.csv_sha256(os.path.join(out, name)), sha, sig)
