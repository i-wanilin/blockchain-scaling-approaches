package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	ethwallet "github.com/perun-network/perun-eth-backend/wallet"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/wire"
	"perun.network/perun-examples/hub-demo/lanwire"
)

// TestNetworkBusRoundTrip proves that the distributed transport is real TCP,
// not a LocalBus. It needs neither Anvil nor external processes.
func TestNetworkBusRoundTrip(t *testing.T) {
	id1, err := lanwire.NewEphemeral("one")
	if err != nil {
		t.Fatal(err)
	}
	id2, err := lanwire.NewEphemeral("two")
	if err != nil {
		t.Fatal(err)
	}
	p1, p2 := freePort(t), freePort(t)
	rt1, err := newNetworkRuntime(id1, fmt.Sprintf("127.0.0.1:%d", p1))
	if err != nil {
		t.Fatal(err)
	}
	defer rt1.close()
	rt2, err := newNetworkRuntime(id2, fmt.Sprintf("127.0.0.1:%d", p2))
	if err != nil {
		t.Fatal(err)
	}
	defer rt2.close()
	a1 := map[wallet.BackendID]wire.Address{ethwallet.BackendID: id1.Address}
	a2 := map[wallet.BackendID]wire.Address{ethwallet.BackendID: id2.Address}
	rt1.dialer.Register(a2, fmt.Sprintf("127.0.0.1:%d", p2))
	recv := wire.NewReceiver()
	if err := rt2.bus.SubscribeClient(recv, a2); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := rt1.bus.Publish(ctx, &wire.Envelope{Sender: a1, Recipient: a2, Msg: wire.NewPingMsg()}); err != nil {
		t.Fatal(err)
	}
	got, err := recv.Next(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := got.Msg.(*wire.PingMsg); !ok {
		t.Fatalf("got %T, want PingMsg", got.Msg)
	}
}

func TestLoadClusterConfigDefaults(t *testing.T) {
	cfg := clusterConfig{
		ChainURL: "ws://127.0.0.1:8545", ChainID: 31337,
		Hub: clusterNode{Name: "ingrid", Bind: "0.0.0.0:9000", Endpoint: "127.0.0.1:9000", Control: "0.0.0.0:8080"},
		Clients: []clusterNode{
			{Name: "alice", Bind: "0.0.0.0:9000", Endpoint: "127.0.0.1:9000", Control: "http://127.0.0.1:8080"},
			{Name: "bob", Bind: "0.0.0.0:9000", Endpoint: "127.0.0.1:9000", Control: "http://127.0.0.1:8080"},
		},
	}
	data, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "cluster.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := loadClusterConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.StartupTimeoutSec != 300 || got.OperationTimeoutSec != 180 || got.ResultDir != "results" {
		t.Fatalf("defaults not applied: %+v", got)
	}
}

func TestHubControlUnregisterRemovesStalePeer(t *testing.T) {
	control := &hubControl{
		cfg: clusterConfig{Clients: []clusterNode{{Name: "alice"}, {Name: "bob"}}},
		peers: map[string]peerInfo{
			"bob": {Name: "bob", Endpoint: "127.0.0.1:9000"},
		},
		virtualSettled:       map[string]bool{"bob": true},
		clientLedgersSettled: map[string]bool{"bob": true},
		hubLedgersSettled:    map[string]bool{"bob": true},
	}
	body, _ := json.Marshal(nodeMark{Name: "bob"})
	req := httptest.NewRequest(http.MethodPost, "/v1/unregister", bytes.NewReader(body))
	resp := httptest.NewRecorder()
	control.serve().ServeHTTP(resp, req)
	if resp.Code != http.StatusNoContent {
		t.Fatalf("status=%d body=%s", resp.Code, resp.Body.String())
	}
	if len(control.peers) != 0 || len(control.virtualSettled) != 0 ||
		len(control.clientLedgersSettled) != 0 || len(control.hubLedgersSettled) != 0 {
		t.Fatalf("stale state remained: %+v", control)
	}
}

