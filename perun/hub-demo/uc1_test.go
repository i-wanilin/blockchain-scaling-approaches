// uc1_test.go — the UC1 direct-channel mode without a chain: protocol, policies, FIFO.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/common"
	ethchannel "github.com/perun-network/perun-eth-backend/channel"
	ethwallet "github.com/perun-network/perun-eth-backend/wallet"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/wire"
	"perun.network/perun-examples/hub-demo/lanwire"
)

// fakeUC1Backend stands in for the go-perun channel: a balance that each
// successful update lowers, and a version that each one raises.
type fakeUC1Backend struct {
	mu       sync.Mutex
	balance  *big.Int
	earned   *big.Int
	version  uint64
	updates  int
	closed   bool
	updateFn func(amount *big.Int) error // optional override
}

func newFakeUC1Backend(deposit int64) *fakeUC1Backend {
	return &fakeUC1Backend{balance: big.NewInt(deposit), earned: new(big.Int)}
}

func (f *fakeUC1Backend) Init(req uc1Request) (string, error) {
	if req.Key == "" {
		return "", fmt.Errorf("missing key")
	}
	return "0x00000000000000000000000000000000000000c1", nil
}
func (f *fakeUC1Backend) Open(deposit *big.Int) (uc1OpenReply, error) {
	f.mu.Lock()
	f.balance = new(big.Int).Set(deposit)
	f.mu.Unlock()
	return uc1OpenReply{TStart: 1, TFunded: 2, ChannelID: "0x01", L1GasUsed: 50000, L1FeeWei: "1", Txs: 1}, nil
}
func (f *fakeUC1Backend) Update(amount *big.Int) (uint64, error) {
	if f.updateFn != nil {
		if err := f.updateFn(amount); err != nil {
			return 0, err
		}
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.updates++
	f.balance.Sub(f.balance, amount)
	f.earned.Add(f.earned, amount)
	f.version++
	return f.version, nil
}
func (f *fakeUC1Backend) State() (uint64, []*big.Int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.version, []*big.Int{new(big.Int).Set(f.balance), new(big.Int).Set(f.earned)}, nil
}
func (f *fakeUC1Backend) Close() (uc1CloseReply, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.closed = true
	f.version++
	return uc1CloseReply{FinalVersion: f.version, FinalBalancesWei: []string{f.balance.String(), f.earned.String()}, Txs: 2}, nil
}
func (f *fakeUC1Backend) Shutdown() {}

// runSidecarLines drives a sidecar on the fake backend one request at a time,
// as a strict request/reply client: send a line, read its reply.
func runSidecarLines(t *testing.T, b uc1Backend, lines ...string) []map[string]any {
	t.Helper()
	inR, inW := io.Pipe()
	outR, outW := io.Pipe()
	done := make(chan error, 1)
	go func() { done <- runUC1Sidecar(inR, outW, b); _ = outW.Close() }()
	sc := bufio.NewScanner(outR)
	var out []map[string]any
	for _, line := range lines {
		if _, err := io.WriteString(inW, line+"\n"); err != nil {
			t.Fatalf("write %q: %v", line, err)
		}
		got := make(chan bool, 1)
		go func() { got <- sc.Scan() }()
		select {
		case ok := <-got:
			if !ok {
				t.Fatalf("no reply to %q", line)
			}
		case <-time.After(10 * time.Second):
			t.Fatalf("no reply to %q within 10 s", line)
		}
		var m map[string]any
		if err := json.Unmarshal(sc.Bytes(), &m); err != nil {
			t.Fatalf("stdout line is not JSON: %q (%v)", sc.Text(), err)
		}
		out = append(out, m)
	}
	_ = inW.Close()
	for sc.Scan() {
		t.Fatalf("unexpected extra stdout line: %q", sc.Text())
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("sidecar: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("sidecar did not exit")
	}
	return out
}

func TestUC1RequestParsing(t *testing.T) {
	b := newFakeUC1Backend(0)
	out := runSidecarLines(t, b,
		`{"op":"init","key":"ab","name":"c1","chain":"ws://x","chain_id":31337,"sensor_control":"http://s","bind":"0.0.0.0:9000","endpoint":"h:9000","state_dir":"/tmp/x","update_timeout_s":10,"challenge_s":10,"persist":true,"startup_timeout_s":120}`,
		`{"op":"open","deposit_wei":"1000000000000000000"}`,
		`{"op":"pay","id":"uc1/r/c1/w1","amount_wei":"1000000000000"}`,
		`{"op":"pay","id":"uc1/r/c1/w2","amount_wei":1000000000000}`, // a number, not a string
		`{"op":"frobnicate"}`,
		`not json at all`,
		`{"op":"pay","id":"uc1/r/c1/w3","amount_wei":"-5"}`,
		`{"op":"state"}`,
		`{"op":"quiesce"}`,
		`{"op":"close"}`,
		`{"op":"exit"}`,
	)
	wantOps := []string{"init", "open", "pay", "pay", "frobnicate", "", "pay", "state", "quiesce", "close", "exit"}
	if len(out) != len(wantOps) {
		t.Fatalf("want %d replies (one per request), got %d: %v", len(wantOps), len(out), out)
	}
	for i, op := range wantOps {
		if out[i]["op"] != op {
			t.Fatalf("reply %d: op %v, want %q (replies must be in request order): %v", i, out[i]["op"], op, out)
		}
	}
	ok := func(i int) bool { v, _ := out[i]["ok"].(bool); return v }
	if !ok(0) || out[0]["address"] == "" {
		t.Fatalf("init: %v", out[0])
	}
	if !ok(1) || out[1]["txs"].(float64) != 1 || out[1]["channel_id"] != "0x01" {
		t.Fatalf("open: %v", out[1])
	}
	if !ok(2) || out[2]["id"] != "uc1/r/c1/w1" || out[2]["version"].(float64) != 1 || out[2]["error"] != "" {
		t.Fatalf("pay w1: %v", out[2])
	}
	if ok(3) || !strings.HasPrefix(out[3]["error"].(string), "rejected:") || out[3]["id"] != "uc1/r/c1/w2" {
		t.Fatalf("a numeric amount must be refused as a failed pay with its id: %v", out[3])
	}
	if ok(4) || out[4]["error"] == "" {
		t.Fatalf("unknown op must get an error reply: %v", out[4])
	}
	if ok(5) || out[5]["error"] == "" {
		t.Fatalf("malformed line must get an error reply: %v", out[5])
	}
	if ok(6) || !strings.HasPrefix(out[6]["error"].(string), "rejected:") {
		t.Fatalf("negative amount must be rejected: %v", out[6])
	}
	if !ok(7) || out[7]["version"].(float64) != 1 || out[7]["broken"] != false {
		t.Fatalf("state: %v", out[7])
	}
	bal := out[7]["balances_wei"].([]any)
	if bal[0] != "999999000000000000" || bal[1] != "1000000000000" {
		t.Fatalf("balances must be decimal strings in wei: %v", bal)
	}
	if !ok(8) || out[8]["dropped"].(float64) != 0 {
		t.Fatalf("quiesce: %v", out[8])
	}
	if !ok(9) || out[9]["final_version"].(float64) != 2 {
		t.Fatalf("close: %v", out[9])
	}
	if !ok(10) {
		t.Fatalf("exit: %v", out[10])
	}
	if b.updates != 1 {
		t.Fatalf("exactly one update must reach the channel, got %d", b.updates)
	}
}

// uc1TestProposal builds the proposal a customer sends: itself index 0 with
// deposit, the sensor index 1 with 0.
func uc1TestProposal(t *testing.T, asset channel.Asset, part common.Address, custWire, sensorWire map[wallet.BackendID]wire.Address, bals []int64, challenge uint64) *client.LedgerChannelProposalMsg {
	t.Helper()
	alloc := channel.NewAllocation(2, []wallet.BackendID{ethwallet.BackendID}, asset)
	balances := make([]channel.Bal, len(bals))
	for i, b := range bals {
		balances[i] = big.NewInt(b)
	}
	alloc.SetAssetBalances(asset, balances)
	prop, err := client.NewLedgerChannelProposal(challenge,
		map[wallet.BackendID]wallet.Address{ethwallet.BackendID: ethwallet.AsWalletAddr(part)},
		alloc, []map[wallet.BackendID]wire.Address{custWire, sensorWire})
	if err != nil {
		t.Fatal(err)
	}
	return prop
}

func uc1TestWire(t *testing.T, name string) map[wallet.BackendID]wire.Address {
	t.Helper()
	id, err := lanwire.NewEphemeral(name)
	if err != nil {
		t.Fatal(err)
	}
	return map[wallet.BackendID]wire.Address{ethwallet.BackendID: id.Address}
}

func TestUC1RetryStopsOnRefusal(t *testing.T) {
	calls := 0
	err := uc1Retry(5*time.Second, "register", func() error {
		calls++
		return fmt.Errorf("http://s/v1/register: 403 Forbidden")
	})
	if err == nil || calls != 1 {
		t.Fatalf("a 4xx refusal must not be retried: %d calls, %v", calls, err)
	}
	calls = 0
	err = uc1Retry(5*time.Second, "manifest", func() error {
		if calls++; calls < 3 {
			return fmt.Errorf("dial tcp: connect: no route to host")
		}
		return nil
	})
	if err != nil || calls != 3 {
		t.Fatalf("a network error must be retried: %d calls, %v", calls, err)
	}
}

func TestUC1SensorAcceptsOnlyExpectedLedger(t *testing.T) {
	lanwire.RegisterDecoder()
	asset := ethchannel.NewAsset(big.NewInt(31337), common.HexToAddress("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"))
	otherAsset := ethchannel.NewAsset(big.NewInt(31337), common.HexToAddress("0x00000000000000000000000000000000000000aa"))
	cust := common.HexToAddress("0x00000000000000000000000000000000000000c1")
	stranger := common.HexToAddress("0x00000000000000000000000000000000000000ee")
	custWire, sensorWire, strangerWire := uc1TestWire(t, "c1"), uc1TestWire(t, "sensor"), uc1TestWire(t, "x")
	adm := &uc1Admission{
		asset: asset, deposit: big.NewInt(1000), challenge: 10, self: sensorWire,
		allow:      map[common.Address]bool{cust: true},
		registered: map[wire.AddrKey]common.Address{wire.Keys(custWire): cust},
	}
	if err := adm.check(uc1TestProposal(t, asset, cust, custWire, sensorWire, []int64{1000, 0}, 10)); err != nil {
		t.Fatalf("the expected ledger must be accepted: %v", err)
	}
	bad := map[string]*client.LedgerChannelProposalMsg{
		"sensor deposits":       uc1TestProposal(t, asset, cust, custWire, sensorWire, []int64{1000, 1}, 10),
		"wrong deposit":         uc1TestProposal(t, asset, cust, custWire, sensorWire, []int64{999, 0}, 10),
		"wrong challenge":       uc1TestProposal(t, asset, cust, custWire, sensorWire, []int64{1000, 0}, 11),
		"other asset":           uc1TestProposal(t, otherAsset, cust, custWire, sensorWire, []int64{1000, 0}, 10),
		"participant not allow": uc1TestProposal(t, asset, stranger, custWire, sensorWire, []int64{1000, 0}, 10),
		"unregistered peer":     uc1TestProposal(t, asset, cust, strangerWire, sensorWire, []int64{1000, 0}, 10),
		"sensor not index 1":    uc1TestProposal(t, asset, cust, custWire, strangerWire, []int64{1000, 0}, 10),
	}
	// A three-party channel.
	alloc3 := channel.NewAllocation(3, []wallet.BackendID{ethwallet.BackendID}, asset)
	alloc3.SetAssetBalances(asset, []channel.Bal{big.NewInt(1000), big.NewInt(0), big.NewInt(0)})
	p3, err := client.NewLedgerChannelProposal(10,
		map[wallet.BackendID]wallet.Address{ethwallet.BackendID: ethwallet.AsWalletAddr(cust)},
		alloc3, []map[wallet.BackendID]wire.Address{custWire, sensorWire, strangerWire})
	if err != nil {
		t.Fatal(err)
	}
	bad["three parties"] = p3
	for name, p := range bad {
		if err := adm.check(p); err == nil || err.Error() == "" {
			t.Errorf("%s: must be rejected with a reason, got %v", name, err)
		}
	}
	// A registered peer whose wire maps to another Ethereum address than the
	// proposal's participant.
	adm.registered[wire.Keys(custWire)] = stranger
	adm.allow[stranger] = true
	if err := adm.check(uc1TestProposal(t, asset, cust, custWire, sensorWire, []int64{1000, 0}, 10)); err == nil {
		t.Error("participant must match the address the peer registered")
	}
}

func uc1TestState(bals ...int64) *channel.State {
	asset := ethchannel.NewAsset(big.NewInt(31337), common.HexToAddress("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"))
	alloc := channel.NewAllocation(2, []wallet.BackendID{ethwallet.BackendID}, asset)
	b := make([]channel.Bal, len(bals))
	for i, v := range bals {
		b[i] = big.NewInt(v)
	}
	alloc.SetAssetBalances(asset, b)
	return &channel.State{Allocation: *alloc, Version: 5}
}

func TestUC1SensorUpdatePolicy(t *testing.T) {
	cur := uc1TestState(1000, 10)
	next := func(final bool, actor channel.Index, bals ...int64) client.ChannelUpdate {
		s := uc1TestState(bals...)
		s.Version, s.IsFinal = 6, final
		return client.ChannelUpdate{State: s, ActorIdx: actor}
	}
	if err := uc1CheckUpdate(cur, next(false, 0, 990, 20), true); err != nil {
		t.Fatalf("a payment from the customer must be accepted: %v", err)
	}
	if err := uc1CheckUpdate(cur, next(true, 0, 1000, 10), true); err != nil {
		t.Fatalf("a final state must be accepted: %v", err)
	}
	if err := uc1CheckUpdate(cur, next(false, 0, 1010, 0), true); err == nil {
		t.Fatal("a state that pays the customer must be rejected")
	}
	if err := uc1CheckUpdate(cur, next(false, 1, 990, 20), true); err == nil {
		t.Fatal("only the customer (index 0) may propose")
	}
	if err := uc1CheckUpdate(cur, next(false, 1, 990, 20), false); err == nil {
		t.Fatal("only the customer (index 0) may propose, strict or not")
	}
	if err := uc1CheckUpdate(cur, next(false, 0, 1010, 0), false); err != nil {
		t.Fatalf("without -strict only the actor rule applies: %v", err)
	}
}

// waitPay collects one pay outcome.
type payCollector struct {
	mu   sync.Mutex
	outs []uc1PayOutcome
	ids  []int
	wg   sync.WaitGroup
}

func (c *payCollector) pay(f *uc1FIFO, id int, amount int64) {
	c.wg.Add(1)
	f.Pay(big.NewInt(amount), func(o uc1PayOutcome) {
		c.mu.Lock()
		c.outs = append(c.outs, o)
		c.ids = append(c.ids, id)
		c.mu.Unlock()
		c.wg.Done()
	})
}

func (c *payCollector) wait(t *testing.T) {
	t.Helper()
	done := make(chan struct{})
	go func() { c.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("pays did not resolve")
	}
}

func TestUC1FIFOOneInFlightInOrder(t *testing.T) {
	var inFlight, maxInFlight atomic.Int32
	var mu sync.Mutex
	var calls []int64
	updater := func(amount *big.Int) (uint64, error) {
		n := inFlight.Add(1)
		for {
			m := maxInFlight.Load()
			if n <= m || maxInFlight.CompareAndSwap(m, n) {
				break
			}
		}
		time.Sleep(time.Millisecond)
		mu.Lock()
		calls = append(calls, amount.Int64())
		v := uint64(len(calls))
		mu.Unlock()
		inFlight.Add(-1)
		return v, nil
	}
	f := newUC1FIFO(updater, func() *big.Int { return big.NewInt(1 << 40) })
	defer f.Stop()
	var c payCollector
	for i := 1; i <= 50; i++ {
		c.pay(f, i, int64(i))
	}
	c.wait(t)
	if maxInFlight.Load() != 1 {
		t.Fatalf("at most one update in flight, saw %d", maxInFlight.Load())
	}
	for i, id := range c.ids {
		if id != i+1 || calls[i] != int64(i+1) || !c.outs[i].OK || c.outs[i].Version != uint64(i+1) {
			t.Fatalf("pay %d: resolved as %d, call %d, outcome %+v: must run and resolve in request order", i+1, id, calls[i], c.outs[i])
		}
		if c.outs[i].TConfirmed.Before(c.outs[i].TSubmitted) {
			t.Fatalf("pay %d: confirmed before submitted", i+1)
		}
	}
}

func TestUC1QuiesceDropsQueuedAndWaitsForInFlight(t *testing.T) {
	release := make(chan struct{})
	started := make(chan struct{}, 1)
	var calls atomic.Int32
	updater := func(*big.Int) (uint64, error) {
		n := calls.Add(1)
		if n == 1 {
			started <- struct{}{}
			<-release
		}
		return uint64(n), nil
	}
	f := newUC1FIFO(updater, func() *big.Int { return big.NewInt(1 << 40) })
	defer f.Stop()
	var c payCollector
	for i := 1; i <= 5; i++ {
		c.pay(f, i, 1)
	}
	<-started
	go func() { time.Sleep(100 * time.Millisecond); close(release) }()
	t0 := time.Now()
	dropped := f.Quiesce(5 * time.Second)
	if time.Since(t0) < 80*time.Millisecond {
		t.Fatal("quiesce must wait for the update in flight")
	}
	if dropped != 4 {
		t.Fatalf("4 queued pays must be dropped, got %d", dropped)
	}
	c.wait(t)
	byID := map[int]uc1PayOutcome{}
	for i, id := range c.ids {
		byID[id] = c.outs[i]
	}
	if !byID[1].OK {
		t.Fatalf("the in-flight pay must complete: %+v", byID[1])
	}
	for id := 2; id <= 5; id++ {
		if byID[id].OK || byID[id].Err != "unresolved: dropped at quiesce" {
			t.Fatalf("pay %d: %+v", id, byID[id])
		}
	}
	var late payCollector
	late.pay(f, 6, 1)
	late.wait(t)
	if late.outs[0].OK || late.outs[0].Err != "unresolved: after quiesce" {
		t.Fatalf("pay after quiesce: %+v", late.outs[0])
	}
	if calls.Load() != 1 {
		t.Fatalf("no dropped or late pay may reach the updater, got %d calls", calls.Load())
	}
}

func TestUC1TimeoutMarksChannelBroken(t *testing.T) {
	var calls atomic.Int32
	updater := func(*big.Int) (uint64, error) {
		if calls.Add(1) == 2 {
			return 0, fmt.Errorf("updating: %w", client.RequestTimedOutError("peer did not respond"))
		}
		return uint64(calls.Load()), nil
	}
	f := newUC1FIFO(updater, func() *big.Int { return big.NewInt(1 << 40) })
	defer f.Stop()
	var c payCollector
	for i := 1; i <= 5; i++ {
		c.pay(f, i, 1)
	}
	c.wait(t)
	if !c.outs[0].OK {
		t.Fatalf("pay 1: %+v", c.outs[0])
	}
	if c.outs[1].OK || c.outs[1].Err != "unresolved: update timeout" {
		t.Fatalf("the timed-out pay must be unresolved: %+v", c.outs[1])
	}
	for i := 2; i < 5; i++ {
		if c.outs[i].OK || !strings.HasPrefix(c.outs[i].Err, "channel broken: ") {
			t.Fatalf("pay %d after the timeout: %+v", i+1, c.outs[i])
		}
	}
	if calls.Load() != 2 {
		t.Fatalf("no pay after the timeout may reach the updater, got %d calls", calls.Load())
	}
	if f.Broken() == "" {
		t.Fatal("the channel must report broken")
	}
}

func TestUC1ChannelEmptyIsFailedNotBroken(t *testing.T) {
	var mu sync.Mutex
	balance := big.NewInt(10)
	var calls atomic.Int32
	updater := func(amount *big.Int) (uint64, error) {
		mu.Lock()
		defer mu.Unlock()
		balance.Sub(balance, amount)
		return uint64(calls.Add(1)), nil
	}
	bal := func() *big.Int { mu.Lock(); defer mu.Unlock(); return new(big.Int).Set(balance) }
	f := newUC1FIFO(updater, bal)
	defer f.Stop()
	var c payCollector
	c.pay(f, 1, 6) // ok, 4 left
	c.pay(f, 2, 6) // empty
	c.pay(f, 3, 4) // ok, 0 left
	c.pay(f, 4, 1) // empty
	c.wait(t)
	if !c.outs[0].OK || c.outs[1].OK || c.outs[1].Err != "channel empty" || !c.outs[2].OK || c.outs[3].Err != "channel empty" {
		t.Fatalf("outcomes: %+v", c.outs)
	}
	if f.Broken() != "" || calls.Load() != 2 {
		t.Fatalf("channel empty must not break the channel (broken=%q, calls=%d)", f.Broken(), calls.Load())
	}
	// A rejection by the sensor is a failed pay; the channel stays usable.
	rej := newUC1FIFO(func(*big.Int) (uint64, error) {
		return 0, fmt.Errorf("update: %w", client.PeerRejectedError{ItemType: "channel update", Reason: "payment policy"})
	}, func() *big.Int { return big.NewInt(100) })
	defer rej.Stop()
	var r payCollector
	r.pay(rej, 1, 1)
	r.pay(rej, 2, 1)
	r.wait(t)
	for _, o := range r.outs {
		if o.OK || !strings.HasPrefix(o.Err, "rejected: ") {
			t.Fatalf("rejection: %+v", o)
		}
	}
	if rej.Broken() != "" {
		t.Fatal("a rejection must not break the channel")
	}
}

// TestUC1ClockCheck: -clock-check answers each input line with time.Now() as
// unixSeconds, so Python can bracket Go's wall clock between two time.time().
func TestUC1ClockCheck(t *testing.T) {
	var out strings.Builder
	before := float64(time.Now().UnixNano()) / 1e9
	if err := runUC1ClockCheck(strings.NewReader("a\nb\n"), &out); err != nil {
		t.Fatal(err)
	}
	after := float64(time.Now().UnixNano()) / 1e9
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) != 2 {
		t.Fatalf("want one reply per input line, got %q", out.String())
	}
	for _, l := range lines {
		var r struct {
			Now float64 `json:"now"`
		}
		if err := json.Unmarshal([]byte(l), &r); err != nil || r.Now < before || r.Now > after {
			t.Fatalf("reply %q (err %v) not within [%f, %f]", l, err, before, after)
		}
	}
}
