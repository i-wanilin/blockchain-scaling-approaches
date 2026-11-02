"""UC1 confirmations per second -> analysis/figures/f7_uc1_persecond.pdf, .png.

F7  The first run of each UC1 setting (system x load): the payments confirmed in each second since
    the first payment (thin line, the system's colour), and per minute divided by 60 (thick dark line,
    counted exactly as F5 counts each run, including the minute of the wait). zkSync confirms a whole batch at once and nothing in between, so counted per
    second it jumps between zero and peaks, while Perun and Arbitrum confirm almost evenly. The panel
    titles give the highest count in one second and the share of seconds with no confirmation.

Drawn on white in the thesis colours, for a landscape page. Reads the customers' ledger.csv.gz of
the runs, not results.csv; the thesis quotes no number from it.

    .venv/bin/python analysis/figures_persecond.py
"""
from __future__ import annotations

import csv
import glob
import gzip
import os

import figures as fg                  # style, colours, save()
import figures_timeline as ft         # the per-minute count of F5
import results_table as rt            # the final runs: rt.cells()
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


# spaced dashes (the offered load is a tick on the axis): dashes 0.5-3.0 of each period of 6
DOTTED = {"ls": (5.5, (2.5, 3.5)), "lw": 1.3}


def per_second(run_dir):
    """Confirmations per second since the run's first payment, and per minute divided by 60."""
    rows = []
    for p in sorted(glob.glob(os.path.join(run_dir, "results", "c*", "ledger.csv.gz"))):
        with gzip.open(p, "rt") as f:
            rows += list(csv.DictReader(f))
    t0 = min(float(r["t_intended"]) for r in rows if r["t_intended"])
    conf = [float(r["t_confirmed"]) - t0 for r in rows if r["outcome"] == "succeeded" and r["t_confirmed"]]
    sec = [0] * (int(max(conf)) + 1)
    for t in conf:
        sec[int(t)] += 1
    good, _, _ = ft._run_series(run_dir)          # per minute exactly as F5 counts it, for this run
    return sec, [good[k] for k in sorted(good)]


def f7_uc1_persecond():
    cells = {(s, w): runs for uc, s, w, runs in rt.cells() if uc == "uc1"}
    fig, axes = plt.subplots(3, 3, figsize=(fg.LANDSCAPE, 5.6), sharex=True)
    for i, w in enumerate(fg.WINDOWS):
        for j, s in enumerate(fg.SYSTEMS):
            sec, mins = per_second(cells[(s, w)][0])
            ax = axes[i][j]
            ax.plot([k / 60 for k in range(len(sec))], sec, color=fg.COLOR[s], lw=0.5, alpha=0.8, zorder=2)
            # dashed everywhere, so the per-second line shows through
            ax.plot(range(1, len(mins) + 1), mins, color=fg.INK, alpha=0.6, zorder=3, **DOTTED)
            # the offered load as a bold tick on the axis instead of a dashed line
            off = fg.OFFERED[w]
            ax.set_ylim(0, max(ax.get_ylim()[1], off * 1.08))
            lo, hi = ax.get_ylim()
            ticks = [t for t in ax.get_yticks() if lo <= t <= hi and abs(t - off) > 0.14 * (hi - lo)]
            ax.set_yticks(sorted(ticks + [off]))
            for lab, t in zip(ax.get_yticklabels(), sorted(ticks + [off])):
                if t == off:
                    lab.set_color(fg.INK)
            zero = sum(1 for x in sec if x == 0) / len(sec)
            ax.set_title(f"{fg.NAME[s]}, {fg.OFFERED[w]} per s\nat most {max(sec)} per s; "
                         f"{zero:.0%} of seconds empty", fontsize=7.5, color=fg.COLOR[s], loc="left")
        axes[i][0].set_ylabel("confirmed payments")
    for ax in axes[2]:
        ax.set_xlabel("minutes since the first payment")
    fig.legend(handles=[Line2D([], [], color=fg.INK2, lw=0.8, label="per second (the system's colour)"),
                        Line2D([], [], color=fg.INK, alpha=0.6, label="per minute ÷ 60", **DOTTED),
                        ],
               loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fg.save(fig, "f7_uc1_persecond")


if __name__ == "__main__":
    f7_uc1_persecond()
