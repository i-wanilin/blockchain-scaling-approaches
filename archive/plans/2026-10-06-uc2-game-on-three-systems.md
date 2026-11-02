> **ARCHIVED** — executed 2026-10-06; the code and the cluster spec §8 are authoritative.

# UC2 — the game on Perun, Arbitrum and zkSync on the Pi cluster — Implementation Plan

**Status:** draft, written 2026-10-06. Not approved yet: the owner reviews it first.

**Goal:** One command per system, `deploy/cluster run uc2-<stack> --reps 3`, plays the frozen six-player `tank-arena` match on the Pi cluster: the game runs peer to peer, and each board's shots and hits are paid in points through the system under test. The run records everything spec §7.3 asks for, and `measure/analyze_uc2.py` turns the nine run folders into the UC2 results.

**Architecture:** the game (`tank-arena/arena/node.py`, mesh lockstep, 20 Hz) runs on six player boards. Each board's node hands the payments that board owes (its own shots; the hits it takes) to the local payment client over a Unix socket, as the UC1 customer does, and never waits. The payment client is `measure.payclient`, the UC1 code with the same ledger, queue, drain and lifecycle. On the rollups it sends one ERC-20 `transfer` of `PointsToken` per payment through the existing pipelined `OrderedSubmitter`. On Perun it drives a new Go sidecar, `perun-node -mode uc2-player`, which holds one ledger channel to the hub (ammo) and five virtual channels (hits), one FIFO per channel. The hub, `perun-node -mode uc2-hub`, runs on `bath1` beside player 0. `deploy/cluster` gains a UC2 run path: setup (token, mint, funding), payment clients, then the game, then collection, the event-log hash check and the points balances.

**Tech Stack:** Python 3.12 (`measure/`, web3 8.0.0; `tank-arena` stdlib only, Python ≥ 3.11), Go 1.26.4 with go-perun v0.15.0, perun-eth-backend v0.6.0 and go-ethereum v1.15.11 (`perun/hub-demo/go.mod`), static arm64 builds, Anvil 1.7.1, the Arbitrum and zkSync stacks on `bath0` as in UC1, Node.js with zksync-ethers 6 for the one zkSync contract deployment, bash.

**Spec:** `docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md`: §7.3 in full (especially *Game rules*, *Points, not money*, *Who pays which fee*, *What the boards run*, *Fairness*, *Payment ledger*, *Perun channel capacity*, *Run parameters*, *New code*), §6 recording, §8 step 6 (the order of work), §9 U7/U8. Parent spec: `docs/superpowers/specs/2026-09-03-l2-benchmark-design.md`. Format template: `archive/plans/2026-10-01-uc1-perun-measurement-path.md`.

---

## Decided — not open

