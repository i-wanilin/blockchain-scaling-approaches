# tank-arena — the UC2 game (real-time multiplayer with token payments)

The self-built gaming use case (UC2) of the thesis (*A Use-Case-Driven Comparison
of Layer-2 Scaling Approaches for IoT Micropayments and On-Chain Gaming*): a
deterministic multiplayer 2D top-down tank arena, inspired by the
pick-up-and-play combat of *Tanks 1990 / Battle City*.

## UC2 as built (thesis framing)

The authoritative description is the cluster spec
(`docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md`, §7.3); the
frozen game parameters are in `TUNING.md`.

- **Six bot players, one per Raspberry Pi**, play one match of **60 s at 20 Hz,
  seed 42**, in the game's peer-to-peer lockstep mode (`arena/node.py`, topology
  `mesh`): every board sends its own tank's input each tick to every other board
  and computes the same game. No relay and no game server.
- **Movement is not a transaction.** The per-tick inputs travel directly between
  the boards over the game's own mesh, the same way for all three systems.
- **Only shots and hits are payments**, carried by the Layer-2 under test: a shot
  costs 1 point (shooter → arena), every hit pays 5 points (victim → shooter).
  The game hands each payment to the local payment client (`arena/paybridge.py`)
  and never waits for its confirmation. Seed 42 gives 813 shots and 257 hits,
  1 070 payments, about 17.8 per second (a result of the frozen tuning, not a
  target).
- **Frozen at Checkpoint 1 (owner, 2026-10-06):** weapon, damage, bot shooting
  behaviour and the config digest are recorded in `TUNING.md`; every cluster run
  checks the game hash (the `*.py` files of `arena/` and `net/`) and reproduces
  the seed-42 reference log (`reference/`) byte for byte.
- UC2 measures payment processing under an honest workload; it does not enforce
  the game's rules on-chain (cluster spec §7.3, *Scope*).

The simulation is **deterministic** given `(seed, players, tickrate,
duration)`, which makes reproducible traces *and* lockstep distributed
execution possible. Core is Python ≥ 3.11 **stdlib only**; `pygame` is an
optional extra for the renderer and the viewer.

## Quickstart (headless)

```sh
cd tank-arena

# the UC2 match as a trace: 6 bots, 20 Hz, 60 s, seed 42
python -m arena.sim --players 6 --tickrate 20 --duration 60 --seed 42 \
                    --out trace.jsonl

# throughput timeline + burstiness
python -m arena.stats trace.jsonl

# tests
python -m unittest discover -s tests -t . -v
```

## Trace schema (JSONL, one event per line)

```json
{"tick": 137, "t_ms": 6850.0, "type": "move", "player": 2,
 "payload": {"x": 412.3, "y": 88.1, "heading": 1.5708,
             "turret_heading": 0.92, "speed": 61.2}}
```

- `move` — movement state-update, one per **alive** tank per tick
  (a state message, not a value transfer).
- `fire` — token transfer, payload `{angle, ammo_left, from, to: "arena",
  amount}`.
- `hit` — token transfer victim → shooter, payload `{victim, shooter,
  damage, from, to, amount, killed}`.
- `break` — shared-state message for a destroyed brick block, payload
  `{block, kind, x, y}`. Stone absorbs the shell and never emits `break`.

See `arena/events.py` (authoritative, including `validate()`).

## Layout

| Path | What |
|---|---|
| `arena/world.py` | deterministic fixed-timestep core: physics, collisions, weapon, respawn, point balances; `Config` holds the frozen UC2 weapon |
| `arena/bots.py` | Hunter (even ids) and Flanker (odd ids) agents; `FireParams` holds the frozen shooting behaviour |
| `arena/events.py` | the trace event schema and `validate()` |
| `arena/sim.py` | headless runner / CLI → JSONL trace |
| `arena/stats.py` | tx/s timeline, move/fire/hit counts, peak/mean burstiness |
| `arena/node.py` | distributed lockstep node (one player per process) — **what the cluster runs** |
| `arena/paybridge.py` | UC2 payment bridge: hands each payment the board owes to the local payment client over a Unix socket |
| `arena/bound.py` | the points bound *S* and the channel / deposit amounts derived from it |
| `arena/tune.py` | per-seed tuning statistics against the acceptance band, config digest |
| `arena/render.py` | optional pygame viewer (live + plain replay of a trace) |
| `arena/util.py` | shared helpers |
| `net/protocol.py` | framed-JSON-over-TCP protocol used by the lockstep mesh |
| `arena/multiplayer.py`, `arena/showcase.py`, `net/relay.py` | human pygame client, stable-seed showcase, relay server — **kept, not part of the measurement**; they lie inside the game hash (`arena/`, `net/`), so they must not be deleted or edited |
| `viz/` | UC2 match viewer and paper figures (`viz/match.py`, sprites in `viz/sprites.py`), and one board's view of a measured run (`viz/board.py`); outside the game hash |
| `watch.sh` | wrapper to watch the UC2 match, another seed, or a cluster run's trace |
| `reference/` | the seed-42 UC2 reference log and its SHA-256 |
| `TUNING.md` | the tuning record and frozen parameters (Checkpoint 1) |
| `workloads/` | normalized-event adapter for a trace (`workloads/game.py`); test-only, not used by the measurements |
| `tests/` | unittest suite incl. multi-process lockstep integration tests |

