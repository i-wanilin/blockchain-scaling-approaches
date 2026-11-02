#!/usr/bin/env bash
# uc2-local.sh — one whole UC2 match on the workstation (plan Task 12): six loopback players play the
# cluster's part, and the run folder has deploy/cluster's one_run_uc2 layout, so measure.analyze_uc2 reads it.
#
#   deploy/uc2-local.sh perun|arbitrum OUTDIR
#
# The chain must already be up (as `deploy/cluster up` is its own step): Perun needs an Anvil
# (PERUN_L1 / PERUN_CHAIN_WS), Arbitrum the nitro-testnode (ARB_L1 / ARB_L2_WS / ARB_L2_HTTP).
# Steps (one function each): bound -> setup (Perun: fund 110..116, uc2-deploy, cluster.json, uc2-hub;
# Arbitrum: measure.uc2_setup) -> six payment clients, wait for every `ready` -> six arena.node on
# GAME_PORT0.. -> wait for every node result, then every payment client's `complete` (Perun: the hub
# settles and exits) -> points balances -> reference log (arena.sim) and the hash check -> analyze_uc2.
# Exits non-zero on any failure (RUN_FAILED in OUTDIR says where), or when summary.json is not
# complete with hash_ok.
#
# The command lines of the payment clients, the game nodes, uc2-deploy, uc2-hub, cluster.json, the
# bound, the setup and the points balances are built in the *_cmd / *_json functions below,
# with only the per-board parts (host, port, paths, python) as arguments. deploy/cluster has its own copies
# of these command lines (Task 13); a change here must be made there too.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

SYSTEM=${1:-}; OUT=${2:-}
case "$SYSTEM" in perun|arbitrum) ;; *) echo "usage: $0 perun|arbitrum OUTDIR" >&2; exit 2 ;; esac
[ -n "$OUT" ] || { echo "usage: $0 perun|arbitrum OUTDIR" >&2; exit 2; }