Everything in spec §7.3 *Run parameters* (the owner's decisions of 2026-10-06 and the rulings there). In short:
- 6 players, 20 Hz, 1 200 ticks, seed 42, mesh; 3 runs per system with the same seed.
- `bath1` is player 0 and the hub, then `bath2`, `bath7`, `bath4`, `bath5`, `bath6`. `bath3` is idle and excluded.
- Mixed Wi-Fi (`WIFI_5GHZ_BOARDS="bath1 bath2"`).
- 10 000 points per player, no negative balance on any system.
- The fixed weapon: 10 rounds/s, 30-round magazine, 2.5 s reload. Only bullet damage and bot behaviour are tuned. The acceptance band: hits 10–40 % of shots, every player fires, kills in every match, no kill in the first 3 s.
- The rules-derived channel bound.
- UC1's queue, in-flight, drain, retry, fee-cap, receipt and Close rules.

## Rulings made in this plan (cost if wrong)

| # | Ruling | Cost if wrong |
|---|---|---|
| R1 | **Accounts:** 110 = the hub on Perun and the `arena` recipient on the rollups; 111…116 = players 0…5; 119 = the points-token deployer and minter on the rollups. All from the public mnemonic (`measure/keys.py`) and funded on the L1 from index 0 in setup, untimed. | none: local chains only |
| R2 | **A payment event names a logical recipient,** `"arena"` or `"p<k>"`. Each payment client maps it: the rollups to an address (`--recipients` file), Perun to a channel (`hub` or the virtual channel with player k). For UC2, `amount_wei` carries points (token units); the field name is kept so the ledger is unchanged. | rename later; only docstrings change |
| R3 | **The event ID is `uc2/<run>/p<payer>/t<tick>/<kind><seq>`.** `kind` is `fire` or `hit`; `seq` counts that payer's payment events of that kind in the tick, from 0. The payer is the shooter for `fire` and the victim for `hit`. The analysis reads payer, tick and kind from the ID. | none |
| R4 | **`t_intended` of an event at world tick T** = the board's match start (wall clock, taken right after the ready barrier) + (T − 1) / 20 s, the moment the tick that produced it was scheduled. `t_generated` = `time.time()` right after `World.step` returned. | none: both are reported |
| R5 | **A hit capped to 0 points** by the no-negative rule produces no payment. It cannot happen with 10 000 points, and the hit stays in the event log. | none |
| R6 | **The zkSync token is deployed by a small Node script** (`deploy/zk-token/deploy.js`, zksync-ethers' `ContractFactory`). An EraVM deployment needs an EIP-712 transaction with factory deps, which `web3.py` does not build. `mintBatch` and all payments stay in Python (type-2 transactions, accepted on zkSync as in UC1's Close). The EraVM bytecode is committed (`contracts/points/zksync/PointsToken.zbin`) with its SHA-256. | a Node dependency on the workstation only; the boards never run Node |
| R7 | **The hub enforces the payment policy on ledger channels** (`validatePaymentUpdate`: a player may only lower its own balance); today's hub accepts any update. Finalize updates still pass, as in UC1. | if the policy breaks virtual-channel funding, Task 9 shows it at once; then hub policy off, stated in the thesis |
| R8 | **The UC2 code hash** (`game-hash.txt`) covers `tank-arena/arena/*.py` and `tank-arena/net/*.py`; it is checked on every board like `code-hash.txt`. `code_hash` (measure only) is unchanged. | none |
| R9 | **`IDLE_BOARDS="bath3"`** in every UC2 env removes `bath3` from `BOARDS` for that run: no code check, no facts, no recorder. Pre-flight's register check expects the remaining 7 and records `idle_boards` in the manifest. | none |
| R10 | **Rollup Close** is UC1's: withdraw half the remaining L2 ETH (the withdrawal *submission*). Points are not withdrawn: they live on the rollup (spec §7.3 *Who pays which fee*). | none |
| R11 | **Final points balances** are read once by the workstation after every payment client has completed: `balanceOf` for the six players and `arena` on the rollups, or the withdrawn token balances on Anvil for Perun (`results/points-balances.json`), as UC1 reads `sensor-balance.json`. | none |
| R12 | **The workstation end-to-end test** (Task 12) runs Perun on a local Anvil and Arbitrum on the local nitro-testnode. zkSync runs locally only for its token deployment and gas estimate (Task 6); its first full UC2 match is the cluster smoke. | a zkSync-only bug shows up one stage later (at Checkpoint 3) |

## Checkpoints — stop and ask the owner

1. **Checkpoint 1, after Task 3 (the game is frozen):** show `tank-arena/TUNING.md`, which has the values tried, the chosen set, the statistics for seeds 1–20 and 42, and the computed *S* and channel amounts. Nothing on the Pis starts before the owner's go. After the go, the game code is not changed again.
2. **Checkpoint 2, before Task 14 (the first UC2 run on the boards):** show the `deploy/cluster` diff and the experiment files. `bash -n` and `shellcheck` must be clean.
3. **Checkpoint 3, after Task 14 (three smoke matches, one per system):** for each system, show outcomes, latency, tick lag, match stretch, the hash check, reconciliation, Open/Close, and the CPU of the player boards (U7, U8). The series starts only after the owner's go.
4. **Anything unexpected:** a spec rule that cannot be met, a number far off, or a board fault. Describe it and ask; do not choose silently.

Between checkpoints, work on; rulings needed on the way go into the SDD ledger with their cost if wrong and into one list at the end (`feedback-autonomous-sessions`). Nothing is pushed until the owner says so.

## Global Constraints

- **Frozen parameters** (spec §7.3): `n_players=6`, `tickrate=20`, 1 200 ticks (`--duration 60`), seed 42, topology `mesh`; start balance **10 000**; shot cost 1; hit payment 5; fire interval 0.10 s, magazine 30, reload 2.5 s. Changing any of them after Checkpoint 1 needs the owner.
- **Bound condition**, checked by every run's setup: `start ≥ (shot_cost + hit_payment × (n − 1)) × S`. The setup takes *S* from `python -m arena.bound`, never from the spec text. Virtual stake = `hit_payment × S` per side; hub deposit per ledger channel = `(n − 1) × hit_payment × S`; the player deposits its whole 10 000.
- **Determinism:** `World.step` stays a pure function of (state, controls). All randomness comes from seeded `Random`s (`world-<seed>`, `bot-<seed>-<pid>`). The event log holds no wall-clock value. `python -m arena.sim` and six `arena.node` processes with the same parameters write **byte-identical** logs; a test pins this.
- **The game never waits for a payment.** The bridge only writes to the socket (no `await drain()` inside the tick loop); a slow payment client can only fill the socket buffer. If the socket is not listening before tick 0, the run aborts.
- **Unchanged UC1 behaviour:** every existing test in `measure/tests/`, `perun/hub-demo` and `tank-arena/tests` passes, except tests that pin the **old weapon** (`tank-arena/tests/test_bots.py`, `test_determinism.py`), which are updated in Tasks 1–2 with the reason in the commit. `deploy/cluster run uc1-*` must behave as before (UC2 branches on `USE_CASE=uc2`).
- **Layout** (spec §7.3): `PLAYERS=(bath1 bath2 bath7 bath4 bath5 bath6)` = players 0…5; hub on `bath1`; `bath0` the stack; `IDLE_BOARDS="bath3"`. Game mesh port 9100 on every player; Perun wire 9000; hub control HTTP `192.168.178.211:8091` (8091, not UC1's 8090, so a stray UC1 sensor never answers).
- **Payment client per player:** `MAX_QUEUE=10000`, `DRAIN_S=60`; Arbitrum `MAX_IN_FLIGHT=64`, `RECEIPT_MODE=own`; zkSync `MAX_IN_FLIGHT=20`, `RECEIPT_TIMEOUT_S=1200`, `CLOSE_TIMEOUT_S=1800`, `ZK_COMMIT_DEADLINE_MS=20000`; fee cap 10 × base fee (`FEE_CAP_MULTIPLIER`); Perun `MAX_IN_FLIGHT=64` outstanding in the sidecar, `UPDATE_TIMEOUT_S=10`, `CHALLENGE_S=10`, `PERSIST=1`, `STRICT_POLICY=1`, no retries.
- **Rollup funds:** `FUND_WEI=2 ETH` on the L1 per account; `DEPOSIT_WEI=1 ETH` bridged per player as *Open*.
- **Keys:** public mnemonic, local chains only. The Perun sidecar gets its key on stdin (`init`), the hub from a 0600 file.
- **Style:** Python as in `measure/` (`unittest`, docstrings that say why); `tank-arena` stdlib only; Go as in `perun/hub-demo` (stdlib `testing`, Anvil tests skip without `ANVIL_URL`); bash as in `deploy/cluster` (every wait has a deadline, a failure writes `RUN_FAILED`).
- **No commit to `v2` while a series runs** (aggregation refuses mixed commits). After a change to `measure/`, `tank-arena/` or `perun/`, run `deploy/cluster sync`. Long jobs run in tmux on the PC, never as a background task of a session.

## Review Focus

1. **A board's payment client is slower than its game.** Events pile up in the socket and the client's queue. The game's tick timing must not change because of it (no `await` on the socket in the tick loop), and every event must still get exactly one ledger outcome. Pinned in Task 4 (`test_bridge_never_blocks_the_tick_loop`).
2. **Two players hit each other in the same tick on Perun.** Both ends of one virtual channel propose at once. The collision policy must resolve it (one yields), both payments must succeed, and both ends must end with the same version. Pinned in Task 9 (`TestUC2HeadOnHitsInOneTick`).
3. **A player's lockstep stalls** (Wi-Fi drop): the match must abort after 30 s with a reason, the payment client must still drain and close, and the run must be failed rather than reported. Pinned in Task 4 (`test_stall_aborts_with_reason`) and Task 13 (the cluster fails the run on a node result with `aborted`).
4. **Reconciliation across boards.** A player's final balance is 10 000 − its own succeeded payments + the succeeded payments others made to it. Payments that were still in flight at the drain deadline may execute later, so the analysis must explain the difference rather than hide it. Pinned in Task 11 (`test_reconciliation_explains_late_rollup_payments`).
5. **The event-log hash differs on one board** (a nondeterminism or a dropped input). The run must be flagged and kept out of aggregation, never averaged in. Pinned in Task 11 (`test_hash_mismatch_flags_run`) and Task 13 (the cluster writes `HASH_MISMATCH`).

## What is verified and what is not

| Fact | Status | Source |
|---|---|---|
| `World.step` order: timers before firing, so a cooldown of 2 ticks fires every 2nd tick | verified (code read) | `arena/world.py` `step` |
| The node writes the same JSON lines as `sim.py` (`separators=(",", ":")`) | verified (code read); byte-equality pinned in Task 4 | `arena/node.py`, `arena/sim.py` |
| `TICK_TIMEOUT_S = 30` already bounds a lockstep wait; today it raises out of `run()` | verified (code read) | `arena/node.py` |
| `OrderedSubmitter` signs `data` (any contract call) | verified | `measure/txsign.py` |
| A reverted transfer ends as *failed* ("reverted") in the ledger | verified | `measure/ledger.py` `finish` |
| The hub accepts any ledger update today (no payment policy) | verified (code read) | `perun/hub-demo/hub.go` `updateHandler` |
| Ledger open with a different hub deposit: `OpenLedgerToWire(hubWire, my, hub, challenge)` | verified (signature) | `distributed.go` |
| Virtual stakes per side: `OpenVirtualToPeerWithChallenge(pw, ledger, payer, payee, challenge)` | verified (signature) | `client.go` |
| Pays on six channels in parallel from one player (one FIFO per channel) | **verify in Task 9** | — |
| *S* for the implemented weapon: 336 by this plan's reading of the tick order (the spec's 330 assumed 110 ticks per magazine cycle; the implementation gives 108) | **compute in Task 1**, then correct the spec table | — |
| Gas of one `PointsToken.transfer` on Arbitrum and on zkSync | **measure in Task 6** | — |
| zkSync accepts the Node-deployed token and type-2 `mintBatch` | **verify in Task 6** | — |
| A Pi 3B keeps 20 Hz lockstep with the payment client beside it (U7, U8) | **unknown — Task 14** | — |
| `bath7`'s link today (−51 dBm, 72/43 Mbit/s) holds during a match | **unknown — Task 14** | — |

---

## File structure

| File | Responsibility |
|---|---|
| `tank-arena/arena/world.py` | modify: magazine + reload weapon, no-negative rule, `Config` defaults frozen at Checkpoint 1 |
| `tank-arena/arena/bound.py` | new: *S* by simulation, the channel amounts, the bound check (`python -m arena.bound --json`) |
| `tank-arena/arena/bots.py` | modify: trigger-happy bots (`FireParams`), burst length, speculative shots |
| `tank-arena/arena/tune.py` | new: tuning statistics per seed, the acceptance check |
| `tank-arena/TUNING.md` | new: the tuning record |
| `tank-arena/arena/paybridge.py` | new: which events a board pays, the payment lines, the non-blocking socket writer |
| `tank-arena/arena/node.py` | modify: the bridge, `ticks.csv`, `node-result.json`, the stall abort with reason |
| `tank-arena/tests/test_weapon.py`, `test_bound.py`, `test_tune.py`, `test_paybridge.py`, `test_node_uc2.py` | new tests |
| `measure/keys.py` | modify: UC2 account indices |
| `measure/points.py` | new: ERC-20 calldata, the recipients file, token gas per system |
| `measure/payclient.py` | modify: `--asset points --token --recipients`; `--use-case uc2` dispatch for Perun |
| `measure/uc2_setup.py` | new: rollup setup (fund, bridge the deployer, deploy token, `mintBatch`, `token.json`) |
| `measure/perun.py` | modify: `PerunSidecar.pay(id_, amount_wei, to=None)` |
| `measure/perun_uc2.py` | new: the Perun UC2 lifecycle (`uc2-player` sidecar) |
| `measure/analyze_uc2.py` | new: one UC2 run folder → `summary.json` |
| `measure/aggregate.py` | modify: UC2 fields |
| `measure/preflight.py`, `measure/manifest.py` | modify: `IDLE_BOARDS`, game hash, game parameters, reference-log hash |
| `measure/tests/test_points.py`, `test_uc2_setup.py`, `test_perun_uc2.py`, `test_analyze_uc2.py`, `fixtures/uc2-run/` | new tests |
| `contracts/points/zksync/PointsToken.zbin` (+ `.sha256`) | new: EraVM bytecode |
| `deploy/zk-token/{package.json,package-lock.json,deploy.js}` | new: zkSync token deployment (workstation only) |
| `perun/hub-demo/uc2.go` | new: `uc2-deploy`, the UC2 config fields and validation, `uc2Main` dispatch |
| `perun/hub-demo/uc2_player.go` | new: `-mode uc2-player` sidecar |
| `perun/hub-demo/distributed.go`, `hub.go`, `main.go` | modify: hub from a contracts file and key file, hub payment policy, dispatch |
| `perun/hub-demo/uc2_test.go`, `uc2_integration_test.go` | new tests |
| `deploy/cluster` | modify: `sync` ships `tank-arena`; `IDLE_BOARDS`; the UC2 run path |
| `deploy/experiments/uc2-{perun,arbitrum,zksync}{,-smoke}.env` | new |
| `deploy/uc2-local.sh` | new: the workstation end-to-end match (Task 12) |
| `tank-arena/reference/uc2-seed42.jsonl.gz`, `.sha256` | new: the reference event log (Task 14) |

---

### Task 1: The weapon, the no-negative rule and the bound

**Files:**
- Modify: `tank-arena/arena/world.py`
- Create: `tank-arena/arena/bound.py`, `tank-arena/tests/test_weapon.py`, `tank-arena/tests/test_bound.py`
- Modify (old-weapon pins only): `tank-arena/tests/test_determinism.py`, `tank-arena/tests/test_bots.py`

**Interfaces:**
- Produces:
  - `Config` fields: `fire_cooldown_s: float = 0.10`, `max_ammo: int = 30` (the magazine), `reload_s: float = 2.5` (replaces `ammo_regen_s`), `start_tokens: int = 10000`; `projectile_damage` keeps a provisional 16 until Task 3.
  - `Tank.reload_ticks: int = 0` (> 0 while reloading).
  - `arena.bound.max_shots(config: Config, tickrate: int, ticks: int) -> int`
  - `arena.bound.amounts(config, tickrate, ticks, n_players) -> dict` with keys `S`, `ammo`, `virtual_stake`, `hub_deposit`, `player_need`, `start_points`, `respawn_ok`, `ok`
  - CLI `python -m arena.bound --players 6 --tickrate 20 --duration 60 --json` prints that dict and exits 1 if `ok` is false.

- [ ] **Step 1: Write the failing tests**

```python
# tank-arena/tests/test_weapon.py
"""The AK-style weapon and the no-negative rule (cluster spec §7.3)."""
import unittest

from arena.world import Config, Controls, World


def hold(world, ticks, pid=0):
    fires = []
    for _ in range(ticks):
        ev = world.step({pid: Controls(fire=True, drive_angle=world.tanks[pid].heading)})
        fires += [e for e in ev if e["type"] == "fire" and e["player"] == pid]
    return fires


class WeaponTest(unittest.TestCase):
    def world(self, **kw):
        return World(n_players=1, tickrate=20, seed=1, config=Config(enable_blocks=False, **kw))

    def test_holding_fires_every_second_tick(self):
        fires = hold(self.world(), 10)
        self.assertEqual([e["tick"] for e in fires], [1, 3, 5, 7, 9])

    def test_magazine_then_reload(self):
        w = self.world()
        fires = hold(w, 200)
        ticks = [e["tick"] for e in fires]
        self.assertEqual(ticks[29], 59)                 # 30th bullet
        self.assertEqual(ticks[30], 59 + 50)            # 50 ticks of reload after the last bullet
        self.assertEqual(fires[29]["payload"]["ammo_left"], 0)

    def test_tap_fires_one_bullet(self):
        w = self.world()
        ev = w.step({0: Controls(fire=True, drive_angle=w.tanks[0].heading)})
        ev += w.step({0: Controls(fire=False, drive_angle=w.tanks[0].heading)})
        self.assertEqual(sum(e["type"] == "fire" for e in ev), 1)

    def test_no_shot_below_the_shot_cost(self):
        w = self.world(start_tokens=2)
        fires = hold(w, 20)
        self.assertEqual(len(fires), 2)
        self.assertEqual(w.balances[0], 0)

    def test_hit_moves_at_most_the_victims_balance(self):
        w = World(n_players=2, tickrate=20, seed=3, config=Config(enable_blocks=False))
        w.balances[1] = 3
        w.tanks[1].x, w.tanks[1].y = w.tanks[0].x + 100, w.tanks[0].y
        w.tanks[0].heading = w.tanks[0].turret_heading = 0.0
        hits = []
        for _ in range(20):
            ev = w.step({0: Controls(fire=True, drive_angle=0.0), 1: Controls(drive_angle=w.tanks[1].heading)})
            hits += [e for e in ev if e["type"] == "hit"]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["payload"]["amount"], 3)
        self.assertTrue(all(h["payload"]["amount"] == 0 for h in hits[1:]))
        self.assertGreaterEqual(w.balances[1], 0)
```

```python
# tank-arena/tests/test_bound.py
"""The rules-derived points bound (cluster spec §7.3 *Perun channel capacity*)."""
import unittest

from arena.bound import amounts, max_shots
from arena.world import Config


class BoundTest(unittest.TestCase):
    def test_max_shots_matches_the_tick_arithmetic(self):
        # first bullet at tick 1, a magazine every 108 ticks (29 gaps of 2 + 50 reload):
        # 11 full magazines up to tick 1188, then bullets at 1189..1199 (6)
        self.assertEqual(max_shots(Config(), 20, 1200), 11 * 30 + 6)

    def test_amounts_and_condition(self):
        a = amounts(Config(), 20, 1200, 6)
        s = a["S"]
        self.assertEqual(a["virtual_stake"], 5 * s)
        self.assertEqual(a["hub_deposit"], 5 * 5 * s)
        self.assertEqual(a["player_need"], (1 + 5 * 5) * s)
        self.assertTrue(a["respawn_ok"])
        self.assertTrue(a["ok"])                        # 26 * 336 = 8 736 <= 10 000

    def test_condition_fails_when_the_balance_is_too_small(self):
        self.assertFalse(amounts(Config(start_tokens=5000), 20, 1200, 6)["ok"])

    def test_respawn_shorter_than_a_magazine_cycle_breaks_the_bound(self):
        self.assertFalse(amounts(Config(respawn_s=1.0), 20, 1200, 6)["respawn_ok"])
```

- [ ] **Step 2: Run them; they fail**

Run: `cd tank-arena && python -m unittest tests.test_weapon tests.test_bound -v`
Expected: errors (`reload_s` unknown, `arena.bound` missing).

- [ ] **Step 3: Implement**

In `world.py`:
- `Config`: as in *Produces*; delete `ammo_regen_s`.
- `Tank`: `ammo: int = 30`, `reload_ticks: int = 0`; drop `ammo_regen_ticks`.
- Timers, replacing the regen block:

```python
            if tank.cooldown_ticks > 0:
                tank.cooldown_ticks -= 1
            if tank.reload_ticks > 0:
                tank.reload_ticks -= 1
                if tank.reload_ticks == 0:
                    tank.ammo = c.max_ammo
```

- The fire condition gains `and self.balances[pid] >= c.shot_cost`. After `tank.ammo -= 1`: `if tank.ammo == 0: tank.reload_ticks = self._ticks(c.reload_s)`.
- Hit: `amount = min(c.hit_payment, max(self.balances[victim.pid], 0))`, move `amount`, and pass `amount` to `hit_event`.
- The module docstring gets one paragraph: the weapon (spec §7.3) and the no-negative rule.

`bound.py`:

```python
"""The points bound of a match (cluster spec §7.3 *Perun channel capacity*).

S = the most bullets one tank can fire in a match: the trigger held from the
first tick to the last, never killed, computed by running the real World so it
always matches the implementation. A tank that dies cannot gain bullets as long
as its respawn time is at least one magazine cycle (it respawns with a full
magazine); respawn_ok checks that, otherwise S is not a bound.
"""
from __future__ import annotations

import argparse
import json
import sys

from arena.world import Config, Controls, World


def max_shots(config: Config, tickrate: int, ticks: int) -> int:
    cfg = Config(**{**config.__dict__, "enable_blocks": False, "start_tokens": 10**9})
    w = World(n_players=1, tickrate=tickrate, seed=0, config=cfg)
    n = 0
    for _ in range(ticks):
        n += sum(e["type"] == "fire" for e in w.step({0: Controls(fire=True, drive_angle=w.tanks[0].heading)}))
    return n


def amounts(config: Config, tickrate: int, ticks: int, n_players: int) -> dict:
    s = max_shots(config, tickrate, ticks)
    tick = lambda sec: max(1, round(sec * tickrate))
    cycle = (config.max_ammo - 1) * tick(config.fire_cooldown_s) + tick(config.reload_s)
    stake = config.hit_payment * s
    need = (config.shot_cost + config.hit_payment * (n_players - 1)) * s
    respawn_ok = tick(config.respawn_s) >= cycle
    return {"S": s, "ammo": config.shot_cost * s, "virtual_stake": stake,
            "hub_deposit": (n_players - 1) * stake, "player_need": need,
            "start_points": config.start_tokens, "respawn_ok": respawn_ok,
            "ok": respawn_ok and need <= config.start_tokens}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m arena.bound")
    ap.add_argument("--players", type=int, default=6)
    ap.add_argument("--tickrate", type=int, default=20)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = amounts(Config(), a.tickrate, int(round(a.duration * a.tickrate)), a.players)
    print(json.dumps(r) if a.json else "\n".join(f"{k}: {v}" for k, v in r.items()))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

Then fix the tests that pin the old weapon. Keep their intent (determinism, bot movement) and replace numbers that came from the old fire rate. List each change in the commit message.

- [ ] **Step 4: Run all `tank-arena` tests**

Run: `cd tank-arena && python -m unittest discover -s tests -t . -v`
Expected: all PASS. Run `python -m arena.bound --json`; expected `"S": 336, … "ok": true`.

- [ ] **Step 5: Correct the spec's bound table**

In spec §7.3 *The bound*, replace S = 330 with the computed value and recompute the derived numbers. With S = 336: magazine cycle 108 ticks, virtual stake 1 680, player need 8 736, hub deposit 8 400 (50 400 in total), worst-case minimum 1 264. Add one line on why: timers run before firing, so the reload's 50 ticks start at the last bullet.

- [ ] **Step 6: Commit**

```bash
git add tank-arena/arena/world.py tank-arena/arena/bound.py tank-arena/tests docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md
git commit -m "tank-arena: AK-style weapon (10/s, 30-round magazine, 2.5 s reload), no negative balances, rules-derived points bound"
```

---

### Task 2: Trigger-happy bots

**Files:**
- Modify: `tank-arena/arena/bots.py`
- Test: `tank-arena/tests/test_bots.py` (extend)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class FireParams` with fields `plausible_lane: float = 60.0` (world units off the line of fire that still count as a plausible hit), `lead_ticks: int = 6` (an opponent predicted to enter the lane within this many ticks counts), `long_range: float = 240.0` (beyond it a bot taps), `burst_min: int = 3`, `burst_max: int = 8` (bullets per burst), `spec_p: float = 0.02` (per-tick chance of a speculative tap when an opponent is in range but not plausible).
  - `make_bot(pid, seed, tickrate, fire: FireParams | None = None)`, `make_bots(n, seed, tickrate, fire=None)`. The default `FireParams()` is what the game uses; Task 3 freezes its values.
  - `Bot._trigger(world, me, heading) -> bool`: holds the trigger for the remaining bullets of the current burst; it starts a burst or a tap from the rules above. All random draws come from `self.rng`.

- [ ] **Step 1: Write the failing tests**

```python
# added to tank-arena/tests/test_bots.py
class TriggerHappyTest(unittest.TestCase):
    def test_bursts_hold_the_trigger_on_consecutive_ticks(self):
        # place an opponent in the lane at mid range; the bot must hold fire >= burst_min ticks in a row
        ...
    def test_long_range_is_a_tap(self):
        ...
    def test_speculative_shots_are_rare_and_seeded(self):
        # same seed -> identical fire ticks; over 2 000 ticks without a plausible target the
        # share of ticks with a speculative tap is within spec_p +- 0.01
        ...
    def test_same_seed_same_controls(self):
        # two make_bots(6, 42, 20) driving two Worlds give identical Controls every tick
        ...
```

Write these four in full in the file, using the existing helpers in `test_bots.py` for placing tanks; the placement code follows the existing tests. Run `python -m unittest tests.test_bots -v`: they fail.

- [ ] **Step 2: Implement**

- Both `Bot.decide` and `FlankerBot.decide` replace their `fire = …` expressions with `fire = self._trigger(world, me, heading)`, keeping the existing reasons not to fire: a Flanker backing away (`distance < FLANK_HOLD_MIN`) and a detour that changed the heading.
- `_trigger`:
  1. If a burst is running (`self._burst_left > 0`): decrement and return True.
  2. Find the nearest live opponent within `ENGAGE_RANGE`. A hit is plausible if its lateral offset from the hull's line of fire is ≤ `plausible_lane`, or if, moving at its current speed and heading, it will be within the lane in ≤ `lead_ticks` ticks.
  3. If plausible: at distance > `long_range`, a tap (return True, `_burst_left = 0`); otherwise `_burst_left = rng.randint(burst_min, burst_max) − 1` and return True.
  4. Otherwise, with probability `spec_p` (one `rng.random()` per tick, drawn every tick so the sequence does not depend on the branch), a tap.
- The world's cadence (every 2 ticks) and the magazine decide when a held trigger actually fires.

- [ ] **Step 3: Run all `tank-arena` tests**

Run: `cd tank-arena && python -m unittest discover -s tests -t . -v`. Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tank-arena/arena/bots.py tank-arena/tests/test_bots.py
git commit -m "tank-arena: trigger-happy bots — plausible hits, bursts and taps, rare speculative shots, all seeded"
```

---

### Task 3: Tuning and freezing the game → Checkpoint 1

**Files:**
- Create: `tank-arena/arena/tune.py`, `tank-arena/tests/test_tune.py`, `tank-arena/TUNING.md`
- Modify: `tank-arena/arena/world.py` (`Config.projectile_damage` and other tuned defaults), `tank-arena/arena/bots.py` (`FireParams` defaults)

**Interfaces:**
- Produces:
  - `arena.tune.match_stats(seed, config=None, fire=None, players=6, tickrate=20, ticks=1200) -> dict`: `shots`, `hits`, `hit_share`, `kills`, `first_kill_tick`, `shots_per_player` (list), `payments` (shots + hits with amount > 0), `payments_per_s`.
  - `arena.tune.accept(stats) -> list[str]`: the violations; empty means accepted. The rules: every player fires; 0.10 ≤ `hit_share` ≤ 0.40; `kills ≥ 1`; `first_kill_tick > 60` (3 s at 20 Hz).
  - CLI `python -m arena.tune --seeds 1-20,42 [--damage 16] [--plausible-lane 60] …` prints one row per seed and the violations.
  - `arena.tune.config_digest(config, fire) -> str`: SHA-256 of the sorted JSON of both dataclasses (for the manifest).

- [ ] **Step 1: Write the failing tests**

```python
# tank-arena/tests/test_tune.py
import unittest

from arena.tune import accept, config_digest, match_stats


class TuneTest(unittest.TestCase):
    def test_accept_rules(self):
        ok = {"shots_per_player": [5] * 6, "hit_share": 0.2, "kills": 2, "first_kill_tick": 61}
        self.assertEqual(accept(ok), [])
        self.assertTrue(accept({**ok, "hit_share": 0.05}))
        self.assertTrue(accept({**ok, "kills": 0}))
        self.assertTrue(accept({**ok, "first_kill_tick": 60}))
        self.assertTrue(accept({**ok, "shots_per_player": [5, 5, 5, 5, 5, 0]}))

    def test_stats_are_deterministic(self):
        self.assertEqual(match_stats(7), match_stats(7))

    def test_frozen_defaults_pass_on_every_seed(self):
        for seed in list(range(1, 21)) + [42]:
            self.assertEqual(accept(match_stats(seed)), [], f"seed {seed}")

    def test_digest_changes_with_damage(self):
        from arena.world import Config
        self.assertNotEqual(config_digest(Config(), None), config_digest(Config(projectile_damage=17), None))
```

`test_frozen_defaults_pass_on_every_seed` is expected to fail until Step 3 freezes values that pass.

- [ ] **Step 2: Implement `tune.py`**

It uses `iter_sim`'s loop with `make_bots(..., fire=fire)` and a `World` with the given config, so the statistics come from the real game.

- [ ] **Step 3: Tune once, then freeze**

Start from the provisional values (damage 16, `FireParams()` defaults). Change one parameter at a time. Record every combination tried in `TUNING.md` (a table: the values, how many seeds pass, mean hit share, kills, mean payments/s). Stop at the first combination that passes on all 21 seeds. If none passes within 12 combinations, stop and ask (Checkpoint 4). Write the chosen values into the `Config` and `FireParams` defaults.

`TUNING.md` also contains:
- the `python -m arena.bound --json` output;
- `config_digest`;
- the per-seed table for the frozen values;
- seed 42's payments per second, which is the offered load the thesis reports. It is a result, not a target.

- [ ] **Step 4: Run all `tank-arena` tests**

Run: `cd tank-arena && python -m unittest discover -s tests -t . -v`. Expected: PASS, including `test_frozen_defaults_pass_on_every_seed`.

- [ ] **Step 5: Commit, then Checkpoint 1**

```bash
git add tank-arena/arena/tune.py tank-arena/tests/test_tune.py tank-arena/TUNING.md tank-arena/arena/world.py tank-arena/arena/bots.py
git commit -m "tank-arena: tuning harness and the frozen UC2 game (damage, bot fire parameters, record in TUNING.md)"
```

Show `TUNING.md` to the owner and wait for the go. Update spec §7.3 *Game rules* with the frozen values in the same commit as the owner's go.

---

### Task 4: The game → payment bridge in the lockstep node

**Files:**
- Create: `tank-arena/arena/paybridge.py`, `tank-arena/tests/test_paybridge.py`, `tank-arena/tests/test_node_uc2.py`
- Modify: `tank-arena/arena/node.py`

**Interfaces:**
- Consumes: the payment-line format of `measure/events.py` `PaymentEvent` (`id`, `to`, `amount_wei`, `t_intended`, `t_generated`; one JSON object per line; `{"end": true}` ends the stream), copied rather than imported (`tank-arena` stays stdlib only).
- Produces:
  - `paybridge.payments_for(events: list[dict], me: int, run: str) -> list[dict]`: one dict per payment this board owes, with `to` = `"arena"` for its own `fire` and `"p<shooter>"` for a `hit` it took (amount > 0). The ID follows R3. `t_intended` and `t_generated` are not set here.
  - `class PayBridge(path: str)`: `async connect()` (raises `ConnectionError` if nothing listens), `emit(lines: list[dict], t_intended: float, t_generated: float) -> None` (sync: `writer.write` only), `async close()` (writes `{"end": true}`, then drains), and the counter `emitted`.
  - `node.py` flags: `--pay-socket PATH` (optional; without it, today's behaviour), `--run RUN_ID`, `--ticks-out PATH` (CSV `tick,t_intended,t_actual`, one row per tick, `t_actual` = wall time when the tick's step finished), `--result-out PATH` (`node-result.json`: `{"complete": bool, "aborted": str|null, "ticks": int, "t_start": float, "t_end": float, "emitted": int, "counts": {...}, "trace_sha256": str}`).
  - The stall: when one tick waits `TICK_TIMEOUT_S` (30 s), the node broadcasts `abort`, writes `aborted: "stall at tick K (no input from players [...])"`, closes the bridge (end marker) and exits 3. Any other abort exits 4. A complete match exits 0.

- [ ] **Step 1: Write the failing tests**

```python
# tank-arena/tests/test_paybridge.py
import asyncio, json, os, tempfile, time, unittest

from arena.paybridge import PayBridge, payments_for


def fire(t, p): return {"tick": t, "type": "fire", "player": p, "payload": {"amount": 1}}
def hit(t, s, v, a=5): return {"tick": t, "type": "hit", "player": s, "payload": {"victim": v, "shooter": s, "amount": a}}


class PaymentsForTest(unittest.TestCase):
    def test_own_fire_and_hits_taken(self):
        evs = [fire(7, 2), fire(7, 3), hit(7, 3, 2), hit(7, 4, 2), hit(7, 2, 5), {"tick": 7, "type": "move", "player": 2, "payload": {}}]
        got = payments_for(evs, 2, "R")
        self.assertEqual([(g["id"], g["to"], g["amount_wei"]) for g in got],
                         [("uc2/R/p2/t7/fire0", "arena", 1), ("uc2/R/p2/t7/hit0", "p3", 5), ("uc2/R/p2/t7/hit1", "p4", 5)])

    def test_zero_amount_hit_is_not_paid(self):
        self.assertEqual(payments_for([hit(3, 1, 0, a=0)], 0, "R"), [])


class BridgeTest(unittest.TestCase):
    def test_lines_parse_as_payment_events(self):
        # a Unix server collects lines; every line has exactly the PaymentEvent keys; the last is {"end": true}
        ...

    def test_bridge_never_blocks_the_tick_loop(self):
        # a server that never reads: 20 000 emits return in < 0.5 s in total (writes are buffered,
        # nothing awaits drain); close() then times out cleanly when bounded by asyncio.wait_for
        ...

    def test_connect_fails_without_listener(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ConnectionError):
                asyncio.run(PayBridge(os.path.join(d, "none.sock")).connect())
```

```python
# tank-arena/tests/test_node_uc2.py
"""Six lockstep node processes on loopback, with payment sockets."""
class NodeUC2Test(unittest.TestCase):
    def test_six_nodes_match_sim_byte_for_byte(self):
        # python -m arena.sim --players 6 --tickrate 20 --duration 10 --seed 42 --out ref.jsonl
        # six `python -m arena.node --id i --peers :p0,...,:p5 --duration 10 --seed 42 --pay-socket s_i ...`
        # with a fake listener per socket; every node's trace sha256 == ref's, every node-result complete
        ...
    def test_payments_cover_every_owed_event_exactly_once(self):
        # union of all six sockets' ids == payments_for(ref events, i) over i; no duplicates
        ...
    def test_stall_aborts_with_reason(self):
        # TICK_TIMEOUT_S patched to 2 s via env ARENA_TICK_TIMEOUT_S; kill node 5 at tick ~40;
        # the other five exit 3 within 10 s, node-result aborted starts with "stall at tick",
        # and each socket received the end marker
        ...
    def test_missing_socket_aborts_before_tick_0(self):
        # --pay-socket to a path nobody listens on: exit 4, aborted "payment client not listening", ticks 0
        ...
```

Write all the elided bodies in full, with the existing `tests/test_net.py` as the pattern for spawning node processes. Run them; they fail.

- [ ] **Step 2: Implement**

- `paybridge.py` as specified. Writes go through `asyncio.StreamWriter.write` with a high-water limit set to 64 MiB (`writer.transport.set_write_buffer_limits(high=64 << 20)`), so the buffer never pauses the writer during a match. `close()` is bounded by the caller.
- `node.py`:
  - `TICK_TIMEOUT_S = float(os.environ.get("ARENA_TICK_TIMEOUT_S", "30"))`.
  - `amain` connects the bridge **before** `mark_ready` (no listener → `aborted = "payment client not listening"`, exit 4).
  - The match start is taken right after `wait_until_ready`: `t0_wall = time.time()`, plus `start = time.monotonic()` as today.
  - Per tick after `world.step`, take `t_gen = time.time()` and call `bridge.emit(payments_for(events_now, self.id, run), t0_wall + (tick_index) * dt, t_gen)`, where `tick_index` is the loop's 0-based index, so world tick T = index + 1 (R4). Append the `ticks.csv` row.
  - The `asyncio.wait_for` timeout becomes a caught `TimeoutError`: set `aborted`, broadcast `abort`, break.
  - At the end: `await asyncio.wait_for(bridge.close(), 120)`, write `node-result.json`, exit as specified. The trace's SHA-256 is computed from the file after writing.

- [ ] **Step 3: Run all `tank-arena` tests**

Run: `cd tank-arena && python -m unittest discover -s tests -t . -v`. Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tank-arena/arena/paybridge.py tank-arena/arena/node.py tank-arena/tests/test_paybridge.py tank-arena/tests/test_node_uc2.py
git commit -m "tank-arena: lockstep node hands its payments to the local payment client (never waits), tick log, stall abort with reason"
```

---

### Task 5: Rollup payments in points

**Files:**
- Create: `measure/points.py`, `measure/tests/test_points.py`
- Modify: `measure/keys.py`, `measure/payclient.py`, `measure/events.py` (docstring: the UC2 meaning of `to` and `amount_wei`, R2)

**Interfaces:**
- Produces:
  - `keys.ARENA = 110`, `keys.UC2_PLAYER0 = 111`, `keys.TOKEN_DEPLOYER = 119`, `keys.player_index(k) -> 111 + k`.
  - `points.TRANSFER = "0xa9059cbb"`; `points.transfer_data(to_addr: str, amount: int) -> str`; `points.balance_of_data(addr) -> str`; `points.load_recipients(path) -> dict[str, str]` (logical name → checksummed address; refuses a file without `"arena"` and `"p0"`…`"p<n-1>"`); `points.token_tx(token, recipients, ev, gas, max_fee) -> dict` (`{"to": token, "value": 0, "data": transfer_data(recipients[ev.to], ev.amount_wei), "gas": gas, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": 0}`; an unknown `ev.to` raises `KeyError`, which `pay_loop` turns into a *failed* row "submit failed").
  - `points.token_gas(system_module, l2, frm, token, to_addr) -> int`: Arbitrum `int(estimate_gas({"from","to":token,"data"}) * 1.5)`; zkSync the constant `zksync.TOKEN_PAYMENT_GAS` (set in Task 6).
  - `payclient` flags `--asset {eth,points}` (default `eth`, so UC1 is unchanged), `--token ADDR`, `--recipients FILE`. With `points`, `make_tx` is `points.token_tx`, `--pay-to` is not required, and `lifecycle.json` gains `points_before_close` (the account's `balanceOf`, read with `read_retry`; a failed read is a missing fact, so the run is not complete). The reconciliation stays client-side (`observed_source: "client"`); the cross-board one is the analysis's job (Task 11).

- [ ] **Step 1: Write the failing tests**

```python
# measure/tests/test_points.py
import json, os, tempfile, unittest

from measure import keys, points
from measure.events import PaymentEvent


class PointsTest(unittest.TestCase):
    def test_transfer_calldata(self):
        d = points.transfer_data("0x00000000000000000000000000000000000000aa", 5)
        self.assertEqual(d, "0xa9059cbb" + "0" * 62 + "aa" + "0" * 63 + "5")

    def test_token_tx_maps_logical_recipient(self):
        rec = {"arena": "0x" + "11" * 20, **{f"p{k}": "0x" + f"{k + 2:02x}" * 20 for k in range(6)}}
        tx = points.token_tx("0x" + "ee" * 20, rec, PaymentEvent("i", "p3", 5, 0.0, 0.0), 90000, 7)
        self.assertEqual(tx["to"], "0x" + "ee" * 20)
        self.assertEqual(tx["value"], 0)
        self.assertTrue(tx["data"].endswith("5"))
        self.assertIn("05" * 20, tx["data"])

    def test_unknown_recipient_raises(self):
        with self.assertRaises(KeyError):
            points.token_tx("0x" + "ee" * 20, {"arena": "0x" + "11" * 20}, PaymentEvent("i", "p9", 5, 0, 0), 1, 1)

    def test_recipients_file_must_be_complete(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.json")
            json.dump({"arena": "0x" + "11" * 20, "p0": "0x" + "22" * 20}, open(p, "w"))
            self.assertEqual(set(points.load_recipients(p, n_players=1)), {"arena", "p0"})
            with self.assertRaises(ValueError):
                points.load_recipients(p, n_players=2)

    def test_uc2_account_indices(self):
        self.assertEqual((keys.ARENA, keys.player_index(0), keys.player_index(5), keys.TOKEN_DEPLOYER), (110, 111, 116, 119))
```

Extend `measure/tests/test_payclient.py` with:
- `test_points_mode_sends_token_transfers` (the fake submitter sees `data` with the recipient's address);
- `test_points_mode_records_balance_before_close`;
- `test_eth_mode_unchanged` (the existing UC1 arguments produce the same `make_tx` as before).

Run: `cd ~/Blockchain-scaling-approaches && .venv/bin/python -m unittest measure.tests.test_points measure.tests.test_payclient -v`. They fail.

- [ ] **Step 2: Implement** as specified. `load_recipients(path, n_players)`.

- [ ] **Step 3: Run the whole measure suite**

Run: `.venv/bin/python -m unittest discover -s measure/tests -t . -v`. Expected: all PASS, the UC1 tests unchanged.

- [ ] **Step 4: Commit**

```bash
git add measure/points.py measure/keys.py measure/payclient.py measure/events.py measure/tests/test_points.py measure/tests/test_payclient.py
git commit -m "measure: rollup payments in points (ERC-20 transfer per event, logical recipients, balance before Close)"
```

---

### Task 6: The token on the rollups — setup, deployment, gas

**Files:**
- Create: `contracts/points/zksync/PointsToken.zbin`, `contracts/points/zksync/PointsToken.zbin.sha256`, `deploy/zk-token/package.json`, `deploy/zk-token/package-lock.json`, `deploy/zk-token/deploy.js`, `measure/uc2_setup.py`, `measure/tests/test_uc2_setup.py`
- Modify: `measure/zksync.py` (`TOKEN_PAYMENT_GAS`), `contracts/points/README.md` (the committed zbin, "deployed per run" instead of "part of each chain's snapshot"), `.gitignore` (`deploy/zk-token/node_modules/`)

**Interfaces:**
- Produces:
  - `node deploy/zk-token/deploy.js --l2 http://H:3050 --key-file F --zbin contracts/points/zksync/PointsToken.zbin --abi perun/hub-demo/pointstoken/PointsToken.abi`. It prints one JSON line, `{"token":"0x…","tx":"0x…","gas_used":N}`, and exits non-zero on any failure.
  - `python -m measure.uc2_setup --stack arbitrum|zksync --l1 URL --l2-http URL --players 6 --points 10000 --fund-wei W --out DIR`. In order, every step untimed setup:
    1. fund 110, 111…116 and 119 on the L1 from the stack's funder (`m.funder()` or index 0);
    2. bridge 0.5 ETH to 119 on the L2 (`m.open_deposit`);
    3. deploy the token (Arbitrum: a type-2 contract creation from `PointsToken.bin` signed with `eth_account`; zkSync: `deploy.js`, with the key in a 0600 temp file);
    4. `mintBatch([p0…p5], points)` from 119;
    5. check that every player's `balanceOf` equals `points` and `arena`'s is 0.
    It writes `DIR/token.json` (`{"stack","token","deployer","deploy_tx","mint_tx","deploy_gas","mint_gas","points":10000}`) and `DIR/recipients.json` (`{"arena": addr(110), "p0": addr(111), …}`).
  - `zksync.TOKEN_PAYMENT_GAS`: 2 × the largest `eth_estimateGas` of 20 `transfer`s measured in Step 3, rounded up to 10 000.

- [ ] **Step 1: Build and commit the EraVM bytecode**

```bash
cd contracts/points
~/.local/zksync-tools/zksolc --solc ~/.local/zksync-tools/era-solc -O3 --codegen evmla --bin -o build/zksync PointsToken.sol
mkdir -p zksync && cp build/zksync/PointsToken.sol/PointsToken.zbin zksync/
sha256sum zksync/PointsToken.zbin | cut -d' ' -f1 > zksync/PointsToken.zbin.sha256
```

- [ ] **Step 2: Write the failing tests**

```python
# measure/tests/test_uc2_setup.py
class SetupTest(unittest.TestCase):
    def test_recipients_follow_the_account_indices(self):  # arena=110, p_k=111+k
        ...
    def test_zbin_hash_matches(self):                        # sha256 of the committed zbin == .sha256 file
        ...
    def test_setup_against_local_arbitrum(self):             # skip unless ARB_L1 and ARB_L2_HTTP are set:
        ...                                                  # token.json written, 6 balances == 10000, arena 0
    def test_setup_against_local_zksync(self):               # skip unless ZK_L1 and ZK_L2_HTTP are set: the same
        ...
```

- [ ] **Step 3: Implement, then measure on the workstation stacks**

Write `deploy/zk-token` (`npm init`, pin `zksync-ethers@6.21.2` and `ethers@6.17.0` as in `zksync-setup/bench-bridge`, run `npm ci`) and `uc2_setup.py`.

Start the local nitro-testnode (`nitro-testnode/test-node.bash` as in UC1's workstation tests). Run the Arbitrum test, then send 20 `transfer`s and record `gasUsed` (it is the cost figure for a shot on Arbitrum). Stop it.

Start the local zkSync chain (`zksync-setup/thesis_local`, as in the UC1 zkSync plan's workstation steps). Run the zkSync test, then estimate and send 20 `transfer`s, set `TOKEN_PAYMENT_GAS`, and record the gas. Stop it.

- [ ] **Step 4: Run the measure suite**

Run: `.venv/bin/python -m unittest discover -s measure/tests -t . -v`. Expected: PASS; the stack tests skip without their env.

- [ ] **Step 5: Commit**

```bash
git add contracts/points deploy/zk-token/package.json deploy/zk-token/package-lock.json deploy/zk-token/deploy.js .gitignore measure/uc2_setup.py measure/zksync.py measure/tests/test_uc2_setup.py
git commit -m "measure: UC2 rollup setup — points token deployed per run (zkSync via zksync-ethers), mintBatch, recipients; token transfer gas"
```

---

### Task 7: Perun — UC2 contracts, the hub from files, asymmetric funding, hub policy

**Files:**
- Create: `perun/hub-demo/uc2.go`, `perun/hub-demo/uc2_test.go`
- Modify: `perun/hub-demo/distributed.go` (`clusterConfig` fields, `runHub` from files), `perun/hub-demo/hub.go` (payment policy, R7), `perun/hub-demo/main.go` (dispatch `uc2-` modes), `perun/hub-demo/util.go` (a mint-to-list deploy variant)

**Interfaces:**
- Produces:
  - `perun-node -mode uc2-deploy -url ws://H:8545 -chainid 31337 -mint 0xP0,…,0xP5 -points 10000 -hub 0xH -hub-points 50400 -out contracts.json`. The deployer is `anvilKeys[0]`. It deploys the adjudicator, the ERC-20 asset holder and the `PointsToken`, then one `mintBatch(players, points)` and one `mint(hub, hub_points)`. It writes `{"chain_id","adjudicator","asset_holder","token","asset":"points","minted":{"0x…":10000,…},"deploy_gas","deploy_txs"}`.
  - `clusterConfig` new fields: `HubDeposit float64 json:"hub_deposit"`, `AmmoReserve float64 json:"ammo_reserve"`, `ContractsFile string json:"contracts_file"`, `HubKeyFile string json:"hub_key_file"`.
  - `validateUC2Funding()` refuses a config unless:
    - `(n−1)·VirtualStake + AmmoReserve ≤ LedgerDeposit`, and
    - `(n−1)·VirtualStake ≤ HubDeposit`, and
    - with `ContractsFile`, every client and the hub are minted at least their deposit (`HubDeposit·n` for the hub).
    It is called from `loadClusterConfigAsset` when `HubDeposit > 0`. The existing `pointsMint` check applies only without `ContractsFile`.
  - `perun-node -mode uc2-hub -config cluster.json`: `runHub` with contracts from `ContractsFile` (no deployment) and the key from `HubKeyFile`. `RequireSafePaymentUpdates`-style policy on the hub's ledger channels when `!DisablePaymentPolicy` (R7). Everything else as `-mode hub`.
  - Client side (used in Task 8): `OpenLedgerToWire(hubWire, cfg.LedgerDeposit, cfg.HubDeposit, cfg.ChallengeSecs)` when `HubDeposit > 0`.

- [ ] **Step 1: Write the failing tests**

```go
// uc2_test.go
func TestUC2FundingValidation(t *testing.T)   // 6 clients, stake 1680, ammo 336, deposit 10000, hub 8400: ok;
                                              // deposit 9000 -> error naming ledger_deposit; hub 8000 -> error naming hub_deposit
func TestUC2MintCheckFromContractsFile(t *testing.T) // minted 9999 for one client -> error naming that address
func TestUC2HubRejectsPaymentToPlayer(t *testing.T)  // the hub's update handler with policy on: an update raising the
                                                     // client's balance is rejected; one lowering it is accepted; a final
                                                     // state with unchanged balances is accepted
func TestUC2DeployWritesContractsFile(t *testing.T)  // skip without ANVIL_URL: uc2-deploy, then balanceOf of each minted address
```

Run: `cd perun/hub-demo && go test -count=1 -run 'TestUC2' -v`. It fails to compile.

- [ ] **Step 2: Implement**

`uc2Main(mode)` is dispatched from `main.go` like `uc1Main`. The UC2 flags are registered in `uc2.go`'s `init()`. The hub's policy reuses `validatePaymentUpdate` (client.go) with the hub as the receiving party.

- [ ] **Step 3: Run**

Run: `go test -count=1 ./... && go vet ./...` (Anvil tests with `ANVIL_URL=ws://127.0.0.1:8545` and a local `anvil --chain-id 31337 --block-time 1`). Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add perun/hub-demo/uc2.go perun/hub-demo/uc2_test.go perun/hub-demo/distributed.go perun/hub-demo/hub.go perun/hub-demo/main.go perun/hub-demo/util.go
git commit -m "perun: UC2 contracts and mint list, hub from contracts/key files, asymmetric ledger funding, hub payment policy"
```

---

### Task 8: Perun — the `uc2-player` sidecar

**Files:**
- Create: `perun/hub-demo/uc2_player.go`
- Modify: `perun/hub-demo/uc2_test.go`

**Interfaces:**
- Consumes: `uc1FIFO`, `uc1Replies`, `parseWei`, `unixSeconds`, `isUpdateTimeout` (uc1_customer.go); `NewClientWithIdentity`, `OpenLedgerToWire`, `registerKnownPeers`, `waitForLedgers`, `openMesh`, `snapshotVirtualChannels`, `closeDistributedClient`, `httpJSON`, the hub control endpoints (distributed.go); `payCounted` (client.go).
- Produces: `perun-node -mode uc2-player`, JSON lines on stdin/stdout as `uc1-customer` (one reply per request, in request order; stdout carries only protocol lines):

```text
-> {"op":"init","key":"<hex>","name":"p2","config":"/tmp/m-RUN/cluster.json","state_dir":"…",
    "update_timeout_s":10,"persist":true,"startup_timeout_s":120}
<- {"op":"init","ok":true,"address":"0x…","index":2}
-> {"op":"open"}
<- {"op":"open","ok":true,"t_start":…,"t_ledger_funded":…,"t_virtual_open":…,"ledger_channel_id":"0x…",
    "virtual":{"p0":"0x…",…},"l1_gas_used":N,"l1_fee_wei":"…","txs":2}
-> {"op":"pay","id":"uc2/RUN/p2/t137/hit0","to":"p3","amount_wei":"5"}
<- {"op":"pay","id":"…","ok":true,"t_submitted":…,"t_confirmed":…,"version":v,"error":""}
   (to "hub" = the ledger channel; "p<k>" = the virtual channel with player k; an unknown target ->
    "rejected: no channel to <to>"; other errors exactly as uc1-customer)
-> {"op":"state"}
<- {"op":"state","ok":true,"channels":{"hub":{"version":v,"balances_wei":["mine","peer"],"broken":false},"p0":{…},…}}
-> {"op":"quiesce"}  <- {"op":"quiesce","ok":true,"dropped":k,"versions":{"hub":v,…}}
-> {"op":"close"}    <- {"op":"close","ok":true,"t_start":…,"t_virtual_settled":…,"t_withdrawn":…,
                         "final_ledger_balances_wei":["mine","hub"],"virtual_final":[…],"collisions":n,
                         "l1_gas_used":N,"l1_fee_wei":"…","txs":n,"by_contract":{…}}
-> {"op":"exit"}
```

- Semantics:
  - `open` = ledger channel (player deposit `LedgerDeposit`, hub `HubDeposit`), register with the hub, wait for all ledgers, open the mesh with `VirtualStake` on both sides, then the `virtual-open` barrier. The timings are reported separately (ledger Open is on-chain; virtual Open is off-chain).
  - **One `uc1FIFO` per target.** Its updater is `payCounted(ch, asset, amount)` on that channel, and its balance is this player's own balance in that channel. A timeout breaks only that channel.
  - `close` = the `payments-done` barrier, snapshot, the `snapshot` barrier, `closeDistributedClient`.
  - The channel index decides who has priority in collisions (`update_policy.go`).

- [ ] **Step 1: Write the failing unit tests (fake backend, no chain)**

```go
func TestUC2PlayerRoutesByTarget(t *testing.T)        // pays to hub, p0, p3 reach three different fake channels, in order per channel
func TestUC2PlayerChannelsRunInParallel(t *testing.T) // a slow fake p3 channel does not delay hub pays (max concurrency 2 observed)
func TestUC2PlayerUnknownTargetRejected(t *testing.T)
func TestUC2PlayerTimeoutBreaksOnlyThatChannel(t *testing.T)
func TestUC2PlayerQuiesceDropsAllQueues(t *testing.T)
func TestUC2PlayerStateListsEveryChannel(t *testing.T)
```

- [ ] **Step 2: Implement** `uc2_player.go` with a `uc2Backend` interface (`Init`, `Open`, `Targets`, `Update(target, amount)`, `Balance(target)`, `State`, `Close`, `Shutdown`), so the tests above replace it.

- [ ] **Step 3: Run**

Run: `go test -count=1 -run 'TestUC2Player' -v && go test -count=1 ./... && go vet ./...`. Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add perun/hub-demo/uc2_player.go perun/hub-demo/uc2_test.go
git commit -m "perun: uc2-player sidecar — ledger to the hub and five virtual channels, one FIFO per channel, same protocol as uc1-customer"
```

---

### Task 9: Perun — six players and the hub under paced live input (the riskiest Go test)

**Files:**
- Create: `perun/hub-demo/uc2_integration_test.go`

**Interfaces:**
- Consumes: the binaries from Tasks 7–8, a local Anvil.

- [ ] **Step 1: Write the tests**

```go
func TestUC2SixPlayersPacedLive(t *testing.T)
// skip without ANVIL_URL. uc2-deploy (6 players = accounts 111..116, hub 110), uc2-hub, six uc2-player
// sidecars driven over stdin/stdout by the test. 20 s of paced input at the frozen game's rate shape:
// each player sends "hub" 1 point every 200 ms and pays two fixed opponents 5 points at ~2/s each,
// so every virtual channel carries payments in both directions. Then quiesce, state, close, exit.
// Expect: every pay ok; per player final = 10000 - out + in (from the test's own bookkeeping);
// the hub's final token balance = 6*8400 + total ammo; both ends of each virtual channel report the
// same final version; collisions > 0 is allowed and printed.
func TestUC2HeadOnHitsInOneTick(t *testing.T)
// two players send a pay to each other at the same instant, 200 times, 50 ms apart: all 400 ok,
// equal final versions, collisions counted (Review Focus 2)
func TestUC2PlayerTimeoutIsUnresolvedAndCloseStillRuns(t *testing.T)
// a peer process is SIGSTOPped for 12 s mid-run: the pays to it end "unresolved: update timeout",
// later pays to it "channel broken", pays on other channels keep succeeding; close returns an error
// naming the broken channel (the run would be incomplete), no hang beyond the operation timeout
```

- [ ] **Step 2: Run on the workstation**

Run: `anvil --chain-id 31337 --block-time 1 &` then `ANVIL_URL=ws://127.0.0.1:8545 go test -count=1 -run 'TestUC2(SixPlayers|HeadOn|PlayerTimeout)' -v -timeout 20m`. Expected: PASS. Record the per-update p50/p99 over loopback, the collisions, and the Open/Close gas per player and for the hub in the SDD ledger. Stop Anvil.

- [ ] **Step 3: Commit**

```bash
git add perun/hub-demo/uc2_integration_test.go
git commit -m "perun: UC2 integration tests — six players and the hub under paced live input, head-on hits, a stalled peer"
```

---

### Task 10: The Perun UC2 lifecycle in Python

**Files:**
- Create: `measure/perun_uc2.py`, `measure/tests/test_perun_uc2.py`, `measure/tests/fixtures/fake_uc2_sidecar.py`
- Modify: `measure/perun.py` (`pay(id_, amount_wei, to=None)`; `make_tx` keeps UC1's shape), `measure/payclient.py` (`--use-case uc1|uc2`, default `uc1`; `--perun-config PATH`; `perun` + `uc2` → `perun_uc2.main_async`)

**Interfaces:**
- Consumes: `PerunSidecar`, `PerunSubmitter`, `call`, `l1_balance` (perun.py); `pay_loop`, `accept`, `EventQueue`, `serve_socket`, `write_json` (payclient.py); the Task 8 protocol.
- Produces:
  - `perun_uc2.make_tx(ev) -> {"id": ev.id, "to": "hub" if ev.to == "arena" else ev.to, "amount_wei": ev.amount_wei}`; `PerunSubmitter` passes `to` through.
  - `perun_uc2.main_async(a) -> int`, mirroring `perun.main_async`. It writes `lifecycle.json` with `open` (the reply), `channels_before_close` (the `state` reply after quiesce), `close` (the reply), `complete`. The local reconciliation is per channel: `out_succeeded[target]` and the channel's own balance. The cross-board one is in Task 11. A broken channel or a missing fact means not complete, as in UC1.

- [ ] **Step 1: Write the failing tests** (the fake sidecar answers per the Task 8 protocol)

```python
class PerunUC2Test(unittest.TestCase):
    def test_arena_maps_to_hub_and_players_pass_through(self): ...
    def test_lifecycle_complete_with_fake_sidecar(self): ...      # init/open/pays/quiesce/state/close/exit; ledger rows all succeeded
    def test_broken_channel_means_incomplete(self): ...
    def test_sidecar_death_resolves_everything_unresolved(self): ...   # as UC1's test, through the UC2 path
    def test_uc1_path_unchanged(self): ...                         # --system perun without --use-case uc2 still calls perun.main_async
```

- [ ] **Step 2: Implement; run the measure suite**

Run: `.venv/bin/python -m unittest discover -s measure/tests -t . -v`. Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add measure/perun.py measure/perun_uc2.py measure/payclient.py measure/tests/test_perun_uc2.py measure/tests/fixtures/fake_uc2_sidecar.py
git commit -m "measure: Perun UC2 lifecycle (uc2-player sidecar, logical recipients to channels)"
```

---

### Task 11: The UC2 analysis

**Files:**
- Create: `measure/analyze_uc2.py`, `measure/tests/test_analyze_uc2.py`, `measure/tests/fixtures/uc2-run/` (a small synthetic run folder: two players, a 40-tick match)
- Modify: `measure/aggregate.py` (UC2 keys; refuses runs with `hash_ok: false` unless `--include-incomplete`)

**Interfaces:**
- Consumes (the run folder that Task 13 writes):
  - `experiment.env`; `manifest.json`;
  - `results/p<k>/pc/{ledger.csv,lifecycle.json}`;
  - `results/p<k>/game/{trace.jsonl(.gz),ticks.csv,node-result.json}`;
  - `results/reference.sha256`; `results/points-balances.json` (`{"players":{"p0":N,…},"arena":N}`); `results/token.json` or `results/contracts.json`; `recorder/<host>.csv`.
- Produces: `python -m measure.analyze_uc2 RUN_DIR` writes `RUN_DIR/summary.json`:
  - `complete`, `hash_ok`, `hash_mismatch: [boards]`;
  - `match`: `duration_s`, `stretch` (= duration / 60), `tick_lag_ms` per board and pooled `{p50,p99,max}`;
  - `offered`: `payments`, `fire`, `hit`, `per_s`;
  - `outcomes` (`succeeded`/`failed`/`unresolved` counts and shares; `late` = succeeded with `t_confirmed` after the drain deadline);
  - `latency_ms` (`generated_to_confirmed` and `submitted_to_confirmed`, p50/p99, for `fire`, `hit`, `all`) for `guests` (p1…p5) and `host` (p0) separately;
  - `goodput_per_s`; `next_shot_miss_share` (guests and host);
  - `reconciliation`: per player `game` (from the trace), `ledger` (10 000 − own succeeded + succeeded paid to it), `chain` (points-balances), `diff`, `explained`, `ok`;
  - `open`/`close` per player (time and gas); `gas` (`per_fire`, `per_hit` medians on the rollups, 0 on Perun);
  - `cpu` and `throttle` per board; `analysis_commit`.
- Rules:
  - **Next-shot deadline:** for each payer, its `fire` rows in tick order; row i misses if it is not succeeded or `t_confirmed > t_intended(row i+1)`; the last fire is excluded.
  - **Explained difference:** the sum of the unresolved rows that have a `t_submitted` (they may have executed), as UC1's `explained_wei`.
  - **Percentiles:** type 7, as `analyze_uc1._pct`.

- [ ] **Step 1: Write the failing tests**

```python
class AnalyzeUC2Test(unittest.TestCase):
    def test_summary_fields(self): ...                         # fixture -> every key above present, types right
    def test_next_shot_miss(self): ...                         # hand-built rows: confirmed before/after next shot; last shot excluded
    def test_guests_and_host_reported_apart(self): ...
    def test_tick_lag_and_stretch(self): ...                   # ticks.csv with a 0.5 s stall at tick 20 -> max lag 500 ms
    def test_reconciliation_explains_late_rollup_payments(self): ...  # chain shows a transfer whose row is unresolved
                                                                      # (submitted): diff == explained, ok_explained true
    def test_hash_mismatch_flags_run(self): ...                # one board's trace differs -> hash_ok false, aggregate refuses
    def test_game_score_from_trace(self): ...                  # replaying fire/hit amounts gives the world's final balances
```

- [ ] **Step 2: Implement; run the measure suite**

Run: `.venv/bin/python -m unittest discover -s measure/tests -t . -v`. Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add measure/analyze_uc2.py measure/aggregate.py measure/tests/test_analyze_uc2.py measure/tests/fixtures/uc2-run
git commit -m "measure: UC2 analysis — latency per payment kind, next-shot deadline misses, tick lag, stretch, cross-board reconciliation, hash check"
```

---

### Task 12: The whole match on the workstation

**Files:**
- Create: `deploy/uc2-local.sh`

**Interfaces:**
- Produces: `deploy/uc2-local.sh perun|arbitrum OUTDIR` runs a full 60 s match on the workstation. It plays the cluster's part with six loopback players and writes a run folder in the Task 11 layout:
  1. setup: `uc2-deploy` + funding for Perun; `measure.uc2_setup` for Arbitrum;
  2. six payment clients, then wait for every `ready`;
  3. six `arena.node` processes on ports 9100–9105;
  4. wait for every payment client's `complete`;
  5. points balances, the reference log via `arena.sim`, and the hash check;
  6. `measure.analyze_uc2`.
  It exits non-zero on any failure.

- [ ] **Step 1: Write the script**, reusing the exact command lines Task 13 will put into `deploy/cluster`, so the two cannot drift apart. In the commit message, list which lines are shared.

- [ ] **Step 2: Run Perun** (local Anvil): `deploy/uc2-local.sh perun /tmp/uc2-local-perun`. Expected:
  - `summary.json` `complete: true`, `hash_ok: true`;
  - every reconciliation `ok`;
  - outcomes all *succeeded*;
  - `next_shot_miss_share` and the loopback latency recorded in the SDD ledger.

- [ ] **Step 3: Run Arbitrum** (local nitro-testnode): `deploy/uc2-local.sh arbitrum /tmp/uc2-local-arbitrum`. Expected: as for Perun; record the latency and the gas per shot.

- [ ] **Step 4: Commit**

```bash
git add deploy/uc2-local.sh
git commit -m "deploy: uc2-local.sh — a full UC2 match on the workstation (Perun on Anvil, Arbitrum on the nitro-testnode)"
```

---

### Task 13: `deploy/cluster` — the UC2 run path and the experiment files → Checkpoint 2

**Files:**
- Modify: `deploy/cluster`, `measure/preflight.py`, `measure/manifest.py`, `deploy/README.md` §4
- Create: `deploy/experiments/uc2-perun.env`, `uc2-perun-smoke.env`, `uc2-arbitrum.env`, `uc2-arbitrum-smoke.env`, `uc2-zksync.env`, `uc2-zksync-smoke.env`
- Test: `measure/tests/test_preflight.py` (`IDLE_BOARDS`), `measure/tests/test_cluster.py` (UC2 env parsing)

**Interfaces:**
- Produces:
  - **`sync`** also ships `tank-arena/arena` and `tank-arena/net` to `~/measure-code/tank-arena` on every active board. `game_hash` (R8) is written as `game-hash.txt` and checked on every board in `one_run`.
  - **`IDLE_BOARDS`** (R9): after the env is sourced, `BOARDS` = `BOARDS` minus `IDLE_BOARDS`. Pre-flight gets `IDLE_BOARDS` from `experiment.env` and drops those boards from the register before `check(…, expected_boards=8 − len(idle))`. The manifest records `idle_boards`.
  - **`one_run` dispatches** on `USE_CASE`: `uc1` → today's body, unchanged; `uc2` → `one_run_uc2`.
  - **`one_run_uc2`**, every wait with a deadline, every failure `die`:
    1. leftovers; code and game hash; `perun_code_check` for Perun; run-start commit; down; cooling gate; up; stack config; clock gate; pre-flight.
    2. **Bound:** `python -m arena.bound --json` with `N_PLAYERS`, `TICKRATE`, `GAME_TICKS / TICKRATE` → `results/bound.json`. `ok` must be true and `start_points` must equal `START_POINTS`.
    3. **Setup:**
       - Perun: fund 110…116 on Anvil; `uc2-deploy` (minting 10 000 per player and `6 × hub_deposit` to the hub) → `results/contracts.json`; `cluster.json` (hub `arena` on `bath1:9000`, control `0.0.0.0:8091`; clients `p0`…`p5` at their board IPs `:9000`, control `http://192.168.178.211:8091`; `ledger_deposit` = `START_POINTS`, `hub_deposit`, `virtual_stake`, `ammo_reserve` from `bound.json`; `challenge_seconds`, `update_timeout_seconds`, `contracts_file`, `hub_key_file`) to every player and `results/`; the hub key (0600) to `bath1`; start `uc2-hub` on `bath1` and wait for its control API to answer from every player.
       - Rollups: `measure.uc2_setup` → `results/token.json`, `results/recipients.json`; `recipients.json` to every player.
    4. Recorders on every active board (and the zk sampler on zkSync).
    5. **Payment clients** on every player:
       - rollups: `payclient --system $STACK --asset points --token … --recipients … --account $((111 + k)) --deposit-wei $DEPOSIT_WEI …`;
       - Perun: `payclient --system perun --use-case uc2 --perun-config … --sidecar … --account $((111 + k)) …`.
       Then wait for every `ready` (`wait_payclients`, generalised to a `PAYERS` list).
    6. Temperatures at match start, recorded.
    7. **The game:** every player at once, `python -m arena.node --id k --peers 192.168.178.211:9100,…(in PLAYERS order) --tickrate 20 --duration 60 --seed $GAME_SEED --run $RUN_ID --pay-socket $rd/pay.sock --out $rd/game/trace.jsonl --ticks-out $rd/game/ticks.csv --result-out $rd/game/node-result.json`. Wait for every `node-result.json` (deadline: 60 s × 3 + 120 s). Any `aborted` → `die "match aborted on <board>: <reason>"`.
    8. Wait for every payment client's `complete` (as UC1, with `complete_extra_s`); for Perun also wait for the hub to settle and exit.
    9. **Points balances** (R11) → `results/points-balances.json`.
    10. **Hash check:** the SHA-256 of every board's trace against `tank-arena/reference/uc2-seed42.jsonl.sha256`. A mismatch writes `HASH_MISMATCH` (board list) into the run folder and fails the run.
    11. `finish_run`: collection into `results/p<k>/{pc,game}`; for Perun, `results/hub/` and `logs/bath1-uc2-hub.out`; manifest with `n_players`, `tickrate`, `ticks`, `seed`, `config_digest`, the reference SHA-256 and every board's trace SHA-256 (spec §6); gzip.
  - **Experiment files.** Common keys:
    - `USE_CASE=uc2`, `PLAYERS=(bath1 bath2 bath7 bath4 bath5 bath6)`, `IDLE_BOARDS="bath3"`, `WIFI_5GHZ_BOARDS="bath1 bath2"`;
    - `N_PLAYERS=6`, `TICKRATE=20`, `GAME_TICKS=1200`, `GAME_SEED=42`, `START_POINTS=10000`;
    - `MAX_QUEUE=10000`, `DRAIN_S=60`, `FUND_WEI=2000000000000000000`, `DEPOSIT_WEI=1000000000000000000`;
    - the per-system keys of the Global Constraints.
    The `-smoke` files are the same match with a comment line: not a measurement.

- [ ] **Step 1: Write the failing tests**
  - `test_preflight.py`: `test_idle_boards_are_not_required`. Facts for 7 boards and `IDLE_BOARDS="bath3"` → no failure. Without it → "bath3: unreachable".
  - `test_cluster.py`: `test_uc2_env_parses`. Every `uc2-*.env` sources in bash and defines the keys above; `PLAYERS` has 6 entries and does not contain `bath3`.

- [ ] **Step 2: Implement.** Then:
  - `bash -n deploy/cluster && shellcheck deploy/cluster`;
  - `deploy/cluster sync`, with the boards on (`bath3` stays off; `sync` loops over `BOARDS` minus the idle ones when `IDLE_BOARDS` is set in the environment: `IDLE_BOARDS=bath3 deploy/cluster sync`).

- [ ] **Step 3: Update `deploy/README.md` §4** with the UC2 commands and keys.

- [ ] **Step 4: Commit, then Checkpoint 2**

```bash
git add deploy/cluster deploy/experiments/uc2-*.env deploy/README.md measure/preflight.py measure/manifest.py measure/tests/test_preflight.py measure/tests/test_cluster.py
git commit -m "deploy: UC2 run path in cluster (game on six players, hub, token setup, hash check, points balances), idle boards, experiment files"
```

Show the owner the `deploy/cluster` diff and the experiment files; wait for the go.

---

### Task 14: The reference log and three smoke matches → Checkpoint 3

**Files:**
- Create: `tank-arena/reference/uc2-seed42.jsonl.gz`, `tank-arena/reference/uc2-seed42.jsonl.sha256`
- Modify: spec §8 step 6 (the smoke results), §9 U7/U8

- [ ] **Step 1: The reference log on `bath2`** (arm64, the synced code):

```bash
ssh bath2 'cd ~/measure-code/tank-arena && ~/venv/bin/python -m arena.sim --players 6 --tickrate 20 --duration 60 --seed 42 --out /tmp/ref.jsonl && sha256sum /tmp/ref.jsonl'
scp bath2:/tmp/ref.jsonl /tmp/ref.jsonl && sha256sum /tmp/ref.jsonl   # same hash after the copy
python -m arena.sim --players 6 --tickrate 20 --duration 60 --seed 42 --out /tmp/ref-amd64.jsonl && sha256sum /tmp/ref-amd64.jsonl  # recorded, not relied on
gzip -9 -c /tmp/ref.jsonl > tank-arena/reference/uc2-seed42.jsonl.gz
sha256sum /tmp/ref.jsonl | cut -d' ' -f1 > tank-arena/reference/uc2-seed42.jsonl.sha256
```

Record in the SDD ledger whether the amd64 hash equals the arm64 one (spec §7.3 *Fairness*: not relied on).

- [ ] **Step 2: Commit the reference, then sync**

```bash
git add tank-arena/reference
git commit -m "tank-arena: UC2 reference event log (seed 42, generated on bath2, arm64)"
IDLE_BOARDS=bath3 deploy/cluster sync
```

- [ ] **Step 3: One smoke match per system**, in tmux on the PC:

```bash
deploy/cluster run uc2-perun-smoke && deploy/cluster run uc2-arbitrum-smoke && deploy/cluster run uc2-zksync-smoke
for d in results/pi/uc2/*-smoke-rep1; do .venv/bin/python -m measure.analyze_uc2 "$d"; done
```

- [ ] **Step 4: Checkpoint 3.** Report per system: `complete`, `hash_ok`, outcomes, latency (guests/host), next-shot miss share, tick lag p50/p99/max, stretch, reconciliation, Open/Close, gas per shot, CPU and throttle of the player boards, `bath7`'s link rate. This answers U7 and U8. Wait for the owner's go before the series.

- [ ] **Step 5: Record and commit** (after the go):

```bash
git add docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md results/pi/uc2/*-smoke-rep1
git commit -m "results: UC2 smoke matches on the cluster (Perun, Arbitrum, zkSync); spec U7/U8"
```

---

### Task 15: The series, aggregation and the results

**Files:**
- Modify: spec §8 step 6 (results), §9; `perun/hub-demo/DISTRIBUTED.md` (*UC2: hub and virtual channels on the cluster*); `tank-arena/README.md` (the UC2 weapon, the bridge, `arena.bound`, `arena.tune`)

- [ ] **Step 1: The nine runs**, in tmux, with no commits to `v2` while they run:

```bash
deploy/cluster run uc2-perun --reps 3 && deploy/cluster run uc2-arbitrum --reps 3 && deploy/cluster run uc2-zksync --reps 3
```

A failed repetition stops its series (`RUN_FAILED`). Record why, rerun only that system after the cause is clear, and never mix in a run from another commit.

- [ ] **Step 2: Analyse and aggregate**

```bash
for d in results/pi/uc2/*-uc2-{perun,arbitrum,zksync}-rep*; do .venv/bin/python -m measure.analyze_uc2 "$d"; done
for s in perun arbitrum zksync; do .venv/bin/python -m measure.aggregate results/pi/uc2/*-uc2-$s-rep* > results/pi/uc2/aggregate-uc2-$s.txt; done
```

- [ ] **Step 3: Write the results** into spec §8 step 6:
  - one table, the three systems side by side: latency per kind, next-shot miss share, goodput, outcomes, tick lag, stretch, Open/Close time and gas, gas per shot, CPU per board class;
  - the reconciliation and hash status of every run;
  - what is measured, what is inferred, and n = 3.
  Update the docs listed under *Files*.

- [ ] **Step 4: Commit**

```bash
git add results/pi/uc2 docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md perun/hub-demo/DISTRIBUTED.md tank-arena/README.md
git commit -m "results: UC2 on Perun, Arbitrum and zkSync (3 runs each, seed 42) — spec §8 step 6"
```

Report to the owner with the list of rulings made on the way. Do not push.

---

## Self-review (done while writing)

- **Spec coverage, §7.3 *New code*:**
  - weapon and bots → Tasks 1–3; game → payment bridge → Task 4; payment ledger (reused) → Tasks 5, 10;
  - Perun live mode → Tasks 7–10; rollup sender → Tasks 5–6; points token → Task 6 (rollups) and Task 7 (Perun);
  - paced live Perun test → Task 9; running and analysis → Tasks 11–13.
- **Spec coverage, the other §7.3 rules:**
  - *Run parameters*: the Global Constraints and Task 13's env files;
  - *Fairness* (reference log, hash) → Tasks 4, 13, 14; *Perun channel capacity* (the bound) → Tasks 1, 7, 13;
  - *Reported per system* → Task 11; U7/U8 → Task 14; §6 manifest fields → Task 13.
- **A deliberate deviation, noted for the owner:** the implemented *S* is expected to be 336, not the spec's 330. Task 1 Step 5 corrects the spec, and the bound still holds (26 × 336 = 8 736 ≤ 10 000).
- **Names used across tasks:**
  - `payments_for`, `PayBridge`; `points.token_tx`, `load_recipients(path, n_players)`;
  - `arena.bound.amounts` keys (`S`, `virtual_stake`, `hub_deposit`, `ammo`, `start_points`, `ok`);
  - `clusterConfig.HubDeposit`/`AmmoReserve`/`ContractsFile`/`HubKeyFile`; the protocol ops `init`/`open`/`pay`/`state`/`quiesce`/`close`/`exit`;
  - accounts 110/111–116/119.
  Each is defined once, in the task that produces it.
