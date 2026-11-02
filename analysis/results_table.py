"""Final run summaries -> analysis/data/results.csv, the one table of result numbers (the thesis quotes it).

Which numbers, from which key and how one number per run is made: analysis/metrics.md
(fixed on 2026-10-16). METRICS below implements that list one to one; tests/test_results_table.py
checks the ids against it.

The runs are the 3 named in the first line of each final aggregate (results/pi/uc1/aggregate-
<system>5mx7-w<window>.txt, results/pi/uc2/aggregate-uc2-<system>.txt): 27 UC1 and 9 UC2 runs.

Checks, each stops the script:
- every run complete, all 36 summaries from one analysis commit, UC2 runs hash_ok;
- reconciliation ok_with_late in every run;
- the offered load is the setting: UC1 5 / 50 / 500 per s by window, UC2 1070 payments
  (813 fire, 257 hit) at about 17.8 per s;
- UC2 gas per fire = gas per hit;
- every value that an aggregate also shows equals it, run by run.

--verify also re-runs the analysis (measure/analyze_uc1.py, analyze_uc2.py) on a copy of every
run folder and checks that it reproduces the committed summary.json, so the CSV is tied to the
raw logs, not only to the summaries. Takes about 15 to 20 minutes, and needs the committed
summary.json files in results/ (all from one analysis commit).

    .venv/bin/python analysis/results_table.py [--verify]
"""
from __future__ import annotations

import ast
import csv
import glob
import json
import math
import os
import re
import shutil
import statistics
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

UC1_DIR = os.path.join(REPO, "results", "pi", "uc1")
UC2_DIR = os.path.join(REPO, "results", "pi", "uc2")
OUT_CSV = os.path.join(REPO, "analysis", "data", "results.csv")

SYSTEMS = ("perun", "arbitrum", "zksync")
ROLLUPS = ("arbitrum", "zksync")
WINDOWS = ("w1000", "w100", "w10")
UC1_OFFERED_PER_S = {"w1000": 5.0, "w100": 50.0, "w10": 500.0}   # 5 customers, one payment per window
UC2_PAYMENTS, UC2_FIRE, UC2_HIT = 1070, 813, 257                 # seed 42, the same in every run
UC2_OFFERED_PER_S = 17.8                                          # 1070 over the nominal 60 s match
UC1_PI3B = ("bath4", "bath5", "bath6", "bath7")                  # the undervolted customers (metrics.md C)
UC2_OTHERS = ("bath2", "bath4", "bath5", "bath6", "bath7")       # players other than the host bath1

# ---------------------------------------------------------------- metric definitions
# One entry per metrics.md row: (id, unit, source, per-run function, systems, stat).
# per-run function: summary -> one number (stat "mean_sd") or a list of numbers (stat "range").
# Per-board metrics are expanded below into one id per board, or carry the board in its own column.


def _median_of(key, field=None):
    """Median over the customers / players of summary[key], optionally of one field of each."""
    def f(s):
        vals = [v if field is None else (v or {}).get(field) for v in s[key].values()]
        vals = [v for v in vals if v is not None]
        return statistics.median(vals) if vals else None
    return f


def _all_of(key, field):
    """Every player's value of summary[key][p][field] (for the range rows)."""
    return lambda s: [v[field] for v in s[key].values() if v and v.get(field) is not None]


def _lat(group, kind, measure, p):
    return lambda s: s["latency_ms"][group][kind][measure][p]


