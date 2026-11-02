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
)

func TestSelectTraceEventsKeepsOnlyOwnPayments(t *testing.T) {
	events := []tracePayment{
		{kind: "hit", from: 0, to: 1, amount: 5}, {kind: "hit", from: 1, to: 0, amount: 5},
		{kind: "fire", from: 2, to: hubTarget, amount: 1}, {kind: "hit", from: 1, to: 2, amount: 5},
	}
	mine, err := selectTraceEvents(events, 3, 1, 3)
	if err != nil {
		t.Fatalf("a many-to-many trace must be accepted: %v", err)
	}
	if len(mine) != 2 || mine[0].to != 0 || mine[1].to != 2 {
		t.Fatalf("player 1 must get exactly its own two payments, got %+v", mine)
	}
	if _, err := selectTraceEvents(events, 3, 0, 2); err == nil {
		t.Fatal("a trace needing 3 players must be rejected on a 2-client cluster")
	}
	// A player who never pays is legitimate; it still receives.
	if mine, err := selectTraceEvents(events[:1], 3, 2, 3); err != nil || len(mine) != 0 {
		t.Fatalf("silent player: got %v, %v", mine, err)
	}
}

func TestClusterConfigRejectsOvercommittedStake(t *testing.T) {
	cfg := clusterConfig{LedgerDeposit: 40, VirtualStake: 10, Clients: make([]clusterNode, 6)}
	if err := cfg.validateMeshStake(); err == nil {
		t.Fatal("5 virtual channels x 10 stake cannot fit a 40 deposit")
	}
	cfg.LedgerDeposit = 50
	if err := cfg.validateMeshStake(); err != nil {
		t.Fatalf("5 x 10 fits 50: %v", err)
	}
}

// TestDistributedMeshSixProcess is UC2's shape: six independent client
// processes, a full mesh of 15 virtual channels, and a trace in which every
// player pays the arena and every player pays every other player in BOTH
// directions. Each process replays only its own payments.
func TestDistributedMeshSixProcess(t *testing.T) { runDistributedMesh(t, "eth") }

// TestDistributedMeshSixProcessPoints is UC2's shape on UC2's asset: fire = 1
// point to the arena, hit = 5 points, all on the points token.
func TestDistributedMeshSixProcessPoints(t *testing.T) { runDistributedMesh(t, "points") }

