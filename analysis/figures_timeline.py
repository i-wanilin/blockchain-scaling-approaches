"""The final UC1 runs over time -> analysis/figures/f5_uc1_timeline.pdf, .png.

F5  UC1 over the 15-minute run, per window: throughput per minute (top) and the payment latency
    p50 of the payments due in each minute (bottom). Mean of the 3 runs, one dot per minute, drawn at
    the end of its minute (1 to 15; 16 is the wait), with a light band from the lowest to the highest
    of the three runs. Further:
    - only the minutes that all three runs have, so every dot is a mean of three;
    - a dotted line at 15 minutes, "last payment due": to its right is the wait of at most 60 s for
      the confirmations still outstanding (experiment files: DURATION_S=900, DRAIN_S=60);
    - throughput: the minute of the wait counts in full, divided by 60, for every run that still
      confirmed payments more than 10 s after the last payment was due (a quiet end is a real zero);
    - latency: where not every customer of a run had a confirmed payment due in that minute, the
      line goes on dashed, with the median of the customers that still had one (at the end of a
      saturated Arbitrum run only the Pi 3B+ customer, bath2, whose payments are fast); where no
      customer had one, it ends;
    - Perun is drawn on top of a wider Arbitrum line in the throughput panels at 5 and 50 payments
      per second, where the two lines coincide.

Unlike figures.py this reads the runs, not results.csv: the per-run summary.json for throughput
(confirmed_per_10s) and the customers' ledger.csv.gz for latency. The thesis quotes no number from
this figure; it shows whether a run is steady over its 15 minutes, and how a saturated run fills its
queue. Definitions as measure/analyze_uc1.py: time 0 is the first t_intended of any customer,
latency = t_confirmed - t_intended of the succeeded payments (survivors only).

    .venv/bin/python analysis/figures_timeline.py
"""
from __future__ import annotations

import csv
import glob
import gzip
import json
import os
import statistics

import figures as fg                  # style, colours, save()
import results_table as rt            # the final runs: rt.cells()
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

BIN_S = 60                            # one value per minute; summary.json counts in 10 s bins
DUE_S, WAIT_END_S = 900, 960          # payments due for 900 s, then the wait of 60 s (DURATION_S, DRAIN_S)
DOT = {"marker": "o", "ms": 2.6, "mew": 0}


