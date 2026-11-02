"""manifest.json for a run folder (cluster spec §6): code version, experiment
parameters, per-board facts (start and end), idle/start temperatures, code
hash, failure marker, container images and a recorder summary per board.
Since 2026-09-30 also, when deploy/cluster wrote them at run start: the commit
the run started from (run-start-commit.txt, preferred over HEAD at manifest
time), the GESL recording replayed (gesl.json) and the SHA-256 of bath0's
compose file, the snapshot's SHA256SUMS and the nitro config
(stack-config.sha256). Older run folders lack these files; the fields are then
None. A UC2 run (USE_CASE=uc2) also records idle_boards and "game": the match
parameters, config digest, game code hash, the reference log's SHA-256 and every
player board's trace SHA-256 (uc2_game).

deploy/cluster calls this after collecting and before `down`, so the images
are still known to bath0. Everything that needs the network is best-effort:
the manifest is written even if ssh fails, with an error string instead."""
from __future__ import annotations

import csv
import glob
import gzip
import hashlib
import json
import os
import subprocess
import sys

from measure.fileio import open_text

ZK_PROJECT = "thesis_local"                                  # deploy/cluster's ZK_PROJECT (F3)
ZK_BIN = "/home/i_wanilin/zksync/bin/zksync_server"           # deploy/cluster's ZK_BIN (F1)
ANVIL_BIN = "/home/i_wanilin/bin/anvil"                       # bath0's Anvil (Task 2; Perun's base chain)


def sh(*cmd, cwd=None, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _throttled(v):
    try:
        return int(v, 16)
    except (TypeError, ValueError):
        return 0


def stream_window(run_dir):
    """(first, last) t_sent of results/rows_sent.csv, or None if unavailable."""
    try:
        with open_text(os.path.join(run_dir, "results", "rows_sent.csv")) as f:
            ts = [float(r["t_sent"]) for r in csv.DictReader(f) if r.get("t_sent")]
        return (min(ts), max(ts)) if ts else None
    except (OSError, ValueError, KeyError):
        return None


def recorder_summary(path, window=None):
    """Summary of one recorder CSV. Truncated or malformed lines are skipped and
    counted. With `window` (t0, t1) only samples inside it count ("scope":
    "stream window"); otherwise the whole file is used ("scope": "whole file")."""
    good, skipped = [], 0
    with open_text(path) as f:
        recs = list(csv.DictReader(f))
    for r in recs:
        try:
            good.append((float(r["t"]), float(r["temp_c"]), _throttled(r["throttled"]), int(r["arm_mhz"]),
                         r["bssid"] or "", _float(r.get("cpu_busy_pct"))))
        except (TypeError, ValueError, KeyError, AttributeError):
            skipped += 1
    scope = "whole file"
    if window:
        good = [g for g in good if window[0] <= g[0] <= window[1]]
        scope = "stream window"
    if not good:
        return {"samples": 0, "skipped_lines": skipped, "scope": scope}
    cpu = [g[5] for g in good if g[5] is not None]      # recorder files before 2026-09-30 have no CPU column
    return {"samples": len(good), "skipped_lines": skipped, "scope": scope,
            "undervolted_share": sum(g[2] & 0x1 != 0 for g in good) / len(good),
            "mean_arm_mhz": sum(g[3] for g in good) / len(good),
            "max_temp_c": max(g[1] for g in good),
            "bssids": sorted({g[4] for g in good}),
            "mean_cpu_busy_pct": sum(cpu) / len(cpu) if cpu else None,
            "max_cpu_busy_pct": max(cpu) if cpu else None}


def _float(v):
    """A float, or None for an empty or missing field (one failed sample must not drop the line)."""
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def git_dirty(repo):
    """True if source changed (untracked source files count, results/ does not).
    None if git failed; consumers treat None as dirty."""
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--", ".", ":!results"], capture_output=True,
                           text=True, cwd=repo, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(r.stdout.strip()) if r.returncode == 0 else None


def perun_versions(d):
    """Perun's version record from bath0, best-effort (env STACK=perun): Anvil's own --version
    (bath0 runs no docker for Perun, cluster spec Task 5), the perun-node binary's SHA-256 as
    recorded in the run folder's perun-node-sha256.txt (sync's code check), and the deployed
    contract addresses the run already copied to results/contracts.json."""
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "bath0"]
    try:
        anvil = sh(*ssh, f"{ANVIL_BIN} --version", timeout=25)
        out = {
            "anvil": anvil or None,
            "perun_node_sha256": _read(os.path.join(d, "perun-node-sha256.txt")),
            "contracts": _json(os.path.join(d, "results", "contracts.json")),
        }
        if not anvil:
            out["error"] = "ssh bath0 gave no anvil version"
        return out
    except Exception as e:                           # never fail the manifest for this
        return {"error": f"{type(e).__name__}: {e}"}


