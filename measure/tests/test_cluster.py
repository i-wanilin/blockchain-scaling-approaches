# measure/tests/test_cluster.py
"""deploy/cluster shell pieces, run locally in bash with the remote side stubbed:
the leftover-process pattern (final review T7-5) and the checked collection of
a board directory (final review I3)."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLUSTER = os.path.join(ROOT, "deploy", "cluster")


def bash(script, timeout=30, **env):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout,
                          env={"PATH": os.environ.get("PATH", ""), **env})


def extract(*names):
    """A bash snippet that defines the named variables (NAME=...) and functions (NAME() {...})
    exactly as deploy/cluster does (a one-line function, or up to its closing "}" line)."""
    with open(CLUSTER) as f:
        lines = f.read().split("\n")
    out = []
    for n in names:
        i = next(k for k, l in enumerate(lines) if l.startswith(f"{n}=") or l.startswith(f"{n}() "))
        j = i
        if lines[i].startswith(f"{n}() ") and not lines[i].rstrip().endswith("}"):
            j = next(k for k in range(i, len(lines)) if lines[k] == "}")
        out += lines[i:j + 1]
    return "\n".join(out)


class TestLeftoverPattern(unittest.TestCase):
    """T7-5: perun-node is found by its full deployed path, never as a substring
    (the owner's rule: processes by exact name or full path)."""

    def matches(self, cmdline):
        r = bash(extract("PERUN_PROC", "LEFTOVERS") + '; printf "%s\\n" "$CMD" | grep -Eq -- "$LEFTOVERS"',
                 CMD=cmdline)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r.returncode == 0

    def test_the_deployed_sidecar_and_sensor_match(self):
        self.assertTrue(self.matches("/home/i_wanilin/measure-code/bin/perun-node -mode uc1-customer"))
        self.assertTrue(self.matches("/home/i_wanilin/measure-code/bin/perun-node -mode uc1-sensor -customers 5"))

    def test_sensor_pattern_matches_only_the_deployed_sensor(self):
        def m(cmd):
            return bash(extract("PERUN_SENSOR_PROC") + '; printf "%s\\n" "$CMD" | grep -Eq -- "$PERUN_SENSOR_PROC"',
                        CMD=cmd).returncode == 0
        self.assertTrue(m("/home/i_wanilin/measure-code/bin/perun-node -mode uc1-sensor -url ws://x"))
        self.assertFalse(m("/home/i_wanilin/measure-code/bin/perun-node -mode uc1-customer"))
        self.assertFalse(m("bash -c cd /tmp/m-1 || exit 1; setsid nohup ~/measure-code/bin/perun-node -mode uc1-sensor"))

    def test_other_perun_node_strings_do_not_match(self):
        for cmd in ("vim /home/i_wanilin/perun-node.log",
                    "/home/i_wanilin/perun-probe/perun-node -mode probe",
                    "tail -f /tmp/m-1/perun-node-sensor.out",
                    "/home/i_wanilin/bin/my-perun-node -mode uc1-customer",
                    "less /home/i_wanilin/measure-code/bin/perun-node -mode uc1-customer"):
            self.assertFalse(self.matches(cmd), cmd)

    def test_the_pattern_does_not_match_the_command_that_carries_it(self):
        r = bash(extract("PERUN_PROC", "LEFTOVERS") + """; line="bash -c pkill -TERM -f '$LEFTOVERS'; true"
                 printf '%s\\n' "$line" | grep -Eq -- "$LEFTOVERS" """)
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_the_other_leftovers_still_match(self):
        for cmd in ("/home/i_wanilin/venv/bin/python -m measure.payclient --system perun",
                    "bash /home/i_wanilin/measure-code/deploy/recorder.sh 1",
                    "bash zk-sampler.sh"):
            self.assertTrue(self.matches(cmd), cmd)


class TestCollectDir(unittest.TestCase):
    """I3: a board directory is copied with a size-scaled timeout and checked
    file by file; a gap or a failed copy is written to COLLECT_INCOMPLETE."""

    def setUp(self):
        self.remote = tempfile.mkdtemp()
        self.run = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.remote, "pc"))
        with open(os.path.join(self.remote, "pc", "ledger.log.jsonl"), "w") as f:
            f.write("x" * 5000 + "\n")
        with open(os.path.join(self.remote, "pc", "sidecar.log"), "w") as f:
            f.write("log\n")
        os.makedirs(os.path.join(self.run, "results", "c1"))

    def collect(self, scp_body):
        """Run collect_dir with `on` running the remote command locally and scp_h replaced by scp_body
        (it gets HOST SECONDS ARGS...; the timeout it was given is printed to stderr)."""
        stubs = ('on() { shift; bash -c "$*"; }\n'
                 f'scp_h() {{ echo "timeout=$2" >&2; {scp_body}; }}\n'
                 'DEAD_HOSTS=" "\n')
        script = (extract("copy_timeout", "copy_gaps", "collect_dir") + "\n" + stubs
                  + f'RUN_OUT={self.run}; collect_dir bath2 {self.remote}/pc {self.run}/results/c1 c1')
        return bash(script)

    def marker(self):
        p = os.path.join(self.run, "COLLECT_INCOMPLETE")
        return open(p).read() if os.path.exists(p) else None

    def test_a_whole_copy_leaves_no_marker(self):
        r = self.collect(f'cp -r {self.remote}/pc/. {self.run}/results/c1/')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.marker())
        self.assertIn("timeout=300", r.stderr)

    def test_a_timed_out_copy_is_named_in_the_marker(self):
        body = (f'cp {self.remote}/pc/ledger.log.jsonl {self.run}/results/c1/; '
                f'truncate -s 100 {self.run}/results/c1/ledger.log.jsonl; return 124')
        r = self.collect(body)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = self.marker()
        self.assertIsNotNone(m)
        self.assertIn("c1", m)
        self.assertIn("exit 124", m)
        self.assertIn("ledger.log.jsonl: 100 of 5001 bytes", m)
        self.assertIn("sidecar.log: missing of 4 bytes", m)
        self.assertIn("COLLECT_INCOMPLETE", r.stderr)

    def test_a_size_gap_without_an_error_is_still_caught(self):
        body = f'cp -r {self.remote}/pc/. {self.run}/results/c1/; rm {self.run}/results/c1/sidecar.log'
        self.collect(body)
        self.assertIn("sidecar.log: missing", self.marker())

    def test_an_unreadable_remote_directory_is_noted(self):
        r = bash(extract("copy_timeout", "copy_gaps", "collect_dir") + "\n"
                 + 'on() { return 1; }\nscp_h() { return 1; }\nDEAD_HOSTS=" "\n'
                 + f'RUN_OUT={self.run}; collect_dir bath2 /nonexistent {self.run}/results/c1 c1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("file list not readable", self.marker())

    def test_timeout_scales_with_size(self):
        def t(n):
            r = bash(extract("copy_timeout") + f"; copy_timeout {n}")
            self.assertEqual(r.returncode, 0, r.stderr)
            return int(r.stdout)
        self.assertEqual(t(0), 300)
        self.assertEqual(t(10_000_000), 300)
        self.assertGreaterEqual(t(60_000_000), 300 + 500)    # a Perun w10 customer: ~57 MB uncompressed
        self.assertGreater(t(60_000_000), t(30_000_000))


class TestPerunBuildRecord(unittest.TestCase):
    """T7-1: the build record names the toolchain embedded in the built binary."""

    def test_build_record_has_the_binary_toolchain(self):
        with open(CLUSTER) as f:
            src = f.read()
        self.assertIn('go version "$PERUN_DIST/arm64/perun-node"', src)
        self.assertIn("binary_toolchain", src)


class TestUC2Env(unittest.TestCase):
    """Task 13: every uc2-*.env sources in bash and carries the frozen UC2 keys."""

    KEYS = ("USE_CASE", "STACK", "IDLE_BOARDS", "WIFI_5GHZ_BOARDS", "N_PLAYERS", "TICKRATE", "GAME_TICKS",
            "GAME_SEED", "START_POINTS", "MAX_QUEUE", "DRAIN_S", "FUND_WEI", "DEPOSIT_WEI", "MAX_IN_FLIGHT",
            "RECEIPT_MODE", "RECEIPT_TIMEOUT_S", "CLOSE_TIMEOUT_S", "ZK_COMMIT_DEADLINE_MS", "UPDATE_TIMEOUT_S",
            "CHALLENGE_S", "PERSIST", "STRICT_POLICY", "YIELD_TIMEOUT_MS", "MAX_COLLISION_RETRIES")
    COMMON = {"USE_CASE": "uc2", "IDLE_BOARDS": "bath3", "WIFI_5GHZ_BOARDS": "bath1 bath2", "N_PLAYERS": "6",
              "TICKRATE": "20", "GAME_TICKS": "1200", "GAME_SEED": "42", "START_POINTS": "10000",
              "MAX_QUEUE": "10000", "DRAIN_S": "60", "FUND_WEI": "2000000000000000000",
              "DEPOSIT_WEI": "1000000000000000000"}
    PER_SYSTEM = {
        "perun": {"MAX_IN_FLIGHT": "64", "UPDATE_TIMEOUT_S": "10", "CHALLENGE_S": "10", "PERSIST": "1",
                  "STRICT_POLICY": "1", "YIELD_TIMEOUT_MS": "200", "MAX_COLLISION_RETRIES": "10"},
        "arbitrum": {"MAX_IN_FLIGHT": "64", "RECEIPT_MODE": "own"},
        "zksync": {"MAX_IN_FLIGHT": "20", "RECEIPT_TIMEOUT_S": "1200", "CLOSE_TIMEOUT_S": "1800",
                   "ZK_COMMIT_DEADLINE_MS": "20000"},
    }

    def source(self, path):
        script = (f"set -euo pipefail; source {path}; "
                  + "".join(f'printf "%s=%s\\n" {k} "${{{k}:-}}"; ' for k in self.KEYS)
                  + 'printf "PLAYERS=%s\\n" "${PLAYERS[*]}"; printf "N=%s\\n" "${#PLAYERS[@]}"')
        r = bash(script)
        self.assertEqual(r.returncode, 0, f"{path}: {r.stderr}")
        return dict(l.split("=", 1) for l in r.stdout.splitlines())

    def files(self):
        import glob
        return sorted(glob.glob(os.path.join(ROOT, "deploy", "experiments", "uc2-*.env")))

    def test_uc2_env_parses(self):
        names = [os.path.basename(p) for p in self.files()]
        self.assertEqual(names, [f"uc2-{s}{x}.env" for s in ("arbitrum", "perun", "zksync") for x in ("-smoke", "")])
        for p in self.files():
            stack = os.path.basename(p)[4:-4].replace("-smoke", "")
            env = self.source(p)
            for k, v in {**self.COMMON, "STACK": stack, **self.PER_SYSTEM[stack]}.items():
                self.assertEqual(env[k], v, f"{p}: {k}")
            self.assertEqual(env["N"], "6", p)
            self.assertEqual(env["PLAYERS"].split(), ["bath1", "bath2", "bath7", "bath4", "bath5", "bath6"], p)
            self.assertNotIn("bath3", env["PLAYERS"].split(), p)

    def test_smoke_files_are_the_same_match(self):
        for p in self.files():
            if "-smoke" not in p:
                continue
            full = p.replace("-smoke", "")
            self.assertEqual(self.source(p), self.source(full), p)
            self.assertIn("not a measurement", open(p).read(), p)


class TestIdleBoards(unittest.TestCase):
    """R9: BOARDS = every board minus IDLE_BOARDS; bath0/bath1 and unknown names are refused."""

    def boards(self, idle):
        r = bash(extract("ALL_BOARDS", "die", "apply_idle")
                 + f'; BOARDS=("${{ALL_BOARDS[@]}}"); IDLE_BOARDS="{idle}"; apply_idle; echo "${{BOARDS[*]}}"')
        return r.returncode, r.stdout.strip(), r.stderr

    def test_idle_board_is_removed_in_order(self):
        self.assertEqual(self.boards("bath3")[:2], (0, "bath0 bath1 bath2 bath4 bath5 bath6 bath7"))

    def test_unset_keeps_every_board(self):
        self.assertEqual(self.boards("")[:2], (0, "bath0 bath1 bath2 bath3 bath4 bath5 bath6 bath7"))

    def test_stack_board_time_server_and_unknown_names_are_refused(self):
        for idle in ("bath0", "bath1", "bath9", "bath3 pi4"):
            rc, _, err = self.boards(idle)
            self.assertNotEqual(rc, 0, idle)
            self.assertIn("IDLE_BOARDS", err, idle)


class TestUC2Patterns(unittest.TestCase):
    """The UC2 processes are leftovers too: the hub, the player sidecar and the game node."""

    def matches(self, cmdline):
        r = bash(extract("PERUN_PROC", "LEFTOVERS") + '; printf "%s\\n" "$CMD" | grep -Eq -- "$LEFTOVERS"',
                 CMD=cmdline)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r.returncode == 0

    def test_uc2_processes_match(self):
        for cmd in ("/home/i_wanilin/measure-code/bin/perun-node -mode uc2-hub -config /tmp/m-1/cluster.json",
                    "/home/i_wanilin/measure-code/bin/perun-node -mode uc2-player",
                    "/home/i_wanilin/venv/bin/python -u -m arena.node --id 0 --peers x"):
            self.assertTrue(self.matches(cmd), cmd)

    def test_hub_pattern_matches_only_the_deployed_hub(self):
        def m(cmd):
            return bash(extract("PERUN_HUB_PROC") + '; printf "%s\\n" "$CMD" | grep -Eq -- "$PERUN_HUB_PROC"',
                        CMD=cmd).returncode == 0
        self.assertTrue(m("/home/i_wanilin/measure-code/bin/perun-node -mode uc2-hub -config /tmp/m-1/cluster.json"))
        self.assertFalse(m("/home/i_wanilin/measure-code/bin/perun-node -mode uc2-player"))
        self.assertFalse(m("bash -c ~/measure-code/bin/perun-node -mode uc2-hub -config x; echo $? > hub.rc"))


class TestUC2RunPieces(unittest.TestCase):
    """one_run_uc2's checks fail closed (Task 12 review): the reference must exist, a differing or
    unreadable trace writes HASH_MISMATCH, an aborted match or a silent board fails the run."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def run_fn(self, names, body, **env):
        return bash(extract(*names) + "\nsay() { echo \"$*\"; }\nset -euo pipefail\n" + body, **env)

    def test_reference_is_required(self):
        ref = os.path.join(self.d, "ref.sha256")
        r = self.run_fn(["die", "uc2_reference"], f"REFERENCE_SHA={ref}; uc2_reference")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing a UC2 run", r.stderr)
        open(ref, "w").write("not-a-hash  x\n")
        r = self.run_fn(["die", "uc2_reference"], f"REFERENCE_SHA={ref}; uc2_reference")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a SHA-256", r.stderr)
        open(ref, "w").write("AB" * 32 + "  /tmp/ref.jsonl\n")
        r = self.run_fn(["die", "uc2_reference"], f"REFERENCE_SHA={ref}; uc2_reference")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ab" * 32), r.stderr)

    def hash_check(self, answers):
        os.makedirs(os.path.join(self.d, "results"), exist_ok=True)
        open(os.path.join(self.d, "results", "reference.sha256"), "w").write("a" * 64 + "\n")
        stub = "on() { case $1 in " + " ".join(f"{h}) {a} ;;" for h, a in answers.items()) + " esac; }\n"
        return self.run_fn(["die", "uc2_hash_check"],
                           stub + f"PLAYERS=({' '.join(answers)}); uc2_hash_check {self.d} /tmp/m-x")

    def test_matching_traces_pass(self):
        r = self.hash_check({"bath1": "echo " + "a" * 64, "bath2": "echo " + "a" * 64})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.d, "HASH_MISMATCH")))
        self.assertEqual(open(os.path.join(self.d, "results", "trace-sha256.txt")).read().split(),
                         ["bath1", "a" * 64, "bath2", "a" * 64])

    def test_a_differing_or_unreadable_trace_writes_hash_mismatch_and_fails(self):
        r = self.hash_check({"bath1": "echo " + "a" * 64, "bath2": "echo " + "b" * 64, "bath7": "return 255"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(open(os.path.join(self.d, "HASH_MISMATCH")).read().split(), ["bath2", "bath7"])
        self.assertIn("trace hash differs", r.stderr)
        self.assertIn("bath7 unreadable", open(os.path.join(self.d, "results", "trace-sha256.txt")).read())

    def wait_game(self, states, limit=0):
        stub = ("game_state() { case $1 in " + " ".join(f"{h}) echo '{s}' ;;" for h, s in states.items())
                + " esac; }\n")
        return self.run_fn(["die", "uc2_wait_game"], stub + f"PLAYERS=({' '.join(states)}); uc2_wait_game /tmp/m-x {limit}")

    def test_game_wait(self):
        self.assertEqual(self.wait_game({"bath1": "complete", "bath2": "complete"}).returncode, 0)
        r = self.wait_game({"bath1": "complete", "bath2": "aborted: stall: no input from player 3 for 30 s"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("match aborted on bath2: stall", r.stderr)
        r = self.wait_game({"bath1": "dead", "bath2": "complete"})
        self.assertIn("bath1 exited without node-result.json", r.stderr)
        r = self.wait_game({"bath1": "complete", "bath2": ""})        # no answer never counts as done
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("match not finished after 0s: bath2:no answer", r.stderr)

    def test_game_wait_quiet_period(self):
        # no poll before QUIET_S has passed; the deadline (0 s here) still fails the run once polling starts
        stub = "game_state() { echo \"$SECONDS\" >> /tmp/uc2-quiet-$$; echo complete; }\n"
        script = (stub + "PLAYERS=(bath1); S0=$SECONDS; uc2_wait_game /tmp/m-x 5 2; "
                  "echo first=$(head -n1 /tmp/uc2-quiet-$$) start=$S0; rm -f /tmp/uc2-quiet-$$")
        r = self.run_fn(["die", "uc2_wait_game"], script)
        self.assertEqual(r.returncode, 0, r.stderr)
        first, start = (int(x.split("=")[1]) for x in r.stdout.split())
        self.assertGreaterEqual(first - start, 2)
        r = self.wait_game({"bath1": ""}, limit=0)            # quiet is capped at the deadline: still fails closed
        self.assertIn("match not finished after 0s", r.stderr)

    def test_payment_client_wait_fails_closed(self):
        def w(states, want="ready"):
            stub = ("pc_state() { case $1 in " + " ".join(f"{h}) {s} ;;" for h, s in states.items()) + " esac; }\n")
            return self.run_fn(["die", "wait_players"], stub + f"PLAYERS=({' '.join(states)}); wait_players {want} 0 /tmp/m-x/pc")
        self.assertEqual(w({"bath1": "echo ready", "bath2": "echo complete"}).returncode, 0)
        r = w({"bath1": "echo ready", "bath2": "true"})                   # empty answer: pending, then the deadline
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("bath2:no answer", r.stderr)
        r = w({"bath1": "echo 'failed:open_error: boom'", "bath2": "echo ready"})
        self.assertIn("payment client on bath1 failed:open_error: boom", r.stderr)
        r = w({"bath1": "echo complete", "bath2": "echo dead"}, want="complete")
        self.assertIn("bath2 exited before 'complete'", r.stderr)


if __name__ == "__main__":
    unittest.main()
