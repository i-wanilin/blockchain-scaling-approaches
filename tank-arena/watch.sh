#!/usr/bin/env bash
# watch.sh — watch a UC2 tank-arena match.
#
#   tank-arena/watch.sh                 the UC2 match (seed 42) with shells, hit bursts and payments (viz/match.py;
#                                       re-run from the frozen game and checked against the UC2 reference log)
#   tank-arena/watch.sh 7               the frozen game with seed 7 (6 bots, 20 Hz, 60 s; TUNING.md per-seed table)
#   tank-arena/watch.sh <run dir>       plain replay of the trace a UC2 cluster run recorded (arena/render.py; the
#                                       trace has no shell positions, so no bullets) -- results/pi/uc2/<run>
#   tank-arena/watch.sh <run dir> p3    the run as one board lived it (viz/board.py): ticks at their recorded
#                                       times, so lockstep waits show as freezes, and that board's own payments
#                                       (paid / waiting, paid before its next shot); p0..p5, 0..5 or a board name
#   tank-arena/watch.sh <trace.jsonl>   plain replay of any trace file (.jsonl or .jsonl.gz)
#   SPEED=2 tank-arena/watch.sh ...     faster (0.5 = slow motion); keys in viz/match.py, ESC or close to stop
#
# Outside arena/ on purpose: the game hash (deploy/cluster GAME_HASH_CMD) covers arena/ and net/ only.
# Needs pygame: VIEWER_PY (default ~/.venvs/arena-viewer/bin/python; python3 -m venv ~/.venvs/arena-viewer &&
# ~/.venvs/arena-viewer/bin/pip install pygame==2.6.1, the version the figures were made with).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
py=${VIEWER_PY:-$HOME/.venvs/arena-viewer/bin/python}
cache=${XDG_CACHE_HOME:-$HOME/.cache}/tank-arena-replays
mkdir -p "$cache"
arg=${1:-}
if [ -z "$arg" ] || [[ $arg =~ ^[0-9]+$ ]]; then
  cd "$here" && exec "$py" -m viz.match --seed "${arg:-42}" --speed "${SPEED:-1}" --vehicle "${VEHICLE:-classic}"
elif [ -d "$arg" ] && [ -n "${2:-}" ]; then
  run=$(cd "$arg" && pwd)
  cd "$here" && exec "$py" -m viz.board "$run" --player "$2" --speed "${SPEED:-1}"
elif [ -d "$arg" ]; then
  src=$(ls "$arg"/results/p0/game/trace.jsonl* 2>/dev/null | head -1)
  [ -n "$src" ] || { echo "no results/p0/game/trace.jsonl in $arg" >&2; exit 1; }
  trace="$cache/$(basename "$arg").jsonl"; zcat -f "$src" > "$trace"
else
  trace="$cache/$(basename "${arg%.gz}")"; zcat -f "$arg" > "$trace"
fi
echo "replaying $trace (speed ${SPEED:-1})"
cd "$here" && exec "$py" -m arena.render --replay "$trace" --speed "${SPEED:-1}"