# ---- parameters (frozen, plan Global Constraints; the env may override for a test) ----------------------
LPY=${LPY:-"$REPO/.venv/bin/python"}      # measure/ python (web3); in a worktree: LPY=<repo>/.venv/bin/python
GPY=${GPY:-python3}                        # tank-arena python (stdlib only)
PLAYERS=(bath1 bath2 bath7 bath4 bath5 bath6)   # roles only: local process k plays board PLAYERS[k]
N_PLAYERS=6; TICKRATE=20; GAME_TICKS=1200; GAME_SEED=42; START_POINTS=10000
DURATION_S=$((GAME_TICKS / TICKRATE))
MAX_QUEUE=10000; DRAIN_S=60; MAX_IN_FLIGHT=64
FUND_WEI=2000000000000000000; DEPOSIT_WEI=1000000000000000000
RECEIPT_MODE=own; RECEIPT_TIMEOUT_S=120; CLOSE_TIMEOUT_S=300        # Arbitrum
UPDATE_TIMEOUT_S=10; CHALLENGE_S=10; PERSIST=1                       # Perun
YIELD_TIMEOUT_MS=200; MAX_COLLISION_RETRIES=10                       # Perun (owner, Checkpoint 2)
PERUN_CHAIN_ID=31337
GAME_HOST=${GAME_HOST:-127.0.0.1}; GAME_PORT0=${GAME_PORT0:-9100}    # player k on GAME_PORT0 + k
# Endpoints (this PC's Docker cannot publish 8545-8548: pass e.g. ARB_L1=http://127.0.0.1:18545).
# The payment client derives its L2 HTTP from ARB_L2_WS (measure/arbitrum.http_url: ws->http, :8548->:8547),
# so the L2 must answer WS on :8548 and HTTP on :8547 of one host; with remapped Docker ports, forward
# 127.0.0.1:8547/8548 to them. ARB_L2_HTTP is used by the setup and the points balances only.
ARB_L1=${ARB_L1:-http://127.0.0.1:8545}; ARB_L2_WS=${ARB_L2_WS:-ws://127.0.0.1:8548}
ARB_L2_HTTP=${ARB_L2_HTTP:-http://127.0.0.1:8547}
PERUN_L1=${PERUN_L1:-http://127.0.0.1:8545}; PERUN_CHAIN_WS=${PERUN_CHAIN_WS:-ws://127.0.0.1:8545}
PERUN_HOST=${PERUN_HOST:-127.0.0.1}
PERUN_WIRE0=${PERUN_WIRE0:-19000}          # hub wire on PERUN_WIRE0, player k on PERUN_WIRE0 + 1 + k
PERUN_CONTROL_PORT=${PERUN_CONTROL_PORT:-18091}
PERUN_BIN=${PERUN_BIN:-}                   # empty: built from perun/hub-demo into OUTDIR/work
# Deadlines (s)
READY_WAIT_S=${READY_WAIT_S:-600}; GAME_WAIT_S=$((DURATION_S * 3 + 120))
if [ "$SYSTEM" = perun ]; then COMPLETE_EXTRA_S=${COMPLETE_EXTRA_S:-960}; else COMPLETE_EXTRA_S=${COMPLETE_EXTRA_S:-400}; fi
HUB_EXIT_WAIT_S=${HUB_EXIT_WAIT_S:-600}

RUN_ID="$(date +%Y%m%d-%H%M%S)-local-$SYSTEM-uc2"
RES="$OUT/results"; WORK="$OUT/work"; LOGS="$OUT/logs"
SOCK=""                                    # short dir for the Unix sockets (sun_path is 108 bytes)
PIDS=(); HUB_PID=""; STAGE="start"

say() { echo "[$(date +%H:%M:%S)] $*"; }
die() { echo "uc2-local: $STAGE: $*" >&2; echo "$STAGE: $*" > "$OUT/RUN_FAILED"; exit 1; }

cleanup() {
  local rc=$? p
  set +e
  local live=()
  for p in "${PIDS[@]}" $HUB_PID; do kill -0 "$p" 2>/dev/null && live+=("$p"); done
  if [ "${#live[@]}" -gt 0 ]; then
    say "stopping ${#live[@]} process(es)"
    kill -TERM "${live[@]}" 2>/dev/null
    for _ in $(seq 90); do
      local any=""; for p in "${live[@]}"; do kill -0 "$p" 2>/dev/null && any=1; done
      [ -n "$any" ] || break; sleep 1
    done
    kill -KILL "${live[@]}" 2>/dev/null
  fi
  [ -z "$PERUN_BIN" ] || pkill -KILL -f "^$PERUN_BIN -mode uc2-" 2>/dev/null
  [ -z "$SOCK" ] || rm -rf "$SOCK"
  if [ "$rc" -ne 0 ] && [ ! -f "$OUT/RUN_FAILED" ]; then echo "$STAGE: exit $rc" > "$OUT/RUN_FAILED"; fi
  exit "$rc"
}

# ---- the shared command lines (Task 13 uses these; per-board parts are arguments) ----------------------

# bound_cmd PY — S and the channel amounts from the rules (never from the spec text).
bound_cmd() { echo "$1 -m arena.bound --json --players $N_PLAYERS --tickrate $TICKRATE --duration $DURATION_S"; }

# rollup_setup_cmd PY L1 L2_HTTP OUTDIR — fund 110..116 and 119, deploy the token, mintBatch.
rollup_setup_cmd() {
  echo "$1 -m measure.uc2_setup --stack $SYSTEM --l1 $2 --l2-http $3 --players $N_PLAYERS --points $START_POINTS --fund-wei $FUND_WEI --out $4"
}

# uc2_deploy_cmd BIN CHAIN_WS MINT_LIST HUB_ADDR HUB_POINTS OUT — Perun: adjudicator, token, mints, asset holder.
uc2_deploy_cmd() {
  echo "$1 -mode uc2-deploy -url $2 -chainid $PERUN_CHAIN_ID -mint $3 -points $START_POINTS -hub $4 -hub-points $5 -out $6"
}

# uc2_hub_cmd BIN CONFIG — the hub (arena): ledger channels from every player, policy on.
uc2_hub_cmd() { echo "$1 -mode uc2-hub -config $2"; }

# payclient_cmd PY K SOCKET PCDIR [ARGS] — player k's payment client.
#   arbitrum|zksync ARGS: L1 L2_WS TOKEN RECIPIENTS        perun ARGS: SIDECAR CLUSTER_JSON
payclient_cmd() {
  local py=$1 k=$2 sock=$3 pc=$4; shift 4
  local common="--account $((111 + k)) --max-in-flight $MAX_IN_FLIGHT --max-queue $MAX_QUEUE --drain $DRAIN_S --socket $sock --out $pc"
  if [ "$SYSTEM" = perun ]; then
    echo "$py -m measure.payclient --system perun --use-case uc2 --perun-config $2 --sidecar $1 --deposit-wei 0 --update-timeout $UPDATE_TIMEOUT_S --persist $PERSIST $common"
  else
    local rm_flag=""; [ -z "${RECEIPT_MODE:-}" ] || rm_flag="--receipt-mode $RECEIPT_MODE"
    echo "$py -m measure.payclient --system $SYSTEM --asset points --token $3 --recipients $4 --players $N_PLAYERS --l1 $1 --l2 $2 --deposit-wei $DEPOSIT_WEI --receipt-timeout $RECEIPT_TIMEOUT_S --close-timeout $CLOSE_TIMEOUT_S $rm_flag $common"
  fi
}

# node_cmd PY K PEERS SOCKET GAMEDIR — player k's lockstep game node (run from tank-arena/).
node_cmd() {
  echo "$1 -m arena.node --id $2 --peers $3 --tickrate $TICKRATE --duration $DURATION_S --seed $GAME_SEED --run $RUN_ID --pay-socket $4 --out $5/trace.jsonl --ticks-out $5/ticks.csv --result-out $5/node-result.json"
}

# reference_cmd PY OUT — the reference event log (Task 14 commits the cluster's, generated on bath2).
reference_cmd() { echo "$1 -m arena.sim --players $N_PLAYERS --tickrate $TICKRATE --duration $DURATION_S --seed $GAME_SEED --out $2"; }

# cluster_json BOUND CONTRACTS HUB_KEY STATE_ROOT RESULT_DIR HUB_HOST CTL_BIND CTL_URL HOST0..HOST5 — the
# Perun cluster.json on stdout. Hub "arena" on HUB_HOST:PERUN_WIRE0, players p0..p5 on HOSTk:PERUN_WIRE0+1+k
# (on the cluster every board uses the same port; here they share one host). Amounts from bound.json.
cluster_json() {
  "$LPY" - "$@" "$PERUN_CHAIN_WS" "$PERUN_CHAIN_ID" "$START_POINTS" "$CHALLENGE_S" "$UPDATE_TIMEOUT_S" \
    "$YIELD_TIMEOUT_MS" "$MAX_COLLISION_RETRIES" "$PERUN_WIRE0" "$PERUN_WIRE_SAME_PORT" <<'PY'
import json, sys
(bound, contracts, key, state, result, hub_host, ctl_bind, ctl_url, *rest) = sys.argv[1:]
hosts, (chain, chain_id, ledger, challenge, upd, yield_ms, retries, wire0, same) = rest[:-9], rest[-9:]
sys.path.insert(0, ".")
from measure import keys
b = json.load(open(bound))
wire0 = int(wire0)
def port(k):        # k = -1 is the hub
    return wire0 if same == "1" else wire0 + 1 + k
cfg = {"chain_url": chain, "chain_id": int(chain_id), "asset": "points",
       "state_root": state, "result_dir": result,
       "ledger_deposit": int(ledger), "virtual_stake": b["virtual_stake"],
       "hub_deposit": b["hub_deposit"], "ammo_reserve": b["ammo"],
       "challenge_seconds": int(challenge), "update_timeout_seconds": int(upd),
       "yield_timeout_ms": int(yield_ms), "max_collision_retries": int(retries),
       "contracts_file": contracts, "hub_key_file": key,
       "hub": {"name": "arena", "bind": f"0.0.0.0:{wire0}" if same == "1" else f"{hub_host}:{wire0}",
               "endpoint": f"{hub_host}:{wire0}", "control": ctl_bind,
               "ethereum": keys.account(keys.ARENA).address},
       "clients": [{"name": f"p{k}", "bind": f"0.0.0.0:{port(k)}" if same == "1" else f"{h}:{port(k)}",
                    "endpoint": f"{h}:{port(k)}", "control": ctl_url,
                    "ethereum": keys.account(keys.player_index(k)).address} for k, h in enumerate(hosts)]}
print(json.dumps(cfg, indent=1))
PY
}
PERUN_WIRE_SAME_PORT=0     # 1 on the cluster (one board per node, every node on :PERUN_WIRE0)

# points_balances_json — R11: the final points of the six players and the arena, after every payment client
# completed. Rollups: balanceOf on the L2. Perun: the withdrawn token balances on the L1; arena = the hub's
# net gain = its balance - what it was minted (= withdrawn - n x hub_deposit: it deposits all it was minted).
points_balances_json() {
  local url=$1
  (cd "$REPO" && "$LPY" - "$SYSTEM" "$url" "$RES" "$N_PLAYERS" <<'PY')
import json, os, sys
from web3 import Web3
from measure import keys, points as pts
system, url, res, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
def bal(token, addr):
    return int(w3.eth.call({"to": token, "data": pts.balance_of_data(addr)}).hex(), 16)
players = {f"p{k}": keys.account(keys.player_index(k)).address for k in range(n)}
hub = keys.account(keys.ARENA).address
if system == "perun":
    c = json.load(open(os.path.join(res, "contracts.json")))
    b = json.load(open(os.path.join(res, "bound.json")))
    minted = {Web3.to_checksum_address(a): v for a, v in c["minted"].items()}
    if minted.get(hub) != n * b["hub_deposit"]:
        sys.exit(f"hub minted {minted.get(hub)} != {n} x hub_deposit {b['hub_deposit']}")
    token = Web3.to_checksum_address(c["token"])
    hub_bal = bal(token, hub)
    out = {"players": {p: bal(token, a) for p, a in players.items()}, "arena": hub_bal - minted[hub],
           "hub_balance": hub_bal, "hub_minted": minted[hub], "token": token, "chain": "L1 (Anvil)"}
else:
    t = json.load(open(os.path.join(res, "token.json")))
    rec = pts.load_recipients(os.path.join(res, "recipients.json"), n)
    token = Web3.to_checksum_address(t["token"])
    out = {"players": {p: bal(token, rec[p]) for p in players}, "arena": bal(token, rec["arena"]),
           "token": token, "chain": "L2"}
print(json.dumps(out, indent=1))
PY
}

# ---- local steps -------------------------------------------------------------------------------------

write_env() {
  { echo "# local workstation run (deploy/uc2-local.sh, $RUN_ID): six loopback players, not a measurement"
    echo "USE_CASE=uc2"; echo "STACK=$SYSTEM"; echo "PLAYERS=(${PLAYERS[*]})"
    echo "N_PLAYERS=$N_PLAYERS"; echo "TICKRATE=$TICKRATE"; echo "GAME_TICKS=$GAME_TICKS"; echo "GAME_SEED=$GAME_SEED"
    echo "START_POINTS=$START_POINTS"; echo "MAX_QUEUE=$MAX_QUEUE"; echo "DRAIN_S=$DRAIN_S"; echo "MAX_IN_FLIGHT=$MAX_IN_FLIGHT"
    echo "FUND_WEI=$FUND_WEI"
    if [ "$SYSTEM" = perun ]; then
      echo "UPDATE_TIMEOUT_S=$UPDATE_TIMEOUT_S"; echo "CHALLENGE_S=$CHALLENGE_S"; echo "PERSIST=$PERSIST"
    else
      echo "DEPOSIT_WEI=$DEPOSIT_WEI"; echo "RECEIPT_MODE=$RECEIPT_MODE"
    fi
    echo "RUN_ID=$RUN_ID"; } > "$OUT/experiment.env"
}

step_bound() {
  STAGE="bound"
  (cd "$REPO/tank-arena" && $(bound_cmd "$GPY")) > "$RES/bound.json" || die "arena.bound failed"
  "$LPY" - "$RES/bound.json" "$START_POINTS" <<'PY' || die "bound check failed (see results/bound.json)"
import json, sys
b = json.load(open(sys.argv[1]))
if b.get("ok") is not True or b.get("start_points") != int(sys.argv[2]):
    sys.exit(f"bound {b} not ok or start_points != {sys.argv[2]}")
PY
  say "bound: $(cat "$RES/bound.json")"
}

step_setup_arbitrum() {
  STAGE="setup (arbitrum)"
  (cd "$REPO" && timeout 900 $(rollup_setup_cmd "$LPY" "$ARB_L1" "$ARB_L2_HTTP" "$RES")) \
    > "$LOGS/uc2-setup.out" 2>&1 || die "measure.uc2_setup failed (see logs/uc2-setup.out)"
  say "token: $(tail -1 "$LOGS/uc2-setup.out")"
}

step_setup_perun() {
  STAGE="setup (perun)"
  if [ -z "$PERUN_BIN" ]; then
    PERUN_BIN="$WORK/perun-node"
    (cd "$REPO/perun/hub-demo" && go build -o "$PERUN_BIN" .) > "$LOGS/go-build.out" 2>&1 \
      || die "go build perun-node failed (see logs/go-build.out)"
  fi
  (cd "$REPO" && timeout 300 "$LPY" - "$PERUN_L1" "$FUND_WEI" "$N_PLAYERS" <<'PY') || die "funding 110..116 on Anvil failed"
import sys
from web3 import Web3
from measure import arbitrum, keys
url, amount, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
l1 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
for i in [keys.ARENA] + [keys.player_index(k) for k in range(n)]:
    arbitrum.fund_on_l1(l1, keys.account(keys.FUNDER), keys.account(i).address, amount)
print(f"funded {keys.ARENA}..{keys.player_index(n - 1)} on Anvil (perun)")
PY
  local mint hub hub_points
  mint=$(cd "$REPO" && "$LPY" -c "import sys; from measure import keys; print(','.join(keys.account(keys.player_index(k)).address for k in range(int(sys.argv[1]))))" "$N_PLAYERS")
  hub=$(cd "$REPO" && "$LPY" -c "from measure import keys; print(keys.account(keys.ARENA).address)")
  hub_points=$("$LPY" -c "import json, sys; print(int(sys.argv[2]) * json.load(open(sys.argv[1]))['hub_deposit'])" "$RES/bound.json" "$N_PLAYERS")
  timeout 180 $(uc2_deploy_cmd "$PERUN_BIN" "$PERUN_CHAIN_WS" "$mint" "$hub" "$hub_points" "$RES/contracts.json") \
    > "$LOGS/uc2-deploy.out" 2>&1 || die "uc2-deploy failed (see logs/uc2-deploy.out)"
  (cd "$REPO" && "$LPY" -c "from measure import keys; print(keys.account(keys.ARENA).key.hex())") \
    | (umask 077; cat > "$WORK/hub.key")
  local hosts=() k
  for k in $(seq 0 $((N_PLAYERS - 1))); do hosts+=("$PERUN_HOST"); done
  (cd "$REPO" && cluster_json "$RES/bound.json" "$RES/contracts.json" "$WORK/hub.key" "$WORK/perun-state" \
      "$RES/hub" "$PERUN_HOST" "$PERUN_HOST:$PERUN_CONTROL_PORT" "http://$PERUN_HOST:$PERUN_CONTROL_PORT" "${hosts[@]}") \
    > "$RES/cluster.json" || die "writing cluster.json failed"
  mkdir -p "$RES/hub"
  STAGE="perun hub"
  (cd "$WORK" && exec $(uc2_hub_cmd "$PERUN_BIN" "$RES/cluster.json")) > "$LOGS/uc2-hub.out" 2>&1 &
  HUB_PID=$!
  local deadline=$((SECONDS + 120))
  until curl -s -m 3 "http://$PERUN_HOST:$PERUN_CONTROL_PORT/v1/manifest" | grep -q '"RunID"'; do   # the hub's manifest (distributed.go manifest), not uc1-sensor's
    kill -0 "$HUB_PID" 2>/dev/null || die "uc2-hub exited at start (see logs/uc2-hub.out)"
    [ "$SECONDS" -lt "$deadline" ] || die "uc2-hub: no /v1/manifest after 120 s (see logs/uc2-hub.out)"
    sleep 1
  done
  say "perun: contracts $(tr -d '\n ' < "$RES/contracts.json" | cut -c1-120)…; hub up (pid $HUB_PID)"
}

step_payclients() {
  STAGE="payment clients"
  local k pc cmd token="" recip=""
  if [ "$SYSTEM" != perun ]; then
    token=$("$LPY" -c "import json, sys; print(json.load(open(sys.argv[1]))['token'])" "$RES/token.json")
    recip="$RES/recipients.json"
  fi
  for k in $(seq 0 $((N_PLAYERS - 1))); do
    pc="$RES/p$k/pc"; mkdir -p "$pc" "$RES/p$k/game"
    if [ "$SYSTEM" = perun ]; then
      cmd=$(payclient_cmd "$LPY -u" "$k" "$SOCK/p$k.sock" "$pc" "$PERUN_BIN" "$RES/cluster.json")
    else
      cmd=$(payclient_cmd "$LPY -u" "$k" "$SOCK/p$k.sock" "$pc" "$ARB_L1" "$ARB_L2_WS" "$token" "$recip")
    fi
    echo "$cmd" > "$LOGS/p$k-payclient.cmd"
    (cd "$REPO" && exec $cmd) > "$LOGS/p$k-payclient.out" 2>&1 &
    PIDS+=($!)
  done
  STAGE="wait ready"; wait_payclients ready "$READY_WAIT_S"; say "payment clients ready"
}

# pc_states — one word per player, as deploy/cluster's pc_state: complete | failed:<why> | dead | ready | starting
pc_states() {
  local k alive=()
  for k in $(seq 0 $((N_PLAYERS - 1))); do
    if kill -0 "${PIDS[k]}" 2>/dev/null; then alive+=(1); else alive+=(0); fi
  done
  "$LPY" - "$RES" "${alive[@]}" <<'PY'
import json, os, sys
res, alive = sys.argv[1], sys.argv[2:]
for k, a in enumerate(alive):
    d = os.path.join(res, f"p{k}", "pc")
    try:
        life = json.load(open(os.path.join(d, "lifecycle.json")))
    except (OSError, ValueError):
        life = {}
    bad = next((x for x in ("open_error", "close_error", "error") if life.get(x)), None)
    if life.get("complete"):
        s = "complete"
    elif bad:
        s = f"failed:{bad}:" + str(life[bad])[:160].replace(" ", "_")
    elif a == "0":
        s = "dead"
    elif os.path.exists(os.path.join(d, "ready")):
        s = "ready"
    else:
        s = "starting"
    print(f"p{k}={s}")
PY
}

# wait_payclients WANT DEADLINE_S
wait_payclients() {
  local want=$1 deadline=$((SECONDS + $2)) line p s pending states k lines=()
  while :; do
    pending=""
    # Captured first, never read from a process substitution: its exit status is ignored under set -e,
    # so a crashed helper would yield no lines and pass the wait without checking any client.
    states=$(pc_states) || die "reading the payment clients' states failed (pc_states exit $?)"
    mapfile -t lines <<< "$states"
    [ "${#lines[@]}" -eq "$N_PLAYERS" ] || die "pc_states gave ${#lines[@]} line(s), want $N_PLAYERS: ${states:0:200}"
    for k in $(seq 0 $((N_PLAYERS - 1))); do
      line=${lines[k]}; p=${line%%=*}; s=${line#*=}
      [ "$p" = "p$k" ] && [ -n "$s" ] && [ "$s" != "$line" ] || die "pc_states line $k is '$line', want p$k=<state>"
      case "$s" in
        failed:*) die "payment client $p $s (see logs/$p-payclient.out, results/$p/pc/payclient.log)" ;;
        dead) die "payment client $p exited before '$want' (see logs/$p-payclient.out)" ;;
      esac
      [ "$s" = "$want" ] || { [ "$want" = ready ] && [ "$s" = complete ]; } || pending="$pending $p:$s"
    done
    [ -n "$pending" ] || return 0
    [ "$SECONDS" -lt "$deadline" ] || die "payment clients not '$want' after ${2}s:$pending"
    sleep 3
  done
}

step_game() {
  STAGE="game"
  local k peers="" cmd gpids=()
  for k in $(seq 0 $((N_PLAYERS - 1))); do peers="$peers${peers:+,}$GAME_HOST:$((GAME_PORT0 + k))"; done
  for k in $(seq 0 $((N_PLAYERS - 1))); do
    cmd=$(node_cmd "$GPY -u" "$k" "$peers" "$SOCK/p$k.sock" "$RES/p$k/game")
    echo "$cmd" > "$LOGS/p$k-node.cmd"
    (cd "$REPO/tank-arena" && exec $cmd) > "$LOGS/p$k-node.out" 2>&1 &
    gpids+=($!); PIDS+=($!)
  done
  say "match: ${N_PLAYERS} nodes, ${DURATION_S}s at ${TICKRATE} Hz, seed $GAME_SEED"
  local deadline=$((SECONDS + GAME_WAIT_S)) left
  while :; do
    left=0; for k in "${gpids[@]}"; do kill -0 "$k" 2>/dev/null && left=$((left + 1)); done
    [ "$left" -gt 0 ] || break
    [ "$SECONDS" -lt "$deadline" ] || die "$left game node(s) still running after ${GAME_WAIT_S}s"
    sleep 2
  done
  "$LPY" - "$RES" "$N_PLAYERS" <<'PY' || die "match aborted or node result missing"
import json, os, sys
res, n = sys.argv[1], int(sys.argv[2])
bad = []
for k in range(n):
    try:
        r = json.load(open(os.path.join(res, f"p{k}", "game", "node-result.json")))
    except (OSError, ValueError) as e:
        bad.append(f"p{k}: no node-result.json ({e})"); continue
    if r.get("aborted") or not r.get("complete"):
        bad.append(f"p{k}: aborted: {r.get('aborted')}")
    else:
        print(f"p{k}: {r['ticks']} ticks, emitted {r['emitted']}, trace {r['trace_sha256'][:16]}")
if bad:
    sys.exit("; ".join(bad))
PY
}

step_wait_complete() {
  STAGE="wait complete"
  wait_payclients complete $((DRAIN_S + COMPLETE_EXTRA_S))
  say "payment clients complete"
  if [ "$SYSTEM" = perun ]; then
    STAGE="wait hub"
    local deadline=$((SECONDS + HUB_EXIT_WAIT_S)) rc=0
    while kill -0 "$HUB_PID" 2>/dev/null; do
      [ "$SECONDS" -lt "$deadline" ] || die "uc2-hub still running ${HUB_EXIT_WAIT_S}s after every client completed"
      sleep 2
    done
    wait "$HUB_PID" || rc=$?
    HUB_PID=""
    [ "$rc" -eq 0 ] || die "uc2-hub exited $rc (see logs/uc2-hub.out)"
    say "hub settled and exited"
  fi
  local p; for p in "${PIDS[@]}"; do wait "$p" 2>/dev/null || true; done; PIDS=()
}

step_balances() {
  STAGE="points balances"
  local url=$ARB_L2_HTTP; [ "$SYSTEM" != perun ] || url=$PERUN_L1
  points_balances_json "$url" > "$RES/points-balances.json" || die "reading the points balances failed"
  say "points balances: $(tr -d '\n ' < "$RES/points-balances.json")"
}

step_hash() {
  STAGE="reference and hash check"
  (cd "$REPO/tank-arena" && $(reference_cmd "$GPY" "$WORK/reference.jsonl")) || die "arena.sim failed"
  sha256sum "$WORK/reference.jsonl" | cut -d' ' -f1 > "$RES/reference.sha256"
  local ref k s bad=""; ref=$(cat "$RES/reference.sha256")
  for k in $(seq 0 $((N_PLAYERS - 1))); do
    s=$(sha256sum "$RES/p$k/game/trace.jsonl" 2>/dev/null | cut -d' ' -f1)
    [ "$s" = "$ref" ] || bad="$bad ${PLAYERS[k]}"
  done
  if [ -n "$bad" ]; then echo "${bad# }" > "$OUT/HASH_MISMATCH"; say "HASH MISMATCH:$bad"; else say "hash ok: $ref"; fi
}

step_analyze() {
  STAGE="analyze"
  (cd "$REPO" && "$LPY" -m measure.analyze_uc2 "$OUT") > "$LOGS/analyze.out" 2>&1 || die "analyze_uc2 failed (see logs/analyze.out)"
  "$LPY" - "$OUT/summary.json" <<'PY' || die "summary not complete (see summary.json complete_checks)"
import json, sys
s = json.load(open(sys.argv[1]))
o, r = s["outcomes"], s["reconciliation"]
print(f"complete {s['complete']}  hash_ok {s['hash_ok']}  payments {s['offered']['payments']}  "
      f"succeeded {o.get('succeeded')} failed {o.get('failed')} unresolved {o.get('unresolved')} late {o.get('late')}")
print(f"reconciliation ok {r['ok']} ok_with_late {r['ok_with_late']}  next_shot_miss guests "
      f"{s['next_shot_miss_share']['guests']['share']} host {s['next_shot_miss_share']['host']['share']}")
if not (s["complete"] and s["hash_ok"]):
    print(json.dumps(s["complete_checks"]), file=sys.stderr)
    sys.exit(1)
PY
}

main() {
  [ -x "$LPY" ] || { echo "no measure python at $LPY (set LPY=...)" >&2; exit 2; }
  if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then echo "$OUT exists and is not empty" >&2; exit 2; fi
  mkdir -p "$RES" "$WORK" "$LOGS"; OUT=$(cd "$OUT" && pwd); RES="$OUT/results"; WORK="$OUT/work"; LOGS="$OUT/logs"
  SOCK=$(mktemp -d /tmp/uc2-sock.XXXXXX)
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  say "run $RUN_ID -> $OUT"
  write_env
  step_bound
  if [ "$SYSTEM" = perun ]; then step_setup_perun; else step_setup_arbitrum; fi
  step_payclients
  step_game
  step_wait_complete
  step_balances
  step_hash
  step_analyze
  [ ! -f "$OUT/HASH_MISMATCH" ] || { STAGE="hash check"; die "trace hash differs on: $(cat "$OUT/HASH_MISMATCH")"; }
  STAGE="done"; say "run complete: $OUT/summary.json"
}

main
