"""The payment ledger (cluster spec §7.3): one row per payment, exactly one
outcome, five timestamps.

During a run it only *appends*: one JSON line per status change, flushed at
once, so a crash loses nothing already known and the SD card never rewrites a
growing file (a 15-minute run at the 10 ms window is 90 000 payments per
customer). `write_csv` builds ledger.csv at the end; `load_log` rebuilds the
rows from the log after a crash.

For Perun (measure/perun.py) a payment is an off-chain channel update, so the
same columns mean: `block` holds the channel version, `gas_used` and
`effective_gas_price` are 0 and `tx_hash` is empty (set on succeeded rows;
t_submitted/t_confirmed come from the Go sidecar's clock)."""
from __future__ import annotations

import csv
import json
import time

from measure.fileio import open_text

OUTCOMES = ("generated", "queued", "submitted", "succeeded", "failed", "unresolved")
FIELDS = ("id", "to", "amount_wei", "outcome", "t_intended", "t_generated", "t_queued",
          "t_submitted", "t_confirmed", "tx_hash", "block", "gas_used", "effective_gas_price",
          "attempts", "resigned", "error")


def write_csv_rows(rows, path):
    """Write rows to a CSV file."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def load_log(log_path) -> list[dict]:
    rows: dict[str, dict] = {}
    lines = []
    with open_text(log_path) as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.strip():
            try:
                change = json.loads(line)
                rows.setdefault(change["id"], {k: None for k in FIELDS}).update(change)
            except json.JSONDecodeError:
                if i == len(lines) - 1:
                    continue
                raise ValueError(f"Corrupted JSON at line {i + 1}: {line}")
    return list(rows.values())


class PaymentLedger:
    def __init__(self, log_path: str, max_queue: int):
        self.max_queue = max_queue
        self._rows: dict[str, dict] = {}
        self._log = open(log_path, "a", buffering=1)          # line-buffered: one write per change
        self._pending_count = 0                                # running count of pending rows

    def _change(self, id_, **fields):
        old_outcome = self._rows[id_].get("outcome")
        self._rows[id_].update(fields)

        if "outcome" in fields:
            new_outcome = fields["outcome"]
            if old_outcome in ("generated", "queued", "submitted"):
                self._pending_count -= 1
            if new_outcome in ("generated", "queued", "submitted"):
                self._pending_count += 1

        self._log.write(json.dumps(dict(fields, id=id_), separators=(",", ":")) + "\n")

    def pending(self) -> int:
        """Return the count of rows with pending outcomes in O(1)."""
        return self._pending_count

    def outcome(self, id_) -> str:
        """Return the row's current outcome."""
        return self._rows[id_]["outcome"]

    def add(self, ev) -> bool:
        if ev.id in self._rows:
            return False
        self._rows[ev.id] = {k: None for k in FIELDS}
        self._change(ev.id, id=ev.id, to=ev.to, amount_wei=ev.amount_wei, outcome="generated",
                     t_intended=ev.t_intended, t_generated=ev.t_generated, attempts=0, error="")
        if self.pending() > self.max_queue:
            self._change(ev.id, outcome="failed", error="queue full")
        return True

    def queued(self, id_):
        self._change(id_, outcome="queued", t_queued=time.time())

    def submitted(self, id_, t):
        self._change(id_, outcome="submitted", t_submitted=t)

    def finish(self, id_, res):
        ok = res.status == 1
        self._change(id_,
            outcome="succeeded" if ok else ("unresolved" if res.error.startswith("unresolved") else "failed"),
            t_submitted=res.t_submitted or self._rows[id_]["t_submitted"], t_confirmed=res.t_confirmed,
            tx_hash=res.tx_hash, block=res.block, gas_used=res.gas_used,
            effective_gas_price=res.effective_gas_price, attempts=res.attempts,
            resigned=getattr(res, "resigned", 0),
            error=res.error or ("" if ok else "reverted"))

    def unresolve_pending(self):
        for id_, r in self._rows.items():
            if r["outcome"] in ("generated", "queued", "submitted"):
                self._change(id_, outcome="unresolved", error=r["error"] or "drain deadline")

    def rows(self):
        return list(self._rows.values())

    def write_csv(self, path):
        self._log.flush()
        write_csv_rows(self.rows(), path)

    def close(self):
        if not self._log.closed:
            self._log.close()

    def reconcile(self, observed_wei: int) -> dict:
        ok = sum(int(r["amount_wei"]) for r in self._rows.values() if r["outcome"] == "succeeded")
        counts = {o: 0 for o in OUTCOMES}
        for r in self._rows.values():
            counts[r["outcome"]] += 1
        return {"succeeded_wei": ok, "observed_wei": observed_wei, "difference_wei": observed_wei - ok,
                "counts": counts}