def images(d, env):
    """Image/version record from bath0; best-effort. Arbitrum (default, unchanged): compose
    images, falling back to the configured image names and their local image IDs if the stack is
    already down. zkSync (env STACK=zksync): ZK_PROJECT's compose images, the server binary's
    SHA-256, and the already-collected results/config.txt and config.sha256 from the run folder."""
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "bath0"]
    try:
        if (env or {}).get("STACK") == "zksync":
            compose = sh(*ssh, f"docker compose -p {ZK_PROJECT} images --format json", timeout=25)
            bin_sha = sh(*ssh, f"sha256sum {ZK_BIN}", timeout=25)
            out = {
                "compose_images": compose or None,
                "zk_bin_sha256": bin_sha.split()[0] if bin_sha else None,
                "config_txt": _read(os.path.join(d, "results", "config.txt")),
                "config_sha256": _read(os.path.join(d, "results", "config.sha256")),
            }
            if not compose and not bin_sha:
                out["error"] = "ssh bath0 gave no zkSync image/version info"
            return out
        out = sh(*ssh, "cd ~/arbitrum && docker compose images --format json", timeout=25)
        if out:
            return out
        names = sh(*ssh, "cd ~/arbitrum && docker compose config --images", timeout=25).split()
        if not names:
            return {"error": "ssh bath0 gave no images (unreachable, or no compose config)"}
        ids = {}
        for n in names:
            ids[n] = sh(*ssh, f"docker image inspect --format '{{{{.Id}}}}' {n}", timeout=25) or None
        return {"configured_images": ids, "note": "compose images was empty; IDs from docker image inspect"}
    except Exception as e:                       # never fail the manifest for this
        return {"error": f"{type(e).__name__}: {e}"}


