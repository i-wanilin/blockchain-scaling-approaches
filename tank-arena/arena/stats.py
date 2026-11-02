"""Trace statistics: throughput timeline and burstiness.

Usage:
    python -m arena.stats trace.jsonl

Prints total/mean/peak tx per second, move vs fire vs hit counts, the
peak/mean burstiness ratio, and an ASCII per-second transaction histogram.
The peak/mean ratio quantifies the "nicht konstante Frequenzen" property:
1.0 would be a metronome; firefight bursts push it well above.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from typing import Any


def load_trace(path: str) -> list[dict]:
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def compute_stats(events: Iterable[dict]) -> dict[str, Any]:
    """Aggregate counts, per-second buckets and burstiness of a trace."""
    counts: dict[str, int] = {}
    buckets: dict[int, int] = {}     # second -> total events
    transfer_buckets: dict[int, int] = {}
    t_max_ms = 0.0
    total = 0
    for ev in events:
        total += 1
        counts[ev["type"]] = counts.get(ev["type"], 0) + 1
        sec = int(ev["t_ms"] // 1000)
        buckets[sec] = buckets.get(sec, 0) + 1
        if ev["type"] in ("fire", "hit"):
            transfer_buckets[sec] = transfer_buckets.get(sec, 0) + 1
        t_max_ms = max(t_max_ms, ev["t_ms"])

    n_secs = (max(buckets) + 1) if buckets else 0
    per_sec = [buckets.get(s, 0) for s in range(n_secs)]
    transfers_per_sec = [transfer_buckets.get(s, 0) for s in range(n_secs)]
    mean = (total / n_secs) if n_secs else 0.0
    peak = max(per_sec) if per_sec else 0
    t_mean = (sum(transfers_per_sec) / n_secs) if n_secs else 0.0
    t_peak = max(transfers_per_sec) if transfers_per_sec else 0
    return {
        "total": total,
        "counts": counts,
        "duration_s": n_secs,
        "per_sec": per_sec,
        "mean_tx_per_sec": mean,
        "peak_tx_per_sec": peak,
        "burstiness": (peak / mean) if mean else 0.0,
        "transfers_per_sec": transfers_per_sec,
        "transfer_mean_per_sec": t_mean,
        "transfer_peak_per_sec": t_peak,
        "transfer_burstiness": (t_peak / t_mean) if t_mean else 0.0,
    }


def _bar(v: int, vmax: int, width: int = 50) -> str:
    n = round(v / vmax * width) if vmax else 0
    return "#" * n


def print_stats(stats: dict[str, Any], timeline: bool = True) -> None:
    c = stats["counts"]
    print(f"events total      : {stats['total']}")
    print(f"  move (state upd): {c.get('move', 0)}")
    print(f"  fire (transfer) : {c.get('fire', 0)}")
    print(f"  hit  (transfer) : {c.get('hit', 0)}")
    print(f"duration          : {stats['duration_s']} s")
    print(f"mean tx/s         : {stats['mean_tx_per_sec']:.1f}")
    print(f"peak tx/s         : {stats['peak_tx_per_sec']}")
    print(f"burstiness peak/mean          : {stats['burstiness']:.3f}")
    print(f"transfers mean/s  : {stats['transfer_mean_per_sec']:.1f}")
    print(f"transfers peak/s  : {stats['transfer_peak_per_sec']}")
    print(f"transfer burstiness peak/mean : {stats['transfer_burstiness']:.3f}")
    if timeline and stats["per_sec"]:
        vmax = stats["peak_tx_per_sec"]
        print("\nper-second tx histogram (all events | transfers only):")
        for sec, (v, tv) in enumerate(
                zip(stats["per_sec"], stats["transfers_per_sec"])):
            print(f"  {sec:4d}s {v:6d} {_bar(v, vmax):<50} | "
                  f"{tv:4d} {_bar(tv, max(stats['transfer_peak_per_sec'], 1), 20)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m arena.stats",
        description="Print throughput/burstiness statistics of a JSONL trace.")
    ap.add_argument("trace", help="path to a trace.jsonl produced by arena.sim")
    ap.add_argument("--no-timeline", action="store_true",
                    help="suppress the per-second histogram")
    args = ap.parse_args(argv)
    stats = compute_stats(load_trace(args.trace))
    print_stats(stats, timeline=not args.no_timeline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
