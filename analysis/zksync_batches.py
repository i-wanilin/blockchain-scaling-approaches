#!/usr/bin/env python3
"""zkSync's batches on the Pi 4, from the server's own log, over the nine final UC1 runs.

Between two L1 batches the zkSync server makes no L2 block. When a batch's time limit
(ZK_COMMIT_DEADLINE_MS, 20 s) has passed, the server finishes the batch in its VM and seals
it. It can open the next batch only with the state root of the sealed one: a second
component (the protective-reads writer) executes the batch again, then the Merkle tree
computes the root (protective_reads_persistence_enabled: false in general.yaml). Only then
does the first L2 block of the next batch start (zksync-era state_keeper: keeper.rs,
wait_for_new_batch_init_params; seal_criteria/io_criteria.rs). The time limit counts from the
batch's timestamp, which is set when the previous batch is sealed.

Per batch, from logs/bath0-zksync-server.log.gz:
- finish: "Decided to seal L1 batch" to "Sealed L1 batch";
- root wait: "Sealed L1 batch" to the first "Setting next L2 block" of the next batch;
- pause: the two together, the time without an L2 block;
- transfers: the transactions in the batch ("Sealing L1 batch N ... K (K L2 + 0 L1) txs");
- interval: from one "Decided to seal" to the next.

Pooled over all batches of the nine runs: median ("mean" column, stat "median"), min and max.
Output: analysis/data/zksync_batches.csv, with the columns of results.csv (use_case uc1,
system zksync, metric zk_batch_*), read by the thesis's macro script.

    python3 analysis/zksync_batches.py
"""
import csv
import datetime
import gzip
import os
import re
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "analysis", "data", "zksync_batches.csv")
RUNS = [   # the runs of results.csv for uc1, zksync (w1000, w100, w10)
    "20261011-215439-zksync-uc1-zksync5mx7-w1000-rep1",
    "20261011-222529-zksync-uc1-zksync5mx7-w1000-rep2",
    "20261012-033531-zksync-uc1-zksync5mx7-w1000-rep1",
    "20261011-233809-zksync-uc1-zksync5mx7-w100-rep1",
    "20261012-001829-zksync-uc1-zksync5mx7-w100-rep2",
    "20261012-005743-zksync-uc1-zksync5mx7-w100-rep3",
    "20261012-013750-zksync-uc1-zksync5mx7-w10-rep1",
    "20261012-021724-zksync-uc1-zksync5mx7-w10-rep2",
    "20261012-025547-zksync-uc1-zksync5mx7-w10-rep3",
]
COLUMNS = ["use_case", "system", "window", "metric", "board", "unit", "stat", "n", "mean", "sd",
           "min", "max", "rep1", "rep2", "rep3", "saturated_runs", "runs", "source"]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def ts(s):
    return datetime.datetime.fromisoformat(s.rstrip("Z")).timestamp()


def batches(run):
    """Per sealed batch: (finish_s, root_wait_s, pause_s, transfers, decided_at)."""
    ev = []
    path = os.path.join(REPO, "results", "pi", "uc1", run, "logs", "bath0-zksync-server.log.gz")
    with gzip.open(path, "rt", errors="ignore") as fh:
        for line in fh:
            line = ANSI.sub("", line)
            if "Decided to seal L1 batch" in line:
                ev.append(("decide", ts(line[:27])))
            elif "Sealed L1 batch" in line:
                ev.append(("sealed", ts(line[:27])))
            elif "Sealing L1 batch" in line:
                ev.append(("txs", int(re.search(r"(\d+) \(\d+ L2", line).group(1))))
            elif "Setting next L2 block" in line:
                ev.append(("l2", ts(line[:27])))
    out = []
    for k, e in enumerate(ev):
        if e[0] != "decide":
            continue
        rest = ev[k + 1:]
        sealed = next((x[1] for x in rest if x[0] == "sealed"), None)
        txs = next((x[1] for x in rest if x[0] == "txs"), None)
        nxt = next((x[1] for x in rest if x[0] == "l2" and sealed and x[1] > sealed), None)
        if sealed and nxt:
            out.append((sealed - e[1], nxt - sealed, nxt - e[1], txs, e[1]))
    return out


def row(metric, values, unit, source):
    r = dict.fromkeys(COLUMNS, "")
    r.update(use_case="uc1", system="zksync", metric=metric, unit=unit, stat="median", n=str(len(values)),
             mean=f"{statistics.median(values):.15g}", min=f"{min(values):.15g}", max=f"{max(values):.15g}",
             runs=" ".join(x[:15] for x in RUNS), source=source)
    return r


def main():
    fin, wait, pause, txs, interval = [], [], [], [], []
    for run in RUNS:
        b = batches(run)
        fin += [x[0] for x in b]
        wait += [x[1] for x in b]
        pause += [x[2] for x in b]
        txs += [x[3] for x in b]
        interval += [b2[4] - b1[4] for b1, b2 in zip(b, b[1:])]
    src = "logs/bath0-zksync-server.log.gz, "
    out = [
        row("zk_batch_finish_s", fin, "s", src + "Decided to seal L1 batch -> Sealed L1 batch"),
        row("zk_batch_root_wait_s", wait, "s", src + "Sealed L1 batch -> first L2 block of the next batch"),
        row("zk_batch_pause_s", pause, "s", src + "Decided to seal L1 batch -> first L2 block of the next batch"),
        row("zk_batch_transfers", txs, "count", src + "Sealing L1 batch: transactions in the batch"),
        row("zk_batch_interval_s", interval, "s", src + "Decided to seal L1 batch -> the next one"),
    ]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows(out)
    for r in out:
        print(r["metric"], r["n"], round(float(r["mean"]), 2), round(float(r["min"]), 2), round(float(r["max"]), 2))


if __name__ == "__main__":
    main()
