# measure/tests/test_preflight.py
"""Each pre-flight rule of cluster spec §5 fails on its own condition."""
import json
import os
import tempfile
import unittest

from measure import preflight

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TIME_SERVER = "192.168.178.211"
GOOD = dict(model="Raspberry Pi 3 Model B Rev 1.2", kernel="6.8.0-1065-raspi", bssid="02:00:00:00:00:01",
            freq_mhz=2462, power_save="off", throttled="0x0", chrony_source=TIME_SERVER,
            chrony_offset_ms=0.4, netem=False, disk_free_gb=20.0, docker_up="0", anvil_up="0")


def facts(over=None):
    reg = preflight.fleet_register(os.path.join(REPO, "deploy/FLEET.md"))
    out = {}
    for h, r in reg.items():
        out[h] = dict(GOOD, host=h, wifi_mac=r["wifi_mac"], serial=r["serial"])
    out["bath0"]["docker_up"] = "3"
    out["bath1"]["chrony_source"] = "0.pool.ntp.example"
    for (h, k), v in (over or {}).items():
        out[h][k] = v
    return out, reg


def run(f, reg, **kw):
    return preflight.check(f, reg, bssid="02:00:00:00:00:01", clean_power={"bath0", "bath1"},
                           max_offset_ms=5.0, min_disk_gb=5.0, stack_board="bath0", expect_docker=True,
                           time_server_ip=TIME_SERVER, **kw)


def write_facts_dir(base, over=None):
    """A real $out/facts-start directory (one <host>.json per registered board) under base, for
    driving preflight.run_preflight(dir) -- the thing main() calls -- end to end."""
    f, reg = facts(over)
    d = os.path.join(base, "facts-start")
    os.makedirs(d)
    for h, v in f.items():
        with open(os.path.join(d, h + ".json"), "w") as fh:
            json.dump(v, fh)
    return d


def patch_fact(facts_dir, host, **kw):
    p = os.path.join(facts_dir, host + ".json")
    v = json.load(open(p))
    v.update(kw)
    json.dump(v, open(p, "w"))


