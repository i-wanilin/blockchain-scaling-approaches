"""UC1 total cost per customer against its payments -> analysis/figures/f10_uc1_breakeven.pdf, .png.

F10 The cost of one customer's payments in euros, gas only, at the reference prices (cost_eur.csv):
    Perun's channel costs the same however many payments it carries (the customer's Open and Close
    and the sensor's Close of that channel); a rollup costs its Open and Close and then the fee of
    every payment. The lines cross at the break-even count N* (break_even_payments). Below the
    chart, one ruler per load gives the time a customer needs to make that many payments (one
    payment per window: 1, 10 and 100 per second). Drawn on white in the thesis colours.

    .venv/bin/python analysis/figures_breakeven.py
"""
from __future__ import annotations

import csv
import os

import figures as fg                  # style, colours, save()
import matplotlib.pyplot as plt

COST = os.path.join(fg.REPO, "analysis", "data", "cost_eur.csv") if hasattr(fg, "REPO") else \
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analysis", "data", "cost_eur.csv")
NMAX, YMAX = 250, 0.30
RULERS = (("5 per s", 1, [60, 120, 180, 240], lambda t: f"{t // 60} min"),
          ("50 per s", 10, [5, 10, 15, 20, 25], lambda t: f"{t} s"),
          ("500 per s", 100, [0.5, 1, 1.5, 2, 2.5], lambda t: f"{t:g} s"))


def _cost():
    with open(COST) as f:
        return {(r["system"], r["metric"]): float(r["mean"]) for r in csv.DictReader(f)
                if r["use_case"] == "uc1" and r["window"] == "w1000"}


def _when(sec):
    if sec >= 60:
        return f"about {round(sec / 60)} min"
    return f"about {round(sec)} s" if sec >= 10 else f"about {sec:.1f} s"


def f10_uc1_breakeven():
    c = _cost()
    channel = c[("perun", "eur_open")] + c[("perun", "eur_close")] + c[("perun", "eur_sensor_close_per_channel")]
    fixed = {s: c[(s, "eur_open")] + c[(s, "eur_close")] for s in ("arbitrum", "zksync")}
    per = {s: c[(s, "eur_payment")] for s in ("arbitrum", "zksync")}
    be = c[("arbitrum", "break_even_payments")]

    fig, axes = plt.subplots(4, 1, figsize=(fg.WIDTH, 4.8),
                             gridspec_kw={"height_ratios": [5, 0.5, 0.5, 0.5], "hspace": 0.55})
    ax = axes[0]
    ax.plot([0, NMAX], [channel, channel], color=fg.COLOR["perun"], lw=2)
    ax.plot([0, NMAX], [fixed["arbitrum"], fixed["arbitrum"] + NMAX * per["arbitrum"]],
            color=fg.COLOR["arbitrum"], lw=2)
    n_out = (YMAX - fixed["zksync"]) / per["zksync"]         # where zkSync leaves the chart
    ax.annotate("", xy=(n_out, YMAX), xytext=(0, fixed["zksync"]),
                arrowprops={"arrowstyle": "-|>", "color": fg.COLOR["zksync"], "lw": 2, "shrinkA": 0, "shrinkB": 0})
    ax.annotate("Perun", (NMAX, channel), xytext=(6, 0), textcoords="offset points", va="center",
                color=fg.COLOR["perun"])
    ax.annotate("Arbitrum", (NMAX, fixed["arbitrum"] + NMAX * per["arbitrum"]), xytext=(6, 0),
                textcoords="offset points", va="center", color=fg.COLOR["arbitrum"])
    ax.annotate("zkSync, off the chart", (n_out, YMAX), xytext=(6, -2), textcoords="offset points",
                va="top", color=fg.COLOR["zksync"])
    ax.plot(be, channel, "o", ms=6, mfc="white", mec=fg.INK, mew=1.4, zorder=5)
    ax.annotate(f"{be:.0f} payments", (be, channel), xytext=(-8, 10), textcoords="offset points", ha="right",
                color=fg.INK, fontweight="bold")
    ax.set_ylim(0, YMAX)
    ax.set_xlim(0, NMAX)
    ax.set_xticks([0, 50, 100, 150, 200, 250])
    ax.set_xlabel("payments per customer")
    ax.set_ylabel("total cost per customer (€)")
    ax.set_yticks([0, 0.1, 0.2, 0.3], ["0", "0.10", "0.20", "0.30"])
    for a in axes:
        a.axvline(be, color=fg.INK, lw=0.8, ls="--", zorder=1)

    # the rulers: time until a customer has made N payments, at one payment per window
    for a, (load, rate, ticks, label) in zip(axes[1:], RULERS):
        a.set_xlim(0, NMAX)
        a.set_yticks([])
        a.set_ylim(0, 1)
        for side in ("left", "right", "top"):
            a.spines[side].set_visible(False)
        a.grid(False)
        a.set_ylabel(load, rotation=0, ha="right", va="center", fontsize=8)
        a.set_xticks([rate * t for t in ticks], [label(t) for t in ticks], fontsize=7.5)
        a.tick_params(axis="x", labelbottom=True, length=3)
        a.annotate(_when(be / rate), (be, 0.5), xytext=(5, 0), textcoords="offset points", va="center",
                   fontsize=8, color=fg.COLOR["perun"], fontweight="bold")
    axes[-1].set_xlabel("time until a customer has made that many payments, per offered load")
    fig.subplots_adjust(left=0.14, right=0.84, top=0.97, bottom=0.1)
    fg.save(fig, "f10_uc1_breakeven")


if __name__ == "__main__":
    f10_uc1_breakeven()