func runDistributedMesh(t *testing.T, asset string) {
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

	const players, rounds = 6, 4
	state := t.TempDir()

	// Every round: each player fires once, then every ordered pair (i, j)
	// exchanges a hit. Opposite-direction hits share a timestamp on purpose.
	var trace bytes.Buffer
	for r := 0; r < rounds; r++ {
		tms := float64(r * 100)
		for i := 0; i < players; i++ {
			fmt.Fprintf(&trace, `{"type":"fire","t_ms":%g,"payload":{"from":%d,"to":"arena","amount":1}}`+"\n", tms, i)
		}
		for i := 0; i < players; i++ {
			for j := 0; j < players; j++ {
				if i != j {
					fmt.Fprintf(&trace, `{"type":"hit","t_ms":%g,"payload":{"from":%d,"to":%d,"amount":5}}`+"\n", tms+10, i, j)
				}
			}
		}
	}
	tracePath := filepath.Join(state, "mesh.jsonl")
	if err := os.WriteFile(tracePath, trace.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}

	hubPort, controlPort := freePort(t), freePort(t)
	ctl := fmt.Sprintf("http://127.0.0.1:%d", controlPort)
	cfg := clusterConfig{
		ChainURL: url, ChainID: 31337, StateRoot: state, ResultDir: filepath.Join(state, "results"),
		StartupTimeoutSec: 60, OperationTimeoutSec: 120, Asset: asset,
		YieldTimeoutMS: 40, UpdateTimeoutSec: 20, MaxCollisionRetries: 10000,
		Hub: clusterNode{Name: "ingrid", Bind: fmt.Sprintf("127.0.0.1:%d", hubPort),
			Endpoint: fmt.Sprintf("127.0.0.1:%d", hubPort), Control: fmt.Sprintf("127.0.0.1:%d", controlPort)},
	}
	for i := 0; i < players; i++ {
		ep := fmt.Sprintf("127.0.0.1:%d", freePort(t))
		cfg.Clients = append(cfg.Clients, clusterNode{Name: strings.ToLower(clientNames[i]), Bind: ep, Endpoint: ep, Control: ctl})
	}
	configPath := filepath.Join(state, "cluster.json")
	b, _ := json.Marshal(cfg)
	if err := os.WriteFile(configPath, b, 0o600); err != nil {
		t.Fatal(err)
	}

	logs := map[string]*bytes.Buffer{}
	start := func(label string, args ...string) *exec.Cmd {
		cmd := exec.Command(bin, args...)
		logs[label] = &bytes.Buffer{}
		cmd.Stdout, cmd.Stderr = logs[label], logs[label]
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		return cmd
	}
	dump := func() string {
		var sb strings.Builder
		for label, log := range logs {
			fmt.Fprintf(&sb, "---- %s ----\n%s\n", label, log.String())
		}
		return sb.String()
	}
	hub := start("hub", "-mode", "hub", "-config", configPath)
	t.Cleanup(func() { _ = hub.Process.Kill() })
	if err := waitForHTTP(ctl+"/v1/manifest", 15*time.Second); err != nil {
		t.Fatalf("hub did not start: %v\n%s", err, dump())
	}
	done := make(chan error, players)
	for _, node := range cfg.Clients {
		cmd := start(node.Name, "-mode", "client", "-node", node.Name, "-config", configPath, "-trace", tracePath)
		t.Cleanup(func() { _ = cmd.Process.Kill() })
		go func() { done <- cmd.Wait() }()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 400*time.Second)
	defer cancel()
	for range players {
		select {
		case err := <-done:
			if err != nil {
				t.Fatalf("client failed: %v\n%s", err, dump())
			}
		case <-ctx.Done():
			t.Fatalf("mesh timed out\n%s", dump())
		}
	}

	type end struct {
		node string
		channelFinal
	}
	ends := map[string][]end{}
	totalCollisions := 0
	for _, node := range cfg.Clients {
		matches, _ := filepath.Glob(filepath.Join(cfg.ResultDir, "*-"+node.Name+".json"))
		if len(matches) != 1 {
			t.Fatalf("%s: %d result files\n%s", node.Name, len(matches), dump())
		}
		raw, _ := os.ReadFile(matches[0])
		var out distributedResultFile
		if err := json.Unmarshal(raw, &out); err != nil {
			t.Fatal(err)
		}
		if want := rounds * players; out.Trace == nil || out.Trace.OK != want || out.Trace.Failed != 0 {
			t.Fatalf("%s: trace %+v, want %d ok and 0 failed\n%s", node.Name, out.Trace, want, logs[node.Name].String())
		}
		if len(out.VirtualFinal) != players-1 {
			t.Fatalf("%s holds %d virtual channels, want %d", node.Name, len(out.VirtualFinal), players-1)
		}
		for _, f := range out.VirtualFinal {
			ends[f.Channel] = append(ends[f.Channel], end{node.Name, f})
		}
		totalCollisions += out.Collisions
	}
	if want := players * (players - 1) / 2; len(ends) != want {
		t.Fatalf("%d distinct virtual channels, want %d", len(ends), want)
	}
	for id, pair := range ends {
		if len(pair) != 2 {
			t.Fatalf("channel %s seen by %d nodes, want 2", id[:8], len(pair))
		}
		a, b := pair[0], pair[1]
		if a.Version != b.Version || strings.Join(a.Balances, ",") != strings.Join(b.Balances, ",") {
			t.Fatalf("channel %s DISAGREES: %s v%d %v vs %s v%d %v", id[:8], a.node, a.Version, a.Balances, b.node, b.Version, b.Balances)
		}
		if a.Version != 2*rounds {
			t.Fatalf("channel %s (%s-%s) at version %d, want %d", id[:8], a.node, b.node, a.Version, 2*rounds)
		}
		// Every pair traded the same hits both ways, so each channel ends where
		// it began. In points mode that is the stake as a plain point count.
		if asset == "points" {
			stake := fmt.Sprint(assetPoints.defaultVirtualStake())
			if a.Balances[0] != stake || a.Balances[1] != stake {
				t.Fatalf("channel %s balances %v, want %s points each", id[:8], a.Balances, stake)
			}
		}
	}
	for _, node := range cfg.Clients {
		checkLedgerGas(t, node.Name, resultPath(t, cfg.ResultDir, node.Name), asset)
	}
	t.Logf("6 clients, %d channels, %d payments, %d give-ways", len(ends), rounds*players*players, totalCollisions)
}

// Both ends of a virtual channel must settle it at the same time: go-perun's hub
// waits only 10 s for the matching proposal. So every round of the schedule has
// to be a set of disjoint pairs, and every pair has to meet exactly once.
func TestSettleScheduleIsARoundRobin(t *testing.T) {
	for n := 2; n <= 7; n++ {
		met := map[[2]int]int{}
		for r := 0; r < settleRounds(n); r++ {
			for me := 0; me < n; me++ {
				p := settlePartner(n, r, me)
				if p == -1 {
					continue // odd n: one client sits the round out
				}
				if p < 0 || p >= n || p == me {
					t.Fatalf("n=%d r=%d: client %d got partner %d", n, r, me, p)
				}
				if back := settlePartner(n, r, p); back != me {
					t.Fatalf("n=%d r=%d: %d->%d but %d->%d", n, r, me, p, p, back)
				}
				if me < p {
					met[[2]int{me, p}]++
				}
			}
		}
		if len(met) != n*(n-1)/2 {
			t.Fatalf("n=%d: %d pairs met, want %d", n, len(met), n*(n-1)/2)
		}
		for pair, times := range met {
			if times != 1 {
				t.Fatalf("n=%d: pair %v met %d times", n, pair, times)
			}
		}
	}
}
