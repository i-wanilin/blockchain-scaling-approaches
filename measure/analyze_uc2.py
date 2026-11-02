"""One UC2 run folder (one match) -> summary.json (cluster spec §7.3,
"Reported per system" and "Payment ledger").

    python -m measure.analyze_uc2 RUN_DIR

The run folder is deploy/cluster's one_run_uc2 layout: experiment.env,
manifest.json, results/p<k>/pc/{ledger.csv,lifecycle.json} (the payment client
of player k), results/p<k>/game/{trace.jsonl(.gz),ticks.csv,node-result.json}
(its game node), results/reference.sha256, results/points-balances.json
({"players": {"p0": N, ...}, "arena": N}), recorder/<host>.csv. PLAYERS in
experiment.env maps p<k> to its board (p0 = the host, bath1, which also runs
Perun's hub); the other players are the guests, the headline (spec §7.3).
Player rows, latency and next-shot misses are reported for guests and host
apart. A ledger row's kind (fire | hit), payer and tick come from its event
ID uc2/<run>/p<payer>/t<tick>/<kind><seq> (plan R3; the payer is the shooter
for fire, the victim for hit); `to` is logical ("arena" | "p<k>"), and
amount_wei carries points (R2).

Every payment timestamp is taken on the payer's own board, so no latency
crosses boards. t_generated may precede t_intended by about 1 ms (wall clock
against the node's monotonic pacing); such rows are kept as they are.

Percentiles: Hyndman-Fan type 7 (analyze_uc1._pct). Latency in ms.

- `match`: per board, tick lag = t_actual - t_intended of every tick in
  ticks.csv (ms; p50, p99, max), and pooled over all boards. duration_s of a
  board = its last tick's t_actual - its first tick's t_intended + 1/TICKRATE
  (N tick periods, so a match without lag lasts GAME_TICKS / TICKRATE);
  match duration_s = the longest board's. nominal_s = GAME_TICKS / TICKRATE
  (60 s in the suite: 1 200 ticks at 20 Hz), stretch = duration_s /
  nominal_s.
- `offered`: ledger rows (payments) in all, of each kind, and per_s =
  payments / duration_s; emitted = what the game nodes handed to their
  payment clients (node-result.json), which must equal the ledger rows.
- `outcomes`: counts and shares (over all payments) of succeeded, failed and
  unresolved (rows still generated/queued/submitted, from a rebuilt crashed
  ledger, count as unresolved: mapped_to_unresolved). `late` =
  late_executed_total, its share over all payments; by_group per group.
- `late_executed` (as UC1): per player, the payments its ledger gave up on
  (rows still queued at the drain deadline are never sent; rows in flight
  then are unresolved) that executed anyway. Candidates are the rows not
  succeeded that have a t_submitted (on the rollups: and no receipt), in
  submission order.
  Rollups: from the nonces, exactly UC1's late_executed
  (analyze_uc1.late_from_nonces: executed_onchain + executed_after_close -
  succeeded rows - non-succeeded rows with a receipt; null if a nonce fact is
  null). Nonces execute in order, so the late rows are the first
  late_executed candidates; their points are null when the count is outside
  0..candidates.
  Perun: from the channel state after quiesce (channels_before_close,
  [mine, peer]). A virtual channel p_k-p_j has equal stakes, so (mine -
  peer) / 2 + succeeded k->j - succeeded j->k = late j->k - late k->j: exact
  for p_k's direction when p_j had nothing in flight to p_k (late k->j = the
  negated surplus); null with the reason when both ends had payments in flight
  (the channel shows only their net). The ledger channel to the hub holds
  FREE funds = the deposit (START_POINTS, the player's whole balance) - ammo
  paid - the stakes locked in the virtual channels (stake = (mine + peer) / 2
  of each), so late shots = START_POINTS - stakes - free - succeeded fire
  points. In both cases the late rows are the shortest prefix of the
  candidates (one FIFO per channel) whose points match; null if none does.
  late_executed_total and late_executed_points are null if any player's is
  (late_executed_reason says why). reconciliation.ok_with_late holds when
  every player's chain - ledger == late_in - late_out (the late rows paid to
  and by it) and the arena's chain - ledger == the late shots: an equality,
  unlike ok_explained's bound; null when any late count is unknown.
- `latency_ms`, for guests and host apart, per kind (fire, hit, all), over
  succeeded rows: generated_to_confirmed = t_confirmed - t_generated, and
  submitted_to_confirmed = t_confirmed - t_submitted; n, p50, p99, max.
- `latency_lower_bound_ms`: per group, generated_to_confirmed over every
  payment; one that never succeeded counts as its payer's last t_confirmed -
  its t_generated (still unconfirmed then): a lower bound, as UC1's
  payment_latency_lower_bound_s. Equal to latency_ms[g]["all"] when all succeeded.
- `goodput_per_s`: succeeded payments / (last - first t_confirmed of any
  board), as UC1's pooled goodput; by group in goodput_per_s_by_group.
- `next_shot_miss_share`: per payer, its fire rows in tick order; row i
  misses if it is not succeeded or t_confirmed > t_generated of row i+1 (when
  the player actually fired next, cluster spec §7.3; t_intended, the tick's
  schedule, only if t_generated is missing); each payer's last shot is left
  out. misses / shots summed over the guests and over the host. Until
  2026-10-12 the deadline was row i+1's t_intended while the latency starts at
  row i's t_generated, so the game's own tick lag counted as payment lateness.
- `reconciliation`, per player p_k: game = its score from replaying the trace
  (START_POINTS each; a fire moves its amount from the shooter to the arena,
  a hit from the victim to the shooter); ledger = START_POINTS - p_k's
  succeeded rows + the succeeded rows of the others paid to p_k; chain =
  points-balances.json players[p_k]; diff = chain - ledger; ok = diff == 0.
  A row still in flight at the drain deadline may have executed (UC1's
  explained_wei): explained_out = p_k's unresolved rows that have a
  t_submitted, explained_in = the others' such rows paid to p_k, explained =
  their sum, and ok_explained holds when -explained_out <= diff <=
  explained_in. game_minus_chain shows where the game and the payments
  diverged (failed payments). arena: game, ledger = succeeded fire rows, chain
  = points-balances.json arena, explained = unresolved fire rows with a
  t_submitted, ok_explained 0 <= diff <= explained. The game score is
  replayed from the first player's readable trace (game_trace); hash_ok says
  whether all boards had the same. The run's ok / ok_explained need every
  player's and the arena's; None when points-balances.json is unreadable.
  Perun only, `perun_channels`: per virtual channel p_k-p_j, the two ends'
  channels_before_close (balances [mine, peer], version) must mirror each
  other, and mine_k - mine_j must equal 2 x (succeeded j->k - succeeded k->j)
  (both ends stake the same), so a late execution makes it false; the ledger
  channel to the hub reports free funds only and is not checked here.
- `hash_ok`: results/reference.sha256 is readable and every player's trace
  hash, both as node-result.json trace_sha256 and recomputed from the file
  (gzip-decompressed if trace.jsonl.gz), equals it. hash_mismatch = the boards
  where either differs (or whose trace is missing). measure/aggregate refuses
  hash_ok false runs unless --include-incomplete.
- `open` / `close` per player: time and gas (gas units; fee_wei beside it).
  Rollups: open s = t_l2_credited - t_start, gas l1_gas_used (the ETH bridge
  deposit); close s = t_confirmed - t_start, gas l2_gas_used (the withdrawal
  submission). Perun: open s = t_virtual_open - t_start (ledger channel on
  the L1, then the virtual channels; ledger_s and virtual_s beside it), gas
  l1_gas_used; close s = t_withdrawn - t_start (virtual settle, ledger settle,
  withdrawal), gas l1_gas_used. None when the step has no record.
- `gas`: per_fire / per_hit = median gas_used of the succeeded rows of that
  kind on the rollups, 0 on Perun (an off-chain update); open_median and
  close_median over the players.
- `cpu` and `throttle` per board (recorder/<host>.csv, samples inside the
  match window = first tick's t_intended to the last tick's t_actual of any
  board, whole file if there are no ticks): mean/max CPU busy; throttled_share
  = share of samples with a current throttle flag (0x2 ARM frequency capped,
  0x4 throttled, 0x8 soft temperature limit), undervolted_share (0x1),
  max_temp_c, mean_arm_mhz.
- `complete`: every player folder, ledger, lifecycle complete, node-result
  complete, hash_ok, late_executed_total known, points-balances readable, ledger rows == emitted per
  player, no RUN_FAILED or HASH_MISMATCH file; what failed is in
  complete_checks. `saturated` (a "queue full" row) as UC1.
- `analysis_commit`: the repository HEAD when the summary was made."""
from __future__ import annotations

