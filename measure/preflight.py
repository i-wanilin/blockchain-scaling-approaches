# measure/preflight.py
"""Pre-flight checks before every run (cluster spec §5): abort on any failure.

Power: bath0 and bath1 must be clean (the soft-temperature bit 0x80000
excepted); the Pi 3Bs are undervolted under load on every supply, so their
flags are recorded in the manifest, not a reason to stop (deploy/FLEET.md).

A fact that is missing or empty is a failure, never a pass: the run must not
start on a board whose facts could not be read properly.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import sys

BSSID_24GHZ = "02:00:00:00:00:01"
BSSID_5GHZ = "02:00:00:00:00:02"   # the router's 5 GHz radio (WIFI_5GHZ_BOARDS, 2026-10-04 wifi-5ghz test)
MIN_5GHZ_FREQ_MHZ = 4900
SOFT_TEMP = 0x80000
# Q5 a: on a board the owner opted in (THERMAL_STICKY_OK), the two other since-boot temperature
# bits are also allowed, but only alongside SOFT_TEMP and only when no undervoltage bit (now or
# since boot) and no currently-active cap bit is set.
THERMAL_STICKY_EXTRA = 0x20000 | 0x40000
THERMAL_STICKY_BAD = 0x1 | 0x10000 | 0x2 | 0x4 | 0x8
TIME_SERVER_IP = "192.168.178.211"  # bath1; every other board syncs from it
TIME_SERVER_HOST = "bath1"
ZK_PROJECT = "thesis_local"  # deploy/cluster's ZK_PROJECT (F3)
ZK_BIN = "/home/i_wanilin/zksync/bin/zksync_server"  # deploy/cluster's ZK_BIN: the measured binary (F1)
REQUIRED = ("wifi_mac", "serial", "kernel", "bssid", "freq_mhz", "power_save", "throttled",
            "chrony_source", "netem", "disk_free_gb", "docker_up", "anvil_up")


def fleet_register(path) -> dict:
    reg = {}
    with open(path) as fh:
        lines = fh.readlines()
    for line in lines:
        m = re.match(r"\|\s*\d+\s*\|\s*`(bath\d)`\s*\|[^|]*\|\s*`([0-9a-f:]+)`\s*\|\s*`([0-9a-f]+)`", line)
        if m:
            reg[m.group(1)] = {"wifi_mac": m.group(2), "serial": m.group(3).lstrip("0")}
    return reg


def _missing(f, key):
    return f.get(key) in (None, "")


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def load_facts(d):
    """Read <host>.json for every file in d -> (facts, errors). Unreadable files go to errors."""
    facts, errors = {}, []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        host = os.path.basename(p)[:-5]
        try:
            with open(p) as fh:
                v = json.load(fh)
        except (OSError, ValueError) as e:
            errors.append(f"{host}: facts unreadable ({e})")
            continue
        if not isinstance(v, dict):
            errors.append(f"{host}: facts unreadable (not a JSON object)")
            continue
        facts[host] = v
    return facts, errors


def zksync_binaries(paths):
    """The zksync_server* executables among board-facts' zksync_server_paths (argv[0] of every
    process whose argv[0] contains "zksync_server"). Postgres backends rewrite their argv[0] to
    e.g. "postgres: postgres zksync_server_thesis_chain ..." -- not a binary, skipped."""
    out = []
    for p in paths or []:
        exe = str(p).split()[0] if str(p).split() else ""
        if os.path.basename(exe).startswith("zksync_server"):
            out.append(exe)
    return out


def check(facts, register, *, bssid, clean_power, max_offset_ms, min_disk_gb, stack_board, expect_docker,
          time_server_ip=TIME_SERVER_IP, expected_boards=8, expect_stack=None, thermal_sticky_ok=frozenset(),
          wifi_5ghz_boards=frozenset()):
    bad = []
    if len(register) != expected_boards:
        bad.append(f"register has {len(register)} boards, expected {expected_boards}")
    for host in sorted(set(facts) - set(register)):
        bad.append(f"{host}: not in the register")
    kernels = {f["kernel"] for h, f in facts.items()
               if h in register and isinstance(f, dict) and not _missing(f, "kernel")}
    if len(kernels) > 1:
        bad.append(f"kernels differ: {sorted(kernels)}")
    for host, reg in sorted(register.items()):
        f = facts.get(host)
        if f is None:
            bad.append(f"{host}: unreachable")
            continue
        if not isinstance(f, dict):
            bad.append(f"{host}: facts unreadable (not an object)")
            continue
        gaps = [k for k in REQUIRED if _missing(f, k)]
        if host != TIME_SERVER_HOST and _missing(f, "chrony_offset_ms"):
            gaps.append("chrony_offset_ms")
        for k in gaps:
            bad.append(f"{host}: fact {k} missing or empty")
        ok = lambda k: k not in gaps
        if ok("wifi_mac") and ok("serial") and (f["wifi_mac"] != reg["wifi_mac"] or f["serial"] != reg["serial"]):
            bad.append(f"{host}: card not in its own board ({f['wifi_mac']}, {f['serial']})")
        if ok("bssid"):
            freq = f.get("freq_mhz")
            if host in wifi_5ghz_boards:
                if f["bssid"] != BSSID_5GHZ or not _num(freq) or freq <= MIN_5GHZ_FREQ_MHZ:
                    bad.append(f"{host}: on {f['bssid']} ({freq} MHz), not {BSSID_5GHZ} "
                               f"(freq_mhz > {MIN_5GHZ_FREQ_MHZ})")
            elif f["bssid"] != bssid:
                bad.append(f"{host}: on {f['bssid']} ({freq} MHz), not {bssid}")
        if ok("power_save") and f["power_save"] != "off":
            bad.append(f"{host}: Wi-Fi power save {f['power_save']}")
        if ok("throttled"):
            try:
                flags = int(f["throttled"], 16)
            except (TypeError, ValueError):
                bad.append(f"{host}: throttled not hex: {f['throttled']!r}")
            else:
                allowed = SOFT_TEMP
                if host in thermal_sticky_ok and not (flags & THERMAL_STICKY_BAD):
                    allowed |= THERMAL_STICKY_EXTRA
                if host in clean_power and flags & ~allowed:
                    bad.append(f"{host}: throttle flags {f['throttled']} (reboot it)")
        if ok("chrony_source") and host != TIME_SERVER_HOST and f["chrony_source"] != time_server_ip:
            bad.append(f"{host}: chrony_source {f['chrony_source']}, not {time_server_ip}")
        if host != TIME_SERVER_HOST and ok("chrony_offset_ms"):
            off = f["chrony_offset_ms"]
            if not _num(off) or abs(off) > max_offset_ms:
                bad.append(f"{host}: clock offset {off} ms")
        if ok("netem") and f["netem"]:
            bad.append(f"{host}: netem delay present")
        if ok("disk_free_gb"):
            gb = f["disk_free_gb"]
            if not _num(gb) or gb < min_disk_gb:
                bad.append(f"{host}: {gb} GB free")
        if ok("docker_up"):
            try:
                up = int(f["docker_up"])
            except (TypeError, ValueError):
                bad.append(f"{host}: docker_up not a number: {f['docker_up']!r}")
                continue
            if host == stack_board:
                # Perun uses no docker at all on bath0 (it runs Anvil directly): any container up
                # there is unexpected, unlike arbitrum/zkSync where docker_up>0 is the stack itself.
                if expect_stack == "perun":
                    if up:
                        bad.append(f"{host}: unexpected containers running (perun uses no docker)")
                elif expect_docker and up == 0:
                    bad.append(f"{host}: stack not up")
            if host != stack_board and up:
                bad.append(f"{host}: unexpected containers running")
        if ok("anvil_up"):
            try:
                anvil = int(f["anvil_up"])
            except (TypeError, ValueError):
                bad.append(f"{host}: anvil_up not a number: {f['anvil_up']!r}")
                continue
            if host == stack_board:
                if expect_stack == "perun" and anvil == 0:
                    bad.append(f"{host}: Anvil not running")
                elif expect_stack in ("arbitrum", "zksync") and anvil:
                    bad.append(f"{host}: Anvil running, {expect_stack} expected")
            elif anvil:
                bad.append(f"{host}: Anvil running")
        if host == stack_board and expect_stack is not None:
            try:
                zsu = int(f.get("zksync_server_up") or 0)
            except (TypeError, ValueError):
                zsu = 0
            containers = f.get("containers") or []
            if expect_stack == "zksync":
                if zsu == 0:
                    bad.append(f"{host}: zkSync server not running")
                if any(c.startswith("arbitrum-") for c in containers):
                    bad.append(f"{host}: an arbitrum-* container is up, zkSync expected")
                proj_up = sum(1 for c in containers if c.startswith(f"{ZK_PROJECT}-"))
                if proj_up < 2:
                    bad.append(f"{host}: only {proj_up} {ZK_PROJECT}-* containers up, expected at least 2")
            elif expect_stack == "arbitrum" and zsu > 0:
                bad.append(f"{host}: zkSync server running, arbitrum expected")
            elif expect_stack == "perun":
                if zsu > 0:
                    bad.append(f"{host}: zkSync server running, perun expected")
                if any(c.startswith("arbitrum-") for c in containers):
                    bad.append(f"{host}: an arbitrum-* container is up, perun expected")
                if any(c.startswith(f"{ZK_PROJECT}-") for c in containers):
                    bad.append(f"{host}: a {ZK_PROJECT}-* container is up, perun expected")
            # final review Minor 2: a stray build (the 2026-09-30 zksync_server.a72) is not counted by
            # zksync_server_up; zkSync allows only the measured binary, Arbitrum none at all
            for exe in zksync_binaries(f.get("zksync_server_paths")):
                if expect_stack == "arbitrum":
                    bad.append(f"{host}: {exe} running, arbitrum expected")
                elif exe != ZK_BIN:
                    bad.append(f"{host}: {exe} running, only {ZK_BIN} may run")
    return bad


def _unquote(v):
    """Strip one matching pair of surrounding quotes (bash needs them around a value with
    spaces, e.g. WIFI_5GHZ_BOARDS="bath0 bath1 bath2"; the shell strips them, this parser must too)."""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_env(text):
    """{key: value} from an experiment.env-style "KEY=VALUE" text, "#" comments skipped,
    surrounding quotes stripped from the value as bash itself would."""
    return {k.strip(): _unquote(v) for k, v in
            (l.strip().split("=", 1) for l in (text or "").splitlines()
             if "=" in l and not l.strip().startswith("#"))}


def read_env(path):
    """parse_env of path, or {} if it cannot be read (no env found keeps today's behaviour)."""
    try:
        with open(path) as fh:
            return parse_env(fh.read())
    except OSError:
        return {}


def run_preflight(d):
    """The full pre-flight check for facts directory d (today's rule, or stack-aware when
    d/../experiment.env sets STACK / THERMAL_STICKY_OK, per Q5 a; IDLE_BOARDS removes boards, R9):
    the list of failures, or []
    if the run may start. Split out of main() so the env wiring is unit-testable without a
    process exit."""
    facts, errors = load_facts(d)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        register = fleet_register(os.path.join(repo, "deploy/FLEET.md"))
    except OSError as e:
        register = {}
        errors.append(f"cannot read the board register: {e}")
    # the facts directory is $out/facts-start; the run's experiment.env is one level up
    env_path = os.path.join(d, "..", "experiment.env")
    env = read_env(env_path)
    # IDLE_BOARDS (R9, space-separated board names, UC2's bath3): not part of the run, so dropped from
    # the register (expected_boards = 8 - idle) and from the facts. A run's experiment.env decides; with
    # none (`deploy/cluster preflight`) the IDLE_BOARDS environment variable does, as for the other commands.
    idle_txt = env.get("IDLE_BOARDS", "") if os.path.exists(env_path) else os.environ.get("IDLE_BOARDS", "")
    idle = frozenset(idle_txt.split())
    register = {h: r for h, r in register.items() if h not in idle}
    facts = {h: f for h, f in facts.items() if h not in idle}
    errors = [e for e in errors if e.split(":")[0] not in idle]
    unreadable = {e.split(":")[0] for e in errors}
    expect_stack = env.get("STACK") or None
    thermal_sticky_ok = frozenset(env["THERMAL_STICKY_OK"].split(",")) if env.get("THERMAL_STICKY_OK") else frozenset()
    # WIFI_5GHZ_BOARDS (space-separated board names): boards pinned to the router's 5 GHz radio
    # for a test (deploy/pin-5ghz.sh); unset keeps today's all-boards-on-2.4-GHz rule.
    wifi_5ghz_boards = frozenset(env["WIFI_5GHZ_BOARDS"].split()) if env.get("WIFI_5GHZ_BOARDS") else frozenset()
    return errors + [b for b in check(facts, register, bssid=BSSID_24GHZ,
                                      clean_power={"bath0", "bath1"}, max_offset_ms=5.0, min_disk_gb=5.0,
                                      stack_board="bath0", expect_docker=True, time_server_ip=TIME_SERVER_IP,
                                      expect_stack=expect_stack, thermal_sticky_ok=thermal_sticky_ok,
                                      wifi_5ghz_boards=wifi_5ghz_boards,
                                      expected_boards=8 - len(idle))
                     if b.split(":")[0] not in unreadable or not b.endswith(": unreachable")]


def main():
    bad = run_preflight(sys.argv[1])
    for b in bad:
        print("PRE-FLIGHT FAIL:", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
