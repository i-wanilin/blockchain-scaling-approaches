package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"perun.network/go-perun/channel"
)

func TestUpdatePolicyRoles(t *testing.T) {
	p := updatePolicy{Yield: 50 * time.Millisecond, Long: 10 * time.Second, MaxRetries: 3}
	if got := p.timeoutFor(channel.Index(0)); got != p.Long {
		t.Fatalf("index 0 has priority and must wait long, got %v", got)
	}
	if got := p.timeoutFor(channel.Index(1)); got != p.Yield {
		t.Fatalf("index 1 must give way after the yield timeout, got %v", got)
	}
}

func TestLoadClusterConfigUpdatePolicyDefaults(t *testing.T) {
	cfg := clusterConfig{}
	cfg.applyUpdatePolicyDefaults()
	if cfg.YieldTimeoutMS != 1000 || cfg.UpdateTimeoutSec != 10 || cfg.MaxCollisionRetries != 10 {
		t.Fatalf("defaults not applied: %+v", cfg)
	}
	p := cfg.updatePolicy()
	// The two timeouts must never be able to fire together: that is what forks
	// a channel. Refuse any configuration that lets them get close.
	if p.Long < 4*p.Yield {
		t.Fatalf("long timeout %v too close to yield %v", p.Long, p.Yield)
	}
	bad := clusterConfig{YieldTimeoutMS: 6000, UpdateTimeoutSec: 10}
	if err := bad.validateUpdatePolicy(); err == nil {
		t.Fatal("yield 6s vs long 10s must be rejected")
	}
}

// TestDistributedHeadOnCollision makes both ends of ONE virtual channel pay each
// other in tight closed loops, so simultaneous proposals are certain. go-perun
// allows one update in flight per channel and does not resolve a simultaneous
// proposal: unguarded, both ends wait forever; with equal timeouts both abandon
// their proposal and the channel stalls within a few payments. The asymmetric
// policy must prevent both: every payment completes, both ends finish on the
// identical version and balances, and settlement works.
func TestDistributedHeadOnCollision(t *testing.T) { runDistributedHeadOn(t, "eth") }

// TestDistributedHeadOnCollisionPoints is the same collision workload with one
// point per payment on the points token.
func TestDistributedHeadOnCollisionPoints(t *testing.T) { runDistributedHeadOn(t, "points") }

