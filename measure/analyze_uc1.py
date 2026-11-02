"""One UC1 run folder -> summary.json (parent spec §5; cluster spec §7.2, §7.3).

Payment latency = confirmation - window arrival (t_confirmed - t_intended),
over succeeded payments. Its two components are queueing (t_submitted -
t_queued) and confirmation (t_confirmed - t_submitted). Every timestamp of a
payment is taken on the customer's own board, so latency crosses no boards and
needs no clock-offset correction. Open/Close are also single-board durations.

Percentiles use linear interpolation between order statistics (Hyndman-Fan
type 7, numpy's default "linear"); p50 is the ordinary median.

Deadline miss (ruling R15): windows are periodic, so every ledger row is one
window with deadline t_intended + WINDOW_MS. A window is missed if its payment
is not succeeded, or was confirmed after its deadline. Every window is in the
denominator. WINDOW_MS must be in experiment.env (no silent fallback). Rows
still in generated/queued/submitted (a rebuilt crashed ledger) are mapped to
"unresolved" and counted in mapped_to_unresolved. windows_expected
(DURATION_S*1000/WINDOW_MS) is reported beside the ledger row count so lost
events are visible. The headline is the share per window size.

Fees: the fee of every row with gas_used and effective_gas_price, whatever the
outcome, so reverted payments (failed, error "reverted") are paid for;
`payments_l2_reverted` is the part from those. gas.payment_l2_median is over
succeeded rows. gas.effective_gas_price_max_wei (arbitrum/zksync only, not perun) is
the highest effective_gas_price among succeeded rows: how close the run's base fee
came to lifecycle.json's fee_cap_wei (the admission ceiling payclient actually used).

Rows that were submitted but never resolved (drain deadline) have no receipt,
so no gas data and no fee; they are counted in fees_unpriced_submitted_rows.

Reconciliation compares the sum the customers' ledgers call succeeded with the
sensor's L2 balance change, which is independent of the ledgers. `ok` needs
equality. A payment still in flight at the drain deadline may execute later, so
`explained_wei` is the sum of the unresolved rows that have a t_submitted and
`ok_explained` holds when 0 <= difference <= explained_wei. A sensor-balance.json
with a null balance (cast failed) gives reconciliation None and is listed as
missing.

Saturation (cluster spec §8 step 3): a row that failed with "queue full" was
refused by the client's bounded queue (MAX_QUEUE). If any row did, the run is
`saturated`: its latency percentiles then describe only the admitted payments
and are set by MAX_QUEUE and DRAIN_S (queueing ~ MAX_QUEUE / service rate), so
the `headline` block carries goodput, the admitted/dropped shares and the
confirmation time next to them.

Survivorship: payment_latency_s covers succeeded payments only. Whenever any
payment did not succeed (saturated or not: zkSync leaves most unresolved at
w100 without filling the queue), headline.latency_note says so, and
payment_latency_lower_bound_s counts every other payment too, with its
customer's last t_confirmed - its t_intended: it was still unconfirmed then, so
each value, and each percentile, is a lower bound.

Goodput of a customer = its succeeded payments / (its last t_confirmed - its
first t_confirmed), the confirmation span, not DURATION_S. n payments span n-1
intervals, so a run that pays exactly the offered rate shows it raised by a
factor n/(n-1) (5.005 for 5 per second over 900 s); kept as defined. The pooled goodput
divides all succeeded payments by the span from the first to the last
confirmation of any customer (board clocks agree within the clock gate's 5 ms).

Receive lag = t_received (customer board) - t_sent (sensor board) of every row
of rows_received.csv: how long a sensor row took to reach the customer's
process. It crosses two boards, so it carries their clock offset (< 5 ms by the
clock gate). t_intended is taken on receipt, so this lag is not in the payment
latency.

Offered rate = EXPECTED_CUSTOMERS (default 6) x 1000 / WINDOW_MS, payments per
second: a property of the experiment, not of which customer folders survived. The
achieved rate is goodput_per_s above (it starts at the first confirmation, not
at the first arrival). confirmed_per_10s counts the succeeded confirmations in
10 s bins from the first t_intended of any customer, for the figure.

Backlog of a customer at time t = its rows with t_queued <= t that have not
left it by t. A row leaves at its t_confirmed. The ledger sets t_confirmed only
for a row with an on-chain receipt (succeeded or reverted) and for a row whose
submit failed before sending (payclient: the time of the failure). The ledger
keeps no time for the other non-receipt outcomes (rejected by the node, receipt
timeout "unresolved: no receipt"), so those rows, like the rows still pending
at the drain deadline, stay in the backlog to the end of the sweep. backlog_max is the largest backlog, from a sweep over
the queue and finish events (a finish before a queue at the same instant);
per customer and overall (all customers' events together, board clocks within
5 ms).

Late executions (rollups: zkSync measurement path, from lifecycle.json's nonce
bookkeeping; Perun: from the channel, see below): late_executed of a customer = executed_onchain (+
executed_after_close, see not_admitted below) - (succeeded rows + non-succeeded
rows with a receipt, i.e. gas data: a reverted payment also used a nonce), the payments that executed on chain without the ledger knowing,
e.g. in flight at the drain deadline. null if the lifecycle has no
executed_onchain (folders from before it was recorded, or Close failed);
late_executed_total is null if any one customer's is null (e.g. one customer's
Close failed), and then late_executed_wei and ok_with_late are null for the
whole run: the nonces must cover every customer. reconciliation.ok_with_late
holds when the sensor received exactly succeeded_wei + late_executed_wei
(= late_executed_total x PRICE_WEI); null if either is unknown. Unlike
ok_explained (a bound from the submitted-but-unresolved rows) it is an
equality proven by the nonces. close_pending is pending - latest nonce just
before Close (pending_before_close), per customer, null if not recorded.
not_admitted is, per customer, the number of sent payments the node had not
admitted before Close (submitted_nonce - pending after the bounded admission
wait), null if not recorded (older runs). Some of them may still execute after
Close (zkSync can hold nonces queued behind a missing one): the lifecycle's
executed_after_close (final_nonce - close_nonce - 1, read after a bounded
post-Close wait) counts those, and late_executed includes them (it is
executed_onchain + executed_after_close - succeeded - receipt failures;
executed_after_close is taken as 0 when absent, i.e. runs before it was
recorded, and late_executed is null when it was recorded as null).

`complete` needs every expected customer directory (EXPECTED_CUSTOMERS,
default 6), every lifecycle.json complete, no RUN_FAILED file, no missing
items, a readable sensor log (logs/bath1-sensor.log) that dropped no customer,
no ledger shorter than windows_expected and no customer stream reporting
complete False (logs/*-customer.log; an unreadable customer log is listed); a
folder with missing ledgers or sensor-balance.json gives an incomplete summary
listing them, not an exception. What Close means is in `close_definition`
(per stack, CLOSE_DEFINITION; for the rollups the withdrawal submission, parent
§11.4). `analysis_commit` is the repository HEAD when the summary was made.

The stack is experiment.env STACK (missing = arbitrum; arbitrum and zksync are
analysed alike; an unknown stack raises). Perun (STACK=perun) differs in:
- open_s = open.t_funded - open.t_start; open gas/fee from the same keys
  (l1_gas_used, l1_fee_wei; Anvil is the L1).
- close_s = close.t_withdrawn - close.t_start (finalize + concludeFinal +
  withdrawal, timed on the customer); close_finalize_s = t_finalized - t_start
  beside it. The customer's close cost is gas.close_l1_median and
  fees_eth.close_l1; gas.withdraw_l2_median and fees_eth.withdraw_l2 are null.
  The sensor's own close transactions (results/sensor/sensor-result.json
  close_gas) are reported apart: gas.close_sensor_l1_total, fees_eth.
  close_sensor_l1, and gas.close_sensor_l1_per_channel = total / settled
  channels, a mean: the sensor's settles overlap and the backend hands out no
  per-channel receipts, so a per-channel median does not exist.
- A payment is an off-chain update: gas_used = 0 on every succeeded row, so
  payment gas and fees are 0, and fees_unpriced_submitted_rows is 0.
- `channels[c]`: the sensor's channel of customer c (matched by the Open
  reply's channel_id, else by the name customer-<account>): final_version
  (the sensor's last version), broken (the customer's channel_before_close),
  updates_accepted, settled, the sensor's balance, the customer's close view
  or close_error (a broken channel is never closed, ruling R-P6, and is still
  reported), and unresolved_in_final_state = (the sensor's final balance -
  sum succeeded) / PRICE_WEI when positive, else 0 (Review Focus 1: an update
  the customer gave up on that the sensor holds).
- reconciliation is three-way (Review Focus 4); `ok` needs all of: (a) per
  customer, sum succeeded == the sensor's final balance in that channel; (b)
  per customer, close.final_balances_wei and final_version == the sensor's
  (null and not ok when the customer has no close); (c) the sensor's L1
  balance change (sensor-balance.json l1_balance_*) + its own close fee ==
  the sum of the sensor's final balances over all its channels; and no sensor
  channel without a customer. sensor_received_wei = L1 change + close fee,
  difference_wei = that - succeeded_wei. ok_explained relaxes (a) to 0 <=
  sensor's balance - succeeded <= the customer's unresolved submitted rows
  (per channel; (b), (c) still needed). Late executions come from the
  channel, not from nonces: every Perun payment is PRICE_WEI, so
  late_executed of a customer = unresolved_in_final_state, exact (null when
  the sensor's surplus is negative or not a whole number of payments, or its
  channel is unknown); late_executed_total = their sum (null if any is null),
  late_executed_wei = late_executed_total x PRICE_WEI, and ok_with_late holds
  when per customer the sensor's final balance == succeeded + late, (b), (c),
  no unmatched sensor channel and sensor_received_wei == succeeded_wei +
  late_executed_wei (null if late_executed_wei is). These are the payments a
  customer marked unresolved at the drain deadline that the sidecar still
  executed (in flight or queued until quiesce). close_pending and
  not_admitted are null (no nonces). l1_matches_settled_channels
  checks (c) against the settled channels only, to locate a gap.
- `complete` also needs results/sensor/sensor-result.json with complete true
  (complete_checks.sensor_result_complete; null if the file is missing)."""