import csv
import glob
import gzip
import hashlib
import json
import os
import re
import statistics
import sys

from measure.analyze_uc1 import (NON_TERMINAL, QUEUE_FULL, _analysis_commit, _env, _int, _load_json, _num,
                                 _pct, board_list, late_from_nonces)
from measure.fileio import exists, open_text
from measure.ledger import load_log
from measure.manifest import _throttled, recorder_summary

PLAYER_BOARDS = ("bath1", "bath2", "bath7", "bath4", "bath5", "bath6")   # spec §7.3 layout, players 0..5
STACKS = ("arbitrum", "zksync", "perun")
HOST = "p0"                                    # bath1: player 0 and Perun's hub
KINDS = ("fire", "hit")
ID_RE = re.compile(r"^uc2/[^/]+/p(\d+)/t(\d+)/(fire|hit)(\d+)$")
THROTTLE_NOW = 0x2 | 0x4 | 0x8                 # vcgencmd get_throttled: current capped / throttled / soft limit
DEFAULT_START = 10000


def parse_id(id_):
    """(payer, tick, kind, seq) of a UC2 event ID, or None."""
    m = ID_RE.match(id_ or "")
    return (int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))) if m else None


def _kind(r):
    p = parse_id(r.get("id"))
    return p[2] if p else None