UC1_METRICS = [
    # A. the final UC1 table
    ("latency_p50", "s", "payment_latency_s.p50", lambda s: s["payment_latency_s"]["p50"], SYSTEMS),
    ("latency_lb_p50", "s", "payment_latency_lower_bound_s.p50",
     lambda s: s["payment_latency_lower_bound_s"]["p50"], SYSTEMS),
    ("latency_p99", "s", "payment_latency_s.p99", lambda s: s["payment_latency_s"]["p99"], SYSTEMS),
    ("goodput", "1/s", "headline.goodput_per_s", lambda s: s["headline"]["goodput_per_s"], SYSTEMS),
    ("succeeded_share", "fraction", "headline.succeeded_share", lambda s: s["headline"]["succeeded_share"], SYSTEMS),
    ("deadline_miss_share", "fraction", "deadline_miss_share", lambda s: s["deadline_miss_share"], SYSTEMS),
    ("open_s", "s", "open_s.c1..c5, median", _median_of("open_s"), SYSTEMS),
    ("close_confirmed_s", "s", "close_s.c1..c5, median", _median_of("close_s"), ("perun",)),
    ("withdrawal_submitted_s", "s", "close_s.c1..c5, median", _median_of("close_s"), ROLLUPS),
    # C. quoted in the text next to the table
    ("confirmation_p50", "s", "confirmation_s.p50", lambda s: s["confirmation_s"]["p50"], SYSTEMS),
    ("gas_payment", "gas", "gas.payment_l2_median", lambda s: s["gas"]["payment_l2_median"], SYSTEMS),
    ("late_executed", "count", "late_executed_total", lambda s: s["late_executed_total"], SYSTEMS),
    ("cpu_bath0_mean", "%", "diagnostics.recorder.bath0.mean_cpu_busy_pct",
     lambda s: s["diagnostics"]["recorder"]["bath0"]["mean_cpu_busy_pct"], SYSTEMS),
    ("receive_lag_p99", "s", "receive_lag_s.p99", lambda s: s["receive_lag_s"]["p99"], SYSTEMS),
    # D. for the figures
    ("gas_open", "gas", "gas.deposit_l1_median", lambda s: s["gas"]["deposit_l1_median"], SYSTEMS),
    ("gas_close", "gas", "gas.close_l1_median", lambda s: s["gas"]["close_l1_median"], ("perun",)),
    ("gas_close", "gas", "gas.withdraw_l2_median", lambda s: s["gas"]["withdraw_l2_median"], ROLLUPS),
    # E. the counterpart's cost
    ("sensor_gas_close_per_channel", "gas", "gas.close_sensor_l1_per_channel",
     lambda s: s["gas"]["close_sensor_l1_per_channel"], ("perun",)),
]
for _b in UC1_PI3B:
    UC1_METRICS.append((f"undervolted_{_b}", "fraction", f"diagnostics.recorder.{_b}.undervolted_share",
                        lambda s, b=_b: s["diagnostics"]["recorder"][b]["undervolted_share"], SYSTEMS))