func runDistributedHeadOn(t *testing.T, asset string) {
	if os.Getenv("RUN_DISTRIBUTED_TEST") != "1" {
		t.Skip("set RUN_DISTRIBUTED_TEST=1 with Anvil running")
	}
	url := anvilURL()
	skipIfNoAnvil(t, url)

	bin := filepath.Join(t.TempDir(), "perun-node")
	if runtime.GOOS == "windows" {
		bin += ".exe"
	}
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("build: %v\n%s", err, out)
	}

	const each = 150
	hubPort, controlPort, alicePort, bobPort := freePort(t), freePort(t), freePort(t), freePort(t)
	state := t.TempDir()
	node := func(name string, port int, control string) clusterNode {
		ep := fmt.Sprintf("127.0.0.1:%d", port)
		return clusterNode{Name: name, Bind: ep, Endpoint: ep, Control: control}
	}
	ctl := fmt.Sprintf("http://127.0.0.1:%d", controlPort)
	cfg := clusterConfig{
		ChainURL: url, ChainID: 31337, StateRoot: state, ResultDir: filepath.Join(state, "results"),
		StartupTimeoutSec: 15, OperationTimeoutSec: 60, Asset: asset,
		// Loopback round trips are well under 1 ms, so a 40 ms yield keeps the
		// test quick without ever mistaking a slow round trip for a collision.
		YieldTimeoutMS: 40, UpdateTimeoutSec: 10, MaxCollisionRetries: 10000,
		Hub:     node("ingrid", hubPort, fmt.Sprintf("127.0.0.1:%d", controlPort)),
		Clients: []clusterNode{node("alice", alicePort, ctl), node("bob", bobPort, ctl)},
	}
	configPath := filepath.Join(state, "cluster.json")
	b, _ := json.Marshal(cfg)
	if err := os.WriteFile(configPath, b, 0o600); err != nil {
		t.Fatal(err)
	}

	start := func(args ...string) (*exec.Cmd, *bytes.Buffer) {
		cmd := exec.Command(bin, args...)
		var log bytes.Buffer
		cmd.Stdout, cmd.Stderr = &log, &log
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		return cmd, &log
	}
	hub, hubLog := start("-mode", "hub", "-config", configPath)
	t.Cleanup(func() { _ = hub.Process.Kill() })
	if err := waitForHTTP(ctl+"/v1/manifest", 15*time.Second); err != nil {
		t.Fatalf("hub did not start: %v\n%s", err, hubLog.String())
	}
	n := fmt.Sprint(each)
	bob, bobLog := start("-mode", "client", "-node", "bob", "-config", configPath, "-payments", n)
	alice, aliceLog := start("-mode", "client", "-node", "alice", "-config", configPath, "-payments", n)

	done := make(chan error, 2)
	go func() { done <- alice.Wait() }()
	go func() { done <- bob.Wait() }()
	ctx, cancel := context.WithTimeout(context.Background(), 240*time.Second)
	defer cancel()
	dump := func() string {
		return fmt.Sprintf("alice:\n%s\nbob:\n%s\nhub:\n%s", aliceLog.String(), bobLog.String(), hubLog.String())
	}
	for range 2 {
		select {
		case err := <-done:
			if err != nil {
				t.Fatalf("client failed: %v\n%s", err, dump())
			}
		case <-ctx.Done():
			t.Fatalf("clients hung — the unguarded collision deadlock\n%s", dump())
		}
	}

	read := func(name string) distributedResultFile {
		matches, _ := filepath.Glob(filepath.Join(cfg.ResultDir, "*-"+name+".json"))
		if len(matches) != 1 {
			t.Fatalf("%s: %d result files\n%s", name, len(matches), dump())
		}
		raw, err := os.ReadFile(matches[0])
		if err != nil {
			t.Fatal(err)
		}
		var out distributedResultFile
		if err := json.Unmarshal(raw, &out); err != nil {
			t.Fatal(err)
		}
		return out
	}
	a, bo := read("alice"), read("bob")
	if a.Payments != each || bo.Payments != each {
		t.Fatalf("payments alice=%d bob=%d, want %d each\n%s", a.Payments, bo.Payments, each, dump())
	}
	if len(a.VirtualFinal) != 1 || len(bo.VirtualFinal) != 1 {
		t.Fatalf("expected one virtual channel each: %+v / %+v", a.VirtualFinal, bo.VirtualFinal)
	}
	af, bf := a.VirtualFinal[0], bo.VirtualFinal[0]
	if af.Version != bf.Version || strings.Join(af.Balances, ",") != strings.Join(bf.Balances, ",") {
		t.Fatalf("CHANNEL FORKED: alice v%d %v, bob v%d %v", af.Version, af.Balances, bf.Version, bf.Balances)
	}
	if af.Version != uint64(2*each) {
		t.Fatalf("final version %d, want %d (one per payment)", af.Version, 2*each)
	}
	if a.Collisions != 0 {
		t.Fatalf("alice is index 0 and must never give way, gave way %d times", a.Collisions)
	}
	if bo.Collisions == 0 {
		t.Fatalf("no collision occurred, so the test proved nothing; tighten the loops")
	}
	if asset == "points" {
		// Equal payments both ways leave both ends where they started: the
		// default stake, in whole points (no 10^18 scaling anywhere).
		stake := fmt.Sprint(assetPoints.defaultVirtualStake())
		if af.Balances[0] != stake || af.Balances[1] != stake {
			t.Fatalf("points balances %v, want %s each", af.Balances, stake)
		}
	}
	for name, path := range map[string]string{"alice": resultPath(t, cfg.ResultDir, "alice"), "bob": resultPath(t, cfg.ResultDir, "bob")} {
		checkLedgerGas(t, name, path, asset)
	}
	t.Logf("%d+%d payments, final version %d, bob gave way %d times", a.Payments, bo.Payments, af.Version, bo.Collisions)
}