def _ms(xs):
    xs = [x * 1000 for x in xs]
    return {"n": len(xs), "p50": _pct(xs, .5), "p99": _pct(xs, .99), "max": max(xs) if xs else None}


def _outcome(r):
    return "unresolved" if r.get("outcome") in NON_TERMINAL else r.get("outcome")


def next_shot_misses(rows):
    """(misses, shots) of one payer's rows: fire rows in tick order, the last left out; a shot misses if it is
    not succeeded or confirmed after the next shot was actually fired (its t_generated; t_intended if absent)."""
    fires = sorted((parse_id(r["id"])[1], parse_id(r["id"])[3], r) for r in rows if _kind(r) == "fire")
    misses = 0
    for (_, _, r), (_, _, nxt) in zip(fires, fires[1:]):
        if _outcome(r) != "succeeded" or not _num(r.get("t_confirmed")):
            misses += 1
        elif float(r["t_confirmed"]) > float(nxt["t_generated"] if _num(nxt.get("t_generated"))
                                             else nxt["t_intended"]):
            misses += 1
    return misses, max(len(fires) - 1, 0)


def game_score(path, players, start):
    """Final points of every player and the arena from replaying a trace (path, or path.gz)."""
    score = {p: start for p in players}
    score["arena"] = 0
    with open_text(path) as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            p = e.get("payload") or {}
            if e.get("type") == "fire":
                src, dst = f"p{p['from']}", "arena"
            elif e.get("type") == "hit":
                src, dst = f"p{p['victim']}", f"p{p['shooter']}"
            else:
                continue
            score[src] = score.get(src, start) - int(p["amount"])
            score[dst] = score.get(dst, start) + int(p["amount"])
    return score


def _file_sha256(path):
    """SHA-256 of the trace's bytes (decompressed if only path.gz exists); None if neither exists."""
    h = hashlib.sha256()
    if os.path.exists(path):
        f = open(path, "rb")
    elif os.path.exists(path + ".gz"):
        f = gzip.open(path + ".gz", "rb")
    else:
        return None
    with f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _reference(run_dir):
    try:
        with open(os.path.join(run_dir, "results", "reference.sha256")) as f:
            tok = f.read().split()
    except OSError:
        return None
    return tok[0].lower() if tok and re.fullmatch(r"[0-9a-fA-F]{64}", tok[0]) else None


def _ticks(path):
    """[(tick, t_intended, t_actual)] of ticks.csv, or None if missing or unreadable."""
    if not exists(path):
        return None
    try:
        with open_text(path) as f:
            return [(int(r["tick"]), float(r["t_intended"]), float(r["t_actual"])) for r in csv.DictReader(f)]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _rows(pc):
    csv_path, log_path = os.path.join(pc, "ledger.csv"), os.path.join(pc, "ledger.log.jsonl")
    if exists(csv_path):
        with open_text(csv_path) as f:
            return list(csv.DictReader(f))
    if exists(log_path):
        return load_log(log_path)                          # crashed client: rebuild
    return None


def _throttle_share(path, window):
    """Share of recorder samples (inside window, if given) with a current throttle flag; None if no sample."""
    n = hit = 0
    with open_text(path) as f:
        for r in csv.DictReader(f):
            try:
                t = float(r["t"])
            except (TypeError, ValueError, KeyError):
                continue
            if window and not window[0] <= t <= window[1]:
                continue
            n += 1
            hit += _throttled(r.get("throttled")) & THROTTLE_NOW != 0
    return hit / n if n else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _span(life, a, b):
    try:
        return life[b] - life[a]
    except (KeyError, TypeError):
        return None


