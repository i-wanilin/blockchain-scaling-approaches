"""analysis/data/results.csv -> the result figures in analysis/figures/ (PDF for LaTeX, PNG to look at).

Reads only the CSV that results_table.py writes, so every figure shows numbers the thesis can quote.
Mean of the 3 runs, error bars ± 1 sample sd.

F1  UC1 payment latency p50 against the offered load
F2  UC1 goodput against the offered load
F3  Open and Close: time and gas (UC1 at each window; UC2, with Perun's Open and Close split into its own
    steps), the time axis once on a log scale and once linear (f3_open_close_linear)
F4  UC2 payment latency p50 and p99, guests and host, fire and hit

    .venv/bin/python analysis/figures.py
"""
from __future__ import annotations

import csv
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import LogLocator, NullFormatter, NullLocator  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(REPO, "analysis", "data", "results.csv")
OUT = os.path.join(REPO, "analysis", "figures")

SYSTEMS = ("perun", "arbitrum", "zksync")
NAME = {"perun": "Perun", "arbitrum": "Arbitrum", "zksync": "zkSync"}
# The first three slots of the dataviz reference palette (light): validated as a set, all pairs,
# for colour-vision deficiency. Each system also has its own marker, so print in grey still works.
# amber, blue and violet, dark enough to read on white paper
COLOR = {"perun": "#ec8a0c", "arbitrum": "#0a8fe8", "zksync": "#8064bf"}
MARKER = {"perun": "o", "arbitrum": "s", "zksync": "^"}
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e2e1dc"
WINDOWS = ("w1000", "w100", "w10")
OFFERED = {"w1000": 5, "w100": 50, "w10": 500}       # UC1 payments per second (metrics.md)
LANDSCAPE = 8.3                                       # inches, a landscape page's text width (F3, F5)
WIDTH = 6.3                                           # inches, about an A4 text width

plt.rcParams.update({
    "font.size": 8.5, "axes.titlesize": 9, "axes.labelsize": 8.5, "legend.fontsize": 7.5,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": INK2, "axes.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "legend.frameon": False, "pdf.fonttype": 42, "savefig.bbox": "tight", "savefig.dpi": 200,
    "xtick.major.size": 4, "ytick.major.size": 4, "xtick.minor.size": 2.5, "ytick.minor.size": 2.5,
    "xtick.minor.width": 0.6, "ytick.minor.width": 0.6,
})


def plain_log(ax, axis="y"):
    """Label a logarithmic axis with plain numbers (0.01, 0.1, 1, 10, 1 000) instead of powers
    of ten; thousands are grouped with a thin space, as siunitx does in the text."""
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    def fmt(v, _pos):
        if v >= 1:
            return f"{v:,.0f}".replace(",", "\u2009")
        return f"{v:.10f}".rstrip("0")
    a = ax.yaxis if axis == "y" else ax.xaxis
    a.set_major_formatter(FuncFormatter(fmt))
    # unlabelled minor ticks at 2..9 within each decade: their uneven spacing shows the scale is logarithmic
    a.set_minor_locator(LogLocator(base=10, subs=range(2, 10), numticks=100))
    a.set_minor_formatter(NullFormatter())


def load():
    rows = {}
    with open(CSV) as f:
        for r in csv.DictReader(f):
            key = (r["use_case"], r["system"], r["window"], r["metric"], r["board"])
            rows[key] = {k: (float(v) if k in ("mean", "sd", "min", "max") and v != "" else v) for k, v in r.items()}
    return rows


def save(fig, name):
    os.makedirs(OUT, exist_ok=True)
    for ext in ("pdf", "png"):    # no creation date in the PDF: re-running gives the same file
        fig.savefig(os.path.join(OUT, f"{name}.{ext}"), metadata={"CreationDate": None} if ext == "pdf" else None)
    plt.close(fig)
    print(f"analysis/figures/{name}.pdf, .png")


def f1_uc1_latency(rows):
    # drawn at F2's size and text size, so that both appear at the same scale in the thesis
    # (both are included at 0.75 of the text width)
    _f1(rows)