// TestDistributedThreeProcess is intentionally opt-in: it deploys contracts
// and opens real ledger channels on a local Anvil instance. It proves that the
// hub, Alice and Bob are independent OS processes connected by the TCP bus.
func TestDistributedThreeProcess(t *testing.T) { runDistributedThreeProcess(t, "eth") }

// TestDistributedThreeProcessPoints is the same lifecycle on the points token:
// every ledger deposit is approve + deposit on the ERC-20 asset holder.
func TestDistributedThreeProcessPoints(t *testing.T) { runDistributedThreeProcess(t, "points") }

func runDistributedThreeProcess(t *testing.T, asset string) {
	if os.Getenv("RUN_DISTRIBUTED_TEST") != "1" {
		t.Skip("set RUN_DISTRIBUTED_TEST=1 with Anvil running to exercise three processes")
	}
	url := anvilURL()
	skipIfNoAnvil(t, url)

	bin := filepath.Join(t.TempDir(), "perun-node")
	if runtime.GOOS == "windows" {
		bin += ".exe"
	}
	build := exec.Command("go", "build", "-o", bin, ".")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build test binary: %v\n%s", err, out)
	}

	hubPort, controlPort, alicePort, bobPort := freePort(t), freePort(t), freePort(t), freePort(t)
	state := t.TempDir()
	cfg := clusterConfig{
		ChainURL: url, ChainID: 31337, StateRoot: state, ResultDir: filepath.Join(state, "results"),
		StartupTimeoutSec: 15, OperationTimeoutSec: 60, Asset: asset,
		Hub: clusterNode{Name: "ingrid", Bind: fmt.Sprintf("127.0.0.1:%d", hubPort), Endpoint: fmt.Sprintf("127.0.0.1:%d", hubPort), Control: fmt.Sprintf("127.0.0.1:%d", controlPort)},
		Clients: []clusterNode{
			{Name: "alice", Bind: fmt.Sprintf("127.0.0.1:%d", alicePort), Endpoint: fmt.Sprintf("127.0.0.1:%d", alicePort), Control: fmt.Sprintf("http://127.0.0.1:%d", controlPort)},
			{Name: "bob", Bind: fmt.Sprintf("127.0.0.1:%d", bobPort), Endpoint: fmt.Sprintf("127.0.0.1:%d", bobPort), Control: fmt.Sprintf("http://127.0.0.1:%d", controlPort)},
		},
	}
	configPath := filepath.Join(state, "cluster.json")
	b, _ := json.Marshal(cfg)
	if err := os.WriteFile(configPath, b, 0600); err != nil {
		t.Fatal(err)
	}

	hub := exec.Command(bin, "-mode", "hub", "-config", configPath)
	var hubLog bytes.Buffer
	hub.Stdout, hub.Stderr = &hubLog, &hubLog
	if err := hub.Start(); err != nil {
		t.Fatal(err)
	}
	hubWaited := false
	t.Cleanup(func() {
		if !hubWaited {
			_ = hub.Process.Kill()
			_ = hub.Wait()
		}
	})
	if err := waitForHTTP(fmt.Sprintf("http://127.0.0.1:%d/v1/manifest", controlPort), 15*time.Second); err != nil {
		t.Fatalf("hub did not start: %v\n%s", err, hubLog.String())
	}

	bob := exec.Command(bin, "-mode", "client", "-node", "bob", "-config", configPath, "-payments", "0")
	var bobLog bytes.Buffer
	bob.Stdout, bob.Stderr = &bobLog, &bobLog
	if err := bob.Start(); err != nil {
		t.Fatal(err)
	}
	// Prove that clients no longer need to be launched in the same few-second
	// window. Bob remains registered while the operator reaches Alice.
	time.Sleep(2 * time.Second)
	alice := exec.Command(bin, "-mode", "client", "-node", "alice", "-config", configPath, "-payments", "5")
	var aliceLog bytes.Buffer
	alice.Stdout, alice.Stderr = &aliceLog, &aliceLog
	if err := alice.Start(); err != nil {
		t.Fatal(err)
	}

	done := make(chan error, 2)
	go func() { done <- alice.Wait() }()
	go func() { done <- bob.Wait() }()
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	for range 2 {
		select {
		case err := <-done:
			if err != nil {
				t.Fatalf("distributed client failed: %v\nalice:\n%s\nbob:\n%s\nhub:\n%s", err, aliceLog.String(), bobLog.String(), hubLog.String())
			}
		case <-ctx.Done():
			t.Fatalf("distributed clients timed out\nalice:\n%s\nbob:\n%s\nhub:\n%s", aliceLog.String(), bobLog.String(), hubLog.String())
		}
	}
	if err := hub.Wait(); err != nil {
		t.Fatalf("hub did not exit cleanly: %v\n%s", err, hubLog.String())
	}
	hubWaited = true
	if !strings.Contains(hubLog.String(), "hub complete:") {
		t.Fatalf("hub did not report graceful completion:\n%s", hubLog.String())
	}
	if strings.Contains(hubLog.String(), `level=error`) {
		t.Fatalf("hub emitted an error during a successful lifecycle:\n%s", hubLog.String())
	}
	if !bytes.Contains(aliceLog.Bytes(), []byte("distributed payments: 5")) {
		t.Fatalf("missing payment result:\n%s", aliceLog.String())
	}
	for name, log := range map[string]string{"alice": aliceLog.String(), "bob": bobLog.String()} {
		if !strings.Contains(log, "client complete:") {
			t.Fatalf("%s did not complete the graceful lifecycle:\n%s", name, log)
		}
		for _, forbidden := range []string{"sub-channels present", "timed out waiting", `level=error`} {
			if strings.Contains(log, forbidden) {
				t.Fatalf("%s emitted %q during a successful lifecycle:\n%s", name, forbidden, log)
			}
		}
		matches, err := filepath.Glob(filepath.Join(cfg.ResultDir, "*-"+name+".json"))
		if err != nil || len(matches) != 1 {
			t.Fatalf("%s result file count = %d, error = %v", name, len(matches), err)
		}
		checkLedgerGas(t, name, matches[0], asset)
	}
	for _, line := range strings.Split(hubLog.String()+aliceLog.String()+bobLog.String(), "\n") {
		if strings.Contains(line, " gas: ") {
			t.Log(line)
		}
	}
}

