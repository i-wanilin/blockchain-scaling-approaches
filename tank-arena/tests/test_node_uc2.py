"""Six lockstep node processes on loopback, with payment sockets.

Each node hands its payments to a local Unix socket (here a fake listener
standing in for measure/payclient.py). Asserts that the distributed match
still equals the single-process trace, that every owed payment reaches a
socket exactly once with R3 IDs and R4 intended times, that a stalled
peer aborts the match with a reason, and that a missing payment client
stops a node before tick 0.
"""

import csv
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

from arena.paybridge import payments_for
from arena.world import Controls
from net.protocol import encode_frame
from tests.test_net import ROOT, file_hash, free_ports
from tests.test_paybridge import Collector, PAYMENT_KEYS

PLAYERS = 6
TICKRATE = 20
DURATION = 10
SEED = 42
RUN = "test-run"
TIMEOUT_S = 90
# Pacing runs on the monotonic clock, intended and generated times on the
# wall clock. If the wall clock is stepped during a match (WSL2 steps it by
# ~1.5 ms now and then), a tick can finish just before its intended time.
# Allow 5 ms.
EARLY_S = 0.005


def node_cmd(i, peers, tmp, *, sock=True, duration=DURATION):
    cmd = [sys.executable, "-m", "arena.node", "--id", str(i),
           "--peers", peers, "--tickrate", str(TICKRATE),
           "--duration", str(duration), "--seed", str(SEED),
           "--out", os.path.join(tmp, f"n{i}.jsonl"),
           "--run", RUN,
           "--ticks-out", os.path.join(tmp, f"ticks{i}.csv"),
           "--result-out", os.path.join(tmp, f"result{i}.json")]
    if sock:
        cmd += ["--pay-socket", os.path.join(tmp, f"s{i}.sock")]
    return cmd