def _f1(rows):
    # no title (the caption gives it); the marker legend sits below the plot
    fig, ax = plt.subplots(figsize=(WIDTH * 0.62, 2.9))   # F2's width; a little taller for the legend
    for s in SYSTEMS:
        xs = [OFFERED[w] for w in WINDOWS]
        p50 = [rows[("uc1", s, w, "latency_p50", "")] for w in WINDOWS]
        # dotted: only a guide to the eye between the three measured loads
        ax.plot(xs, [r["mean"] for r in p50], color=COLOR[s], lw=2.0, ls=(0, (0.01, 1.8)), dash_capstyle="round", zorder=2)
        for x, r in zip(xs, p50):
            saturated = int(r["saturated_runs"]) > 0
            ax.errorbar(x, r["mean"], yerr=r["sd"], color=COLOR[s], lw=1, capsize=2, zorder=3)
            # dots for every system, as in F3, smaller
            # than the old markers so that more of the error bars shows
            ax.plot(x, r["mean"], "o", ms=5, color=COLOR[s], mfc="white" if saturated else COLOR[s],
                    mew=1.1, zorder=4)
        # the lower bound (all payments counted) is in Table A.1 and the text, not drawn
        # names left of the lowest load, where the three systems lie far apart
        ax.annotate(NAME[s], (xs[0], p50[0]["mean"]), xytext=(-9, 0), textcoords="offset points",
                    ha="right", va="center", color=COLOR[s])
    ax.set_xscale("log")
    ax.set_yscale("log")
    plain_log(ax, "x")
    plain_log(ax, "y")
    ax.set_xticks([5, 50, 500], ["5\n(w1000)", "50\n(w100)", "500\n(w10)"])
    ax.xaxis.set_minor_locator(NullLocator())   # three loads only; the y axis keeps its minor ticks
    ax.set_xlim(1.0, 800)
    ax.set_xlabel("offered load (payments per second)")
    ax.set_ylabel("payment latency p50 (s)")
    # the marker legend below the axis label, outside the data
    ax.legend(handles=[
        Line2D([], [], marker="o", ls="", ms=5, color=INK2, mfc=INK2, label="queue never full"),
        Line2D([], [], marker="o", ls="", ms=5, color=INK2, mfc="white", mew=1.1, label="queue full (saturated)"),
    ], loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=3, handletextpad=0.3, columnspacing=0.9,
        handlelength=1.6)
    save(fig, "f1_uc1_latency")


def f2_uc1_goodput(rows):
    # no title: the caption gives it. The names stay on the right: at 5 payments per second
    # the three systems lie on top of each other
    fig, ax = plt.subplots(figsize=(WIDTH * 0.62, 2.6))
    ax.plot([3, 800], [3, 800], color=INK2, lw=0.8, ls="--", zorder=1)
    ax.annotate("throughput = offered", (800, 800), xytext=(-6, -2), textcoords="offset points", ha="right",
                va="center", fontsize=7.5, color=INK2)
    for s in SYSTEMS:
        xs = [OFFERED[w] for w in WINDOWS]
        g = [rows[("uc1", s, w, "goodput", "")] for w in WINDOWS]
        ax.plot(xs, [r["mean"] for r in g], color=COLOR[s], lw=1.2, ls=(0, (1, 1.5)), zorder=2)
        for x, r in zip(xs, g):
            ax.errorbar(x, r["mean"], yerr=r["sd"], color=COLOR[s], lw=1, capsize=2, zorder=3)
            ax.plot(x, r["mean"], MARKER[s], ms=5 if s == "perun" else 7, color=COLOR[s], mew=1.3,
                    zorder=5 if s == "perun" else 4)          # Perun's circle sits inside Arbitrum's square
        ax.annotate(NAME[s], (xs[-1], g[-1]["mean"]), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=8, color=COLOR[s])
    ax.set_xscale("log")
    ax.set_yscale("log")
    plain_log(ax, "x")
    plain_log(ax, "y")
    ax.set_xticks([5, 50, 500], ["5\n(w1000)", "50\n(w100)", "500\n(w10)"])
    ax.set_yticks([1, 5, 50, 500], ["1", "5", "50", "500"])
    ax.xaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_minor_locator(LogLocator(base=10, subs=range(2, 10), numticks=100))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_xlim(3.5, 1400)
    ax.set_ylim(1, 800)
    ax.set_xlabel("offered load (payments per second)")
    ax.set_ylabel("throughput (payments confirmed per second)")
    save(fig, "f2_uc1_goodput")


def _bars(ax, groups, values, errors, hatched):
    """Grouped bars: one group per entry of groups, one bar per system."""
    w = 0.26
    for i, s in enumerate(SYSTEMS):
        for j, g in enumerate(groups):
            v, e = values[s][j], errors[s][j]
            ax.bar(j + (i - 1) * w, v, w, yerr=e, color=COLOR[s], edgecolor="white", linewidth=1,
                   hatch="////" if (s, g) in hatched else None, error_kw={"lw": 0.8, "capsize": 2, "ecolor": INK2})
    ax.set_xticks(range(len(groups)), groups)
    ax.grid(axis="x", visible=False)


PERUN_COMMON = "#9a4a00"   # Perun's Open with all players starting at once (Figure F3): a darker orange


