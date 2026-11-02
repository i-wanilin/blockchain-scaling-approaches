#!/usr/bin/env python3
"""The measured gas in euros, at reference prices from the public networks.

Prices: analysis/data/reference_prices_24m.json (analysis/reference_prices.py, one sample
per day from 1 November 2024 to 20 October 2026: Ethereum, Arbitrum One, zkSync Era, and
the daily ETH/EUR close on Kraken).

Reference period: the last 12 months, 21 October 2025 to 20 October 2026. Ethereum's gas
price fell sharply in August 2025 (monthly median above 1.2 gwei until July 2025, below
0.5 gwei in every month since), so the last 12 months lie within one fee regime; a longer
period would average two regimes and describe neither. Every price is the **median** over
the period: a single block with extreme tips (2025-09-24: median tip 238 gwei) would move
a mean far, not a median.

euros = gas x gas price of the chain the transaction ran on x ETH/EUR

- A transaction on the L1 (every Perun Open and Close, every rollup Open: the bridge
  deposit) is priced at the Ethereum mainnet gas price (base fee plus median priority fee).
- A transaction on Arbitrum (payment, shot, withdrawal) is priced at the Arbitrum One base
  fee. Its gas gets the L1 share added that the local stack did not charge: the median
  gasUsedForL1 of plain ETH transfers on Arbitrum One over the period. The local L1
  charged a few wei per gas, so the measured gas of a payment is the bare 21000. The share
  of a plain transfer is used for every Arbitrum transaction; a withdrawal or a token
  transfer carries a few more bytes and would pay slightly more (stated in the thesis).
- A transaction on zkSync is priced at the zkSync Era base fee, with the measured gas as is.

Also written:
- the reference prices themselves (use_case "ref");
- the break-even count N* per use case and rollup: how many payments (UC1) or shots and
  hits (UC2) a customer or player must make before a Perun channel costs less than the
  same payments on the rollup. The Perun side counts both ends of the channel: the
  customer's or player's Open and Close, and the counterpart's share (the sensor's close
  of that channel; the hub's deposit into and settlement of it). The rollup side counts
  the Open, the Close and the payments. 0 means the channel costs less than the rollup's
  Open and Close alone;
- the price of one UC1 window in euros, at an assumed price of 1e-6 ETH per second of data
  (the PRICE_WEI of 1e12 wei that every payment carried, read as the price of the 1 s
  window, divided evenly over the smaller windows: the runs sent the same 1e12 wei per
  window at every window size, which changes no gas), and the fee share: a rollup payment's
  fee in euros over the price of the window it pays for (use_case uc1, metric fee_share).
  For Perun the fee share is the cost of the channel, both ends, divided by the payments
  confirmed over it in the 15 minutes (succeeded_share x 900 s / window), over the price
  of the window;
- the cost of one whole UC2 match (use_case uc2, metrics eur_match_open_close,
  eur_match_payments, eur_match): the six players' Opens and Closes (on Perun with the hub's
  deposit into and settlement of each player's ledger channel) and the fees of all 1070
  payments of the match (813 shots and 257 hits, the same in every run: tank-arena/TUNING.md),
  and that total divided by the six players (eur_match_per_player; the players' payments
  differ, so this is the average share).
  Also an estimate for Perun's contracts on Arbitrum One (eur_match_on_arbitrum): the same
  gas of Perun's Opens and Closes at the Arbitrum One base fee, without the share Arbitrum
  charges for posting the transactions' data to the L1, which depends on their size and was
  not measured;
- the same prices and N* month by month over the two years
  (analysis/data/reference_prices_monthly.csv, for figure F6), the range of the monthly N*
  in the reference period (break_even_payments_min/_max) and in the months before August
  2025 (break_even_payments_before_min/_max), and the median Ethereum gas price before
  August 2025.

Output: analysis/data/cost_eur.csv, with the columns of results.csv (unit "eur", stat
"value", the number in "mean"), so that the thesis's macro script reads it like the
results.

    python3 analysis/cost_eur.py
"""
import csv
import datetime as dt
import json
import statistics