def _open_close(life, perun):
    o, c = life.get("open"), life.get("close")
    if perun:
        op = None if not isinstance(o, dict) else {
            "s": _span(o, "t_start", "t_virtual_open"), "ledger_s": _span(o, "t_start", "t_ledger_funded"),
            "virtual_s": _span(o, "t_virtual_start", "t_virtual_open"), "gas": _int(o.get("l1_gas_used")),
            "fee_wei": _int(o.get("l1_fee_wei"))}
        cl = None if not isinstance(c, dict) else {
            "s": _span(c, "t_start", "t_withdrawn"), "virtual_settle_s": c.get("virtual_settle_s"),
            "ledger_settle_s": c.get("ledger_settle_s"), "gas": _int(c.get("l1_gas_used")),
            "fee_wei": _int(c.get("l1_fee_wei"))}
    else:
        op = None if not isinstance(o, dict) else {
            "s": _span(o, "t_start", "t_l2_credited"), "gas": _int(o.get("l1_gas_used")),
            "fee_wei": _int(o.get("l1_fee_wei"))}
        cl = None if not isinstance(c, dict) else {
            "s": _span(c, "t_start", "t_confirmed"), "gas": _int(c.get("l2_gas_used")),
            "fee_wei": _int(c.get("l2_fee_wei"))}
    return op, cl


def _perun_channels(names, lives, ok_to):
    """Cross-board check of every virtual channel (see the module docstring)."""
    out = {}
    for i, k in enumerate(names):
        for j in names[i + 1:]:
            a = ((lives.get(k) or {}).get("channels_before_close") or {}).get(j)
            b = ((lives.get(j) or {}).get("channels_before_close") or {}).get(k)
            ab = [_int(v) for v in (a or {}).get("balances_wei") or []]
            bb = [_int(v) for v in (b or {}).get("balances_wei") or []]
            known = len(ab) == 2 and len(bb) == 2 and None not in ab + bb
            ledger_net = 2 * (ok_to[(j, k)] - ok_to[(k, j)])
            chan_net = ab[0] - bb[0] if known else None
            mirror = known and ab == bb[::-1] and a.get("version") == b.get("version")
            out[f"{k}-{j}"] = {"balances_wei": {k: ab or None, j: bb or None},
                               "versions": {k: (a or {}).get("version"), j: (b or {}).get("version")},
                               "broken": bool((a or {}).get("broken") or (b or {}).get("broken")),
                               "mirror": mirror, "channel_net_wei": chan_net, "ledger_net_wei": ledger_net,
                               "ok": bool(mirror and chan_net == ledger_net)}
    return out


def _priced(r):
    return _num(r.get("gas_used")) and _num(r.get("effective_gas_price"))


def _in_flight(rows, perun, to=None):
    """The rows that may have executed after the ledger gave up on them: unresolved, sent (t_submitted),
    and on the rollups without a receipt; in submission order (the nonce order on the rollups, the
    channel's FIFO on Perun)."""
    rs = [r for r in rows if _outcome(r) == "unresolved" and _num(r.get("t_submitted"))
          and (perun or not _priced(r)) and (to is None or r.get("to") == to)]
    return sorted(rs, key=lambda r: float(r["t_submitted"]))


def _rollup_late_rows(rows, late):
    """(rows, reason): the first `late` in-flight rows in nonce order (nonces execute in order), or None."""
    if late is None:
        return None, "no nonce facts (executed_onchain / executed_after_close not recorded)"
    cands = _in_flight(rows, False)
    if not 0 <= late <= len(cands):
        return None, f"late_executed {late} is outside 0..{len(cands)} (the in-flight rows)"
    return cands[:late], None


def _prefix(cands, points):
    """The shortest prefix of cands whose amounts sum to points, or None."""
    acc, out = 0, []
    for r in [None] + cands:
        if r is not None:
            acc += int(r["amount_wei"])
            out.append(r)
        if acc == points:
            return out
        if acc > points:
            return None
    return None