def _dots(ax, groups, values, errors, hollow, positions=None):
    """Dots with ± sd (on a log axis a bar's length means nothing, so the time panels use dots)."""
    w = 0.22
    positions = positions or list(range(len(groups)))
    for i, s in enumerate(SYSTEMS):
        for j, g in enumerate(groups):
            x = positions[j] + (i - 1) * w
            ax.errorbar(x, values[s][j], yerr=errors[s][j], color=COLOR[s], lw=1, capsize=2, zorder=3)
            ax.plot(x, values[s][j], "o", ms=3.6, color=COLOR[s], mew=1.0,   # dots: the ± sd shows around them
                    mfc="white" if (s, g) in hollow else COLOR[s], zorder=4)
    ax.set_xticks(positions, groups)
    ax.set_xlim(min(positions) - 0.6, max(positions) + 0.6)
    ax.grid(axis="x", visible=False)


def _uc1_close_metric(s):
    return "close_confirmed_s" if s == "perun" else "withdrawal_submitted_s"


def f3_open_close(rows, scale="log"):
    """Top: time (log or linear scale, dots). Bottom: gas (bars from zero). Left: UC1 at 5 payments per second
    (the load the text quotes; the Open and the Close hardly depend on the load, appendix Table A.3), right: UC2
    with Perun's own steps next to its Open and Close. One UC1 group instead of three loads, no title, plain
    axis labels, Perun's steps named on the axis, one legend entry for the rollups' Close."""
    from matplotlib.legend_handler import HandlerTuple
    plt.rcParams["hatch.color"] = "white"
    fig, axes = plt.subplots(2, 2, figsize=(WIDTH, 4.9), gridspec_kw={"width_ratios": [1, 1.7]})
    rollup_close = {(s, "Close") for s in ("arbitrum", "zksync")}

    # UC1 at 5 payments per second: Open and Close
    w = "w1000"
    t = {s: [rows[("uc1", s, w, "open_s", "")], rows[("uc1", s, w, _uc1_close_metric(s), "")]] for s in SYSTEMS}
    g = {s: [rows[("uc1", s, w, "gas_open", "")], rows[("uc1", s, w, "gas_close", "")]] for s in SYSTEMS}
    _dots(axes[0][0], ["Open", "Close"], {s: [r["mean"] for r in t[s]] for s in SYSTEMS},
          {s: [r["sd"] for r in t[s]] for s in SYSTEMS}, rollup_close)
    _bars(axes[1][0], ["Open", "Close"], {s: [r["mean"] / 1e3 for r in g[s]] for s in SYSTEMS},
          {s: [r["sd"] / 1e3 for r in g[s]] for s in SYSTEMS}, rollup_close)
    axes[0][0].set_title("UC1 (5 per s)", loc="left")

    # UC2: Open and Close, each followed by Perun's own steps (min-max over 18 player-runs)
    ax = axes[0][1]
    pos = {"Open": 0, "Close": 2}
    t = {s: [rows[("uc2", s, "", m, "")] for m in ("open_s", "close_s")] for s in SYSTEMS}
    _dots(ax, ["Open", "Close"], {s: [r["mean"] for r in t[s]] for s in SYSTEMS},
          {s: [r["sd"] for r in t[s]] for s in SYSTEMS}, rollup_close, positions=[pos["Open"], pos["Close"]])
    # Perun's Open in the three extra runs with a common start (open_common_start_s): a dot in a darker
    # orange on Perun's column, so that its ± sd shows like the others'
    r = rows[("uc2", "perun", "", "open_common_start_s", "")]
    ax.errorbar(pos["Open"] - 0.22, r["mean"], yerr=r["sd"], color=PERUN_COMMON, lw=1, capsize=2, zorder=3)
    ax.plot(pos["Open"] - 0.22, r["mean"], "o", ms=3.6, color=PERUN_COMMON, zorder=4)
    steps = ((0.72, "open_ledger_s"), (1.22, "open_virtual_s"),
             (2.72, "close_virtual_settle_s"), (3.22, "close_ledger_settle_s"))
    for x, metric in steps:
        r = rows[("uc2", "perun", "", metric, "")]
        ax.errorbar(x, (r["min"] + r["max"]) / 2, yerr=(r["max"] - r["min"]) / 2, fmt="none", ecolor=COLOR["perun"],
                    lw=2.2, capsize=3, zorder=3)
    # Perun's own steps named right above their ranges; the axis names only the two phases, centred on each half
    for x, metric in steps:
        r = rows[("uc2", "perun", "", metric, "")]
        ax.annotate("ledger" if "ledger" in metric else "virtual", (x, r["max"]), xytext=(0, 3),
                    textcoords="offset points", ha="center", va="bottom", fontsize=7, color=COLOR["perun"])
    ax.set_xticks([0.5, 2.5], ["Open", "Close"])
    ax.axvline(1.5, color=GRID, lw=0.8, zorder=0)
    ax.set_xlim(-0.6, 3.6)
    ax.set_title("UC2", loc="left")
    g = {s: [rows[("uc2", s, "", m, "")] for m in ("gas_open", "gas_close")] for s in SYSTEMS}
    _bars(axes[1][1], ["Open", "Close"], {s: [r["mean"] / 1e3 for r in g[s]] for s in SYSTEMS},
          {s: [r["sd"] / 1e3 for r in g[s]] for s in SYSTEMS}, rollup_close)

    for ax in axes[0]:
        ax.set_yscale(scale)
        if scale == "log":
            plain_log(ax)
    axes[0][0].set_ylabel("time (s)" if scale == "log" else "time (s), linear scale")
    axes[1][0].set_ylabel("gas (thousands)")
    top = max(ax.get_ylim()[1] for ax in axes[1])
    for ax in axes[1]:
        ax.set_ylim(0, top)
    lo = min(ax.get_ylim()[0] for ax in axes[0]) if scale == "log" else -1.5   # room for markers near 0
    hi = max(ax.get_ylim()[1] for ax in axes[0])
    for ax in axes[0]:
        ax.set_ylim(lo, hi)
    rollup = (Line2D([], [], marker="o", ls="", ms=4, mew=1.0, color=INK2, mfc="white"),
              Patch(facecolor=INK2, hatch="////", edgecolor="white"))
    dot = {s: Line2D([], [], marker="o", ls="", ms=4, color=COLOR[s]) for s in SYSTEMS}
    steps_h = Line2D([], [], color=COLOR["perun"], lw=2.2)
    # two legends: the systems on the first row, the two notes on the second
    fig.legend(handles=[dot[s] for s in SYSTEMS], labels=[NAME[s] for s in SYSTEMS], loc="upper center", ncol=3,
               bbox_to_anchor=(0.5, 0.0), columnspacing=1.6, handletextpad=0.4, fontsize=8)
    fig.legend(handles=[rollup, steps_h], labels=["rollup Close: withdrawal submitted only",
                                                  "Perun's own steps: min–max, 6 players × 3 runs"],
               handler_map={tuple: HandlerTuple(ndivide=None, pad=0.6)}, loc="upper center", ncol=2,
               bbox_to_anchor=(0.5, -0.045), columnspacing=1.6, handletextpad=0.5, fontsize=8)
    common = Line2D([], [], marker="o", ls="", ms=4, color=PERUN_COMMON)
    fig.legend(handles=[common], labels=["Perun's Open with all players starting at once (3 extra runs)"],
               loc="upper center", bbox_to_anchor=(0.5, -0.09), handletextpad=0.5, fontsize=8)
    fig.tight_layout()
    save(fig, "f3_open_close" if scale == "log" else "f3_open_close_linear")