UC2_METRICS = [
    # B. the final UC2 table
    ("guests_fire_p50", "ms", "latency_ms.guests.fire.generated_to_confirmed.p50",
     _lat("guests", "fire", "generated_to_confirmed", "p50"), SYSTEMS),
    ("guests_hit_p50", "ms", "latency_ms.guests.hit.generated_to_confirmed.p50",
     _lat("guests", "hit", "generated_to_confirmed", "p50"), SYSTEMS),
    ("guests_fire_p99", "ms", "latency_ms.guests.fire.generated_to_confirmed.p99",
     _lat("guests", "fire", "generated_to_confirmed", "p99"), SYSTEMS),
    ("guests_hit_p99", "ms", "latency_ms.guests.hit.generated_to_confirmed.p99",
     _lat("guests", "hit", "generated_to_confirmed", "p99"), SYSTEMS),
    ("host_fire_p50", "ms", "latency_ms.host.fire.generated_to_confirmed.p50",
     _lat("host", "fire", "generated_to_confirmed", "p50"), SYSTEMS),
    ("host_hit_p50", "ms", "latency_ms.host.hit.generated_to_confirmed.p50",
     _lat("host", "hit", "generated_to_confirmed", "p50"), SYSTEMS),
    ("host_all_p99", "ms", "latency_ms.host.all.generated_to_confirmed.p99",
     _lat("host", "all", "generated_to_confirmed", "p99"), SYSTEMS),
    ("next_shot_miss_guests", "fraction", "next_shot_miss_share.guests.share",
     lambda s: s["next_shot_miss_share"]["guests"]["share"], SYSTEMS),
    ("next_shot_miss_host", "fraction", "next_shot_miss_share.host.share",
     lambda s: s["next_shot_miss_share"]["host"]["share"], SYSTEMS),
    ("guests_lb_p50", "ms", "latency_lower_bound_ms.guests.p50",
     lambda s: s["latency_lower_bound_ms"]["guests"]["p50"], SYSTEMS),
    ("goodput", "1/s", "goodput_per_s", lambda s: s["goodput_per_s"], SYSTEMS),
    ("succeeded", "count", "outcomes.succeeded", lambda s: s["outcomes"]["succeeded"], SYSTEMS),
    ("unresolved", "count", "outcomes.unresolved", lambda s: s["outcomes"]["unresolved"], SYSTEMS),
    ("late", "count", "outcomes.late", lambda s: s["outcomes"]["late"], SYSTEMS),
    ("succeeded_share", "fraction", "outcomes.shares.succeeded",
     lambda s: s["outcomes"]["shares"]["succeeded"], SYSTEMS),
    ("unresolved_share", "fraction", "outcomes.shares.unresolved",
     lambda s: s["outcomes"]["shares"]["unresolved"], SYSTEMS),
    ("late_share", "fraction", "outcomes.shares.late", lambda s: s["outcomes"]["shares"]["late"], SYSTEMS),
    ("tick_lag_p50", "ms", "match.tick_lag_ms.pooled.p50", lambda s: s["match"]["tick_lag_ms"]["pooled"]["p50"],
     SYSTEMS),
    ("tick_lag_p99", "ms", "match.tick_lag_ms.pooled.p99", lambda s: s["match"]["tick_lag_ms"]["pooled"]["p99"],
     SYSTEMS),
    ("tick_lag_max", "ms", "match.tick_lag_ms.pooled.max", lambda s: s["match"]["tick_lag_ms"]["pooled"]["max"],
     SYSTEMS),
    ("stretch", "ratio", "match.stretch", lambda s: s["match"]["stretch"], SYSTEMS),
    ("open_s", "s", "open.p0..p5.s, median", _median_of("open", "s"), SYSTEMS),
    ("open_ledger_s", "s", "open.p0..p5.ledger_s, every player", _all_of("open", "ledger_s"), ("perun",), "range"),
    ("open_virtual_s", "s", "open.p0..p5.virtual_s, every player", _all_of("open", "virtual_s"), ("perun",),
     "range"),
    ("close_s", "s", "close.p0..p5.s, median", _median_of("close", "s"), SYSTEMS),
    ("close_s_range", "s", "close.p0..p5.s, every player", _all_of("close", "s"), SYSTEMS, "range"),
    ("close_virtual_settle_s", "s", "close.p0..p5.virtual_settle_s, every player",
     _all_of("close", "virtual_settle_s"), ("perun",), "range"),
    ("close_ledger_settle_s", "s", "close.p0..p5.ledger_settle_s, every player",
     _all_of("close", "ledger_settle_s"), ("perun",), "range"),
    ("gas_open", "gas", "gas.open_median", lambda s: s["gas"]["open_median"], SYSTEMS),
    ("gas_close", "gas", "gas.close_median", lambda s: s["gas"]["close_median"], SYSTEMS),
    ("gas_per_shot", "gas", "gas.per_fire", lambda s: s["gas"]["per_fire"], SYSTEMS),
    ("cpu_bath0_mean", "%", "cpu.bath0.mean_cpu_busy_pct", lambda s: s["cpu"]["bath0"]["mean_cpu_busy_pct"], SYSTEMS),
    ("cpu_bath0_max", "%", "cpu.bath0.max_cpu_busy_pct", lambda s: s["cpu"]["bath0"]["max_cpu_busy_pct"], SYSTEMS),
    ("cpu_bath1_mean", "%", "cpu.bath1.mean_cpu_busy_pct", lambda s: s["cpu"]["bath1"]["mean_cpu_busy_pct"], SYSTEMS),
    ("cpu_bath1_max", "%", "cpu.bath1.max_cpu_busy_pct", lambda s: s["cpu"]["bath1"]["max_cpu_busy_pct"], SYSTEMS),
    ("cpu_others_mean", "%", "cpu.{bath2,bath4..bath7}.mean_cpu_busy_pct, mean of the 5 boards",
     lambda s: statistics.mean(s["cpu"][b]["mean_cpu_busy_pct"] for b in UC2_OTHERS), SYSTEMS),
    ("cpu_others_max", "%", "cpu.{bath2,bath4..bath7}.max_cpu_busy_pct, max of the 5 boards",
     lambda s: max(s["cpu"][b]["max_cpu_busy_pct"] for b in UC2_OTHERS), SYSTEMS),
    # C. quoted in the text
    ("guests_submitted_p50", "ms", "latency_ms.guests.all.submitted_to_confirmed.p50",
     _lat("guests", "all", "submitted_to_confirmed", "p50"), SYSTEMS),
    # D. for figure F4
    ("host_fire_p99", "ms", "latency_ms.host.fire.generated_to_confirmed.p99",
     _lat("host", "fire", "generated_to_confirmed", "p99"), SYSTEMS),
    ("host_hit_p99", "ms", "latency_ms.host.hit.generated_to_confirmed.p99",
     _lat("host", "hit", "generated_to_confirmed", "p99"), SYSTEMS),
]
for _b in ("bath4", "bath6", "bath7"):
    UC2_METRICS.append((f"undervolted_{_b}", "fraction", f"throttle.{_b}.undervolted_share",
                        lambda s, b=_b: s["throttle"][b]["undervolted_share"], SYSTEMS))