RESULTS = "analysis/data/results.csv"
PRICES = "analysis/data/reference_prices_24m.json"
OUT = "analysis/data/cost_eur.csv"
MONTHLY = "analysis/data/reference_prices_monthly.csv"
REF = ("2025-10-21", "2026-10-21")      # reference period, end exclusive
REF_MONTHS = ("2025-11", "2026-10")     # whole months inside it, for the monthly range
BEFORE = ("2024-11", "2025-07")         # the months before the fall of Ethereum's gas price
PLAYERS = 6               # UC2: the host and five guests (experiment files)
MATCH_PAYMENTS = 1070     # UC2: 813 shots + 257 hits in the seed-42 match (tank-arena/TUNING.md)
PER_CUSTOMER_S = 900      # UC1: one customer pays one window after another for 15 min
PRICE_ETH_PER_S = 1e-6   # assumed price of one second of UC1 data (the PRICE_WEI the 1 s window sent)
COLUMNS = ["use_case", "system", "window", "metric", "board", "unit", "stat", "n", "mean", "sd",
           "min", "max", "rep1", "rep2", "rep3", "saturated_runs", "runs", "source"]

# metric -> on which chain the transaction runs, per system
CHAIN = {
    "perun": {"gas_open": "ethereum", "gas_close": "ethereum",
              "hub_gas_open_per_channel": "ethereum", "hub_gas_close_per_channel": "ethereum",
              "sensor_gas_close_per_channel": "ethereum"},
    "arbitrum": {"gas_open": "ethereum", "gas_close": "arbitrum", "gas_payment": "arbitrum",
                 "gas_per_shot": "arbitrum"},
    "zksync": {"gas_open": "ethereum", "gas_close": "zksync", "gas_payment": "zksync",
               "gas_per_shot": "zksync"},
}


def ts(day):
    return dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp()


