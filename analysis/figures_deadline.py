"""UC1 windows missed against the payment deadline -> analysis/figures/f8_uc1_deadline.pdf, .png.

F8  One panel per offered load: the share of the windows missed (not succeeded, or confirmed after
    the payment deadline) for every payment deadline from 5 ms to 1000 s, applied afterwards to the
    payments of the runs (metrics.md G: the deadline changes nothing in a run). The thick line pools
    the windows of the three runs, as the rows deadline_for_miss_5pct / _1pct of results.csv do; the
    thin lines are the runs one by one (at 50 per s the stalled Arbitrum run, rep1, stands apart).
    A dotted line marks the payment deadline of the runs, the window; dashed lines mark 5 % and 1 %.
    Where a line levels off, the rest of the payments never succeeded (miss_share_floor); a line
    that drops to zero leaves the logarithmic axis.

Reads the customers' ledger.csv.gz of the runs through results_table.window_latencies, the same
function as the CSV rows, so the figure and the numbers in the text cannot disagree.

    .venv/bin/python analysis/figures_deadline.py
"""
from __future__ import annotations

import figures as fg                  # style, colours, save()
import results_table as rt            # the final runs and the ledgers' latencies
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

GRID_S = np.logspace(np.log10(0.005), 3, 400)   # payment deadlines from 5 ms to 1000 s (10 ms visible)


def _curve(lat, n):
    return [100 * rt.miss_share(lat, n, d) for d in GRID_S]


def f8_uc1_deadline():
    cells = {(s, w): runs for uc, s, w, runs in rt.cells() if uc == "uc1"}
    fig, axes = plt.subplots(1, 3, figsize=(fg.WIDTH, 2.9), sharey=True)
    for ax, w in zip(axes, fg.WINDOWS):
        for share in (5, 1):
            ax.axhline(share, color=fg.INK2, lw=0.6, ls="--", zorder=1)
        ax.axvline(int(w[1:]) / 1000, color=fg.INK2, lw=0.8, ls=":", zorder=1)
        for s in fg.SYSTEMS:
            runs = [rt.window_latencies(d) for d in cells[(s, w)]]
            for lat, n in runs:
                ax.plot(GRID_S, _curve(lat, n), color=fg.COLOR[s], lw=0.5, alpha=0.45, zorder=2)
            pooled = sorted(x for lat, _ in runs for x in lat), sum(n for _, n in runs)
            ax.plot(GRID_S, _curve(*pooled), color=fg.COLOR[s], lw=1.4, zorder=4 if s == "perun" else 3)
        ax.set_title(f"{fg.OFFERED[w]} per s ({w[1:]} ms window)")
        ax.set_xscale("log")
        fg.plain_log(ax, "x")
        ax.set_xticks([0.01, 0.1, 1, 10, 100, 1000])
        ax.set_xlim(GRID_S[0], GRID_S[-1])
    axes[0].set_yscale("log")
    fg.plain_log(axes[0])
    axes[0].set_yticks([0.1, 1, 5, 10, 100], ["0.1", "1", "5", "10", "100"])
    axes[0].set_ylim(0.05, 130)
    axes[0].set_ylabel("windows missed (%)")
    axes[1].set_xlabel("payment deadline (s)")
    fig.legend(handles=[Line2D([], [], color=fg.COLOR[s], lw=1.4, label=fg.NAME[s]) for s in fg.SYSTEMS]
               + [Line2D([], [], color=fg.INK2, lw=0.5, label="each run"),
                  Line2D([], [], color=fg.INK2, lw=0.8, ls=":", label="payment deadline in the runs"),
                  Line2D([], [], color=fg.INK2, lw=0.6, ls="--", label="5 % and 1 % missed")],
               loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.11, 1, 1))
    fg.save(fig, "f8_uc1_deadline")


if __name__ == "__main__":
    f8_uc1_deadline()