# metrics.md E: the UC2 hub's own gas, which only its log records (one line per run):
# "hub gas: ledger_open=646104 gas / 12 tx (...) ledger_settle=326880 gas / 6 tx (...)"
UC2_PLAYERS = 6
HUB_LOG = os.path.join("logs", "bath1-uc2-hub.out")
HUB_GAS_LINE = re.compile(r"^hub gas: ledger_open=(\d+) gas / (\d+) tx .* ledger_settle=(\d+) gas / (\d+) tx")
UC2_HUB_METRICS = (("hub_gas_open_per_channel", "logs/bath1-uc2-hub.out: ledger_open gas / 6", 0),
                   ("hub_gas_close_per_channel", "logs/bath1-uc2-hub.out: ledger_settle gas / 6", 1))


def hub_gas(run_dir):
    """(open, close) gas of the UC2 Perun hub per ledger channel, from its log."""
    with open(os.path.join(run_dir, HUB_LOG)) as f:
        hits = [HUB_GAS_LINE.match(line) for line in f]
    hits = [h for h in hits if h]
    if len(hits) != 1:
        raise ValueError(f"{os.path.basename(run_dir)}: expected one 'hub gas:' line in {HUB_LOG}, got {len(hits)}")
    open_gas, open_txs, settle_gas, settle_txs = map(int, hits[0].groups())
    if (open_txs, settle_txs) != (2 * UC2_PLAYERS, UC2_PLAYERS):   # approve + deposit per channel; one settle
        raise ValueError(f"{os.path.basename(run_dir)}: hub {open_txs} open / {settle_txs} settle transactions, "
                         f"expected {2 * UC2_PLAYERS} / {UC2_PLAYERS}")
    return open_gas / UC2_PLAYERS, settle_gas / UC2_PLAYERS


# metrics.md F: seconds between the blocks that carry payments, from the raw ledgers
UC1_BLOCK_METRIC = ("block_interval_s", "s",
                    "results/c*/ledger.csv.gz: (last - first t_confirmed) / (distinct blocks - 1), succeeded payments")


def block_interval(run_dir):
    """Seconds between the blocks that carry the succeeded payments of a UC1 rollup run."""
    import gzip
    blocks, times = set(), []
    for f in sorted(glob.glob(os.path.join(run_dir, "results", "c*", "ledger.csv.gz"))):
        with gzip.open(f, "rt") as fh:
            for r in csv.DictReader(fh):
                if r["outcome"] == "succeeded" and r["block"]:
                    blocks.add(int(r["block"]))
                    times.append(float(r["t_confirmed"]))
    if len(blocks) < 2:
        raise ValueError(f"{os.path.basename(run_dir)}: fewer than two blocks with payments")
    return (max(times) - min(times)) / (len(blocks) - 1)


