#!/usr/bin/env python3
"""UC1 on Perun: when the sensor's earnings reach the L1, from the sensor's own result file.

The Close we time (close_confirmed_s) is the customer's: it concludes the channel on the L1
and withdraws the customer's balance. The sensor's earnings need one more L1 transaction, the
sensor's own withdrawal from the channel (perun/hub-demo/uc1_sensor.go, settle). Before the
sensor starts it, its measurement code reads the gas of every L1 block of the run
(snapshotOpenGas, gas.go), which delays the start.

Per run, median over the run's five channels:
- sensor_withdraw_s: the sensor's withdrawal, t_settle_end - t_settle_start
  (results/sensor/sensor-result.json);
- sensor_money_back_s: from the start of the customer's Close (results/cN/lifecycle.json,
  close.t_start) to the end of the sensor's withdrawal from that customer's channel
  (channels matched by name: customer-<account>).

The runs are those of results.csv (the "runs" column of the goodput rows). Over the three runs:
mean, sd, min, max, rep1-rep3, as in results.csv.
Output: analysis/data/perun_sensor.csv, with the columns of results.csv, read by the thesis's
macro script.

    python3 analysis/perun_sensor_close.py
"""
import csv
import glob
import json
import os
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, "analysis", "data", "results.csv")
OUT = os.path.join(REPO, "analysis", "data", "perun_sensor.csv")
COLUMNS = ["use_case", "system", "window", "metric", "board", "unit", "stat", "n", "mean", "sd",
           "min", "max", "rep1", "rep2", "rep3", "saturated_runs", "runs", "source"]


def per_run(run_dir):
    with open(os.path.join(run_dir, "results", "sensor", "sensor-result.json")) as f:
        sensor = json.load(f)
    close_start = {}
    for path in glob.glob(os.path.join(run_dir, "results", "c*", "lifecycle.json")):
        with open(path) as f:
            life = json.load(f)
        close_start[f"customer-{life['account']}"] = life["close"]["t_start"]
    chans = sensor["channels"]
    withdraw = statistics.median(c["t_settle_end"] - c["t_settle_start"] for c in chans)
    back = statistics.median(c["t_settle_end"] - close_start[c["name"]] for c in chans)
    return withdraw, back


def row(window, metric, values, runs, source):
    r = dict.fromkeys(COLUMNS, "")
    r.update(use_case="uc1", system="perun", window=window, metric=metric, unit="s", stat="mean",
             n=str(len(values)), mean=f"{statistics.mean(values):.15g}",
             sd=f"{statistics.stdev(values):.15g}" if len(values) > 1 else "",
             min=f"{min(values):.15g}", max=f"{max(values):.15g}", runs=" ".join(runs), source=source)
    for i, v in enumerate(values[:3], 1):
        r[f"rep{i}"] = f"{v:.15g}"
    return r


def main():
    out = []
    with open(RESULTS) as f:
        cells = [(r["window"], r["runs"].split()) for r in csv.DictReader(f)
                 if r["use_case"] == "uc1" and r["system"] == "perun" and r["metric"] == "goodput"
                 and r["board"] == "" and r["runs"]]
    for window, runs in cells:
        vals = [per_run(glob.glob(os.path.join(REPO, "results", "pi", "uc1", rid + "*-perun-*"))[0]) for rid in runs]
        out.append(row(window, "sensor_withdraw_s", [v[0] for v in vals], runs,
                       "sensor-result.json: t_settle_end - t_settle_start, median over the channels"))
        out.append(row(window, "sensor_money_back_s", [v[1] for v in vals], runs,
                       "cN/lifecycle.json close.t_start -> sensor-result.json t_settle_end, median over the channels"))
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows(out)
    for r in out:
        print(r["window"], r["metric"], round(float(r["mean"]), 2), round(float(r["min"]), 2), round(float(r["max"]), 2))


if __name__ == "__main__":
    main()
