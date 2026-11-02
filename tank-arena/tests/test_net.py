"""Distributed lockstep integration: mesh and relay on localhost.

Spawns real OS processes (one per player) and asserts that
(a) all nodes produce byte-identical traces (lockstep consistency), and
(b) the distributed trace equals the single-process trace for the same
    (seed, N, tickrate, duration) — the determinism contract.
"""

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest

from arena.sim import iter_sim

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYERS = 2
TICKRATE = 10
DURATION = 1.5
SEED = 42
TIMEOUT_S = 60


def free_ports(n: int) -> list[int]:
    socks, ports = [], []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        socks.append(s)
        ports.append(s.getsockname()[1])
    for s in socks:
        s.close()
    return ports


def file_hash(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def local_hash() -> str:
    h = hashlib.sha256()
    for ev in iter_sim(PLAYERS, TICKRATE, DURATION, SEED):
        h.update(json.dumps(ev, separators=(",", ":")).encode() + b"\n")
    return h.hexdigest()


def run_procs(cmds: list[list[str]]) -> None:
    procs = [subprocess.Popen(c, cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) for c in cmds]
    failures = []
    for p in procs:
        try:
            out, err = p.communicate(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            for q in procs:
                q.kill()
            raise
        if p.returncode != 0:
            failures.append(f"rc={p.returncode}\n{err.decode()}")
    if failures:
        raise AssertionError("node process failed:\n" + "\n".join(failures))


class TestLockstepMesh(unittest.TestCase):
    def test_mesh_nodes_agree_and_match_local_sim(self):
        ports = free_ports(PLAYERS)
        peers = ",".join(f"127.0.0.1:{p}" for p in ports)
        with tempfile.TemporaryDirectory() as tmp:
            outs = [os.path.join(tmp, f"n{i}.jsonl")
                    for i in range(PLAYERS)]
            cmds = [[sys.executable, "-m", "arena.node",
                     "--id", str(i), "--peers", peers,
                     "--tickrate", str(TICKRATE),
                     "--duration", str(DURATION), "--seed", str(SEED),
                     "--out", outs[i]] for i in range(PLAYERS)]
            run_procs(cmds)
            hashes = {file_hash(o) for o in outs}
            self.assertEqual(len(hashes), 1,
                             "nodes diverged (lockstep broken)")
            self.assertEqual(hashes.pop(), local_hash(),
                             "distributed trace != single-process trace")


class TestLockstepRelay(unittest.TestCase):
    def test_relay_nodes_agree(self):
        (relay_port,) = free_ports(1)
        with tempfile.TemporaryDirectory() as tmp:
            outs = [os.path.join(tmp, f"n{i}.jsonl")
                    for i in range(PLAYERS)]
            cmds = [[sys.executable, "-m", "net.relay",
                     "--port", str(relay_port),
                     "--expect", str(PLAYERS)]]
            cmds += [[sys.executable, "-m", "arena.node",
                      "--id", str(i), "--players", str(PLAYERS),
                      "--topology", "relay",
                      "--relay", f"127.0.0.1:{relay_port}",
                      "--tickrate", str(TICKRATE),
                      "--duration", str(DURATION), "--seed", str(SEED),
                      "--out", outs[i]] for i in range(PLAYERS)]
            run_procs(cmds)
            hashes = {file_hash(o) for o in outs}
            self.assertEqual(len(hashes), 1)
            self.assertEqual(hashes.pop(), local_hash())


if __name__ == "__main__":
    unittest.main()