# metrics.md G: the payment deadline each UC1 system needs, from the raw ledgers
UC1_DEADLINE_METRICS = (("deadline_for_miss_5pct", 0.05), ("deadline_for_miss_1pct", 0.01))
UC1_DEADLINE_SOURCE = ("results/c*/ledger.csv.gz: smallest payment deadline d with (windows not succeeded "
                       "within d) / windows <= the share; pooled over the three runs, rep1..3 per run")
UC1_FLOOR_METRIC = ("miss_share_floor", "fraction", "results/c*/ledger.csv.gz: windows whose payment did not "
                    "succeed / windows (missed at any payment deadline); pooled over the three runs")


def window_latencies(run_dir):
    """(sorted latencies t_confirmed - t_intended [s] of the succeeded payments, number of windows) of a UC1 run.
    Every ledger row is one window (analyze_uc1.py, ruling R15)."""
    import gzip
    lat, n = [], 0
    for f in sorted(glob.glob(os.path.join(run_dir, "results", "c*", "ledger.csv.gz"))):
        with gzip.open(f, "rt") as fh:
            for r in csv.DictReader(fh):
                n += 1
                if r["outcome"] == "succeeded" and r["t_confirmed"]:
                    lat.append(float(r["t_confirmed"]) - float(r["t_intended"]))
    return sorted(lat), n


def miss_share(lat, n, deadline_s):
    """Share of the n windows missed at a payment deadline: not succeeded, or confirmed after it."""
    import bisect
    return 1 - bisect.bisect_right(lat, deadline_s) / n


def deadline_for(lat, n, share):
    """Smallest payment deadline [s] that at most `share` of the n windows miss; None where even an
    unlimited payment deadline misses more (more than `share` of the payments did not succeed)."""
    need = math.ceil((1 - share) * n - 1e-9)             # windows that must be confirmed in time
    return lat[need - 1] if need <= len(lat) else None


def _pooled_row(system, window, metric, unit, source, pooled, per_run, n, run_ids, saturated):
    r = dict.fromkeys(CSV_FIELDS, "")
    r.update(use_case="uc1", system=system, window=window, metric=metric, unit=unit, stat="pooled", n=n,
             mean=pooled, source=source, runs=" ".join(run_ids), saturated_runs=saturated,
             **{f"rep{i + 1}": ("" if v is None else v) for i, v in enumerate(per_run)})
    return r


# metrics.md C: goodput per customer board, one row per board ("board" column)
UC1_PER_BOARD = ("goodput_per_board", "1/s", "per_customer.cN.goodput_per_s, board from per_customer.cN.board")

# Our id -> the line name in the aggregate .txt, for the run-by-run cross-check.
UC1_AGG_NAMES = {
    "latency_p50": "payment_latency_p50_s", "latency_lb_p50": "payment_latency_lower_bound_p50_s",
    "latency_p99": "payment_latency_p99_s", "goodput": "goodput_per_s", "succeeded_share": "succeeded_share",
    "deadline_miss_share": "deadline_miss_share", "open_s": "open_s_median", "close_confirmed_s": "close_s_median",
    "withdrawal_submitted_s": "close_s_median", "confirmation_p50": "confirmation_p50_s",
    "receive_lag_p99": "receive_lag_p99_s", "late_executed": "late_executed_total",
}
UC2_AGG_NAMES = {
    "guests_fire_p50": "guests_fire_generated_to_confirmed_p50_ms",
    "guests_hit_p50": "guests_hit_generated_to_confirmed_p50_ms",
    "host_all_p99": "host_generated_to_confirmed_p99_ms", "guests_lb_p50": "guests_latency_lower_bound_p50_ms",
    "guests_submitted_p50": "guests_submitted_to_confirmed_p50_ms",
    "next_shot_miss_guests": "next_shot_miss_share_guests", "next_shot_miss_host": "next_shot_miss_share_host",
    "succeeded_share": "succeeded_share", "unresolved_share": "unresolved_share", "late_share": "late_share",
    "goodput": "goodput_per_s", "stretch": "stretch", "tick_lag_p99": "tick_lag_p99_ms",
    "tick_lag_max": "tick_lag_max_ms", "open_s": "open_s_median", "close_s": "close_s_median",
    "gas_per_shot": "gas_per_fire",
}