def _run_series(run_dir):
    """(throughput per minute [1/s], {minute: latency p50 [s]}, minutes in which not every customer
    had a confirmed payment) of one run."""
    with open(os.path.join(run_dir, "summary.json")) as f:
        per10 = json.load(f)["confirmed_per_10s"]
    k = BIN_S // 10
    rows = []
    paths = sorted(glob.glob(os.path.join(run_dir, "results", "c*", "ledger.csv.gz")))
    for c, path in enumerate(paths):
        with gzip.open(path, "rt") as f:
            rows += [(c, r) for r in csv.DictReader(f)]
    t0 = min(float(r["t_intended"]) for _, r in rows if r["t_intended"])
    ok = [(c, r) for c, r in rows if r["outcome"] == "succeeded" and r["t_confirmed"]]
    if max(float(r["t_confirmed"]) - t0 for _, r in ok) > DUE_S + 10:   # still confirming in the wait
        per10 = per10 + [0] * (WAIT_END_S // 10 - len(per10))
        good = [sum(per10[i:i + k]) / BIN_S for i in range(0, WAIT_END_S // 10, k)]
    else:                                                               # whole minutes only
        good = [sum(per10[i:i + k]) / BIN_S for i in range(0, len(per10) - k + 1, k)]
    bins, who = {}, {}
    for c, r in ok:
        t_int = float(r["t_intended"])
        b = int((t_int - t0) // BIN_S)
        bins.setdefault(b, []).append(float(r["t_confirmed"]) - t_int)
        who.setdefault(b, set()).add(c)
    partial = {b for b in bins if len(who[b]) < len(paths)}
    return dict(enumerate(good)), {b: statistics.median(v) for b, v in bins.items()}, partial


def _combine(per_run):
    """[(minute, mean, min, max)] over the runs, for each minute that all runs have."""
    keys = sorted(set.intersection(*(set(run) for run in per_run)))
    return [(k, statistics.mean(v), min(v), max(v)) for k in keys for v in [[run[k] for run in per_run]]]


def _band(ax, xs, pts, color):
    """The light area from the lowest to the highest of the three runs."""
    ax.fill_between(xs, [p[2] for p in pts], [p[3] for p in pts], color=color, alpha=0.18, lw=0, zorder=2)


def f5_uc1_timeline():
    cells = {(s, w): runs for uc, s, w, runs in rt.cells() if uc == "uc1"}
    fig, axes = plt.subplots(2, 3, figsize=(fg.LANDSCAPE, 4.6), sharex=True)
    for j, w in enumerate(fg.WINDOWS):
        top, bottom = axes[0][j], axes[1][j]
        top.axhline(fg.OFFERED[w], color=fg.INK2, lw=0.8, ls="--", zorder=1)
        for ax in (top, bottom):
            ax.axvline(DUE_S / 60, color=fg.INK2, lw=0.8, ls=":", zorder=1)
        for s in fg.SYSTEMS:
            series = [_run_series(d) for d in cells[(s, w)]]
            partial = set().union(*(p for _, _, p in series))
            line = {"color": fg.COLOR[s], "lw": 1.3, "zorder": 4 if s == "perun" else 3}
            dot = {**DOT, "mfc": fg.COLOR[s]}
            # throughput: Arbitrum wider underneath Perun where their lines coincide (5 and 50/s)
            wide = {"lw": 2.4, "ms": 4.2} if s == "arbitrum" and j < 2 else {}
            good = _combine([g for g, _, _ in series])
            xs = [k + 1 for k, *_ in good]                         # each value at the end of its minute
            _band(top, xs, good, fg.COLOR[s])
            top.plot(xs, [p[1] for p in good], **{**line, **dot, **wide})
            # latency: solid where every customer had a confirmed payment, dashed where not
            lat = _combine([l for _, l, _ in series])
            xs, ys = [k + 1 for k, *_ in lat], [p[1] for p in lat]
            _band(bottom, xs, lat, fg.COLOR[s])
            bottom.plot(xs, ys, **{**line, "ls": (0, (2.5, 1.5))})
            bottom.plot(xs, [y if k not in partial else float("nan") for (k, *_), y in zip(lat, ys)], **line)
            bottom.plot(xs, ys, ls="none", **{**dot, "color": fg.COLOR[s], "zorder": line["zorder"]})
        top.set_title(f"{fg.OFFERED[w]} per s ({w[1:]} ms window)")
        bottom.set_yscale("log")
        fg.plain_log(bottom)
        top.set_ylim(0, fg.OFFERED[w] * (1.25 if j < 2 else 1.05))   # room above the offered load
        bottom.set_xlim(0, 16.6)
    axes[0][0].set_ylabel("throughput (1/s), per minute")
    axes[1][0].set_ylabel("latency p50 (s), by due time")
    axes[1][1].set_xlabel("minutes since the first payment")
    for ax in axes[1][1:]:
        ax.sharey(axes[1][0])
    axes[1][0].set_ylim(0.01, 2000)
    fig.legend(handles=[Line2D([], [], color=fg.COLOR[s], lw=1.3, label=fg.NAME[s], **{**DOT, "ms": 4,
                                                                                       "mfc": fg.COLOR[s]})
                        for s in fg.SYSTEMS]
               + [Line2D([], [], color=fg.INK2, lw=0.8, ls="--", label="offered load"),
                  Line2D([], [], color=fg.INK2, lw=0.8, ls=":", label="last payment due"),
                  Line2D([], [], color=fg.INK2, lw=1.3, ls=(0, (2.5, 1.5)),
                         label="not every customer had a payment confirmed")],
               loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.07))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fg.save(fig, "f5_uc1_timeline")


if __name__ == "__main__":
    f5_uc1_timeline()
