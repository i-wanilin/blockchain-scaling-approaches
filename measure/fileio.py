"""Open a run-folder text file whether or not it has been gzip-compressed.

Why: the big per-run files (ledgers, rows_*.csv) are gzipped before the run
folders go into git (see measure/compress.py). The analysis must give the same
answer either way, so every reader goes through here: `X` if it exists, else
`X.gz`."""
import gzip
import os


def exists(path) -> bool:
    return os.path.exists(path) or os.path.exists(path + ".gz")


def open_text(path):
    """Text-mode handle (newline="" as csv wants) on `path` or `path`.gz."""
    if os.path.exists(path):
        return open(path, newline="")
    if os.path.exists(path + ".gz"):
        return gzip.open(path + ".gz", "rt", newline="")
    raise FileNotFoundError(f"neither {path} nor {path}.gz exists")