CSV_FIELDS = ("use_case", "system", "window", "metric", "board", "unit", "stat", "n", "mean", "sd", "min", "max",
              "rep1", "rep2", "rep3", "saturated_runs", "runs", "source")


# ---------------------------------------------------------------- the final runs

def _aggregate_file(uc, system, window=None):
    if uc == "uc1":
        return os.path.join(UC1_DIR, f"aggregate-{system}5mx7-{window}.txt")
    return os.path.join(UC2_DIR, f"aggregate-uc2-{system}.txt")


def _run_ids(agg_path):
    """The 3 run ids in an aggregate's first line: '... 3 runs: <id> <id> <id> all complete ...'."""
    with open(agg_path) as f:
        line = f.readline()
    ids = line.split(" runs: ", 1)[1].split(" all complete", 1)[0].split()
    if len(ids) != 3:
        raise ValueError(f"{agg_path}: expected 3 run ids in the first line, got {ids}")
    return ids


def _run_dir(uc, system, window, run_id):
    base = UC1_DIR if uc == "uc1" else UC2_DIR
    tag = f"-uc1-{system}5mx7-{window}-rep" if uc == "uc1" else f"-uc2-{system}-rep"
    hits = [d for d in glob.glob(os.path.join(base, run_id + "-*")) if tag in os.path.basename(d)]
    if len(hits) != 1:
        raise ValueError(f"run {run_id} ({uc} {system} {window}): expected one folder matching *{tag}*, got {hits}")
    return hits[0]


def cells():
    """[(uc, system, window or "", [run dirs])] for all 12 final cells, in thesis order."""
    out = [("uc1", s, w, [_run_dir("uc1", s, w, r) for r in _run_ids(_aggregate_file("uc1", s, w))])
           for w in WINDOWS for s in SYSTEMS]
    out += [("uc2", s, "", [_run_dir("uc2", s, None, r) for r in _run_ids(_aggregate_file("uc2", s))])
            for s in SYSTEMS]
    return out


def _load(d, name="summary.json"):
    with open(os.path.join(d, name)) as f:
        return json.load(f)


# ---------------------------------------------------------------- checks

def check_runs(all_cells):
    """Stops on the first run that is not usable for the thesis. Returns the analysis commit."""
    commits = set()
    for uc, system, window, dirs in all_cells:
        for d in dirs:
            s = _load(d)
            name = os.path.basename(d)
            if s.get("complete") is not True:
                raise ValueError(f"{name}: not complete")
            if s.get("analysis_measure_dirty"):
                raise ValueError(f"{name}: analysed from a dirty measure/ tree")
            commits.add(s.get("analysis_commit"))
            if s["reconciliation"].get("ok_with_late") is not True:
                raise ValueError(f"{name}: reconciliation ok_with_late is not true")
            if uc == "uc1":
                if s["offered_rate_per_s"] != UC1_OFFERED_PER_S[window]:
                    raise ValueError(f"{name}: offered {s['offered_rate_per_s']} per s, "
                                     f"expected {UC1_OFFERED_PER_S[window]} for {window}")
            else:
                if s.get("hash_ok") is not True:
                    raise ValueError(f"{name}: hash_ok is not true (not the reference game)")
                o = s["offered"]
                if (o["payments"], o["fire"], o["hit"]) != (UC2_PAYMENTS, UC2_FIRE, UC2_HIT):
                    raise ValueError(f"{name}: offered {o['payments']} ({o['fire']} fire, {o['hit']} hit), "
                                     f"expected {UC2_PAYMENTS} ({UC2_FIRE}, {UC2_HIT})")
                if round(o["per_s"], 1) != UC2_OFFERED_PER_S:
                    raise ValueError(f"{name}: offered {o['per_s']} per s, expected about {UC2_OFFERED_PER_S}")
                if s["gas"]["per_fire"] != s["gas"]["per_hit"]:
                    raise ValueError(f"{name}: gas per fire {s['gas']['per_fire']} != per hit {s['gas']['per_hit']}")
    if len(commits) != 1:
        raise ValueError(f"summaries come from different analysis commits: {sorted(map(str, commits))}")
    return commits.pop()


