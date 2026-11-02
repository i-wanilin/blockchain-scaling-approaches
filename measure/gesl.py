"""GESL (ORNL, LLNL 2023, https://gesl.ornl.gov) microPMU recordings.

The raw files are not published; the repository records only the signature
IDs and the SHA-256 of each CSV (cluster spec §7.2), checked here before use.
A row is replayed as its raw CSV line; its own `Time` column (seconds) defines
the UC1 windows, so a 10 ms window at 120 Hz holds one or two rows, never none.
"""
from __future__ import annotations

import hashlib
from typing import Iterator

KNOWN_SHA256 = {
    6006: "a08d7c330d2641c2568c85192e4e2c1221bcfe7b5de645f20404d0525f86be72",
    6021: "0198a44ce15611bf9febb884ac4cbf49250441277fba33442d11f91b4e2b5094",
    6062: "bf59576f7754a6ad013b9155bca23ca15e60be9c43b2d982bf9c1a11b7dd707f",
    6063: "87b2ad10cd5ba219a43f91d871635a142fa24e75016f2760108e297a6b6fd009",
}


def csv_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rows(path) -> Iterator[tuple[float, str]]:
    with open(path) as f:
        next(f)                                   # header
        for line in f:
            line = line.rstrip("\r\n")
            if line:
                yield float(line.split(",", 1)[0]), line


def completeness(path) -> float:
    good = total = 0
    for _, line in rows(path):
        cells = line.split(",")[1:]  # exclude Time column (not a measurement cell)
        total += len(cells)
        good += sum(1 for c in cells if c not in ("", "NaN"))
    return good / total if total else 0.0


def pick_recording(paths: dict[int, str]) -> int:
    def key(sig):
        n = sum(1 for _ in rows(paths[sig]))
        return (round(completeness(paths[sig]), 4), n)
    return max(paths, key=key)