def _perun_late(names, rows_of, lives, ok_to, start):
    """Per player: the in-flight rows its channels show as executed (see the module docstring), or None
    with the reason."""
    late, reason = {}, {}
    for k in names:
        rows = rows_of.get(k, [])
        cbc = (lives.get(k) or {}).get("channels_before_close")
        if not isinstance(cbc, dict):
            late[k], reason[k] = None, "no channels_before_close"
            continue
        got, why, stakes = [], None, 0
        for j in names:
            if j == k:
                continue
            bal = [_int(v) for v in (cbc.get(j) or {}).get("balances_wei") or []]
            if len(bal) != 2 or None in bal or (bal[0] - bal[1]) % 2:
                why = f"virtual channel {j}: balances unusable"
                break
            stakes += (bal[0] + bal[1]) // 2
            surplus = (bal[0] - bal[1]) // 2 + ok_to[(k, j)] - ok_to[(j, k)]   # = late j->k - late k->j
            mine, theirs = _in_flight(rows, True, j), _in_flight(rows_of.get(j, []), True, k)
            if not mine:
                continue
            if theirs:
                why = f"virtual channel {j}: payments in flight at both ends, the channel shows only their net"
                break
            pre = _prefix(mine, -surplus)
            if pre is None:
                why = f"virtual channel {j}: surplus {-surplus} is not a prefix of the in-flight payments"
                break
            got += pre
        if why is None:
            hub = [_int(v) for v in ((cbc.get("hub") or {}).get("balances_wei") or [])]
            fires = _in_flight(rows, True, "arena")
            ok_fire = sum(int(r["amount_wei"]) for r in rows
                          if _outcome(r) == "succeeded" and r.get("to") == "arena")
            if fires:
                if not hub or hub[0] is None:
                    why = "ledger channel: free balance unusable"
                else:   # free funds = deposit (START_POINTS) - ammo paid - the stakes locked in virtual channels
                    pre = _prefix(fires, start - stakes - hub[0] - ok_fire)
                    if pre is None:
                        why = "ledger channel: free-balance surplus is not a prefix of the in-flight shots"
                    else:
                        got += pre
        late[k], reason[k] = (None, why) if why else (got, None)
    return late, reason