def _aggregate_runs(agg_path):
    """{line name: [per-run values]} from an aggregate .txt ('name  mean ± sd ... runs [v1, v2, v3]')."""
    out = {}
    with open(agg_path) as f:
        lines = f.read().splitlines()[1:]
    for line in lines:
        if " runs [" not in line:
            continue
        name = line.split()[0]
        values = line.split(" runs [", 1)[1].split("]", 1)[0]
        out[name] = ast.literal_eval("[" + values + "]")
    return out


def check_against_aggregates(rows):
    """Every per-run value that an aggregate also prints must be the same number."""
    compared = 0
    for r in rows:
        names = UC1_AGG_NAMES if r["use_case"] == "uc1" else UC2_AGG_NAMES
        if r["stat"] != "mean_sd" or r["board"] or r["metric"] not in names:
            continue
        agg = _aggregate_runs(_aggregate_file(r["use_case"], r["system"], r["window"] or None))
        theirs = agg[names[r["metric"]]]
        ours = [r["rep1"], r["rep2"], r["rep3"]]
        if ours != theirs:
            raise ValueError(f"{r['use_case']} {r['system']} {r['window']} {r['metric']}: "
                             f"{ours} here, {theirs} in the aggregate")
        compared += 1
    return compared


# ---------------------------------------------------------------- the table

def _row(uc, system, window, metric, unit, source, stat, values, run_ids, saturated, board=""):
    r = dict.fromkeys(CSV_FIELDS, "")
    r.update(use_case=uc, system=system, window=window, metric=metric, board=board, unit=unit, stat=stat,
             source=source, runs=" ".join(run_ids), saturated_runs=saturated)
    if stat == "range":                                  # every player's value of every run
        flat = [v for per_run in values for v in per_run]
        r.update(n=len(flat), min=min(flat), max=max(flat))
        return r
    ok = [v for v in values if v is not None]
    if len(ok) != len(values):
        raise ValueError(f"{uc} {system} {window} {metric}: missing in some runs: {values}")
    r.update(n=len(ok), mean=statistics.mean(ok), sd=statistics.stdev(ok) if len(ok) > 1 else 0.0,
             min=min(ok), max=max(ok), rep1=values[0], rep2=values[1], rep3=values[2])
    return r


def build_rows(all_cells):
    rows = []
    for uc, system, window, dirs in all_cells:
        sums = [_load(d) for d in dirs]
        run_ids = ["-".join(os.path.basename(d).split("-")[:2]) for d in dirs]     # 20261011-151853
        saturated = sum(bool(s.get("saturated")) for s in sums)
        for m in (UC1_METRICS if uc == "uc1" else UC2_METRICS):
            metric, unit, source, f, systems = m[:5]
            stat = m[5] if len(m) > 5 else "mean_sd"
            if system in systems:
                rows.append(_row(uc, system, window, metric, unit, source, stat, [f(s) for s in sums],
                                 run_ids, saturated))
        if uc == "uc2" and system == "perun":
            hub = [hub_gas(d) for d in dirs]
            for metric, source, i in UC2_HUB_METRICS:
                rows.append(_row(uc, system, window, metric, "gas", source, "mean_sd", [h[i] for h in hub],
                                 run_ids, saturated))
        if uc == "uc1" and system in ROLLUPS:
            metric, unit, source = UC1_BLOCK_METRIC
            rows.append(_row(uc, system, window, metric, unit, source, "mean_sd",
                             [block_interval(d) for d in dirs], run_ids, saturated))
        if uc == "uc1":
            lats = [window_latencies(d) for d in dirs]
            window_s = int(window[1:]) / 1000
            for (lat, n), s, d in zip(lats, sums, dirs):        # the ledgers reproduce the summary's misses
                if abs(miss_share(lat, n, window_s) - s["deadline_miss_share"]) > 1e-12:
                    raise ValueError(f"{os.path.basename(d)}: misses from the ledgers "
                                     f"{miss_share(lat, n, window_s)} != summary {s['deadline_miss_share']}")
            pooled = (sorted(x for lat, _ in lats for x in lat), sum(n for _, n in lats))
            for metric, share in UC1_DEADLINE_METRICS:
                value = deadline_for(*pooled, share)
                if value is not None:                           # no row where no payment deadline reaches it
                    rows.append(_pooled_row(system, window, metric, "s", UC1_DEADLINE_SOURCE, value,
                                            [deadline_for(lat, n, share) for lat, n in lats], pooled[1],
                                            run_ids, saturated))
            metric, unit, source = UC1_FLOOR_METRIC
            rows.append(_pooled_row(system, window, metric, unit, source, 1 - len(pooled[0]) / pooled[1],
                                    [1 - len(lat) / n for lat, n in lats], pooled[1], run_ids, saturated))
            boards = [{c["board"]: c["goodput_per_s"] for c in s["per_customer"].values()} for s in sums]
            if len({tuple(sorted(b)) for b in boards}) != 1:
                raise ValueError(f"uc1 {system} {window}: the customer boards differ between runs: {boards}")
            metric, unit, source = UC1_PER_BOARD
            for b in sorted(boards[0]):
                rows.append(_row(uc, system, window, metric, unit, source, "mean_sd", [x[b] for x in boards],
                                 run_ids, saturated, board=b))
        rows.append(_count_row(uc, system, window, sums, run_ids, saturated))
    return rows


