#!/usr/bin/env python3
"""Heat on the Pi 4 (bath0, the chains' board) in every final run, from its recorder file.

Per run, over the whole recorder file (Open, payments and Close; one sample every 5 s):
- temp_bath0_max: the highest temperature (temp_c);
- templimit_bath0: the share of samples with the soft temperature limit active now
  (throttled bit 0x8; the Pi 4 then lowers its clock).

The runs are those of results.csv (the "runs" column of the goodput rows). Over the three
runs: mean, sd, min, max, rep1-rep3, as in results.csv.
Output: analysis/data/pi4_heat.csv, with the columns of results.csv, read by the thesis's
macro script.

    python3 analysis/pi4_heat.py
"""
import csv
import glob
import os
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, "analysis", "data", "results.csv")
OUT = os.path.join(REPO, "analysis", "data", "pi4_heat.csv")
COLUMNS = ["use_case", "system", "window", "metric", "board", "unit", "stat", "n", "mean", "sd",
           "min", "max", "rep1", "rep2", "rep3", "saturated_runs", "runs", "source"]


def heat(run_dir):
    with open(os.path.join(run_dir, "recorder", "bath0.csv")) as f:
        rows = [r for r in csv.DictReader(f) if r.get("temp_c") and r.get("throttled")]
    temps = [float(r["temp_c"]) for r in rows]
    limit = [int(r["throttled"], 16) & 0x8 != 0 for r in rows]
    return max(temps), sum(limit) / len(limit)


def row(uc, system, window, metric, unit, values, runs, source):
    r = dict.fromkeys(COLUMNS, "")
    r.update(use_case=uc, system=system, window=window, metric=metric, unit=unit, stat="mean",
             n=str(len(values)), mean=f"{statistics.mean(values):.15g}",
             sd=f"{statistics.stdev(values):.15g}" if len(values) > 1 else "",
             min=f"{min(values):.15g}", max=f"{max(values):.15g}", runs=" ".join(runs), source=source)
    for i, v in enumerate(values[:3], 1):
        r[f"rep{i}"] = f"{v:.15g}"
    return r


def main():
    cells = []
    with open(RESULTS) as f:
        for r in csv.DictReader(f):
            if r["metric"] == "goodput" and r["board"] == "" and r["runs"]:
                cells.append((r["use_case"], r["system"], r["window"], r["runs"].split()))
    out = []
    for uc, system, window, runs in cells:
        dirs = []
        for rid in runs:
            hit = [d for d in glob.glob(os.path.join(REPO, "results", "pi", uc, rid + "*")) if f"-{system}-" in d]
            dirs.append(hit[0])
        values = [heat(d) for d in dirs]
        src = "recorder/bath0.csv, whole run"
        out.append(row(uc, system, window, "temp_bath0_max", "degC", [v[0] for v in values], runs, src + ", temp_c"))
        out.append(row(uc, system, window, "templimit_bath0", "fraction", [v[1] for v in values], runs,
                       src + ", throttled bit 0x8 (soft temperature limit active)"))
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows(out)
    for r in out:
        print(r["use_case"], r["system"], r["window"], r["metric"], round(float(r["max"]), 3))


if __name__ == "__main__":
    main()