def month(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m")


def row(use_case, system, window, metric, value, unit="eur", source=""):
    r = {c: "" for c in COLUMNS}
    r.update(use_case=use_case, system=system, window=window, metric=metric, unit=unit,
             stat="value", n="1", mean=f"{value:.15g}", source=source)
    return r


def prices_in(p, keep):
    """Median prices over the samples whose time t (seconds) satisfies keep(t)."""
    gp = {c: statistics.median(s["gas_price_wei"] for s in p[c]["samples"] if keep(s["t"]))
          for c in ("ethereum", "arbitrum", "zksync")}
    arb_l1 = statistics.median(t["gas_used_for_l1"] for s in p["arbitrum"]["samples"] if keep(s["t"])
                               for t in s["transfers"] if t["gas_used_for_l1"] is not None)
    zk_tr = statistics.median(t["gas_used"] for s in p["zksync"]["samples"] if keep(s["t"])
                              for t in s["transfers"])
    eur = statistics.median(v for t, v in p["eth_eur_daily"] if keep(t / 1000))
    return {"gas": gp, "arb_l1": arb_l1, "zk_transfer": zk_tr, "eth_eur": eur}


def euros(results, pr):
    """{(use_case, system, window, eur_metric): (euros, gas metric, chain)} at the prices pr."""
    out = {}
    for r in results:
        chain = CHAIN.get(r["system"], {}).get(r["metric"])
        if chain is None or r["mean"] == "":
            continue
        gas = float(r["mean"]) + (pr["arb_l1"] if chain == "arbitrum" else 0.0)
        metric = ("eur" + r["metric"][len("gas"):] if r["metric"].startswith("gas_")
                  else "eur_" + r["metric"].replace("_gas", ""))   # sensor_gas_close_... -> eur_sensor_close_...
        out[(r["use_case"], r["system"], r["window"], metric)] = (
            gas * pr["gas"][chain] * 1e-18 * pr["eth_eur"], r["metric"], chain)
    return out


def break_even(eur):
    """{(use_case, rollup, window): N*}."""
    v = lambda k: eur[k][0] if k in eur else 0.0
    out = {}
    for (uc, sysname, w, m) in eur:
        if sysname == "perun" or m not in ("eur_payment", "eur_per_shot"):
            continue
        fixed_perun = (v((uc, "perun", w, "eur_open")) + v((uc, "perun", w, "eur_close"))
                       # the counterpart's side of the same channel
                       + v((uc, "perun", w, "eur_sensor_close_per_channel"))
                       + v((uc, "perun", w, "eur_hub_open_per_channel"))
                       + v((uc, "perun", w, "eur_hub_close_per_channel")))
        fixed_rollup = v((uc, sysname, w, "eur_open")) + v((uc, sysname, w, "eur_close"))
        per = v((uc, sysname, w, m)) - v((uc, "perun", w, m))
        # 0: Perun's channel already costs less than the rollup's Open and Close alone
        out[(uc, sysname, w)] = max(0.0, (fixed_perun - fixed_rollup) / per)
    return out


def match_cost(eur, results, pr):
    """[(system, metric, euros, source)] for one whole UC2 match."""
    v = lambda s, m: eur[("uc2", s, "", m)][0] if ("uc2", s, "", m) in eur else 0.0
    out = []
    for s in ("perun", "arbitrum", "zksync"):
        per_player = (v(s, "eur_open") + v(s, "eur_close")
                      + v(s, "eur_hub_open_per_channel") + v(s, "eur_hub_close_per_channel"))
        oc, pay = PLAYERS * per_player, MATCH_PAYMENTS * v(s, "eur_per_shot")
        out += [(s, "eur_match_open_close", oc, f"{PLAYERS} x (Open + Close, with the hub's share on Perun)"),
                (s, "eur_match_payments", pay, f"{MATCH_PAYMENTS} x eur_per_shot"),
                (s, "eur_match", oc + pay, "eur_match_open_close + eur_match_payments"),
                (s, "eur_match_per_player", (oc + pay) / PLAYERS,
                 f"eur_match / {PLAYERS}, on Perun with the hub's cost spread over the players")]
    keys = ("gas_open", "gas_close", "hub_gas_open_per_channel", "hub_gas_close_per_channel")
    gas = {r["metric"]: float(r["mean"]) for r in results
           if r["use_case"] == "uc2" and r["system"] == "perun" and r["metric"] in keys}
    per_player = sum(gas[m] for m in keys)
    out.append(("perun", "eur_match_on_arbitrum",
                PLAYERS * per_player * pr["gas"]["arbitrum"] * 1e-18 * pr["eth_eur"],
                f"{PLAYERS} x Perun's Open and Close gas (with the hub's share) x arbitrum price, "
                "without the L1 data share"))
    return out


def column(uc, s, w):
    return f"break_even_{uc}_{s}" + (f"_{w}" if w else "")


def main():
    p = json.load(open(PRICES))
    results = list(csv.DictReader(open(RESULTS)))
    a, b = ts(REF[0]), ts(REF[1])
    pr = prices_in(p, lambda t: a <= t < b)
    src = f"{PRICES}, medians from {REF[0]} to {REF[1]} (end exclusive)"

    out = [row("ref", "all", "", "eth_eur", pr["eth_eur"], "eur_per_eth", src)]
    for c, g in pr["gas"].items():
        out.append(row("ref", c, "", "gas_price_gwei", g / 1e9, "gwei", src))
    out.append(row("ref", "arbitrum", "", "transfer_gas_for_l1", pr["arb_l1"], "gas", src))
    out.append(row("ref", "zksync", "", "transfer_gas_public", pr["zk_transfer"], "gas", src))

    eur = euros(results, pr)
    succeeded = {(r["use_case"], r["system"], r["window"]): float(r["mean"]) for r in results
                 if r["metric"] == "succeeded_share"}
    for (uc, sysname, w, m), (e, metric, chain) in eur.items():
        out.append(row(uc, sysname, w, m, e, source=f"{RESULTS} {metric} x {chain} price"))
    for (uc, sysname, w, m), (e, _, _) in list(eur.items()):
        if uc != "uc1" or m != "eur_payment" or sysname == "perun":
            continue
        window_s = int(w[1:]) / 1000                     # w1000 -> 1 s
        price = PRICE_ETH_PER_S * window_s * pr["eth_eur"]
        if sysname == "arbitrum":
            out.append(row(uc, "all", w, "eur_window_price", price,
                           source=f"{PRICE_ETH_PER_S} ETH per s of data x {window_s} s x ETH/EUR"))
        out.append(row(uc, sysname, w, "fee_share", e / price, "fraction",
                       f"eur_payment / eur_window_price ({uc} {w})"))
        if sysname == "arbitrum":                        # once per window: Perun's channel per payment
            channel = sum(eur[("uc1", "perun", w, m)][0] for m in
                          ("eur_open", "eur_close", "eur_sensor_close_per_channel"))
            confirmed = PER_CUSTOMER_S / window_s * succeeded[("uc1", "perun", w)]
            out.append(row(uc, "perun", w, "fee_share", channel / confirmed / price, "fraction",
                           f"(Open + Close + sensor's Close) / payments confirmed per customer "
                           f"/ eur_window_price ({uc} {w})"))
    for sysname, m, e, source in match_cost(eur, results, pr):
        out.append(row("uc2", sysname, "", m, e, source=source))
    be = break_even(eur)
    for (uc, sysname, w), n in be.items():
        out.append(row(uc, sysname, w, "break_even_payments", n, "count",
                       f"(Perun channel, both ends - {sysname} Open+Close) / {sysname} payment"))

    # month by month over the two years
    monthly = []
    for m in sorted({month(s["t"]) for s in p["ethereum"]["samples"]}):
        pm = prices_in(p, lambda t, m=m: month(t) == m)
        bm = break_even(euros(results, pm))
        monthly.append({"month": m, "eth_gas_gwei": pm["gas"]["ethereum"] / 1e9,
                        "arb_gas_gwei": pm["gas"]["arbitrum"] / 1e9, "zk_gas_gwei": pm["gas"]["zksync"] / 1e9,
                        "arb_l1_share_gas": pm["arb_l1"], "eth_eur": pm["eth_eur"],
                        **{column(*k): n for k, n in bm.items()}})
    with open(MONTHLY, "w", newline="") as f:
        w = csv.DictWriter(f, list(monthly[0]))
        w.writeheader()
        w.writerows(monthly)

    ref_m = [r for r in monthly if REF_MONTHS[0] <= r["month"] <= REF_MONTHS[1]]
    before = [r for r in monthly if BEFORE[0] <= r["month"] <= BEFORE[1]]
    for k in be:
        for name, rows in (("", ref_m), ("_before", before)):
            vals = [r[column(*k)] for r in rows]
            out.append(row(*k, f"break_even_payments{name}_min", min(vals), "count", MONTHLY))
            out.append(row(*k, f"break_even_payments{name}_max", max(vals), "count", MONTHLY))
    out.append(row("ref", "ethereum", "", "gas_price_gwei_before_min", min(r["eth_gas_gwei"] for r in before),
                   "gwei", f"{MONTHLY}, lowest monthly median {BEFORE[0]} to {BEFORE[1]}"))
    out.append(row("ref", "ethereum", "", "gas_price_gwei_since_max",
                   max(r["eth_gas_gwei"] for r in monthly if r["month"] > BEFORE[1]),
                   "gwei", f"{MONTHLY}, highest monthly median after {BEFORE[1]}"))

    top = max(p["ethereum"]["samples"], key=lambda s: s.get("median_priority_fee_wei", 0))
    out.append(row("ref", "ethereum", "", "outlier_tip_gwei", top["median_priority_fee_wei"] / 1e9, "gwei",
                   f"{PRICES}, the sample with the highest median tip: block {top['block']}, "
                   f"{dt.datetime.fromtimestamp(top['t'], dt.timezone.utc).date()}"))

    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows(out)
    for r in out:
        if r["use_case"] == "ref" or "break_even" in r["metric"]:
            print(r["use_case"], r["system"], r["window"], r["metric"], round(float(r["mean"]), 4))


if __name__ == "__main__":
    main()