class TestPreflight(unittest.TestCase):
    def test_register_has_eight_boards(self):
        reg = preflight.fleet_register(os.path.join(REPO, "deploy/FLEET.md"))
        self.assertEqual(sorted(reg), [f"bath{i}" for i in range(8)])

    def test_all_good_passes(self):
        self.assertEqual(run(*facts()), [])

    def test_each_rule(self):
        cases = {
            ("bath3", "wifi_mac"): "aa:bb:cc:dd:ee:ff",
            ("bath2", "bssid"): "02:00:00:00:00:02",
            ("bath4", "kernel"): "6.8.0-1064-raspi",
            ("bath5", "chrony_offset_ms"): 12.0,
            ("bath6", "power_save"): "on",
            ("bath1", "throttled"): "0x50000",
            ("bath7", "netem"): True,
            ("bath0", "disk_free_gb"): 1.0,
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                bad = run(*facts({key: value}))
                self.assertTrue(any(b.startswith(key[0] + ":") or (key[1] == "kernel" and "kernels differ" in b)
                                    for b in bad), bad)

    def test_undervolted_pi3b_is_recorded_not_failed(self):
        self.assertEqual(run(*facts({("bath5", "throttled"): "0x50005"})), [])

    def test_soft_temperature_bit_is_allowed_on_clean_boards(self):
        self.assertEqual(run(*facts({("bath0", "throttled"): "0x80000"})), [])
        self.assertEqual(run(*facts({("bath1", "throttled"): "0x80000"})), [])

    def test_missing_board_fails(self):
        f, reg = facts()
        del f["bath6"]
        self.assertIn("bath6: unreachable", run(f, reg))

    def test_stack_down_fails(self):
        self.assertIn("bath0: stack not up", run(*facts({("bath0", "docker_up"): "0"})))

    def test_unexpected_containers_fail(self):
        self.assertIn("bath3: unexpected containers running", run(*facts({("bath3", "docker_up"): "2"})))

    def test_missing_or_empty_facts_fail_with_host_and_field(self):
        cases = {
            ("bath2", "power_save"): "power_save",
            ("bath3", "bssid"): "bssid",
            ("bath4", "throttled"): "throttled",
            ("bath5", "chrony_offset_ms"): "chrony_offset_ms",
            ("bath6", "chrony_source"): "chrony_source",
        }
        for (host, field), needle in cases.items():
            # power_save and throttled are strings, so "" (empty) is also a failure; every field may be None
            empty_value = "" if field in ("power_save", "throttled") else None
            for value in (empty_value, None):
                with self.subTest(host=host, field=field, value=value):
                    bad = run(*facts({(host, field): value}))
                    self.assertTrue(any(b.startswith(host + ":") and needle in b for b in bad), bad)

    def test_missing_key_fails_not_raises(self):
        f, reg = facts()
        del f["bath2"]["throttled"]
        del f["bath3"]["kernel"]
        bad = run(f, reg)
        self.assertTrue(any(b.startswith("bath2:") and "throttled" in b for b in bad), bad)
        self.assertTrue(any(b.startswith("bath3:") and "kernel" in b for b in bad), bad)

    def test_client_on_wrong_clock_source_fails(self):
        bad = run(*facts({("bath4", "chrony_source"): "10.0.0.1"}))
        self.assertTrue(any(b.startswith("bath4:") and "chrony_source" in b for b in bad), bad)

    def test_bath1_upstream_source_passes(self):
        f, reg = facts({("bath1", "chrony_source"): "185.1.2.3"})
        self.assertEqual(run(f, reg), [])

    def test_non_numeric_docker_up_is_a_failure(self):
        bad = run(*facts({("bath3", "docker_up"): "n/a"}))
        self.assertTrue(any(b.startswith("bath3:") and "docker_up" in b for b in bad), bad)

    def test_current_throttle_bits_fail_on_clean_board(self):
        for bits in ("0x1", "0x4", "0x50005"):
            with self.subTest(bits=bits):
                bad = run(*facts({("bath0", "throttled"): bits}))
                self.assertTrue(any(b.startswith("bath0:") and "throttle" in b for b in bad), bad)

    def test_missing_kernel_key_alongside_real_mismatch(self):
        f, reg = facts({("bath4", "kernel"): "6.8.0-1064-raspi"})
        del f["bath2"]["kernel"]
        bad = run(f, reg)
        self.assertTrue(any("kernels differ" in b for b in bad), bad)
        self.assertTrue(any(b.startswith("bath2:") and "kernel" in b for b in bad), bad)

    def test_nan_inf_and_bool_are_invalid(self):
        for field in ("chrony_offset_ms", "disk_free_gb"):
            for value in (float("nan"), float("inf"), True):
                with self.subTest(field=field, value=value):
                    bad = run(*facts({("bath3", field): value}))
                    self.assertTrue(any(b.startswith("bath3:") for b in bad), bad)

    def test_extra_host_not_in_register_is_flagged_and_ignored_for_kernels(self):
        f, reg = facts()
        f["bath8"] = dict(f["bath3"], host="bath8", kernel="9.9.9")
        bad = run(f, reg)
        self.assertEqual(bad, ["bath8: not in the register"])

    def test_non_dict_facts_for_a_host_fail(self):
        f, reg = facts()
        f["bath2"] = "ssh: connect timed out"
        bad = run(f, reg)
        self.assertEqual([b for b in bad if b.startswith("bath2")], ["bath2: facts unreadable (not an object)"])

    def test_short_register_fails(self):
        f, reg = facts()
        small = {"bath0": reg["bath0"]}
        bad = run({"bath0": f["bath0"]}, small)
        self.assertIn("register has 1 boards, expected 8", bad)
        self.assertEqual(run({}, {}), ["register has 0 boards, expected 8"])

    def test_missing_fleet_md_raises_oserror_for_main_to_report(self):
        with self.assertRaises(OSError):
            preflight.fleet_register("/nonexistent/FLEET.md")

    def test_expect_stack_zksync_passes_with_one_server_and_two_containers(self):
        f, reg = facts()
        f["bath0"]["zksync_server_up"] = 1
        f["bath0"]["containers"] = ["thesis_local-reth-1", "thesis_local-postgres-1"]
        self.assertEqual(run(f, reg, expect_stack="zksync"), [])

    def test_expect_stack_zksync_fails_when_server_down(self):
        f, reg = facts()
        f["bath0"]["zksync_server_up"] = 0
        f["bath0"]["containers"] = ["thesis_local-reth-1", "thesis_local-postgres-1"]
        bad = run(f, reg, expect_stack="zksync")
        self.assertTrue(any(b.startswith("bath0:") and "zkSync server" in b for b in bad), bad)

    def test_expect_stack_zksync_fails_when_an_arbitrum_container_is_up(self):
        f, reg = facts()
        f["bath0"]["zksync_server_up"] = 1
        f["bath0"]["containers"] = ["thesis_local-reth-1", "thesis_local-postgres-1", "arbitrum-sequencer-1"]
        bad = run(f, reg, expect_stack="zksync")
        self.assertTrue(any(b.startswith("bath0:") and "arbitrum-" in b for b in bad), bad)

    def test_expect_stack_zksync_fails_with_fewer_than_two_project_containers(self):
        f, reg = facts()
        f["bath0"]["zksync_server_up"] = 1
        f["bath0"]["containers"] = ["thesis_local-reth-1"]
        bad = run(f, reg, expect_stack="zksync")
        self.assertTrue(any(b.startswith("bath0:") and "thesis_local-" in b for b in bad), bad)

    def test_expect_stack_arbitrum_fails_when_zksync_server_is_up(self):
        f, reg = facts()
        f["bath0"]["zksync_server_up"] = 1
        bad = run(f, reg, expect_stack="arbitrum")
        self.assertTrue(any(b.startswith("bath0:") and "zkSync server" in b for b in bad), bad)

    PG = "postgres: postgres zksync_server_thesis_chain 172.18.0.1(33594) idle"   # real facts: not a binary
    ZK = "/home/i_wanilin/zksync/bin/zksync_server"

    def zk_ok(self, f):
        f["bath0"]["zksync_server_up"] = 1
        f["bath0"]["containers"] = ["thesis_local-reth-1", "thesis_local-postgres-1"]

    def test_zksync_passes_with_the_measured_binary_and_postgres_lines(self):
        f, reg = facts()
        self.zk_ok(f)
        f["bath0"]["zksync_server_paths"] = [self.ZK, self.PG]
        self.assertEqual(run(f, reg, expect_stack="zksync"), [])

    def test_zksync_fails_when_another_zksync_server_binary_runs(self):
        """Final review Minor 2: the 2026-09-30 incident was a zksync_server.a72 left running."""
        for stray in ("/home/i_wanilin/zksync/bin/zksync_server.a72", "./zksync_server", "/tmp/zksync_server"):
            f, reg = facts()
            self.zk_ok(f)
            f["bath0"]["zksync_server_paths"] = [self.ZK, stray, self.PG]
            bad = run(f, reg, expect_stack="zksync")
            self.assertTrue(any(b.startswith("bath0:") and stray in b for b in bad), (stray, bad))

    def test_arbitrum_fails_on_any_zksync_server_binary(self):
        for path in (self.ZK, "/home/i_wanilin/zksync/bin/zksync_server.a72"):
            f, reg = facts()
            f["bath0"]["zksync_server_paths"] = [path]
            bad = run(f, reg, expect_stack="arbitrum")
            self.assertTrue(any(b.startswith("bath0:") and path in b for b in bad), (path, bad))

    def test_zk_bin_matches_deploy_cluster(self):
        import re
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "deploy", "cluster")) as fh:
            m = re.search(r"^ZK_BIN=(\S+)", fh.read(), re.M)
        self.assertEqual(m.group(1), preflight.ZK_BIN)

    def test_arbitrum_passes_with_postgres_lines_only(self):
        f, reg = facts()
        f["bath0"]["zksync_server_paths"] = [self.PG]
        self.assertEqual(run(f, reg, expect_stack="arbitrum"), [])

    def test_perun_stack_needs_anvil_and_no_containers(self):
        """Task 5 (controller ruling R-P4): bath0 for perun needs Anvil up, docker and the other
        two stacks' containers down, zkSync's server down."""
        base_over = {("bath0", "docker_up"): "0", ("bath0", "anvil_up"): "1"}
        self.assertEqual(run(*facts(base_over), expect_stack="perun"), [])

        bad = run(*facts({**base_over, ("bath0", "anvil_up"): "0"}), expect_stack="perun")
        self.assertTrue(any(b.startswith("bath0:") and "Anvil" in b for b in bad), bad)

        f, reg = facts(base_over)
        f["bath0"]["containers"] = ["arbitrum-sequencer-1"]
        bad = run(f, reg, expect_stack="perun")
        self.assertTrue(any(b.startswith("bath0:") and "arbitrum-" in b for b in bad), bad)

        f, reg = facts(base_over)
        f["bath0"]["containers"] = ["thesis_local-reth-1"]
        bad = run(f, reg, expect_stack="perun")
        self.assertTrue(any(b.startswith("bath0:") and "thesis_local-" in b for b in bad), bad)

        f, reg = facts(base_over)
        f["bath0"]["zksync_server_up"] = 1
        bad = run(f, reg, expect_stack="perun")
        self.assertTrue(any(b.startswith("bath0:") and "zkSync server" in b for b in bad), bad)

        f, reg = facts({**base_over, ("bath0", "docker_up"): "2"})
        bad = run(f, reg, expect_stack="perun")
        self.assertTrue(any(b.startswith("bath0:") and "containers" in b for b in bad), bad)

    def test_arbitrum_stack_fails_if_anvil_runs(self):
        bad = run(*facts({("bath0", "anvil_up"): "1"}), expect_stack="arbitrum")
        self.assertTrue(any(b.startswith("bath0:") and "Anvil" in b for b in bad), bad)

    def test_zksync_stack_fails_if_anvil_runs(self):
        f, reg = facts({("bath0", "anvil_up"): "1"})
        self.zk_ok(f)
        bad = run(f, reg, expect_stack="zksync")
        self.assertTrue(any(b.startswith("bath0:") and "Anvil" in b for b in bad), bad)

    def test_missing_anvil_up_is_a_failure(self):
        f, reg = facts()
        del f["bath4"]["anvil_up"]
        bad = run(f, reg)
        self.assertTrue(any(b.startswith("bath4:") and "anvil_up" in b for b in bad), bad)

    def test_anvil_running_on_a_non_stack_board_fails(self):
        bad = run(*facts({("bath3", "anvil_up"): "1"}))
        self.assertTrue(any(b.startswith("bath3:") and "Anvil" in b for b in bad), bad)

    def test_run_preflight_perun_env_applies_the_perun_rule(self):
        base = tempfile.mkdtemp()
        fd = write_facts_dir(base)
        patch_fact(fd, "bath0", docker_up="0", anvil_up="1", zksync_server_up=0, containers=[])
        open(os.path.join(base, "experiment.env"), "w").write("STACK=perun\n")
        self.assertEqual(preflight.run_preflight(fd), [])
        patch_fact(fd, "bath0", anvil_up="0")
        bad = preflight.run_preflight(fd)
        self.assertTrue(any(b.startswith("bath0:") and "Anvil" in b for b in bad), bad)

    def test_expect_stack_default_ignores_zksync_fields(self):
        """All existing preflight tests stay green without passing expect_stack."""
        f, reg = facts()
        f["bath0"]["zksync_server_up"] = 0           # would fail under expect_stack="zksync"
        self.assertEqual(run(f, reg), [])

    def test_thermal_sticky_ok_passes_both_since_boot_bits_on_bath0(self):
        bad = run(*facts({("bath0", "throttled"): "0xe0000"}), thermal_sticky_ok=frozenset({"bath0"}))
        self.assertEqual(bad, [])

    def test_thermal_sticky_ok_still_fails_undervoltage_and_current_bits(self):
        for bits in ("0x50000", "0xe0002", "0x80008"):
            with self.subTest(bits=bits):
                bad = run(*facts({("bath0", "throttled"): bits}), thermal_sticky_ok=frozenset({"bath0"}))
                self.assertTrue(any(b.startswith("bath0:") and "throttle" in b for b in bad), bad)

    def test_thermal_sticky_ok_without_the_parameter_keeps_todays_rule(self):
        bad = run(*facts({("bath0", "throttled"): "0xe0000"}))
        self.assertTrue(any(b.startswith("bath0:") and "throttle" in b for b in bad), bad)

    def test_run_preflight_zksync_env_applies_the_zksync_rule_and_the_thermal_sticky_rule(self):
        base = tempfile.mkdtemp()
        fd = write_facts_dir(base, {("bath0", "throttled"): "0xe0000"})
        patch_fact(fd, "bath0", zksync_server_up=1, containers=["thesis_local-reth-1", "thesis_local-postgres-1"])
        open(os.path.join(base, "experiment.env"), "w").write("STACK=zksync\nTHERMAL_STICKY_OK=bath0\n")
        self.assertEqual(preflight.run_preflight(fd), [])
        # the zkSync rule really is wired in: break it (server down) and it is caught
        patch_fact(fd, "bath0", zksync_server_up=0)
        bad = preflight.run_preflight(fd)
        self.assertTrue(any(b.startswith("bath0:") and "zkSync server" in b for b in bad), bad)

    def test_run_preflight_thermal_sticky_rule_needs_the_env_flag(self):
        base = tempfile.mkdtemp()
        fd = write_facts_dir(base, {("bath0", "throttled"): "0xe0000"})
        patch_fact(fd, "bath0", zksync_server_up=1, containers=["thesis_local-reth-1", "thesis_local-postgres-1"])
        open(os.path.join(base, "experiment.env"), "w").write("STACK=zksync\n")   # no THERMAL_STICKY_OK
        bad = preflight.run_preflight(fd)
        self.assertTrue(any(b.startswith("bath0:") and "throttle" in b for b in bad), bad)

    def test_run_preflight_arbitrum_env_fails_when_zksync_server_is_up(self):
        base = tempfile.mkdtemp()
        fd = write_facts_dir(base)
        patch_fact(fd, "bath0", zksync_server_up=1)
        open(os.path.join(base, "experiment.env"), "w").write("STACK=arbitrum\n")
        bad = preflight.run_preflight(fd)
        self.assertTrue(any(b.startswith("bath0:") and "zkSync server" in b for b in bad), bad)

    def test_run_preflight_no_env_file_keeps_todays_behaviour(self):
        base = tempfile.mkdtemp()
        fd = write_facts_dir(base)                 # no experiment.env written at all
        self.assertEqual(preflight.run_preflight(fd), [])

    def test_parse_env_skips_comments_and_blanks(self):
        env = preflight.parse_env("# comment\nSTACK=zksync\n\nTHERMAL_STICKY_OK=bath0\n")
        self.assertEqual(env, {"STACK": "zksync", "THERMAL_STICKY_OK": "bath0"})

    def test_parse_env_strips_quotes_bash_needs_for_a_value_with_spaces(self):
        env = preflight.parse_env('WIFI_5GHZ_BOARDS="bath0 bath1 bath2"\n')
        self.assertEqual(env, {"WIFI_5GHZ_BOARDS": "bath0 bath1 bath2"})

    def test_wifi_5ghz_boards_listed_board_on_97_at_5ghz_passes(self):
        bad = run(*facts({("bath0", "bssid"): "02:00:00:00:00:02", ("bath0", "freq_mhz"): 5180}),
                  wifi_5ghz_boards=frozenset({"bath0"}))
        self.assertEqual(bad, [])

    def test_wifi_5ghz_boards_listed_board_still_on_96_fails(self):
        bad = run(*facts(), wifi_5ghz_boards=frozenset({"bath0"}))
        self.assertTrue(any(b.startswith("bath0:") and "02:00:00:00:00:02" in b for b in bad), bad)

    def test_wifi_5ghz_boards_unlisted_board_on_97_fails(self):
        bad = run(*facts({("bath0", "bssid"): "02:00:00:00:00:02", ("bath0", "freq_mhz"): 5180}))
        self.assertTrue(any(b.startswith("bath0:") for b in bad), bad)

    def test_wifi_5ghz_boards_unset_keeps_todays_rule(self):
        self.assertEqual(run(*facts()), [])
        bad = run(*facts({("bath0", "bssid"): "02:00:00:00:00:02", ("bath0", "freq_mhz"): 5180}))
        self.assertTrue(any(b.startswith("bath0:") for b in bad), bad)

    def test_run_preflight_wifi_5ghz_boards_env_applies_the_per_board_bssid_rule(self):
        base = tempfile.mkdtemp()
        fd = write_facts_dir(base, {("bath0", "bssid"): "02:00:00:00:00:02", ("bath0", "freq_mhz"): 5180,
                                     ("bath1", "bssid"): "02:00:00:00:00:02", ("bath1", "freq_mhz"): 5180,
                                     ("bath2", "bssid"): "02:00:00:00:00:02", ("bath2", "freq_mhz"): 5180})
        open(os.path.join(base, "experiment.env"), "w").write(
            'STACK=arbitrum\nWIFI_5GHZ_BOARDS="bath0 bath1 bath2"\n')
        self.assertEqual(preflight.run_preflight(fd), [])
        # an unlisted board moved to 5 GHz still fails
        patch_fact(fd, "bath3", bssid="02:00:00:00:00:02", freq_mhz=5180)
        bad = preflight.run_preflight(fd)
        self.assertTrue(any(b.startswith("bath3:") for b in bad), bad)

    def test_idle_boards_are_not_required(self):
        """R9: IDLE_BOARDS in experiment.env drops those boards from the register; the
        remaining 7 must answer, and the idle one is not "unreachable"."""
        base = tempfile.mkdtemp()
        fd = write_facts_dir(base)
        os.remove(os.path.join(fd, "bath3.json"))
        env = os.path.join(base, "experiment.env")
        open(env, "w").write('USE_CASE=uc2\nIDLE_BOARDS="bath3"\n')
        self.assertEqual(preflight.run_preflight(fd), [])
        open(env, "w").write("USE_CASE=uc2\n")
        self.assertIn("bath3: unreachable", preflight.run_preflight(fd))
        # an active board still counts: idling bath3 does not excuse a missing bath4
        open(env, "w").write('USE_CASE=uc2\nIDLE_BOARDS="bath3"\n')
        os.remove(os.path.join(fd, "bath4.json"))
        self.assertIn("bath4: unreachable", preflight.run_preflight(fd))

    def test_read_env_missing_file_is_empty(self):
        self.assertEqual(preflight.read_env("/nonexistent/experiment.env"), {})

    def test_load_facts_reports_garbage_and_keeps_others(self):
        f, reg = facts()
        with tempfile.TemporaryDirectory() as d:
            import json
            for h, v in f.items():
                with open(os.path.join(d, h + ".json"), "w") as fh:
                    if h == "bath5":
                        fh.write("ssh: connect to host bath5 port 22: No route to host")
                    elif h == "bath6":
                        fh.write("[1, 2]")
                    else:
                        json.dump(v, fh)
            loaded, errors = preflight.load_facts(d)
        self.assertEqual(sorted(loaded), ["bath0", "bath1", "bath2", "bath3", "bath4", "bath7"])
        self.assertEqual(len(errors), 2)
        self.assertTrue(errors[0].startswith("bath5: facts unreadable ("), errors)
        self.assertEqual(errors[1], "bath6: facts unreadable (not a JSON object)")
        # the readable hosts are still checked: break one and see it reported
        loaded["bath3"]["netem"] = True
        bad = run(loaded, reg)
        self.assertIn("bath3: netem delay present", bad)