## Distributed mode (lockstep)

Each node broadcasts its per-tick input and steps the same deterministic
world — classic lockstep. All nodes therefore write **byte-identical**
traces; comparing hashes across nodes is the built-in consistency check.
Protocol: 4-byte length-prefixed JSON frames over TCP (`net/protocol.py`).
The human client adds an explicit synchronized lobby: all expected players must
connect and independently click Ready before the countdown begins.

```sh
# full mesh, 3 players on one machine:
python -m arena.node --id 0 --peers :9100,:9101,:9102 --tickrate 20 --duration 30 --seed 42 --out n0.jsonl
python -m arena.node --id 1 --peers :9100,:9101,:9102 --tickrate 20 --duration 30 --seed 42 --out n1.jsonl
python -m arena.node --id 2 --peers :9100,:9101,:9102 --tickrate 20 --duration 30 --seed 42 --out n2.jsonl

# or via a relay (star topology; not used by the measurement):
python -m net.relay --port 9000 --expect 3
python -m arena.node --id 0 --players 3 --topology relay --relay :9000 ...
```

The `--peers` list is ordered by node id and identical on every node; on the
Pi cluster the entries are the six player boards' addresses (cluster spec §7.3,
*What the boards run*), and `deploy/cluster` starts the nodes with
`--pay-socket` (see *UC2* below).

## Human multiplayer (not part of the measurement)

`arena/multiplayer.py` and `net/relay.py` are the earlier human-play mode; they are
kept (inside the game hash) but the thesis measurements use only the bot mesh above.

Install pygame on each **player** machine. The hub only needs stdlib Python.

```sh
# hub machine — two players, star topology
cd tank-arena
python -m net.relay --host 0.0.0.0 --port 9000 --expect 2

# player machine 1
python -m arena.multiplayer --id 0 --players 2 \
  --relay HUB_IP:9000 --duration 180 --out trace-player0.jsonl

# player machine 2
python -m arena.multiplayer --id 1 --players 2 \
  --relay HUB_IP:9000 --duration 180 --out trace-player1.jsonl
```

The lobby lists every slot as `WAITING`, `CONNECTED`, or `READY`. Click the
button or press Enter/R only after all machines are connected. When all players
are ready, every client gets the countdown and enters the same lockstep match.

Controls: **arrows or WASD** move in four directions; **Space** fires. Brick
blocks are destroyed by one shell; steel-gray stone blocks are permanent.
Every hit pays 5 points (victim → shooter), every shot costs 1; destroyed tanks respawn. There is
no base-defense or enemy-wave layer in this multiplayer version.

Every player writes a byte-identical trace. Compare before benchmarking:

```sh
sha256sum trace-player*.jsonl
```

## Not in this repository

Earlier versions of this README described a "datasimulator" (`workloads/platooning.py`,
`workloads/pmu.py`, `workloads/replay.py`), `PaymentBackend` classes (`backends/*`,
including a `web3` EVM JSON-RPC backend and a Perun stub) and a trace replayer
`bench/paced_bench.py`. That code is not in this repository (it belonged to the exploratory spike and was not carried forward), and the thesis
measurements do not replay traces: UC2 payments are made live by `arena/node.py`
through `arena/paybridge.py` and the payment clients in `measure/` and
`perun/hub-demo/` (cluster spec §7.3). Only `workloads/game.py` remains, a
normalized-event adapter exercised by the tests.

## Renderer (optional)

```sh
pip install pygame==2.6.1     # the version the viewer and the UC2 figures were made with
python -m arena.render --replay trace.jsonl --speed 2
python -m arena.render --players 6 --tickrate 20 --seed 42   # live
python -m arena.render --duration 5 --uncapped --screenshot arena.png
```

UC2 viewer and paper figures (`watch.sh`, `viz/match.py`; outside `arena/` so the game hash is
unchanged). The recorded trace has no shell positions, so `viz/match.py` re-runs the frozen game
(as `arena.sim`) and checks that seed 42 reproduces the UC2 reference log; it shows shells in
flight, muzzle and hit bursts, kills and the payments (+5 victim→shooter, 1 point per shot).
Keys: SPACE pause, ←/→ step (SHIFT: 1 s), ↑/↓ speed, T trails, S save a still to `screenshots/`.
Sprite style: `--vehicle` (or `VEHICLE=` for `watch.sh`), one of classic, mbt, ifv, token, car,
current; the default is `classic`.

```sh
tank-arena/watch.sh            # the UC2 match (seed 42)
tank-arena/watch.sh 4          # seed 4 of the frozen game
tank-arena/watch.sh results/pi/uc2/<run> p3   # that run as bath4 (p3) lived it: real tick times, its payments
cd tank-arena && python -m viz.match --figures ../analysis/figures/uc2-match               # 4 stills + 2x2 panel
cd tank-arena && python -m viz.match --figures out --ticks 120,331               # chosen ticks
```

