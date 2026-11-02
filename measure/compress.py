"""`python -m measure.compress RUN_DIR`: gzip the big text files of a run folder.

Why: one run at the 10 ms window is ~640 MB of CSV/JSONL, too big for git;
gzip brings it to ~100 MB. Only the large files are compressed: those, and bath0's
stack logs (logs/bath0-docker.log, both stacks; logs/bath0-zksync-server.log, the
zkSync server at debug level), which nothing in measure/ parses. Small JSON,
recorder CSVs, the other logs, manifest and summary stay readable. Each file is verified
by decompressing and comparing hashes before the original is removed. Idempotent
(an existing X.gz with no X is skipped) and reproducible (mtime=0)."""
import glob
import gzip
import hashlib
import os
import shutil
import sys


BIG_LOGS = ("bath0-docker.log", "bath0-zksync-server.log")   # deploy/cluster collect


def targets(run_dir):
    r = os.path.join(run_dir, "results")
    return (sorted(glob.glob(os.path.join(r, "c[0-9]*", "ledger.csv")))
            + sorted(glob.glob(os.path.join(r, "c[0-9]*", "ledger.log.jsonl")))
            + sorted(glob.glob(os.path.join(r, "c[0-9]*", "rows_received.csv")))
            + [p for p in [os.path.join(r, "rows_sent.csv")] if os.path.exists(p)]
            + [p for p in (os.path.join(run_dir, "logs", f) for f in BIG_LOGS) if os.path.exists(p)])


def _sha(f):
    h = hashlib.sha256()
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
    return h.hexdigest()


def compress_file(path):
    """Gzip `path` to path.gz, verify, remove `path`. Returns (before, after) bytes."""
    gz = path + ".gz"
    tmp = gz + ".tmp"
    with open(path, "rb") as src, open(tmp, "wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as out:
            shutil.copyfileobj(src, out, 1 << 20)
    with open(path, "rb") as a, gzip.open(tmp, "rb") as b:
        if _sha(a) != _sha(b):
            os.remove(tmp)
            raise RuntimeError(f"verification failed for {path}")
    before = os.path.getsize(path)
    os.replace(tmp, gz)
    os.remove(path)
    return before, os.path.getsize(gz)


def compress_run(run_dir):
    before = after = n = 0
    for p in targets(run_dir):
        if os.path.exists(p):
            b, a = compress_file(p)
            before, after, n = before + b, after + a, n + 1
    return n, before, after


def main():
    n, b, a = compress_run(sys.argv[1])
    print(f"compressed {n} file(s): {b / 1e6:.1f} MB -> {a / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