def analyze(run_dir) -> dict:
    env = _env(run_dir)
    if env.get("USE_CASE", "uc2") != "uc2":
        raise ValueError(f"{run_dir}/experiment.env USE_CASE={env['USE_CASE']!r} is not uc2")
    stack = env.get("STACK", "arbitrum")
    if stack not in STACKS:
        raise ValueError(f"{run_dir}/experiment.env STACK={stack!r} is not one of {sorted(STACKS)}")
    perun = stack == "perun"
    boards = board_list(env, "PLAYERS", PLAYER_BOARDS)
    n = int(env.get("N_PLAYERS", len(boards)))
    if n > len(boards):
        raise ValueError(f"{run_dir}/experiment.env N_PLAYERS={n} but PLAYERS has {len(boards)} boards")
    names = [f"p{k}" for k in range(n)]
    board_of = {p: boards[k] for k, p in enumerate(names)}
    start = int(env.get("START_POINTS", DEFAULT_START))
    tickrate = int(env.get("TICKRATE", 20))
    nominal = int(env.get("GAME_TICKS", 1200)) / tickrate
    res = os.path.join(run_dir, "results")

    missing, not_complete, node_incomplete, diag = [], [], [], {}
    rows_of, lives, nodes, ticks = {}, {}, {}, {}
    for p in names:
        pdir = os.path.join(res, p)
        if not os.path.isdir(pdir):
            missing.append(f"results/{p}")
            continue
        rows = _rows(os.path.join(pdir, "pc"))
        if rows is None:
            missing.append(f"results/{p}/pc/ledger")
        rows_of[p] = rows or []
        life = _load_json(os.path.join(pdir, "pc", "lifecycle.json"))
        if life is None:
            missing.append(f"results/{p}/pc/lifecycle.json")
        lives[p] = life or {}
        if lives[p].get("complete") is not True:
            not_complete.append(p)
        nr = _load_json(os.path.join(pdir, "game", "node-result.json"))
        if nr is None:
            missing.append(f"results/{p}/game/node-result.json")
        nodes[p] = nr or {}
        if nodes[p].get("complete") is not True:
            node_incomplete.append(p)
        tk = _ticks(os.path.join(pdir, "game", "ticks.csv"))
        if tk is None:
            missing.append(f"results/{p}/game/ticks.csv")
        ticks[p] = tk or []
        diag[p] = {k: lives[p].get(k) for k in ("duplicates", "late", "not_admitted", "executed_after_close",
                                                 "broken_channels") if k in lives[p]}
        for k in ("open_error", "close_error", "quiesce_error", "state_error", "error"):
            if lives[p].get(k) not in (None, ""):
                diag[p][k] = lives[p][k]
        if nodes[p].get("aborted"):
            diag[p]["match_aborted"] = nodes[p]["aborted"]

    # ---- the match: tick lag, duration, stretch
    lag_by, dur_by, all_lag, t_lo, t_hi = {}, {}, [], None, None
    for p in names:
        tk = ticks.get(p) or []
        if not tk:
            continue
        b = board_of[p]
        lags = [ta - ti for _, ti, ta in tk]
        lag_by[b] = _ms(lags)
        all_lag += lags
        dur_by[b] = tk[-1][2] - tk[0][1] + 1 / tickrate
        t_lo = tk[0][1] if t_lo is None else min(t_lo, tk[0][1])
        t_hi = max(ta for _, _, ta in tk) if t_hi is None else max(t_hi, max(ta for _, _, ta in tk))
    duration = max(dur_by.values()) if dur_by else None
    match = {"duration_s": duration, "nominal_s": nominal,
             "stretch": duration / nominal if duration is not None else None,
             "duration_s_per_board": dur_by,
             "ticks": {board_of[p]: len(ticks[p]) for p in names if p in ticks},
             "tick_lag_ms": dict(lag_by, pooled=_ms(all_lag))}

    # ---- the ledgers
    counts = {"succeeded": 0, "failed": 0, "unresolved": 0}
    group_counts = {g: {"payments": 0, "succeeded": 0, "failed": 0, "unresolved": 0}
                    for g in ("guests", "host")}
    kinds = {k: 0 for k in KINDS}
    lat = {g: {k: {"g2c": [], "s2c": []} for k in KINDS + ("all",)} for g in ("guests", "host")}
    lat_lb = {"guests": [], "host": []}   # generated_to_confirmed incl. lower bounds of the never-succeeded
    conf, conf_by = [], {"guests": [], "host": []}
    gas = {k: [] for k in KINDS}
    out_ok, in_ok, ex_out, ex_in = ({p: 0 for p in names} for _ in range(4))
    ok_to = {(a, b): 0 for a in names for b in names}
    arena_ok = arena_ex = mapped = queue_full = unparsed = 0
    miss = {"guests": [0, 0], "host": [0, 0]}
    miss_by, rows_vs_emitted, wrong_payer, n_ok, n_receipt_fail = {}, {}, [], {}, {}
    for p in names:
        g = "host" if p == HOST else "guests"
        rows = rows_of.get(p, [])
        n_ok[p] = n_receipt_fail[p] = 0
        p_conf, p_open = [], []
        emitted = nodes.get(p, {}).get("emitted")
        if p in rows_of and emitted != len(rows):
            rows_vs_emitted[p] = [len(rows), emitted]
        for r in rows:
            pid = parse_id(r.get("id"))
            kind = pid[2] if pid else None
            if pid is None:
                unparsed += 1
            elif f"p{pid[0]}" != p:
                wrong_payer.append(r["id"])
            if kind:
                kinds[kind] += 1
            out = _outcome(r)
            mapped += r.get("outcome") in NON_TERMINAL
            counts[out] = counts.get(out, 0) + 1
            group_counts[g]["payments"] += 1
            group_counts[g][out] = group_counts[g].get(out, 0) + 1
            amt, to = int(r["amount_wei"]), r.get("to")
            if out != "succeeded" and _priced(r):
                n_receipt_fail[p] += 1                     # a reverted payment also used a nonce (UC1)
            if out == "failed" and r.get("error") == QUEUE_FULL:
                queue_full += 1
            if out == "unresolved" and _num(r.get("t_submitted")):
                ex_out[p] += amt
                if to in ex_in:
                    ex_in[to] += amt
                elif to == "arena":
                    arena_ex += amt
            if out != "succeeded":
                if _num(r.get("t_generated")):
                    p_open.append(float(r["t_generated"]))
                continue
            out_ok[p] += amt
            n_ok[p] += 1
            if to in in_ok:
                in_ok[to] += amt
                ok_to[(p, to)] += amt
            elif to == "arena":
                arena_ok += amt
            tc = float(r["t_confirmed"])
            p_conf.append(tc)
            conf.append(tc)
            conf_by[g].append(tc)
            for k in ((kind, "all") if kind else ("all",)):
                if _num(r.get("t_generated")):
                    lat[g][k]["g2c"].append(tc - float(r["t_generated"]))
                if _num(r.get("t_submitted")):
                    lat[g][k]["s2c"].append(tc - float(r["t_submitted"]))
            if kind and _num(r.get("gas_used")):
                gas[kind].append(int(r["gas_used"]))
        # every payment that never succeeded was still unconfirmed at this payer's last confirmation
        lat_lb[g] += [max(0.0, max(p_conf) - t) for t in p_open] if p_conf else []
        m, s_ = next_shot_misses([r for r in rows if parse_id(r.get("id"))])
        miss_by[p] = {"misses": m, "shots": s_, "share": m / s_ if s_ else None}
        miss[g][0] += m
        miss[g][1] += s_
    total = sum(counts.values())

    # ---- late executions (payments the ledger gave up on that executed anyway), as UC1
    if perun:
        late_rows, late_reason = _perun_late(names, rows_of, lives, ok_to, start)
        late_n = {p: None if late_rows[p] is None else len(late_rows[p]) for p in names}
    else:
        late_n, late_rows, late_reason = {}, {}, {}
        for p in names:
            late_n[p] = late_from_nonces(lives.get(p) or {}, n_ok[p], n_receipt_fail[p])
            late_rows[p], late_reason[p] = _rollup_late_rows(rows_of.get(p, []), late_n[p])
    late_out = {p: None if late_rows[p] is None else sum(int(r["amount_wei"]) for r in late_rows[p]) for p in names}
    rows_known = all(v is not None for v in late_rows.values())
    late_total = sum(late_n.values()) if late_n and None not in late_n.values() else None
    late_points = sum(late_out.values()) if late_out and None not in late_out.values() else None
    late_in = {p: sum(int(r["amount_wei"]) for q in names for r in late_rows[q] if r.get("to") == p)
               for p in names} if rows_known else None
    late_arena = (sum(int(r["amount_wei"]) for q in names for r in late_rows[q] if r.get("to") == "arena")
                  if rows_known else None)
    for g, gc in group_counts.items():
        ps = [p for p in names if (p == HOST) == (g == "host")]
        gc["late"] = sum(late_n[p] for p in ps) if all(late_n[p] is not None for p in ps) else None
        gc["shares"] = {k: gc[k] / gc["payments"] if gc["payments"] and gc[k] is not None else None
                        for k in ("succeeded", "failed", "unresolved", "late")}
    outcomes = dict(counts, late=late_total, queue_full=queue_full, mapped_to_unresolved=mapped,
                    shares={k: (counts[k] if k in counts else late_total) / total
                            if total and (k in counts or late_total is not None) else None
                            for k in ("succeeded", "failed", "unresolved", "late")},
                    by_group=group_counts)

    def goodput(ts):
        span = max(ts) - min(ts) if len(ts) > 1 else None
        return len(ts) / span if span else None

    # ---- the hash check
    ref = _reference(run_dir)
    if ref is None:
        missing.append("results/reference.sha256")
    hashes, mismatch = {}, []
    for p in names:
        nr_sha = (nodes.get(p) or {}).get("trace_sha256")
        f_sha = _file_sha256(os.path.join(res, p, "game", "trace.jsonl"))
        ok = ref is not None and nr_sha == ref and f_sha == ref
        hashes[board_of[p]] = {"player": p, "node_result": nr_sha, "file": f_sha, "ok": ok}
        if ref is not None and not ok:
            mismatch.append(board_of[p])
    hash_ok = ref is not None and not mismatch

    # ---- reconciliation
    game, game_trace = None, None
    for p in names:
        tp = os.path.join(res, p, "game", "trace.jsonl")
        if exists(tp):
            try:
                game = game_score(tp, names, start)
                game_trace = os.path.relpath(tp, run_dir) + ("" if os.path.exists(tp) else ".gz")
                break
            except (OSError, ValueError, KeyError, TypeError, EOFError):
                continue
    bal_path = os.path.join(res, "points-balances.json")
    bal = _load_json(bal_path)
    chain_players = (bal or {}).get("players") if isinstance((bal or {}).get("players"), dict) else {}
    if bal is None:
        missing.append("results/points-balances.json")
    per = {}
    for p in names:
        ledger = start - out_ok[p] + in_ok[p]
        chain = _int(chain_players.get(p))
        diff = None if chain is None else chain - ledger
        per[p] = {"game": None if game is None else game.get(p), "ledger": ledger, "chain": chain, "diff": diff,
                  "explained": ex_out[p] + ex_in[p], "explained_out": ex_out[p], "explained_in": ex_in[p],
                  "ok": None if diff is None else diff == 0,
                  "ok_explained": None if diff is None else -ex_out[p] <= diff <= ex_in[p],
                  "game_minus_chain": None if game is None or chain is None else game.get(p) - chain,
                  "late_out": late_out[p], "late_in": None if late_in is None else late_in[p],
                  "ok_with_late": (None if diff is None or late_in is None
                                   else diff == late_in[p] - late_out[p])}
    a_chain = _int((bal or {}).get("arena"))
    a_diff = None if a_chain is None else a_chain - arena_ok
    arena = {"game": None if game is None else game.get("arena"), "ledger": arena_ok, "chain": a_chain,
             "diff": a_diff, "explained": arena_ex, "ok": None if a_diff is None else a_diff == 0,
             "ok_explained": None if a_diff is None else 0 <= a_diff <= arena_ex,
             "late_in": late_arena,
             "ok_with_late": None if a_diff is None or late_arena is None else a_diff == late_arena}
    parts = list(per.values()) + [arena]
    known = bool(per) and all(v["ok"] is not None for v in parts)
    if bal is not None and not known:
        missing.append("results/points-balances.json (a balance is not an integer)")
    recon = {"ok": all(v["ok"] for v in parts) if known else None,
             "ok_explained": all(v["ok_explained"] for v in parts) if known else None,
             "ok_with_late": (all(v["ok_with_late"] for v in parts)
                              if known and all(v["ok_with_late"] is not None for v in parts) else None),
             "late_executed_points": late_points,
             "start_points": start, "game_trace": game_trace, "players": per, "arena": arena}

    # ---- open / close, gas
    opens, closes = {}, {}
    for p in names:
        opens[p], closes[p] = _open_close(lives.get(p) or {}, perun)
    gas_out = {"per_fire": 0 if perun else _median(gas["fire"]), "per_hit": 0 if perun else _median(gas["hit"]),
               "open_median": _median([(o or {}).get("gas") for o in opens.values()]),
               "close_median": _median([(c or {}).get("gas") for c in closes.values()])}

    # ---- recorders
    window = (t_lo, t_hi) if t_lo is not None else None
    cpu, throttle = {}, {}
    for path in sorted(glob.glob(os.path.join(run_dir, "recorder", "*.csv"))):
        host = os.path.basename(path)[:-4]
        s = recorder_summary(path, window)
        cpu[host] = {k: s.get(k) for k in ("mean_cpu_busy_pct", "max_cpu_busy_pct", "samples", "scope")}
        throttle[host] = {"throttled_share": _throttle_share(path, window),
                          **{k: s.get(k) for k in ("undervolted_share", "max_temp_c", "mean_arm_mhz")}}

    run_failed = os.path.exists(os.path.join(run_dir, "RUN_FAILED"))
    hash_file = os.path.exists(os.path.join(run_dir, "HASH_MISMATCH"))
    complete = (not missing and not not_complete and not node_incomplete and hash_ok and not run_failed
                and not hash_file and not rows_vs_emitted and recon["ok"] is not None
                and late_total is not None)
    commit, dirty = _analysis_commit()
    out = {
        "use_case": "uc2", "stack": stack, "players": board_of, "host": HOST,
        "complete": complete,
        "complete_checks": {"missing": missing, "incomplete_payment_clients": not_complete,
                            "incomplete_game_nodes": node_incomplete, "run_failed": run_failed,
                            "hash_mismatch_file": hash_file, "ledger_rows_vs_emitted": rows_vs_emitted,
                            "late_executed_unknown": late_total is None, "unparsed_ids": unparsed, "ids_of_another_payer": wrong_payer[:20]},
        "hash_ok": hash_ok, "hash_mismatch": mismatch, "reference_sha256": ref, "hashes": hashes,
        "saturated": queue_full > 0,
        "match": match,
        "offered": {"payments": total, **kinds, "per_s": total / duration if duration else None,
                    "emitted": sum(_int(nodes[p].get("emitted")) or 0 for p in nodes)},
        "outcomes": outcomes,
        "late_executed": late_n, "late_executed_total": late_total, "late_executed_points": late_points,
        "late_executed_reason": {p: r for p, r in late_reason.items() if r},
        "latency_ms": {g: {k: {"generated_to_confirmed": _ms(v["g2c"]), "submitted_to_confirmed": _ms(v["s2c"])}
                           for k, v in lat[g].items()} for g in lat},
        # generated_to_confirmed over every payment: a never-succeeded one counts as (its payer's last
        # confirmation - t_generated), still unconfirmed then, so a lower bound (survivorship, zkSync)
        "latency_lower_bound_ms": {g: _ms(lat[g]["all"]["g2c"] + lat_lb[g]) for g in lat},
        "goodput_per_s": goodput(conf),
        "goodput_per_s_by_group": {g: goodput(ts) for g, ts in conf_by.items()},
        "next_shot_miss_share": {**{g: {"misses": m, "shots": s_, "share": m / s_ if s_ else None}
                                    for g, (m, s_) in miss.items()}, "per_player": miss_by},
        "reconciliation": recon,
        "open": opens, "close": closes, "gas": gas_out,
        "cpu": cpu, "throttle": throttle,
        "diagnostics": diag,
        "analysis_commit": commit, "analysis_measure_dirty": dirty,
    }
    if perun:
        out["perun_channels"] = _perun_channels(names, lives, ok_to)
    return out


def main():
    d = sys.argv[1]
    s = analyze(d)
    with open(os.path.join(d, "summary.json"), "w") as f:
        json.dump(s, f, indent=1)
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