from __future__ import annotations

import ast
import csv
import glob
import json
import os
import re
import statistics
import subprocess
import sys

from measure.fileio import exists, open_text
from measure.ledger import load_log
from measure.manifest import recorder_summary, stream_window

NON_TERMINAL = ("generated", "queued", "submitted")


def _pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    h = p * (len(xs) - 1)
    lo = int(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (h - lo)


def _dist(xs):
    return {"n": len(xs), "p50": _pct(xs, .5), "p90": _pct(xs, .9), "p99": _pct(xs, .99),
            "max": max(xs) if xs else None}


def _env(run_dir):
    p = os.path.join(run_dir, "experiment.env")
    if not os.path.exists(p):
        return {}
    return dict(l.strip().split("=", 1) for l in open(p) if "=" in l and not l.startswith("#"))


SENSOR_BOARD = "bath1"      # deploy/cluster SENSOR_BOARD; logs are fetched as logs/<board>-sensor.log
CUSTOMER_BOARDS = ("bath2", "bath3", "bath4", "bath5", "bath6", "bath7")   # deploy/cluster's default CUSTOMERS


def customer_boards(env) -> tuple:
    """c<n> ran on the n-th board of the run's CUSTOMERS (deploy/cluster bash array, e.g.
    "(bath2 bath3) # comment" in experiment.env); deploy/cluster's default when the env does not
    set it. A CUSTOMERS value that is not a plain list of board names raises (final review Minor 5)."""
    return board_list(env, "CUSTOMERS", CUSTOMER_BOARDS)


def board_list(env, key, default) -> tuple:
    """The boards of a deploy/cluster bash array in experiment.env (CUSTOMERS, UC2's PLAYERS),
    `default` when the env does not set it; a value that is not a plain list of board names raises."""
    if key not in env:
        return default
    m = re.match(r"\s*\(([^)]*)\)\s*(#.*)?$", env[key])
    boards = tuple(m.group(1).split()) if m else ()
    if not boards or not all(re.fullmatch(r"bath\d+", b) for b in boards):
        raise ValueError(f"experiment.env {key} is not a list of boards: {env[key]!r}")
    return boards
QUEUE_FULL = "queue full"   # payclient's error for a payment its bounded queue (MAX_QUEUE) refused

# What Close means per stack (experiment.env STACK; a missing STACK is arbitrum). An unknown stack raises.
CLOSE_DEFINITION = {
    "arbitrum": "withdrawal submission on the L2; the 7-day challenge period is documentation (parent §11.4)",
    "zksync": ("withdrawal submission on the L2 (L2BaseToken.withdraw), timed to its L2 receipt; batch commit, "
               "proof and execution are documentation (parent §11.4)"),
    "perun": ("cooperative close: off-chain finalize + concludeFinal + withdrawal, confirmed on the L1, timed on "
              "the customer"),
}
SENSOR_RESULT = "results/sensor/sensor-result.json"   # the Perun sensor node's result (uc1_sensor.go)


def _result(path):
    """The last line starting with '{' of a log, a Python dict repr; None if unreadable."""
    try:
        with open(path) as f:
            lines = [l for l in f.read().splitlines() if l.startswith("{")]
        r = ast.literal_eval(lines[-1])
        return r if isinstance(r, dict) else None
    except (OSError, ValueError, SyntaxError, IndexError):
        return None


def _num(v):
    return v not in (None, "")


def _board_model(run_dir, board):
    try:
        return json.load(open(os.path.join(run_dir, "facts-start", f"{board}.json"))).get("model")
    except (OSError, ValueError):
        return None


def _receive_lag(cdir):
    """t_received - t_sent of every row of rows_received.csv; None if the file is missing or unreadable."""
    path = os.path.join(cdir, "rows_received.csv")
    if not exists(path):
        return None
    try:
        with open_text(path) as f:
            return [float(r["t_received"]) - float(r["t_sent"]) for r in csv.DictReader(f)
                    if _num(r.get("t_received")) and _num(r.get("t_sent"))]
    except (OSError, ValueError, KeyError, EOFError):
        return None


def _backlog_max(events):
    """Largest running sum of (t, +1 queued / -1 finished) events; a finish before a queue at the same t."""
    n = peak = 0
    for _, d in sorted(events):
        n += d
        peak = max(peak, n)
    return peak


def _balance(path):
    """(before, after) from sensor-balance.json, or None if either is not an integer."""
    try:
        bal = json.load(open(path))
        before, after = bal["l2_balance_before_wei"], bal["l2_balance_after_wei"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (before, after)):
        return None
    return before, after


def _load_json(path):
    """The parsed JSON object at path, or None if missing, unreadable or not an object."""
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _int(v):
    """An integer (int or decimal string, as Go writes big.Int); None otherwise."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _ints(vs):
    """A list of _int, or None if vs is not a list or any element is not an integer."""
    if not isinstance(vs, list):
        return None
    out = [_int(v) for v in vs]
    return None if None in out else out


def _perun(run_dir, lives, ok_wei, explained, price):
    """Perun's channel table and three-way reconciliation (Review Focus 1 and 4).

    Returns (channels, reconciliation, sensor_gas, sensor_result_complete, missing, late). A customer's
    channel is the sensor's channel with the channel_id of the customer's Open reply, else the one named
    customer-<account> (the sidecar's registration name). Balances are [customer, sensor]."""
    missing = []
    sr = _load_json(os.path.join(run_dir, *SENSOR_RESULT.split("/")))
    if sr is None:
        missing.append(SENSOR_RESULT)
    sres_complete = None if sr is None else sr.get("complete") is True
    schans = [ch for ch in (sr or {}).get("channels") or [] if isinstance(ch, dict)]
    by_id = {str(ch.get("channel_id", "")).lower(): ch for ch in schans}
    by_name = {ch.get("name"): ch for ch in schans}
    channels, matched, late = {}, set(), {}
    for c, life in lives.items():
        cid = str((life.get("open") or {}).get("channel_id", "")).lower()
        ch = by_id.get(cid) if cid else None
        if ch is None and life.get("account") is not None:
            ch = by_name.get(f"customer-{life['account']}")
        if ch is not None:
            matched.add(id(ch))
        sbal = _ints(ch.get("balances_wei")) if ch else None
        sensor_wei = sbal[1] if sbal and len(sbal) == 2 else None
        held = sensor_wei - ok_wei[c] if sensor_wei is not None else None
        unres = None
        if held is not None and price:
            unres = max(held, 0) // price if max(held, 0) % price == 0 else max(held, 0) / price
        # Late executions: every Perun payment is PRICE_WEI, so the sensor's surplus over the succeeded
        # rows is an exact count; null when it is not a whole, non-negative number of payments.
        late[c] = unres if isinstance(unres, int) and held >= 0 else None
        before = life.get("channel_before_close") or {}
        close = life.get("close") or {}
        channels[c] = {"channel_id": ch.get("channel_id") if ch else (cid or None),
                       "final_version": ch.get("last_version") if ch else None,
                       "broken": before.get("broken") if "broken" in before else None,
                       "updates_accepted": ch.get("accepted_updates") if ch else None,
                       "payments_accepted": ch.get("payments") if ch else None,
                       "updates_rejected": ch.get("rejected_updates") if ch else None,
                       "final": ch.get("final") if ch else None,
                       "settled": ch.get("settled") if ch else None,
                       "settle_error": (ch.get("settle_error") or None) if ch else None,
                       "sensor_balance_wei": sensor_wei,
                       "succeeded_wei": ok_wei[c],
                       "customer_final_version": close.get("final_version"),
                       "customer_final_balances_wei": _ints(close.get("final_balances_wei")),
                       "close_error": life.get("close_error") or None,
                       "unresolved_in_final_state": unres}
    unmatched = [ch.get("channel_id") for ch in schans if id(ch) not in matched]
    gas = (sr or {}).get("close_gas") if isinstance((sr or {}).get("close_gas"), dict) else None
    sensor_gas = {"gas": _int(gas.get("gas")) if gas else None, "fee_wei": _int(gas.get("fee_wei")) if gas else None,
                  "settled": sum(1 for ch in schans if ch.get("settled") is True)}
    bal_path = os.path.join(run_dir, "results", "sensor-balance.json")
    bal = _load_json(bal_path) or {}
    b0, b1 = bal.get("l1_balance_before_wei"), bal.get("l1_balance_after_wei")
    l1 = None
    if all(isinstance(v, int) and not isinstance(v, bool) for v in (b0, b1)):   # as _balance: cast's numbers
        l1 = b1 - b0
    elif os.path.exists(bal_path):
        missing.append("results/sensor-balance.json (L1 balance not readable, e.g. null)")
    else:
        missing.append("results/sensor-balance.json")
    if sr is None or l1 is None:
        return channels, None, sensor_gas, sres_complete, missing, late
    per, a_all, b_all, x_all = {}, True, True, True
    for c, ch in channels.items():
        a = ch["sensor_balance_wei"] is not None and ch["sensor_balance_wei"] == ok_wei[c]
        cb = ch["customer_final_balances_wei"]
        sch = by_id.get(str(ch["channel_id"] or "").lower())
        sbal = _ints(sch.get("balances_wei")) if sch else None
        b = (None if cb is None else
             (sbal is not None and cb == sbal and ch["customer_final_version"] == ch["final_version"]))
        held = None if ch["sensor_balance_wei"] is None else ch["sensor_balance_wei"] - ok_wei[c]
        x = held is not None and 0 <= held <= explained[c]
        per[c] = {"succeeded_wei": ok_wei[c], "sensor_final_wei": ch["sensor_balance_wei"],
                  "succeeded_matches_sensor": a, "customer_matches_sensor": b,
                  "explained_wei": explained[c]}
        a_all, b_all, x_all = a_all and a, b_all and b is True, x_all and x
    sbals = [_ints(ch.get("balances_wei")) for ch in schans]
    final_sum = sum(v[1] for v in sbals if v and len(v) == 2) if all(v and len(v) == 2 for v in sbals) else None
    settled_sum = (sum(v[1] for v, ch in zip(sbals, schans) if ch.get("settled") is True)
                   if final_sum is not None else None)
    fee = sensor_gas["fee_wei"]
    received = l1 + fee if fee is not None else None
    l1_ok = None if received is None or final_sum is None else received == final_sum
    l1_settled = None if received is None or settled_sum is None else received == settled_sum
    ok_sum = sum(ok_wei.values())
    ok = bool(a_all and b_all and l1_ok and not unmatched)
    late_known = bool(late) and None not in late.values()
    late_wei = sum(late.values()) * price if late_known and price else None
    a_late = late_wei is not None and all(ch["sensor_balance_wei"] == ok_wei[c] + late[c] * price
                                          for c, ch in channels.items())
    ok_with_late = None if late_wei is None else bool(a_late and b_all and l1_ok and not unmatched
                                                      and received == ok_sum + late_wei)
    recon = {"succeeded_wei": ok_sum, "sensor_received_wei": received,
             "difference_wei": None if received is None else received - ok_sum, "ok": ok,
             "explained_wei": sum(explained.values()),
             "ok_explained": bool(b_all and l1_ok and x_all and not unmatched),
             "late_executed_wei": late_wei, "ok_with_late": ok_with_late,
             "sensor_l1_balance_change_wei": l1, "sensor_close_fee_wei": fee,
             "sensor_final_balances_wei": final_sum, "sensor_settled_balances_wei": settled_sum,
             "l1_matches_final_balances": l1_ok, "l1_matches_settled_channels": l1_settled,
             "unmatched_sensor_channels": unmatched, "per_customer": per}
    return channels, recon, sensor_gas, sres_complete, missing, late


def late_from_nonces(life, succeeded, receipt_failures):
    """Rollup late executions of one payment client (see the module docstring): executed_onchain +
    executed_after_close (0 when absent, older runs) - succeeded rows - non-succeeded rows with a receipt;
    None when executed_onchain is not recorded or executed_after_close was recorded as null."""
    executed = life.get("executed_onchain")
    after = life.get("executed_after_close", 0)          # absent in runs before it was recorded
    executed = executed + after if isinstance(executed, int) and isinstance(after, int) else None
    return executed - (succeeded + receipt_failures) if isinstance(executed, int) else None


def _analysis_commit():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, timeout=30)
        d = subprocess.run(["git", "status", "--porcelain", "--", "measure"], capture_output=True, text=True,
                           cwd=repo, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None, None
    commit = r.stdout.strip() if r.returncode == 0 else None
    return commit, (bool(d.stdout.strip()) if d.returncode == 0 else None)


def analyze(run_dir) -> dict:
    env = _env(run_dir)
    if "WINDOW_MS" not in env:
        raise ValueError(f"{run_dir}/experiment.env has no WINDOW_MS")
    stack = env.get("STACK", "arbitrum")
    if stack not in CLOSE_DEFINITION:
        raise ValueError(f"{run_dir}/experiment.env STACK={stack!r} is not one of {sorted(CLOSE_DEFINITION)}")
    perun = stack == "perun"
    window_ms = int(env["WINDOW_MS"])
    window_s = window_ms / 1000
    expected = int(env.get("EXPECTED_CUSTOMERS", 6))
    boards = customer_boards(env)
    windows_expected = int(env["DURATION_S"]) * 1000 // window_ms if "DURATION_S" in env else None
    lat, q_lat, c_lat, misses, windows, outcomes, mapped = [], [], [], 0, 0, {}, 0
    open_s, close_s, gas_pay, fees_pay, fees_rev = {}, {}, [], 0, 0
    eff_price_max = None   # highest effective_gas_price among succeeded payments (arbitrum/zksync only)
    dep_gas, dep_fee, wd_gas, wd_fee = [], 0, [], 0
    ok_sum, diag, not_complete, missing, per_customer = 0, {}, [], [], {}
    in_flight_wei, unpriced, queue_full, customers, all_conf, all_rx = 0, 0, 0, {}, [], []
    all_backlog, late, close_pending, not_admitted, t_first = [], {}, {}, {}, None
    lat_lb_open = []   # lower bounds of the latency of payments that never succeeded (survivorship, below)
    close_fin_s, cl1_gas, cl1_fee, lives, c_ok_wei, c_explained = {}, [], 0, {}, {}, {}   # Perun only
    cdirs = sorted(glob.glob(os.path.join(run_dir, "results", "c[0-9]*")))
    for cdir in cdirs:
        c = os.path.basename(cdir)
        csv_path = os.path.join(cdir, "ledger.csv")
        log_path = os.path.join(cdir, "ledger.log.jsonl")
        if exists(csv_path):
            with open_text(csv_path) as f:
                rows = list(csv.DictReader(f))
        elif exists(log_path):
            rows = load_log(log_path)                          # crashed client: rebuild
        else:
            rows = []
            missing.append(f"results/{c}/ledger")
        life_path = os.path.join(cdir, "lifecycle.json")
        life = json.load(open(life_path)) if os.path.exists(life_path) else {}
        if not life:
            missing.append(f"results/{c}/lifecycle.json")
        if not life.get("complete"):
            not_complete.append(c)
        if perun:                                          # Go writes the fees as decimal strings
            if "open" in life:
                open_s[c] = life["open"]["t_funded"] - life["open"]["t_start"]
                dep_gas.append(life["open"]["l1_gas_used"]); dep_fee += int(life["open"]["l1_fee_wei"])
            if "close" in life and "t_withdrawn" in life["close"]:
                close_s[c] = life["close"]["t_withdrawn"] - life["close"]["t_start"]
                close_fin_s[c] = life["close"]["t_finalized"] - life["close"]["t_start"]
                cl1_gas.append(life["close"]["l1_gas_used"]); cl1_fee += int(life["close"]["l1_fee_wei"])
            lives[c] = life
        else:
            if "open" in life:
                open_s[c] = life["open"]["t_l2_credited"] - life["open"]["t_start"]
                dep_gas.append(life["open"]["l1_gas_used"]); dep_fee += life["open"]["l1_fee_wei"]
            if "close" in life and "t_confirmed" in life["close"]:
                close_s[c] = life["close"]["t_confirmed"] - life["close"]["t_start"]
                wd_gas.append(life["close"]["l2_gas_used"]); wd_fee += life["close"]["l2_fee_wei"]
        diag[c] = {k: life.get(k) for k in ("resyncs", "reconnects", "outage_seconds", "quiesce_timeouts",
                                             "late", "duplicates")}
        for k in ("open_error", "close_error", "error"):
            if life.get(k) not in (None, ""):
                diag[c][k] = life[k]
        per_customer[c] = {"ledger_rows": len(rows), "windows_expected": windows_expected}
        c_conf, c_clat, c_full, c_receipt_fail, c_backlog, c_open = [], [], 0, 0, [], []
        c_ok_wei[c] = c_explained[c] = 0
        for r in rows:
            out = r["outcome"]
            if _num(r.get("t_intended")):
                t_first = float(r["t_intended"]) if t_first is None else min(t_first, float(r["t_intended"]))
            if _num(r.get("t_queued")):
                c_backlog.append((float(r["t_queued"]), 1))
                if _num(r.get("t_confirmed")):
                    c_backlog.append((float(r["t_confirmed"]), -1))
            if out in NON_TERMINAL:
                out = "unresolved"
                mapped += 1
            outcomes[out] = outcomes.get(out, 0) + 1
            windows += 1
            priced = _num(r.get("gas_used")) and _num(r.get("effective_gas_price"))
            if priced:
                fee = int(r["gas_used"]) * int(r["effective_gas_price"])
                fees_pay += fee
                if r.get("error") == "reverted":
                    fees_rev += fee
            elif out != "succeeded" and _num(r.get("t_submitted")):
                unpriced += 1
            if priced and out != "succeeded":
                c_receipt_fail += 1
            if out == "unresolved" and _num(r.get("t_submitted")):
                in_flight_wei += int(r["amount_wei"])
                c_explained[c] += int(r["amount_wei"])
            if out == "failed" and r.get("error") == QUEUE_FULL:
                c_full += 1
            if out != "succeeded":
                misses += 1
                if _num(r.get("t_intended")):
                    c_open.append(float(r["t_intended"]))
                continue
            ok_sum += int(r["amount_wei"])
            c_ok_wei[c] += int(r["amount_wei"])
            conf, intended = float(r["t_confirmed"]), float(r["t_intended"])
            lat.append(conf - intended)
            c_conf.append(conf)
            if _num(r.get("t_queued")) and _num(r.get("t_submitted")):
                q_lat.append(float(r["t_submitted"]) - float(r["t_queued"]))
                c_lat.append(conf - float(r["t_submitted"]))
                c_clat.append(conf - float(r["t_submitted"]))
            gas_pay.append(int(r["gas_used"]))
            if _num(r.get("effective_gas_price")):
                ep = int(r["effective_gas_price"])
                eff_price_max = ep if eff_price_max is None else max(eff_price_max, ep)
            misses += conf > intended + window_s
        queue_full += c_full
        all_backlog += c_backlog
        late[c] = late_from_nonces(life, len(c_conf), c_receipt_fail)
        close_pending[c] = life.get("pending_before_close")
        not_admitted[c] = life.get("not_admitted")
        all_conf += c_conf
        if c_open and c_conf:   # still unconfirmed at the customer's last confirmation: a lower bound each
            lat_lb_open += [max(0.0, max(c_conf) - t) for t in c_open]   # every one counts, at least 0
        span = max(c_conf) - min(c_conf) if len(c_conf) > 1 else None
        rx = _receive_lag(cdir)
        all_rx += rx or []
        k = int(c[1:])
        board = boards[k - 1] if 1 <= k <= len(boards) else None
        customers[c] = {"board": board, "board_model": _board_model(run_dir, board) if board else None,
                        "offered": len(rows), "succeeded": len(c_conf), "queue_full": c_full,
                        "confirmation_span_s": span,
                        # span = first -> last confirmation; this stands in for the plan's achieved rate (R1)
                        "goodput_per_s": len(c_conf) / span if span else None,
                        "confirmation_p50_s": _pct(c_clat, .5),
                        "backlog_max": _backlog_max(c_backlog), "late_executed": late[c],
                        "receive_lag_s": None if rx is None else {"n": len(rx), "p50": _pct(rx, .5),
                                                                   "p99": _pct(rx, .99),
                                                                   "max": max(rx) if rx else None}}
    late_total = sum(late.values()) if late and None not in late.values() else None
    late_wei = late_total * int(env["PRICE_WEI"]) if late_total is not None and "PRICE_WEI" in env else None
    bal_path = os.path.join(run_dir, "results", "sensor-balance.json")
    recon = None
    bal = _balance(bal_path) if os.path.exists(bal_path) and not perun else None
    if perun:
        channels, recon, sensor_gas, sres_complete, p_missing, p_late = _perun(
            run_dir, lives, c_ok_wei, c_explained, _int(env.get("PRICE_WEI")))
        missing += p_missing
        for c in late:                                     # no nonces: the late count comes from the channel
            late[c] = p_late.get(c)
            customers[c]["late_executed"] = late[c]
        late_total = sum(late.values()) if late and None not in late.values() else None
    elif bal is not None:
        observed = bal[1] - bal[0]
        diff = observed - ok_sum
        recon = {"succeeded_wei": ok_sum, "sensor_received_wei": observed,
                 "difference_wei": diff, "ok": observed == ok_sum,
                 "explained_wei": in_flight_wei, "ok_explained": 0 <= diff <= in_flight_wei,
                 "late_executed_wei": late_wei,
                 "ok_with_late": None if late_wei is None else observed == ok_sum + late_wei}
    elif os.path.exists(bal_path):
        missing.append("results/sensor-balance.json (balance not readable, e.g. null)")
    else:
        missing.append("results/sensor-balance.json")
    run_failed = os.path.exists(os.path.join(run_dir, "RUN_FAILED"))
    unreadable, dropped, stream_incomplete = [], [], []
    sensor_log = os.path.join("logs", f"{SENSOR_BOARD}-sensor.log")
    sres = _result(os.path.join(run_dir, sensor_log))
    if sres is None:
        unreadable.append(sensor_log)
    else:
        dropped = list(sres.get("dropped") or [])
    for p in sorted(glob.glob(os.path.join(run_dir, "logs", "*-customer.log"))):
        cres = _result(p)
        if cres is None:
            unreadable.append(os.path.join("logs", os.path.basename(p)))
        elif cres.get("complete") is False:
            stream_incomplete.append(os.path.basename(p)[:-len("-customer.log")])
    have_logs = bool(glob.glob(os.path.join(run_dir, "logs", "*-customer.log")))   # runs since customer logs
    for b in boards[:expected] if have_logs else ():   # one never fetched (scp is best-effort) is listed too
        if not os.path.exists(os.path.join(run_dir, "logs", f"{b}-customer.log")):
            unreadable.append(os.path.join("logs", f"{b}-customer.log (missing)"))
    short ={c: [v["ledger_rows"], windows_expected] for c, v in per_customer.items()
             if windows_expected is not None and v["ledger_rows"] < windows_expected}
    complete = (len(cdirs) >= expected and not not_complete and not run_failed and not missing
                and sres is not None and not dropped and not short and not stream_incomplete
                and (not perun or sres_complete is True))
    win = stream_window(run_dir)
    rec = {}
    for p in sorted(glob.glob(os.path.join(run_dir, "recorder", "*.csv"))):
        s = recorder_summary(p, win)
        rec[os.path.basename(p)[:-4]] = {k: s.get(k) for k in ("undervolted_share", "max_temp_c", "samples",
                                                               "skipped_lines", "scope", "mean_cpu_busy_pct")}
    share = misses / windows if windows else None
    saturated = queue_full > 0
    succeeded = outcomes.get("succeeded", 0)
    survivors_only = succeeded < windows   # some payments never succeeded: latency covers the survivors
    pooled_span = max(all_conf) - min(all_conf) if len(all_conf) > 1 else None
    per_10s = [0] * (int((max(all_conf) - t_first) // 10) + 1) if all_conf and t_first is not None else []
    for t in all_conf:
        per_10s[int((t - t_first) // 10)] += 1
    max_queue, drain_s = env.get("MAX_QUEUE"), env.get("DRAIN_S")
    commit, commit_dirty = _analysis_commit()
    headline = {
        "saturated": saturated,
        "offered": windows,
        "succeeded_share": succeeded / windows if windows else None,
        "admitted_share": (windows - queue_full) / windows if windows else None,
        "dropped_share": queue_full / windows if windows else None,
        "unresolved_share": outcomes.get("unresolved", 0) / windows if windows else None,
        "goodput_per_s": succeeded / pooled_span if pooled_span else None,   # the achieved rate (R1)
        "confirmation_s": _dist(c_lat),
        "payment_latency_s": _dist(lat),
        "latency_note": (f"saturated: latency of admitted payments only, set by MAX_QUEUE={max_queue} and "
                         f"DRAIN_S={drain_s} (queueing ~ MAX_QUEUE / service rate); read goodput and "
                         f"confirmation_s" if saturated else
                         "survivors only: payment_latency_s covers the succeeded payments; "
                         "payment_latency_lower_bound_s counts the others too" if survivors_only else None),
        "payment_latency_lower_bound_s": _dist(lat + lat_lb_open),
        "max_queue": max_queue, "drain_s": drain_s,
    }
    out = {
        "complete": complete,
        "saturated": saturated,
        "headline": headline,
        "complete_checks": {"customers_found": len(cdirs), "customers_expected": expected,
                            "incomplete_customers": not_complete, "run_failed": run_failed,
                            "missing": missing, "dropped_customers": dropped,
                            "short_customers": short,
                            "stream_incomplete_customers": stream_incomplete,
                            "unreadable_logs": unreadable},
        "open_s": open_s, "close_s": close_s, "close_pending": close_pending, "not_admitted": not_admitted,
        "offered_rate_per_s": expected * 1000 / window_ms,
        "confirmed_per_10s": per_10s,
        "backlog_max": _backlog_max(all_backlog),
        "late_executed": late, "late_executed_total": late_total,
        "payment_latency_s": _dist(lat),
        # every payment that never succeeded counted with (its customer's last confirmation - t_intended):
        # it was still unconfirmed then, so each is a lower bound and so are the percentiles (survivorship)
        "payment_latency_lower_bound_s": _dist(lat + lat_lb_open),
        "queueing_s": _dist(q_lat),
        "confirmation_s": _dist(c_lat),
        "deadline_miss_share": share,
        "deadline_miss_share_by_window_ms": {str(window_ms): share},
        "receive_lag_s": _dist(all_rx),
        "per_customer": customers,
        "windows": {"ledger_rows": windows, "expected_per_customer": windows_expected,
                    "per_customer": per_customer},
        "outcomes": outcomes,
        "mapped_to_unresolved": mapped,
        "reconciliation": recon,
        "gas": {"deposit_l1_median": statistics.median(dep_gas) if dep_gas else None,
                "payment_l2_median": statistics.median(gas_pay) if gas_pay else None,
                "withdraw_l2_median": statistics.median(wd_gas) if wd_gas else None},
        "fees_eth": {"deposit_l1": dep_fee / 1e18, "payments_l2": fees_pay / 1e18,
                     "payments_l2_reverted": fees_rev / 1e18, "withdraw_l2": wd_fee / 1e18},
        "fees_unpriced_submitted_rows": unpriced,
        "diagnostics": {"customers": diag, "recorder": rec},
        "analysis_commit": commit,
        "analysis_measure_dirty": commit_dirty,
        "close_definition": CLOSE_DEFINITION[stack],
    }
    if not perun:  # arbitrum/zksync only: Perun's updates are off-chain, no effective_gas_price to report
        out["gas"]["effective_gas_price_max_wei"] = eff_price_max
    if perun:
        out["complete_checks"]["sensor_result_complete"] = sres_complete
        out["close_finalize_s"] = close_fin_s
        out["channels"] = channels
        out["fees_unpriced_submitted_rows"] = 0          # an off-chain update costs no gas: nothing is unpriced
        out["gas"].update(withdraw_l2_median=None, close_l1_median=statistics.median(cl1_gas) if cl1_gas else None,
                          close_sensor_l1_total=sensor_gas["gas"],
                          close_sensor_l1_per_channel=(sensor_gas["gas"] / sensor_gas["settled"]
                                                       if sensor_gas["gas"] is not None and sensor_gas["settled"]
                                                       else None))
        out["fees_eth"].update(withdraw_l2=None, close_l1=cl1_fee / 1e18,
                               close_sensor_l1=None if sensor_gas["fee_wei"] is None else sensor_gas["fee_wei"] / 1e18)
    return out


def main():
    d = sys.argv[1]
    s = analyze(d)
    json.dump(s, open(os.path.join(d, "summary.json"), "w"), indent=1)
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