def load_result(tmp, i):
    with open(os.path.join(tmp, f"result{i}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def spawn(cmds, env=None):
    return [subprocess.Popen(c, cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env) for c in cmds]


def finish(procs, timeout=TIMEOUT_S):
    """Wait for every process; return [(returncode, stderr)]."""
    out = []
    try:
        for p in procs:
            _, err = p.communicate(timeout=timeout)
            out.append((p.returncode, err.decode()))
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
    return out


class NodeUC2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = cls.tmp = cls._tmp.name
        ref = os.path.join(tmp, "ref.jsonl")
        subprocess.run([sys.executable, "-m", "arena.sim",
                        "--players", str(PLAYERS), "--tickrate", str(TICKRATE),
                        "--duration", str(DURATION), "--seed", str(SEED),
                        "--out", ref], cwd=ROOT, check=True,
                       capture_output=True)
        cls.ref_hash = file_hash(ref)
        with open(ref, encoding="utf-8") as fh:
            cls.ref_events = [json.loads(x) for x in fh]
        peers = ",".join(f"127.0.0.1:{p}" for p in free_ports(PLAYERS))
        collectors = [Collector(os.path.join(tmp, f"s{i}.sock"))
                      for i in range(PLAYERS)]
        cls.rcs = finish(spawn([node_cmd(i, peers, tmp)
                                for i in range(PLAYERS)]))
        cls.lines = [c.lines() for c in collectors]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_six_nodes_match_sim_byte_for_byte(self):
        total_ticks = DURATION * TICKRATE
        for i, (rc, err) in enumerate(self.rcs):
            self.assertEqual(rc, 0, f"node {i} failed:\n{err}")
            self.assertEqual(file_hash(os.path.join(self.tmp, f"n{i}.jsonl")),
                             self.ref_hash, f"node {i} diverged")
            res = load_result(self.tmp, i)
            self.assertEqual(set(res), {"complete", "aborted", "ticks",
                                        "t_start", "t_end", "emitted",
                                        "counts", "trace_sha256"})
            self.assertTrue(res["complete"])
            self.assertIsNone(res["aborted"])
            self.assertEqual(res["ticks"], total_ticks)
            self.assertEqual(res["trace_sha256"], self.ref_hash)
            self.assertGreater(res["t_end"], res["t_start"])
            self.assertEqual(res["emitted"], len(self.lines[i]) - 1)
            fires = sum(1 for e in self.ref_events if e["type"] == "fire")
            self.assertEqual(res["counts"]["fire"], fires)
            with open(os.path.join(self.tmp, f"ticks{i}.csv"),
                      encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual([int(r["tick"]) for r in rows],
                             list(range(1, total_ticks + 1)))
            for r in rows:
                t_int = res["t_start"] + (int(r["tick"]) - 1) / TICKRATE
                self.assertAlmostEqual(float(r["t_intended"]), t_int, places=5)
                self.assertGreaterEqual(float(r["t_actual"]),
                                        float(r["t_intended"]) - EARLY_S)

    def test_payments_cover_every_owed_event_exactly_once(self):
        all_ids = []
        for i in range(PLAYERS):
            lines = self.lines[i]
            self.assertEqual(lines[-1], {"end": True}, f"socket {i}")
            body = lines[:-1]
            for ln in body:
                self.assertEqual(set(ln), PAYMENT_KEYS)
            owed = payments_for(self.ref_events, i, RUN)
            self.assertEqual([(b["id"], b["to"], b["amount_wei"])
                              for b in body],
                             [(o["id"], o["to"], o["amount_wei"])
                              for o in owed], f"socket {i}")
            # R4: t_intended = match start + (T - 1) * dt
            t_start = load_result(self.tmp, i)["t_start"]
            for ln in body:
                tick = int(ln["id"].split("/")[3][1:])
                self.assertAlmostEqual(
                    ln["t_intended"], t_start + (tick - 1) / TICKRATE,
                    places=5)
                self.assertGreaterEqual(ln["t_generated"],
                                        ln["t_intended"] - EARLY_S)
            all_ids += [b["id"] for b in body]
        self.assertEqual(len(all_ids), len(set(all_ids)), "duplicate ids")
        expected = {o["id"] for i in range(PLAYERS)
                    for o in payments_for(self.ref_events, i, RUN)}
        self.assertEqual(set(all_ids), expected)
        transfers = sum(1 for e in self.ref_events
                        if e["type"] == "fire"
                        or (e["type"] == "hit" and e["payload"]["amount"] > 0))
        self.assertEqual(len(all_ids), transfers)


class NodeUC2AbortTest(unittest.TestCase):
    def test_stall_aborts_with_reason(self):
        env = dict(os.environ, ARENA_TICK_TIMEOUT_S="2")
        with tempfile.TemporaryDirectory() as tmp:
            peers = ",".join(f"127.0.0.1:{p}" for p in free_ports(PLAYERS))
            collectors = [Collector(os.path.join(tmp, f"s{i}.sock"))
                          for i in range(PLAYERS)]
            procs = spawn([node_cmd(i, peers, tmp, duration=60)
                           for i in range(PLAYERS)], env=env)
            try:
                ticks5 = os.path.join(tmp, "ticks5.csv")
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if os.path.exists(ticks5):
                        with open(ticks5, encoding="utf-8") as fh:
                            if sum(1 for _ in fh) > 40:
                                break
                    time.sleep(0.02)
                else:
                    self.fail("node 5 never reached tick 40")
                procs[5].kill()
                killed_at = time.monotonic()
                rcs = finish(procs[:5], timeout=10)
                self.assertLess(time.monotonic() - killed_at, 10)
            finally:
                for p in procs:
                    if p.poll() is None:
                        p.kill()
                    p.communicate()
            for i, (rc, err) in enumerate(rcs):
                self.assertEqual(rc, 3, f"node {i}:\n{err}")
                res = load_result(tmp, i)
                self.assertFalse(res["complete"])
                self.assertTrue(res["aborted"].startswith("stall at tick"),
                                res["aborted"])
                self.assertIn("[5]", res["aborted"])
                self.assertGreaterEqual(res["ticks"], 40)
                self.assertLess(res["ticks"], 60 * TICKRATE)
                lines = collectors[i].lines()
                self.assertEqual(lines[-1], {"end": True}, f"socket {i}")
                self.assertEqual(res["emitted"], len(lines) - 1)
            collectors[5].lines()

    def test_peer_reset_takes_the_stall_path(self):
        # A fake player 1 joins node 0's mesh, sends its inputs for ticks
        # 0..29 up front, never reads, then closes with SO_LINGER 0 while
        # node 0's frames sit unread in its receive buffer: a TCP reset.
        # Node 0's next input broadcast hits the dead connection; it must
        # still stall at tick 31, abort with a reason, end the payment
        # stream and exit 3.
        env = dict(os.environ, ARENA_TICK_TIMEOUT_S="2")
        with tempfile.TemporaryDirectory() as tmp:
            ports = free_ports(2)
            peers = ",".join(f"127.0.0.1:{p}" for p in ports)
            col = Collector(os.path.join(tmp, "s0.sock"))
            (proc,) = spawn([node_cmd(0, peers, tmp, duration=60)], env=env)
            fake = None
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        fake = socket.create_connection(
                            ("127.0.0.1", ports[0]), timeout=1)
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.05)
                wire = Controls().to_wire()
                frames = [{"t": "hello", "id": 1}, {"t": "ready", "id": 1}]
                frames += [{"t": "in", "k": k, "id": 1, "c": wire}
                           for k in range(30)]
                fake.sendall(b"".join(encode_frame(f) for f in frames))
                ticks0 = os.path.join(tmp, "ticks0.csv")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if os.path.exists(ticks0):
                        with open(ticks0, encoding="utf-8") as fh:
                            if sum(1 for _ in fh) > 10:
                                break
                    time.sleep(0.02)
                else:
                    self.fail("node 0 never reached tick 10")
                fake.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                struct.pack("ii", 1, 0))
                fake.close()
                fake = None
                ((rc, err),) = finish([proc], timeout=15)
            finally:
                if fake is not None:
                    fake.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
            self.assertEqual(rc, 3, err)
            res = load_result(tmp, 0)
            self.assertFalse(res["complete"])
            self.assertEqual(res["aborted"],
                             "stall at tick 31 (no input from players [1])")
            self.assertEqual(res["ticks"], 30)
            lines = col.lines()
            self.assertEqual(lines[-1], {"end": True})
            self.assertEqual(res["emitted"], len(lines) - 1)

    def test_unexpected_error_still_writes_result_and_end_marker(self):
        # Peers that never become ready: wait_until_ready times out. The
        # node must still end the payment stream, write node-result.json
        # with a reason, and exit 4 (not crash with exit 1).
        env = dict(os.environ, ARENA_TICK_TIMEOUT_S="1")
        with tempfile.TemporaryDirectory() as tmp:
            ports = free_ports(2)
            peers = ",".join(f"127.0.0.1:{p}" for p in ports)
            col = Collector(os.path.join(tmp, "s0.sock"))
            (proc,) = spawn([node_cmd(0, peers, tmp)], env=env)
            fake = None
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        fake = socket.create_connection(
                            ("127.0.0.1", ports[0]), timeout=1)
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.05)
                fake.sendall(encode_frame({"t": "hello", "id": 1}))  # no ready
                ((rc, err),) = finish([proc], timeout=30)
            finally:
                if fake is not None:
                    fake.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
            self.assertEqual(rc, 4, err)
            res = load_result(tmp, 0)
            self.assertFalse(res["complete"])
            self.assertEqual(res["ticks"], 0)
            self.assertTrue(res["aborted"].startswith("node error: "),
                            res["aborted"])
            self.assertEqual(col.lines(), [{"end": True}])

    def test_missing_socket_aborts_before_tick_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            peers = ",".join(f"127.0.0.1:{p}" for p in free_ports(2))
            # node 0 points at a socket nobody listens on; node 1 has none
            rcs = finish(spawn([node_cmd(0, peers, tmp),
                                node_cmd(1, peers, tmp, sock=False)]),
                         timeout=60)
            for i in (0, 1):
                rc, err = rcs[i]
                self.assertEqual(rc, 4, f"node {i}:\n{err}")
                res = load_result(tmp, i)
                self.assertFalse(res["complete"])
                self.assertEqual(res["ticks"], 0)
                self.assertEqual(res["emitted"], 0)
                self.assertIn("payment client not listening", res["aborted"])
            self.assertEqual(load_result(tmp, 0)["aborted"],
                             "payment client not listening")
            self.assertEqual(load_result(tmp, 1)["aborted"],
                             "payment client not listening (player 0)")


if __name__ == "__main__":
    unittest.main()
