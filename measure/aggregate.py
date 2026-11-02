"""Repetitions -> mean ± sample sd (n-1), min-max, per-run values (parent spec §8).

Provenance guard: refuses runs made from different git commits, runs from a
dirty tree (unless allow_dirty), summaries from different analysis commits,
and incomplete runs (unless
include_incomplete, in which case they are listed).

Saturated runs (summary `saturated`: the client's queue refused payments) are
listed in `_saturated_runs`; their latency figures describe only the admitted
payments and are set by MAX_QUEUE and DRAIN_S, so the latency lines carry a
note saying so, and goodput, succeeded share and confirmation time sit beside
them. A metric with fewer values than runs gets a warning in `_warnings`, except a metric of another
stack (STACK_ONLY, e.g. Perun's close_finalize_s_median in Arbitrum runs), which no run carries.

UC2 runs (summary use_case "uc2", measure/analyze_uc2.py) get UC2_HEADLINE
instead of HEADLINE; UC1 and UC2 runs never mix. A UC2 run whose boards'
event logs did not match the reference (hash_ok not true) did not play the
same game, so it is refused unless include_incomplete, and listed in
`_hash_mismatch_runs`."""
from __future__ import annotations

import json
import os
import statistics
import sys

def _median(d):
    return statistics.median(d.values()) if d else None


def _get(f, s):
    try:
        return f(s)
    except (KeyError, TypeError, statistics.StatisticsError):
        return None


HEADLINE = {
    "payment_latency_p50_s": lambda s: s["payment_latency_s"]["p50"],
    "payment_latency_p99_s": lambda s: s["payment_latency_s"]["p99"],
    "payment_latency_lower_bound_p50_s": lambda s: s["payment_latency_lower_bound_s"]["p50"],
    "payment_latency_lower_bound_p99_s": lambda s: s["payment_latency_lower_bound_s"]["p99"],
    "deadline_miss_share": lambda s: s["deadline_miss_share"],
    "open_s_median": lambda s: _median(s["open_s"]),
    "close_s_median": lambda s: _median(s["close_s"]),
    "payment_fees_eth": lambda s: s["fees_eth"]["payments_l2"],
    "goodput_per_s": lambda s: s["headline"]["goodput_per_s"],
    "succeeded_share": lambda s: s["headline"]["succeeded_share"],
    "dropped_share": lambda s: s["headline"]["dropped_share"],
    "confirmation_p50_s": lambda s: s["confirmation_s"]["p50"],
    "receive_lag_p99_s": lambda s: s["receive_lag_s"]["p99"],
    "backlog_max": lambda s: s["backlog_max"],
    "late_executed_total": lambda s: s["late_executed_total"],
    "close_finalize_s_median": lambda s: _median(s["close_finalize_s"]),   # Perun only; None for the rollups
}
LATENCY = ("payment_latency_p50_s", "payment_latency_p99_s")   # saturated runs: the queue bound sets these
# Metrics of one stack only -> the summary key they need. Runs of another stack lack the key; the metric
# is then n=0 without a warning (a run of that stack without a value still warns).
STACK_ONLY = {"close_finalize_s_median": "close_finalize_s"}


def _uc2_lat(group, kind, measure, p):
    return lambda s: s["latency_ms"][group][kind][measure][p]


def _uc2_median(key):
    return lambda s: statistics.median(v["s"] for v in s[key].values() if v and v.get("s") is not None)


UC2_HEADLINE = {
    "guests_generated_to_confirmed_p50_ms": _uc2_lat("guests", "all", "generated_to_confirmed", "p50"),
    "guests_generated_to_confirmed_p99_ms": _uc2_lat("guests", "all", "generated_to_confirmed", "p99"),
    "guests_submitted_to_confirmed_p50_ms": _uc2_lat("guests", "all", "submitted_to_confirmed", "p50"),
    "guests_submitted_to_confirmed_p99_ms": _uc2_lat("guests", "all", "submitted_to_confirmed", "p99"),
    "guests_fire_generated_to_confirmed_p50_ms": _uc2_lat("guests", "fire", "generated_to_confirmed", "p50"),
    "guests_hit_generated_to_confirmed_p50_ms": _uc2_lat("guests", "hit", "generated_to_confirmed", "p50"),
    "host_generated_to_confirmed_p50_ms": _uc2_lat("host", "all", "generated_to_confirmed", "p50"),
    "host_generated_to_confirmed_p99_ms": _uc2_lat("host", "all", "generated_to_confirmed", "p99"),
    "guests_latency_lower_bound_p50_ms": lambda s: s["latency_lower_bound_ms"]["guests"]["p50"],
    "guests_latency_lower_bound_p99_ms": lambda s: s["latency_lower_bound_ms"]["guests"]["p99"],
    "next_shot_miss_share_guests": lambda s: s["next_shot_miss_share"]["guests"]["share"],
    "next_shot_miss_share_host": lambda s: s["next_shot_miss_share"]["host"]["share"],
    "succeeded_share": lambda s: s["outcomes"]["shares"]["succeeded"],
    "unresolved_share": lambda s: s["outcomes"]["shares"]["unresolved"],
    "late_share": lambda s: s["outcomes"]["shares"]["late"],
    "goodput_per_s": lambda s: s["goodput_per_s"],
    "stretch": lambda s: s["match"]["stretch"],
    "tick_lag_p99_ms": lambda s: s["match"]["tick_lag_ms"]["pooled"]["p99"],
    "tick_lag_max_ms": lambda s: s["match"]["tick_lag_ms"]["pooled"]["max"],
    "open_s_median": _uc2_median("open"),
    "close_s_median": _uc2_median("close"),
    "gas_per_fire": lambda s: s["gas"]["per_fire"],
    "gas_per_hit": lambda s: s["gas"]["per_hit"],
}
UC2_LATENCY = tuple(k for k in UC2_HEADLINE if k.endswith("_ms") and "confirmed" in k)


