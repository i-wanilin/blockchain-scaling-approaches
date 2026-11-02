"""The Perun side of the payment client in UC2: drives the Go sidecar
`perun-node -mode uc2-player` (perun/hub-demo/uc2_player.go) for one player of
the tank game. It reuses UC1's PerunSidecar/PerunSubmitter/call (measure/perun.py)
and payclient's pay_loop/accept/EventQueue/serve_socket unchanged.

Protocol (Task 8): init {key, name, config, state_dir, ...} -> open (ledger to
the hub plus a virtual channel to every other player) -> pay {id, to, amount_wei}
-> quiesce -> state (per channel) -> close (barriers with all players) -> exit.

The game's logical recipients map to channels: "arena" -> "hub" (the ledger
channel), "p<k>" -> the virtual channel with player k.

Facts the lifecycle respects: a failed open is fatal (never retried); `state`
balances are FREE funds, so the per-channel numbers recorded here are the
channels' own [mine, peer] balances; a player with a broken channel refuses
close, which makes the others' close wait out the Go operation timeout (hence
CLOSE_TIMEOUT_S); `collisions` (in the close reply) is per process; a pay
reply's version may be the peer's later version on a bidirectional channel,
so it is stored in the ledger as `block` but never used for ordering.

lifecycle.json keys: system, use_case, account, player, complete, max_in_flight,
max_queue, drain_s, duplicates, late, init, open, open_error, ready_at,
quiesce_sent_at, ended_at, quiesce, quiesce_error, channels_before_close,
state_error, reconciliation (per channel: out_succeeded_wei/_count, free_balance_wei,
version, broken), broken_channels, close, close_error, error, sidecar_rc.
free_balance_wei is this player's FREE funds in that channel: for "hub" (the
ledger channel) the stakes locked in open virtual channels are excluded.
The cross-board reconciliation (my out vs the peer's in) is Task 11's.
"""
from __future__ import annotations

from measure import perun
from measure.perun import OPEN_TIMEOUT_S, STARTUP_TIMEOUT_S


PLAYERS = 6                             # the game's six players (p0..p5)
ACCOUNT_BASE = 111                       # --account 111 + k is player k (plan: deploy, UC2 runs)
INIT_TIMEOUT_S = STARTUP_TIMEOUT_S + 10
UC2_OPEN_TIMEOUT_S = OPEN_TIMEOUT_S * 2  # ledger Open on L1 plus the virtual Open, each behind a barrier
# Close = payments-done barrier + virtual settle + virtual-settled barrier + ledger settle + L1 withdraw,
# and the barriers wait for the slowest of six players; each barrier alone may take Go's operation
# timeout (180 s default) when a peer is broken. 900 s covers one such wait plus the settle with margin;
# a request timeout kills the sidecar, so it must never fire before Go's own timeout does.
CLOSE_TIMEOUT_S = 900


def make_tx(ev) -> dict:
    """The arena is the hub's ledger channel; players are named as in the sidecar."""
    return {"id": ev.id, "to": "hub" if ev.to == "arena" else ev.to, "amount_wei": ev.amount_wei}


def target_of(to: str) -> str:
    return "hub" if to == "arena" else to


def sidecar_argv(a) -> list[str]:
    return [a.sidecar, "-mode", "uc2-player"]


def player_name(a) -> str:
    return f"p{a.account - ACCOUNT_BASE}"


def reconcile_channels(rows, channels: dict) -> dict:
    """Per channel: what this player's succeeded pays added up to, and the
    channel's own free balance after quiesce (the ledger's excludes the stakes
    locked in virtual channels; the cross-board check is Task 11's)."""
    out = {t: {"out_succeeded_wei": 0, "out_succeeded_count": 0,
               "free_balance_wei": int(c["balances_wei"][0]), "version": c.get("version"),
               "broken": bool(c.get("broken"))} for t, c in channels.items()}
    for r in rows:
        if r["outcome"] != "succeeded":
            continue
        e = out.get(target_of(r["to"]))
        if e is None:
            raise KeyError(f"succeeded pay to {r['to']!r}: no such channel")
        e["out_succeeded_wei"] += int(r["amount_wei"])
        e["out_succeeded_count"] += 1
    return out


class UC2Lifecycle(perun.Lifecycle):
    """perun.run_lifecycle's UC2 hooks: the uc2-player sidecar, no L1 pre-check
    (the cluster config carries the chain and the deposits; Go checks funding
    in open), per-channel state and reconciliation."""
    close_timeout_s = None

    def make_tx(self, ev) -> dict:
        return make_tx(ev)

    def initial_life(self) -> dict:
        life = super().initial_life()
        life.update(use_case="uc2", player=player_name(self.a))
        return life

    def argv(self):
        return sidecar_argv(self.a)

    def init_request(self, acct, state_dir):
        a = self.a
        return {"op": "init", "key": acct.key.hex(), "name": player_name(a), "config": a.perun_config,
                "state_dir": state_dir, "update_timeout_s": a.update_timeout, "persist": bool(a.persist),
                "startup_timeout_s": STARTUP_TIMEOUT_S}

    def init_timeout(self):
        return INIT_TIMEOUT_S

    def record_init(self, life, rep):
        life["init"] = {"address": rep.get("address"), "index": rep.get("index")}

    async def pre_open_error(self, acct):
        return None

    def open_request(self):
        return {"op": "open"}

    def open_timeout(self):
        return UC2_OPEN_TIMEOUT_S

    def quiesce_facts(self, rep):
        return {"dropped": rep.get("dropped"), "versions": rep.get("versions")}

    def record_state(self, life, rep):
        ch = rep.get("channels")
        life["channels_before_close"] = ch
        me = player_name(self.a)
        expected = {"hub"} | {f"p{j}" for j in range(PLAYERS) if f"p{j}" != me}
        if not isinstance(ch, dict) or set(ch) != expected:   # fail closed: a missing channel is a missing fact
            life["state_error"] = (f"channels not exactly {sorted(expected)}: "
                                   f"{sorted(ch) if isinstance(ch, dict) else type(ch).__name__}")
            life["channels_incomplete"] = True

    def reconcile(self, life, ledger, facts_missing):
        if life.get("channels_incomplete"):
            facts_missing.append("channels")
        before = life.get("channels_before_close")
        if before is None:
            return
        try:                                              # named first: also when balances are unusable
            life["broken_channels"] = sorted(t for t, c in before.items() if c.get("broken"))
        except (TypeError, AttributeError) as e:
            life["state_error"] = f"channel state unusable: {type(e).__name__}: {e}"
            facts_missing.append("reconciliation")
            return
        try:
            life["reconciliation"] = reconcile_channels(ledger.rows(), before)
        except (TypeError, ValueError, IndexError, KeyError, AttributeError) as e:
            life["state_error"] = f"channel state unusable: {type(e).__name__}: {e}"
            facts_missing.append("reconciliation")

    def close_timeout(self):
        return CLOSE_TIMEOUT_S

    def broken_error(self, life):
        if life.get("broken_channels"):
            return "channel(s) broken before Close: " + ", ".join(life["broken_channels"])
        return None


async def main_async(a) -> int:
    """The Perun UC2 lifecycle for one player (perun.run_lifecycle)."""
    return await perun.run_lifecycle(a, UC2Lifecycle(a))