The renderer uses a deliberately old-2D arcade direction: a 20×18 black
playfield, dense continuous orange brick formations, bright segmented steel,
four-direction pixel tanks and shells, and a fixed gray status rail. The core
never imports pygame; the simulation, the node and the CLI tools run headless.

### Stable agent showcase

```sh
# six visible autonomous agents; fixed spawns/decisions, 1280×960 window
python -m arena.showcase

# byte-stable headless trace of the same seed
python -m arena.sim --players 6 --tickrate 20 --duration 180 --seed 1990 \
  --out trace-showcase-seed1990.jsonl
```

The stable showcase (not part of the measurement) alternates two strategies by
player id, producing an exact three/three split with six tanks. Hunters close on
the nearest live opponent and maintain pressure. Flankers prioritize damaged
opponents, establish a perpendicular firing lane, hold ambush range, and retreat
from close brawls. `H` and `F` beside the HUD player number identify the
assignment. Seed `1990` fixes spawns, wandering, detours, and every subsequent
decision. With the frozen UC2 weapon (checked 2026-10-12 with `arena.sim
--seed 1990`), 20 s give 282 shots, 94 tank hits, 38 destroyed brick blocks and
2 kills; over 60 s, Hunters fire 597 shots and Flankers 184, with 4 kills each.

### Shooting model

- The gun is fixed to the hull in the default game: it cannot turn sideways and
  every shot follows the tank's current cardinal heading.
- The UC2 weapon (below) fires 10 bullets/s from a 30-round magazine with swept
  collision; 27 hits kill (damage 6, 160 HP).
- Human and autonomous tanks submit the same four-direction control shape.
- Autonomous tanks commit to a heading for 0.60 s (36 units / 0.9 tile) before
  a voluntary turn, preventing rapid `WDWD` input from approximating diagonal
  travel. A collision detour may turn immediately so a tank can escape a wall.
- A deterministic stuck detector sends agents on a perpendicular cardinal
  detour when brick, stone, or another tank blocks their route.

### Match pacing

The default balance treats each 40-unit tile as the useful unit of rhythm:

| Measure | Default | Gameplay consequence |
|---|---:|---|
| Arena | 20×18 tiles (800×720) | more flanking space without losing the maze |
| Forward speed | 60 units/s (1.5 tiles/s) | 13.3 s to cross the open arena horizontally |
| Agent heading hold | 0.60 s (0.9 tile) | no frame-by-frame staircase diagonal exploit |
| Shell speed | 320 units/s (8 tiles/s) | firing stays responsive while hulls feel heavy |
| Fire / reload | 0.10 s per bullet, 2.5 s magazine reload | one attacker needs 2.6 s from its first to 27th hit |
| Respawn | 6.0 s | death costs about 2.3 minimum kill-times (6.0 s / 2.6 s) |

The six-second penalty is visible as `R6` through `R1` beside a dead player's
HUD entry. It is long enough to make reckless attacks expensive, but short
enough that a player remains involved in a multiplayer test session.

## UC2: weapon, bridge, bound, tuning

The game of the UC2 benchmark (cluster spec §7.3), frozen by the owner on
2026-10-06; `TUNING.md` holds the record and the frozen digest.

- **Weapon** (`arena/world.py` `Config`): 0.10 s between bullets while the trigger is
  held, a 30-round magazine, 2.5 s reload after the last bullet, damage 6 against
  160 HP, respawn 6 s. A shot costs 1 point (shooter → arena), a hit pays 5 points
  (victim → shooter). No negative balances: a shot needs at least its cost, and a
  hit moves at most the victim's balance. Every player starts with 10 000 points.
- **Bots** (`arena/bots.py` `FireParams`): bursts of 3–8 bullets when an opponent is
  in a plausible lane (40 units off the line of fire), taps beyond 240 units, a
  2 % speculative tap; the trigger is released for 4 ticks after each burst.
- **Bridge** (`arena/paybridge.py`): `python -m arena.node … --pay-socket PATH` hands
  every payment its board owes (its own shots to the arena, the hits it takes to the
  shooter) to the local payment client over a Unix socket, one JSON line each, ID
  `uc2/<run>/p<payer>/t<tick>/<kind><seq>` (deterministic, so a resent line never
  pays twice). The game never waits for the client.
- **`python -m arena.bound [--json]`**: the most bullets one tank can fire in the
  match, *S* = 336 (6 players, 20 Hz, 60 s), and the amounts that follow: ammo 336,
  each virtual channel 1 680 per side, the hub's deposit 8 400 per ledger channel,
  a player's need 8 736 ≤ 10 000. Perun's channels are sized from it, and every
  cluster run checks `ok`.
- **`python -m arena.tune --seeds 1-20,42 [--damage D] [--plausible-lane L] …`**:
  per-seed match statistics against the acceptance band (every player fires, hit
  share 0.10–0.40, at least one kill, the first after 3 s) and the config digest.
- **Reference**: `reference/uc2-seed42.jsonl.gz` and its SHA-256 (`bc1d6874…`), the
  seed-42 match every cluster run must reproduce byte for byte on every board.
  Results of the 2026-10-06 series: cluster spec §8 step 6.