def _read(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None


def temps(path):
    out = {}
    for line in (_read(path) or "").splitlines():
        p = line.split()
        if len(p) == 3 and not line.startswith("#"):
            try:
                out[p[0]] = {"idle_c": float(p[1]), "start_c": float(p[2])}
            except ValueError:
                pass
    return out


def _facts(d, sub):
    return {os.path.basename(p)[:-5]: json.load(open(p)) for p in sorted(glob.glob(os.path.join(d, sub, "*.json")))}


def _json(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def stack_config(path):
    """{file: sha256} from `sha256sum` output lines; None if the file is missing or empty."""
    out = {}
    for line in (_read(path) or "").splitlines():
        p = line.split(None, 1)
        if len(p) == 2 and len(p[0]) == 64:
            out[p[1].strip()] = p[0]
    return out or None


def run_start_commit(path):
    """(commit, dirty) from run-start-commit.txt ("<sha>" or "<sha> dirty"); (None, None) if absent."""
    p = (_read(path) or "").split()
    return (p[0], "dirty" in p[1:]) if p else (None, None)


def _unquote(v):
    v = (v or "").strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v


def _sha256_file(path):
    """SHA-256 of path's bytes (decompressed if only path.gz exists); None if neither exists."""
    h = hashlib.sha256()
    try:
        f = open(path, "rb") if os.path.exists(path) else gzip.open(path + ".gz", "rb")
        with f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def uc2_game(d, env):
    """UC2 (spec §6): the match parameters, the frozen game's config digest and code hash, the reference
    log's SHA-256 the run was checked against (results/reference.sha256), and every player board's trace
    SHA-256 -- from the collected trace (results/p<k>/game/trace.jsonl) and as the board reported it in the
    run's hash check (results/trace-sha256.txt). PLAYERS maps p<k> to its board."""
    players = _unquote(env.get("PLAYERS", "")).strip("()").split()

    def _int(k):
        try:
            return int(env.get(k, ""))
        except ValueError:
            return None
    ref = (_read(os.path.join(d, "results", "reference.sha256")) or "").split()
    on_board = {}
    for line in (_read(os.path.join(d, "results", "trace-sha256.txt")) or "").splitlines():
        p = line.split()
        if len(p) == 2:
            on_board[p[0]] = p[1]
    return {
        "n_players": _int("N_PLAYERS"), "tickrate": _int("TICKRATE"), "ticks": _int("GAME_TICKS"),
        "seed": _int("GAME_SEED"), "start_points": _int("START_POINTS"),
        "players": players,
        "config_digest": _read(os.path.join(d, "config-digest.txt")),
        "game_hash": _read(os.path.join(d, "game-hash.txt")),
        "reference_sha256": ref[0] if ref else None,
        "trace_sha256": {b: _sha256_file(os.path.join(d, "results", f"p{k}", "game", "trace.jsonl"))
                         for k, b in enumerate(players)},
        "trace_sha256_on_board": on_board or None,
        "hash_mismatch": _read(os.path.join(d, "HASH_MISMATCH")),
    }


def build(d, repo=None):
    repo = repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    envtxt = _read(os.path.join(d, "experiment.env")) or ""
    env = dict(l.strip().split("=", 1) for l in envtxt.splitlines() if "=" in l and not l.startswith("#"))
    head = sh("git", "rev-parse", "HEAD", cwd=repo)
    dirty = git_dirty(repo)
    start, start_dirty = run_start_commit(os.path.join(d, "run-start-commit.txt"))
    out = {
        "stack": env.get("STACK"),
        "git_commit": start or head,
        "git_commit_source": "run-start-commit.txt" if start else "HEAD at manifest time",
        "git_commit_at_manifest": head,
        "git_dirty": True if start_dirty else dirty,
        "gesl": _json(os.path.join(d, "gesl.json")),
        "stack_config_sha256": stack_config(os.path.join(d, "stack-config.sha256")),
        "code_hash": _read(os.path.join(d, "code-hash.txt")),
        "run_failed": _read(os.path.join(d, "RUN_FAILED")),
        "experiment": env,
        "boards": _facts(d, "facts-start"),
        "boards_end": _facts(d, "facts-end"),
        "idle_and_start_temps": temps(os.path.join(d, "start-temps.txt")),
        "recorder_summary": {os.path.basename(p)[:-4]: recorder_summary(p, stream_window(d))
                             for p in sorted(glob.glob(os.path.join(d, "recorder", "*.csv")))},
    }
    # Arbitrum/zkSync keep today's "images" key unchanged; Perun writes "stack_versions" instead
    # (Anvil has no container images -- cluster spec Task 5).
    if env.get("USE_CASE") == "uc2":
        out["idle_boards"] = sorted(_unquote(env.get("IDLE_BOARDS", "")).split())
        out["game"] = uc2_game(d, env)
    if env.get("STACK") == "perun":
        out["stack_versions"] = perun_versions(d)
    else:
        out["images"] = images(d, env)
    return out


def main():
    d = sys.argv[1]
    json.dump(build(d), open(os.path.join(d, "manifest.json"), "w"), indent=1)
    print(os.path.join(d, "manifest.json"))


if __name__ == "__main__":
    main()
