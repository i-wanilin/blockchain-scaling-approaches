#!/usr/bin/env python3
"""The stalled run, customer by customer: UC1, Arbitrum, 50 payments per second, rep1.

In run 20261011-190947 the 99th percentile of the payment latency was 56.4 s, against
0.41-0.43 s in the other two runs (cluster spec §8, "Outliers, kept"). This script shows
where the stall was, from the run's raw logs:

- the stall: from the intended time of the first payment that took longer than STALL_S
  to the last payment of the stream;
- per customer: the median payment latency before and during the stall, and the 99th
  percentile during it (results/cN/ledger.csv.gz, succeeded payments, t_confirmed -
  t_intended); the 99th percentile of the delay with which the sensor's rows reached the
  customer over the whole run (summary.json per_customer.cN.receive_lag_s.p99); the mean
  CPU load and the range of the Wi-Fi signal of the customer's board before and during
  the stall (recorder/<board>.csv, one sample every 5 s);
- the start of the stall in seconds after the first payment.

Output: analysis/data/stall.csv, with the columns of results.csv (use_case uc1, system
arbitrum, window w100, metric stall_*, the board in "board", the number in "mean"), so
that the thesis's macro script reads it like the results.

    python3 analysis/stall.py
"""
import csv
import glob
import gzip
import json
import os
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(REPO, "results", "pi", "uc1", "20261011-190947-arbitrum-uc1-arbitrum5mx7-w100-rep1")
OUT = os.path.join(REPO, "analysis", "data", "stall.csv")
STALL_S = 2.0      # a payment slower than this marks the stall (the other runs' p99 is below 0.5 s)
COLUMNS = ["use_case", "system", "window", "metric", "board", "unit", "stat", "n", "mean", "sd",
           "min", "max", "rep1", "rep2", "rep3", "saturated_runs", "runs", "source"]


def row(metric, value, unit, source, board=""):
    r = dict.fromkeys(COLUMNS, "")
    r.update(use_case="uc1", system="arbitrum", window="w100", metric=metric, board=board, unit=unit,
             stat="value", n="1", mean=f"{value:.15g}", runs=os.path.basename(RUN)[:15], source=source)
    return r


def p99(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.99 * len(xs)))]


def main():
    summary = json.load(open(os.path.join(RUN, "summary.json")))
    board = {c: v["board"] for c, v in summary["per_customer"].items()}
    pay = {}
    for f in sorted(glob.glob(os.path.join(RUN, "results", "c*", "ledger.csv.gz"))):
        c = os.path.basename(os.path.dirname(f))
        with gzip.open(f, "rt") as fh:
            pay[c] = [(float(r["t_intended"]), float(r["t_confirmed"]) - float(r["t_intended"]))
                      for r in csv.DictReader(fh) if r["outcome"] == "succeeded"]
    t0 = min(t for v in pay.values() for t, _ in v)
    slow = sorted(t for v in pay.values() for t, lat in v if lat > STALL_S)
    start, end = slow[0], max(t for v in pay.values() for t, _ in v)

    out = [row("stall_start_s", start - t0, "s", f"first payment slower than {STALL_S} s, after the first payment")]
    for c in sorted(pay):
        b, v = board[c], pay[c]
        before = [lat for t, lat in v if t < start]
        during = [lat for t, lat in v if start <= t <= end]
        rec = list(csv.DictReader(open(os.path.join(RUN, "recorder", f"{b}.csv"))))
        cpu = lambda a, z: statistics.mean(float(r["cpu_busy_pct"]) for r in rec if a <= float(r["t"]) < z)
        sig = [int(r["signal_dbm"]) for r in rec if start <= float(r["t"]) <= end and r["signal_dbm"]]
        out += [
            row("stall_latency_p50_before", statistics.median(before), "s", f"results/{c}/ledger.csv.gz", b),
            row("stall_latency_p50_during", statistics.median(during), "s", f"results/{c}/ledger.csv.gz", b),
            row("stall_latency_p99_during", p99(during), "s", f"results/{c}/ledger.csv.gz", b),
            row("stall_receive_lag_p99", summary["per_customer"][c]["receive_lag_s"]["p99"], "s",
                f"summary.json per_customer.{c}.receive_lag_s.p99", b),
            row("stall_cpu_before", cpu(t0, start), "pct", f"recorder/{b}.csv cpu_busy_pct", b),
            row("stall_cpu_during", cpu(start, end), "pct", f"recorder/{b}.csv cpu_busy_pct", b),
            row("stall_signal_min", min(sig), "dBm", f"recorder/{b}.csv signal_dbm, during the stall", b),
            row("stall_signal_max", max(sig), "dBm", f"recorder/{b}.csv signal_dbm, during the stall", b),
        ]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows(out)
    for r in out:
        print(r["metric"], r["board"], round(float(r["mean"]), 3))


if __name__ == "__main__":
    main()