def _load(d, name):
    return json.load(open(os.path.join(d, name)))


def aggregate(run_dirs, allow_dirty=False, include_incomplete=False) -> dict:
    mans = [_load(d, "manifest.json") for d in run_dirs]
    commits = {m["git_commit"] for m in mans}
    if len(commits) > 1:
        raise ValueError(f"runs come from different commits: {sorted(commits)}")
    hashes = {m.get("code_hash") for m in mans}
    if len(hashes) > 1:
        raise ValueError(f"runs ran different code (code_hash differs): {sorted(map(str, hashes))}")
    exps = {json.dumps(m.get("experiment"), sort_keys=True) for m in mans}
    if len(exps) > 1:
        raise ValueError(f"runs have different experiment configurations: {sorted(exps)}")
    dirty = [d for d, m in zip(run_dirs, mans) if m.get("git_dirty") is not False]
    if dirty and not allow_dirty:
        raise ValueError(f"runs from a dirty (or unknown) working tree, pass --allow-dirty: {dirty}")
    sums = [_load(d, "summary.json") for d in run_dirs]
    # the summaries must come from one analysis commit
    a_commits = {s.get("analysis_commit") for s in sums}
    if len(a_commits) > 1:
        raise ValueError(f"summaries come from different analysis commits, re-run the analysis: "
                         f"{sorted(map(str, a_commits))}")
    uc2 = [s.get("use_case") == "uc2" for s in sums]
    if any(uc2) and not all(uc2):
        raise ValueError(f"UC1 and UC2 runs mixed: {[d for d, u in zip(run_dirs, uc2) if u]} are UC2")
    uc2 = any(uc2)
    hash_bad = [d for d, s in zip(run_dirs, sums) if uc2 and s.get("hash_ok") is not True]
    if hash_bad and not include_incomplete:
        raise ValueError(f"runs whose boards' event logs differ from the reference (hash_ok false), "
                         f"pass --include-incomplete: {hash_bad}")
    incomplete = [d for d, s in zip(run_dirs, sums) if not s.get("complete")]
    if incomplete and not include_incomplete:
        raise ValueError(f"incomplete runs, pass --include-incomplete: {incomplete}")
    saturated = [d for d, s in zip(run_dirs, sums) if s.get("saturated")]
    exp = mans[0].get("experiment") or {}
    out = {"_incomplete_runs": incomplete, "_saturated_runs": saturated,
           "_saturated": [bool(s.get("saturated")) for s in sums], "_warnings": []}

    headline, latency = (UC2_HEADLINE, UC2_LATENCY) if uc2 else (HEADLINE, LATENCY)
    if uc2:
        out["_hash_mismatch_runs"] = hash_bad
    for name, get in headline.items():
        vals = [_get(get, s) for s in sums]
        ok = [v for v in vals if v is not None]
        out[name] = {"n": len(ok), "mean": statistics.mean(ok) if ok else None,
                     "sd": (statistics.stdev(ok) if len(ok) > 1 else 0.0) if ok else None,
                     "min": min(ok) if ok else None, "max": max(ok) if ok else None, "runs": vals}
        if len(ok) < len(sums) and (name not in STACK_ONLY or any(STACK_ONLY[name] in s for s in sums)):
            out["_warnings"].append(f"{name}: n={len(ok)} of {len(sums)} runs (the others have no value)")
        if name in latency and saturated:
            out[name]["note"] = (f"{len(saturated)} of {len(sums)} runs saturated: set by "
                                 f"MAX_QUEUE={exp.get('MAX_QUEUE')} and DRAIN_S={exp.get('DRAIN_S')}")
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    agg = aggregate(args, allow_dirty="--allow-dirty" in sys.argv, include_incomplete="--include-incomplete" in sys.argv)
    if agg.get("_hash_mismatch_runs"):
        print("HASH MISMATCH runs included:", agg["_hash_mismatch_runs"])
    if agg["_incomplete_runs"]:
        print("INCOMPLETE runs included:", agg["_incomplete_runs"])
    for w in agg["_warnings"]:
        print("WARNING:", w)
    print(f"{'saturated':24s} {sum(agg['_saturated'])} of {len(agg['_saturated'])}  runs {agg['_saturated']}")
    for k, v in agg.items():
        if k.startswith("_"):
            continue
        note = f"  [{v['note']}]" if v.get("note") else ""
        if v["n"] == 0:
            print(f"{k:24s} n=0  runs {v['runs']}{note}")
            continue
        print(f"{k:24s} {v['mean']:.4g} ± {v['sd']:.2g}  (min {v['min']:.4g}, max {v['max']:.4g}, n={v['n']})  runs {v['runs']}{note}")


if __name__ == "__main__":
    main()
