#!/usr/bin/env python3
"""F6: the reference prices month by month over two years, and the break-even count N*.

Reads analysis/data/reference_prices_monthly.csv (written by analysis/cost_eur.py) and
writes analysis/figures/f6_reference_prices.{pdf,png}: three panels sharing the months,
(a) the median gas price on Ethereum, Arbitrum One and zkSync Era (logarithmic),
(b) the median ETH price in euros, (c) N* of Perun against Arbitrum in UC1 (at the lowest
load) and UC2, with the payments a customer or player actually made, 900 in UC1 at the
lowest load and about 180 per player in a UC2 match. The reference period of the thesis,
21 October 2025 to 20 October 2026, is shaded (the months 2025-11 to 2026-10).

    python3 analysis/figures_prices.py
"""
from __future__ import annotations

import csv
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from figures import COLOR, INK2, WIDTH, plain_log, save  # the style of F1 to F4  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHLY = os.path.join(REPO, "analysis", "data", "reference_prices_monthly.csv")
REF_MONTHS = ("2025-11", "2026-10")
UC1_PAYMENTS = 900          # DURATION_S 900 x 1 payment per second, the lowest UC1 load
UC2_PAYMENTS = 1070 / 6     # payments per match / N_PLAYERS


def main():
    rows = list(csv.DictReader(open(MONTHLY)))
    months = [r["month"] for r in rows]
    x = range(len(months))
    f = lambda k: [float(r[k]) for r in rows]
    lo, hi = months.index(REF_MONTHS[0]), months.index(REF_MONTHS[1])

    fig, ax = plt.subplots(3, 1, figsize=(WIDTH, 6.2), sharex=True,
                           gridspec_kw={"height_ratios": [1.2, 0.8, 1.2]})
    for a in ax:
        a.axvspan(lo - 0.5, hi + 0.5, color="#f1efe8", zorder=0)
    ax[0].text((lo + hi) / 2, 1.0, "reference period", transform=ax[0].get_xaxis_transform(),
               ha="center", va="bottom", fontsize=7.5, color=INK2)

    ax[0].plot(x, f("eth_gas_gwei"), "o-", ms=3, mew=0, lw=1.2, color="#52514e", label="Ethereum")
    ax[0].plot(x, f("arb_gas_gwei"), "o-", ms=3, mew=0, lw=1.2, color=COLOR["arbitrum"], label="Arbitrum One")
    ax[0].plot(x, f("zk_gas_gwei"), "o-", ms=3, mew=0, lw=1.2, color=COLOR["zksync"], label="zkSync Era")
    ax[0].set_yscale("log")
    plain_log(ax[0])
    ax[0].set_ylabel("gas price (gwei)")
    ax[0].legend(loc="upper right", ncol=3)

    ax[1].plot(x, f("eth_eur"), "o-", ms=3, mew=0, lw=1.2, color="#52514e")
    ax[1].set_ylabel("ETH price (EUR)")

    ax[2].plot(x, f("break_even_uc1_arbitrum_w1000"), "o-", ms=3, mew=0, lw=1.2, color=COLOR["perun"],
               label="UC1, against Arbitrum")
    ax[2].plot(x, f("break_even_uc2_arbitrum"), "o-", ms=3, mew=0, lw=1.2, color=COLOR["perun"], alpha=0.55,
               label="UC2, against Arbitrum")
    ax[2].axhline(UC1_PAYMENTS, ls="--", lw=0.8, color=INK2)
    ax[2].text(0, UC1_PAYMENTS * 1.18, "UC1: 900 payments per customer at the lowest load", fontsize=7, color=INK2)
    ax[2].axhline(UC2_PAYMENTS, ls=":", lw=0.8, color=INK2)
    ax[2].text(0, UC2_PAYMENTS * 0.72, "UC2: about 180 payments per player and match", fontsize=7, color=INK2)
    ax[2].set_yscale("log")
    plain_log(ax[2])
    ax[2].set_ylabel("break-even $N^*$ (payments)")
    ax[2].legend(loc="upper right")
    ax[2].set_xticks(list(x))
    ax[2].set_xticklabels([m if i % 2 == 0 else "" for i, m in enumerate(months)], rotation=60, ha="right")
    fig.align_ylabels(ax)
    save(fig, "f6_reference_prices")


if __name__ == "__main__":
    main()