if __name__ == "__main__":
    unittest.main()


class TestClusterCompleteWait(unittest.TestCase):
    """Final review Minor 4: deploy/cluster's wait after the drain must cover the env's
    CLOSE_TIMEOUT_S (zkSync 1800 s); Arbitrum envs set no CLOSE_TIMEOUT_S and get 400 s: Close's fixed
    300 s receipt wait + the 30 s admission wait + the 30 s post-Close settle + RPC retries (fix-close-nonce)."""

    def extra(self, env):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script = ('eval "$(sed -n \'/^CLOSE_MARGIN_S=/p; /^COMPLETE_EXTRA_S=/p; /^complete_extra_s()/,/^}/p\' '
                  f'{root}/deploy/cluster)"; {env} complete_extra_s')
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10,
                             env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual(out.returncode, 0, out.stderr)
        return int(out.stdout.strip())

    def test_default_without_close_timeout_is_unchanged(self):
        self.assertEqual(self.extra(""), 400)

    def test_close_timeout_is_covered(self):
        self.assertGreaterEqual(self.extra("CLOSE_TIMEOUT_S=1800;"), 1800 + 120)
        self.assertGreaterEqual(self.extra("CLOSE_TIMEOUT_S=1800;"), 1800 + 30 + 30 + 60)   # + both new waits

    def test_larger_complete_extra_wins(self):
        self.assertEqual(self.extra("COMPLETE_EXTRA_S=5000; CLOSE_TIMEOUT_S=1800;"), 5000)

    def test_every_env_file(self):
        """The real env files: Arbitrum 400 s, zkSync >= its CLOSE_TIMEOUT_S + margin."""
        import glob
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for p in sorted(glob.glob(os.path.join(root, "deploy", "experiments", "uc1-*.env"))):
            with open(p) as fh:
                env = preflight.parse_env(fh.read())
            got = self.extra(f"source {p};")
            if "CLOSE_TIMEOUT_S" in env:
                self.assertGreaterEqual(got, int(env["CLOSE_TIMEOUT_S"]) + 120, p)
            else:
                self.assertEqual(got, 400, p)