def _count_row(uc, system, window, sums, run_ids, saturated):
    """How many runs reconcile (check_runs already stopped if any did not): '3 of 3' per cell."""
    r = dict.fromkeys(CSV_FIELDS, "")
    ok = sum(s["reconciliation"].get("ok_with_late") is True for s in sums)
    r.update(use_case=uc, system=system, window=window, metric="reconciliation_ok_with_late", unit="runs",
             stat="count", n=len(sums), mean=ok, source="reconciliation.ok_with_late", runs=" ".join(run_ids),
             saturated_runs=saturated)
    return r


def write_csv(rows, path=OUT_CSV):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: (repr(v) if isinstance(v, float) else v) for k, v in r.items()})


# ---------------------------------------------------------------- --verify: raw logs -> summary.json

IGNORED_IN_VERIFY = ("analysis_commit", "analysis_measure_dirty")


def _diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if not path and k in IGNORED_IN_VERIFY:
                continue
            out += _diff(a.get(k, "<missing>"), b.get(k, "<missing>"), f"{path}.{k}" if path else k)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: committed {a!r}, re-analysed {b!r}"]


def verify(all_cells):
    """Re-run the analysis on a copy of every run folder; the result must equal the committed summary."""
    from measure import analyze_uc1, analyze_uc2
    bad = 0
    for uc, system, window, dirs in all_cells:
        for d in dirs:
            with tempfile.TemporaryDirectory() as tmp:
                copy = os.path.join(tmp, os.path.basename(d))
                shutil.copytree(d, copy)
                fresh = (analyze_uc1 if uc == "uc1" else analyze_uc2).analyze(copy)
                fresh = json.loads(json.dumps(fresh))        # tuples -> lists, as in the written file
            diffs = _diff(_load(d), fresh)
            print(f"  {'OK  ' if not diffs else 'DIFF'} {os.path.basename(d)}", flush=True)
            for x in diffs[:10]:
                print(f"       {x}")
            bad += bool(diffs)
    if bad:
        raise ValueError(f"{bad} run(s) not reproduced from their raw logs")


def main():
    all_cells = cells()
    commit = check_runs(all_cells)
    n_runs = sum(len(c[3]) for c in all_cells)
    print(f"{n_runs} final runs in {len(all_cells)} cells, all complete and reconciled, analysis commit {commit[:7]}")
    if "--verify" in sys.argv:
        print("re-analysing every run from its raw logs:")
        verify(all_cells)
        print(f"all {n_runs} summaries reproduced from the raw logs")
    rows = build_rows(all_cells)
    compared = check_against_aggregates(rows)
    print(f"{compared} metric rows equal to their aggregate, run by run")
    write_csv(rows)
    print(f"{len(rows)} rows -> {os.path.relpath(OUT_CSV, REPO)}")


if __name__ == "__main__":
    main()