// checkLedgerGas reads one client's result file and checks the recorded
// on-chain cost of its ledger channel: in points mode Open is one approve on
// the token plus one deposit on the asset holder, in ETH mode one deposit.
// Close is the client's conclude plus its withdrawal in both modes.
func checkLedgerGas(t *testing.T, name, path, asset string) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var out distributedResultFile
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out.Asset != asset {
		t.Fatalf("%s result asset = %q, want %q", name, out.Asset, asset)
	}
	open, settle := out.LedgerOpenGas, out.LedgerSettleGas
	if open == nil || settle == nil || open.Gas == 0 || settle.Gas == 0 {
		t.Fatalf("%s: ledger gas not recorded: open=%v settle=%v", name, open, settle)
	}
	wantOpen := map[string]int{"asset holder": 1}
	if asset == "points" {
		wantOpen["token"] = 1
	}
	if len(open.ByContract) != len(wantOpen) || open.Txs != len(wantOpen) {
		t.Fatalf("%s: open txs %v, want one per contract in %v", name, open, wantOpen)
	}
	for contract, txs := range wantOpen {
		if got := open.ByContract[contract]; got.Txs != txs || got.Gas == 0 {
			t.Fatalf("%s: open on %s = %+v, want %d tx with gas", name, contract, got, txs)
		}
	}
	if w := settle.ByContract["asset holder"]; w.Txs != 1 {
		t.Fatalf("%s: settle %v, want one withdrawal", name, settle)
	}
}

// resultPath returns the single result file a node wrote into dir.
func resultPath(t *testing.T, dir, name string) string {
	t.Helper()
	matches, err := filepath.Glob(filepath.Join(dir, "*-"+name+".json"))
	if err != nil || len(matches) != 1 {
		t.Fatalf("%s: %d result files (%v)", name, len(matches), err)
	}
	return matches[0]
}

func freePort(t *testing.T) int {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}
func waitForHTTP(url string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if r, err := http.Get(url); err == nil {
			_ = r.Body.Close()
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("timed out waiting for %s", url)
}