def f4_uc2_latency(rows):
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 2.8), sharey=True)
    groups = [("guests", "fire"), ("guests", "hit"), ("host", "fire"), ("host", "hit")]
    labels = ["guests\nfire", "guests\nhit", "host\nfire", "host\nhit"]
    for ax, p in zip(axes, ("p50", "p99")):
        vals = {s: [rows[("uc2", s, "", f"{g}_{k}_{p}", "")] for g, k in groups] for s in SYSTEMS}
        _dots(ax, labels, {s: [r["mean"] for r in vals[s]] for s in SYSTEMS},
              {s: [r["sd"] for r in vals[s]] for s in SYSTEMS}, set())
        ax.set_yscale("log")
        plain_log(ax)
        ax.set_title(f"latency {p}", loc="left")
    axes[0].set_ylabel("latency (ms)")
    axes[1].legend(handles=[Line2D([], [], marker="o", ls="", ms=4, color=COLOR[s], label=NAME[s])   # dots, as drawn
                            for s in SYSTEMS], loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.text(0.02, -0.04, "zkSync: succeeded payments only (81–94 % of 1070); the others were unresolved at the "
             "cut-off.", fontsize=7.5, color=INK2)
    fig.suptitle("UC2: payment latency (generated → confirmed) by player group and payment kind", x=0.02,
                 ha="left", fontsize=9)
    fig.tight_layout()
    save(fig, "f4_uc2_latency")


def main():
    rows = load()
    f1_uc1_latency(rows)
    f2_uc1_goodput(rows)
    f3_open_close(rows)
    f3_open_close(rows, scale="linear")
    f4_uc2_latency(rows)


if __name__ == "__main__":
    main()
