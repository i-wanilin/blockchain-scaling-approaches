// uc2_integration_test.go — the real UC2 Perun stack under live input: a
// uc2-hub process and uc2-player sidecars driven over stdin/stdout, on Anvil.
//
//	TestUC2SixPlayersPacedLive                    six players, 20 s of paced shots and hits
//	TestUC2HeadOnHitsInOneTick                    two players pay each other at the same instant, 200 times
//	TestUC2PlayerTimeoutIsUnresolvedAndCloseStillRuns  a peer is SIGSTOPped for 12 s mid-run
//
// The accounts are those of uc2_test.go: mnemonic index 110 the hub, 111..116
// players 0..5. Anvil funds only indices 0..9, so the tests give them ETH with
// anvil_setBalance. They need RUN_DISTRIBUTED_TEST=1 and a local Anvil:
//
//	anvil --chain-id 31337 --block-time 1 --port 18845 &
//	RUN_DISTRIBUTED_TEST=1 ANVIL_URL=ws://127.0.0.1:18845 go test -count=1 \
//	    -run 'TestUC2(SixPlayers|HeadOn|PlayerTimeout)' -v -timeout 20m
package main

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"math/big"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/accounts/abi/bind"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/ethclient"

	"perun.network/perun-examples/hub-demo/pointstoken"
)

// Private keys of mnemonic indices 111..116 (players 0..5), the addresses of
// uc2TestPlayers. Public throwaway keys, local chains only.
var uc2TestPlayerKeys = []string{
	"3659cf616cb9eff3ecdd1ce36221a3744df6deb907007dc2ad4330dc66aa2d13",
	"23fe537a715500e8edf9a949d1a5894fd5296a257b412e6f7e598b22bc62b060",
	"f1a9dd9e1f43e6832b9950520b8fe73203d14f171cb5b07dceb0d3090878045f",
	"2130940937fb474f9a6ac3ea114536c5d693ae1f918bec1e33e98de810db312e",
	"755e7b431c9224a9d798e1c03d0f8d7084486aeee98ab8ea87d4538a502a73c8",
	"914a73ad0b138eedf80704f9ccd81be56f33bbd5f8b371c82de3b6b6a5a23ff7",
}

const (
	uc2Deposit    = 10000 // every player's ledger deposit (all its points)
	uc2HubDeposit = 8400  // the hub's side of every ledger channel
	uc2Stake      = 1680  // each side of every virtual channel
)

type uc2RunOpts struct {
	players        int
	updateTimeoutS uint64 // update_timeout_seconds and the init override
	operationS     uint64
	yieldMS        uint64
	persist        bool
}

// uc2Run is one deployed UC2 cluster on loopback: a uc2-hub process and the
// players' sidecars, initialised and opened.
type uc2Run struct {
	t        *testing.T
	opts     uc2RunOpts
	url      string
	cfg      clusterConfig
	token    common.Address
	hubLog   *syncBuffer
	hub      *exec.Cmd
	hubDone  chan error
	ps       []*uc2Proc
	openReps []map[string]any
}

func uc2Gate(t *testing.T) string {
	t.Helper()
	if os.Getenv("RUN_DISTRIBUTED_TEST") != "1" || os.Getenv("ANVIL_URL") == "" {
		t.Skip("set RUN_DISTRIBUTED_TEST=1 and ANVIL_URL with Anvil running")
	}
	url := anvilURL()
	skipIfNoAnvil(t, url)
	return url
}

// fundUC2Accounts gives the hub and the players ETH for gas: accounts 110..116
// are beyond the ten Anvil funds.
func fundUC2Accounts(t *testing.T, url string, addrs []string) {
	t.Helper()
	ec, err := ethclient.Dial(url)
	if err != nil {
		t.Fatal(err)
	}
	defer ec.Close()
	for _, a := range addrs {
		if err := ec.Client().CallContext(context.Background(), nil, "anvil_setBalance", a, "0x56BC75E2D63100000"); err != nil { // 100 ETH
			t.Fatalf("anvil_setBalance %s: %v", a, err)
		}
	}
}

// startUC2Run deploys, starts the hub and the players, and opens every channel.
func startUC2Run(t *testing.T, url string, o uc2RunOpts) *uc2Run {
	t.Helper()
	bin := filepath.Join(t.TempDir(), "perun-node")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("build: %v\n%s", err, out)
	}
	state := t.TempDir()
	fundUC2Accounts(t, url, append([]string{uc2TestHubAddr}, uc2TestPlayers[:o.players]...))
	var players []common.Address
	for _, a := range uc2TestPlayers[:o.players] {
		players = append(players, common.HexToAddress(a))
	}
	contractsPath := filepath.Join(state, "contracts.json")
	if err := runUC2Deploy(url, 31337, players, uc2Deposit, common.HexToAddress(uc2TestHubAddr), int64(o.players*uc2HubDeposit), contractsPath); err != nil {
		t.Fatal(err)
	}
	c, err := loadUC2Contracts(contractsPath)
	if err != nil {
		t.Fatal(err)
	}
	keyPath := filepath.Join(state, "hub.key")
	if err := os.WriteFile(keyPath, []byte(uc2TestHubKey+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	hubPort, controlPort := freePort(t), freePort(t)
	cfg := clusterConfig{
		ChainURL: url, ChainID: 31337, StateRoot: state, ResultDir: filepath.Join(state, "results"),
		StartupTimeoutSec: 90, OperationTimeoutSec: o.operationS, Asset: "points",
		YieldTimeoutMS: o.yieldMS, UpdateTimeoutSec: o.updateTimeoutS, MaxCollisionRetries: 10000,
		LedgerDeposit: uc2Deposit, VirtualStake: uc2Stake, HubDeposit: uc2HubDeposit, AmmoReserve: 336,
		ContractsFile: contractsPath, HubKeyFile: keyPath,
		Hub: clusterNode{Name: "arena", Bind: fmt.Sprintf("127.0.0.1:%d", hubPort),
			Endpoint: fmt.Sprintf("127.0.0.1:%d", hubPort), Control: fmt.Sprintf("127.0.0.1:%d", controlPort),
			Ethereum: uc2TestHubAddr},
	}
	for i := 0; i < o.players; i++ {
		ep := fmt.Sprintf("127.0.0.1:%d", freePort(t))
		cfg.Clients = append(cfg.Clients, clusterNode{Name: fmt.Sprintf("p%d", i), Bind: ep, Endpoint: ep,
			Control: fmt.Sprintf("http://127.0.0.1:%d", controlPort), Ethereum: uc2TestPlayers[i]})
	}
	configPath := writeUC2Config(t, cfg)

	r := &uc2Run{t: t, opts: o, url: url, cfg: cfg, token: common.HexToAddress(c.Token),
		hubLog: &syncBuffer{b: &bytes.Buffer{}}, hubDone: make(chan error, 1)}
	r.hub = exec.Command(bin, "-mode", "uc2-hub", "-config", configPath)
	r.hub.Stdout, r.hub.Stderr = r.hubLog, r.hubLog
	if err := r.hub.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = r.hub.Process.Kill() })
	go func() { r.hubDone <- r.hub.Wait() }()

	for i := 0; i < o.players; i++ {
		p := startUC2PlayerProc(t, bin, fmt.Sprintf("p%d", i))
		p.send(fmt.Sprintf(`{"op":"init","key":%q,"name":"p%d","config":%q,"state_dir":%q,"update_timeout_s":%d,"persist":%v,"startup_timeout_s":90}`,
			uc2TestPlayerKeys[i], i, configPath, filepath.Join(state, fmt.Sprintf("p%d", i)), o.updateTimeoutS, o.persist))
		r.ps = append(r.ps, p)
	}
	for i, p := range r.ps {
		m := p.wantOK(p.recv(120*time.Second), "init")
		if m["index"] != float64(i) || common.HexToAddress(m["address"].(string)) != players[i] {
			t.Fatalf("p%d init: %v", i, m)
		}
	}
	for _, p := range r.ps {
		p.send(`{"op":"open"}`)
	}
	for i, p := range r.ps {
		m := p.wantOK(p.recv(240*time.Second), "open")
		if v := m["virtual"].(map[string]any); len(v) != o.players-1 {
			t.Fatalf("p%d open: %d virtual channels: %v", i, len(v), m)
		}
		r.openReps = append(r.openReps, m)
		t.Logf("p%d open: ledger %.2fs, virtual %.3fs, gas %v in %v txs", i,
			m["t_ledger_funded"].(float64)-m["t_start"].(float64), m["t_virtual_open"].(float64)-m["t_virtual_start"].(float64),
			m["l1_gas_used"], m["txs"])
	}
	return r
}

// uc2Line is one paced stdin line.
type uc2Line struct {
	at   time.Duration
	line string
}

// uc2Pace writes each player's lines at their offsets from start, every player
// from its own goroutine. It returns when all are written.
func uc2Pace(ps []*uc2Proc, sched [][]uc2Line, start time.Time) []error {
	var wg sync.WaitGroup
	errs := make([]error, len(ps))
	for i := range ps {
		lines := append([]uc2Line(nil), sched[i]...)
		sort.SliceStable(lines, func(a, b int) bool { return lines[a].at < lines[b].at })
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			for _, l := range lines {
				time.Sleep(time.Until(start.Add(l.at)))
				if _, err := io.WriteString(ps[i].in, l.line+"\n"); err != nil {
					errs[i] = fmt.Errorf("p%d: write: %w", i, err)
					return
				}
			}
		}(i)
	}
	wg.Wait()
	return errs
}

// uc2Collect reads n replies from p (pays, in request order) until deadline.
func uc2Collect(p *uc2Proc, n int, deadline time.Time) ([]map[string]any, error) {
	var out []map[string]any
	for len(out) < n {
		select {
		case m, ok := <-p.lines:
			if !ok {
				return out, fmt.Errorf("%s closed stdout after %d of %d replies", p.name, len(out), n)
			}
			if _, bad := m["not_json"]; bad {
				return out, fmt.Errorf("%s: stdout carries a non-protocol line: %v", p.name, m)
			}
			out = append(out, m)
		case <-time.After(time.Until(deadline)):
			return out, fmt.Errorf("%s: %d of %d replies by the deadline", p.name, len(out), n)
		}
	}
	return out, nil
}

// uc2Latencies is t_confirmed - t_submitted of every ok pay, in ms.
type uc2Latencies []float64

func (l uc2Latencies) pct(p float64) float64 {
	if len(l) == 0 {
		return 0
	}
	s := append([]float64(nil), l...)
	sort.Float64s(s)
	i := int(p*float64(len(s))+0.5) - 1
	if i < 0 {
		i = 0
	}
	if i >= len(s) {
		i = len(s) - 1
	}
	return s[i]
}

func (l uc2Latencies) String() string {
	return fmt.Sprintf("n=%d p50=%.2fms p99=%.2fms max=%.2fms", len(l), l.pct(0.50), l.pct(0.99), l.pct(1))
}

func payLatencyMS(m map[string]any) float64 {
	return (m["t_confirmed"].(float64) - m["t_submitted"].(float64)) * 1000
}

// quiesceAndState sends quiesce and state to every player and returns the states'
// channels, after checking that nothing was dropped.
func (r *uc2Run) quiesceAndState() []map[string]any {
	r.t.Helper()
	for _, p := range r.ps {
		p.send(`{"op":"quiesce"}`, `{"op":"state"}`)
	}
	var out []map[string]any
	for i, p := range r.ps {
		q := p.wantOK(p.recv(60*time.Second), "quiesce")
		if q["dropped"] != float64(0) {
			r.t.Fatalf("p%d quiesce dropped pays: %v", i, q)
		}
		st := p.wantOK(p.recv(30*time.Second), "state")
		out = append(out, st["channels"].(map[string]any))
	}
	return out
}

func chanState(chans map[string]any, target string) (version uint64, mine, peer string, broken bool) {
	c := chans[target].(map[string]any)
	b := c["balances_wei"].([]any)
	return uint64(c["version"].(float64)), b[0].(string), b[1].(string), c["broken"].(bool)
}

// checkVirtualAgree checks both ends of every virtual channel: the same
// version, mirrored balances, and (if want is given) the balances the test's
// own bookkeeping expects for the lower index, want[i][j] = i's balance with j.
func (r *uc2Run) checkVirtualAgree(states []map[string]any, want [][]int64) {
	r.t.Helper()
	for i := range states {
		for j := i + 1; j < len(states); j++ {
			vi, mi, pi, bi := chanState(states[i], uc2Target(j))
			vj, mj, pj, bj := chanState(states[j], uc2Target(i))
			if vi != vj || mi != pj || pi != mj || bi || bj {
				r.t.Fatalf("p%d-p%d: p%d sees v%d [%s %s] broken=%v, p%d sees v%d [%s %s] broken=%v",
					i, j, i, vi, mi, pi, bi, j, vj, mj, pj, bj)
			}
			if want != nil && (mi != fmt.Sprint(want[i][j]) || pi != fmt.Sprint(want[j][i])) {
				r.t.Fatalf("p%d-p%d at v%d: [%s %s], want [%d %d]", i, j, vi, mi, pi, want[i][j], want[j][i])
			}
		}
	}
}

// closeAll closes every player, checks the final ledger balances [mine, hub],
// exits them and waits for the hub. It returns the close replies.
func (r *uc2Run) closeAll(finalMine, finalHub []int64) []map[string]any {
	r.t.Helper()
	for _, p := range r.ps {
		p.send(`{"op":"close"}`)
	}
	var reps []map[string]any
	for i, p := range r.ps {
		cl := p.wantOK(p.recv(300*time.Second), "close")
		got := cl["final_ledger_balances_wei"].([]any)
		if got[0] != fmt.Sprint(finalMine[i]) || got[1] != fmt.Sprint(finalHub[i]) {
			r.t.Fatalf("p%d close: final ledger %v, want [%d %d]", i, got, finalMine[i], finalHub[i])
		}
		if vf := cl["virtual_final"].([]any); len(vf) != r.opts.players-1 {
			r.t.Fatalf("p%d close: virtual_final %v", i, vf)
		}
		reps = append(reps, cl)
	}
	for i, p := range r.ps {
		p.send(`{"op":"exit"}`)
		p.wantOK(p.recv(30*time.Second), "exit")
		if err := p.cmd.Wait(); err != nil {
			r.t.Fatalf("p%d exited with %v\n%s", i, err, p.stderr.String())
		}
	}
	select {
	case err := <-r.hubDone:
		if err != nil {
			r.t.Fatalf("hub: %v\n%s", err, r.hubLog.String())
		}
	case <-time.After(90 * time.Second):
		r.t.Fatalf("hub did not finish\n%s", r.hubLog.String())
	}
	return reps
}

// logGas prints each player's Open and Close gas and the hub's line.
func (r *uc2Run) logGas(closes []map[string]any) {
	r.t.Helper()
	for i := range r.ps {
		r.t.Logf("p%d gas: open %v in %v txs, close %v in %v txs; collisions %v; virtual settle %.3fs, ledger settle %.2fs", i,
			r.openReps[i]["l1_gas_used"], r.openReps[i]["txs"], closes[i]["l1_gas_used"], closes[i]["txs"],
			closes[i]["collisions"], closes[i]["virtual_settle_s"], closes[i]["ledger_settle_s"])
	}
	for _, l := range strings.Split(r.hubLog.String(), "\n") {
		if strings.HasPrefix(l, "hub gas:") {
			r.t.Logf("%s", l)
			return
		}
	}
	r.t.Errorf("the hub printed no gas line\n%s", r.hubLog.String())
}

// checkChain compares the settled token balances on chain.
func (r *uc2Run) checkChain(want map[string]int64) {
	r.t.Helper()
	ec, err := ethclient.Dial(r.url)
	if err != nil {
		r.t.Fatal(err)
	}
	defer ec.Close()
	tok, err := pointstoken.NewPointsTokenCaller(r.token, ec)
	if err != nil {
		r.t.Fatal(err)
	}
	for a, v := range want {
		bal, err := tok.BalanceOf(&bind.CallOpts{}, common.HexToAddress(a))
		if err != nil {
			r.t.Fatal(err)
		}
		if bal.Cmp(big.NewInt(v)) != 0 {
			r.t.Errorf("balanceOf(%s) = %s after settlement, want %d", a, bal, v)
		}
	}
}

// uc2PaySent is one pay the test sent, for its own bookkeeping.
type uc2PaySent struct {
	id     string
	from   int
	to     string // "hub" or "p<k>"
	amount int64
	at     time.Duration
}

// TestUC2SixPlayersPacedLive drives six players and the hub for 20 s at the
// frozen game's rate shape: each player pays the hub 1 point (a shot) every
// 200 ms and two fixed opponents (its ring neighbours) 5 points (a hit) at
// about 2/s each, so six virtual channels carry pays in both directions at
// once while every ledger channel carries ammo. Every pay must succeed, each
// player must settle at 10 000 - out + in, the hub at 6 x 8 400 + the ammo,
// and both ends of every virtual channel must agree on the final version.
func TestUC2SixPlayersPacedLive(t *testing.T) {
	url := uc2Gate(t)
	const n = 6
	r := startUC2Run(t, url, uc2RunOpts{players: n, updateTimeoutS: 10, operationS: 180, yieldMS: 40, persist: true})

	const run = 20 * time.Second
	rng := rand.New(rand.NewSource(9))
	sched := make([][]uc2Line, n)
	sent := make([][]uc2PaySent, n) // in each player's request order, below
	for i := 0; i < n; i++ {
		off := time.Duration(i) * 13 * time.Millisecond
		var ps []uc2PaySent
		for k := 0; time.Duration(k)*200*time.Millisecond < run; k++ {
			ps = append(ps, uc2PaySent{id: fmt.Sprintf("p%d-a%d", i, k), from: i, to: "hub", amount: 1,
				at: off + time.Duration(k)*200*time.Millisecond})
		}
		for side, opp := range []int{(i + 1) % n, (i + n - 1) % n} {
			for k := 0; time.Duration(k)*500*time.Millisecond < run; k++ {
				jitter := time.Duration(rng.Intn(81)-40) * time.Millisecond
				at := off + time.Duration(side)*250*time.Millisecond + time.Duration(k)*500*time.Millisecond + jitter
				if at < 0 {
					at = 0
				}
				ps = append(ps, uc2PaySent{id: fmt.Sprintf("p%d-h%d-%d", i, opp, k), from: i, to: uc2Target(opp), amount: 5, at: at})
			}
		}
		sort.SliceStable(ps, func(a, b int) bool { return ps[a].at < ps[b].at })
		sent[i] = ps
		for _, p := range ps {
			sched[i] = append(sched[i], uc2Line{at: p.at, line: uc2Pay(p.id, p.to, int(p.amount))})
		}
	}
	counts := make([]int, n)
	for i := range sent {
		counts[i] = len(sent[i])
	}
	start := time.Now().Add(200 * time.Millisecond)
	var replies [][]map[string]any
	collected := make(chan struct{})
	go func() {
		defer close(collected)
		replies = uc2CollectAllNoFatal(r.ps, counts, start.Add(run+120*time.Second))
	}()
	for _, err := range uc2Pace(r.ps, sched, start) {
		if err != nil {
			t.Fatal(err)
		}
	}
	<-collected
	if replies == nil {
		t.Fatalf("collecting the pay replies failed\n%s", r.dumpPlayers())
	}

	// Bookkeeping from the test's own record of what succeeded.
	ammo := make([]int64, n)
	hits := make([][]int64, n) // hits[i][j] = points i paid j
	for i := range hits {
		hits[i] = make([]int64, n)
	}
	var all, hubLat, virtLat uc2Latencies
	for i := 0; i < n; i++ {
		for k, m := range replies[i] {
			p := sent[i][k]
			if m["op"] != "pay" || m["id"] != p.id {
				t.Fatalf("p%d reply %d is %v, want pay %s", i, k, m, p.id)
			}
			if m["ok"] != true {
				t.Fatalf("p%d pay %s to %s failed: %v\n%s", i, p.id, p.to, m, r.ps[i].stderr.String())
			}
			l := payLatencyMS(m)
			all = append(all, l)
			if p.to == "hub" {
				ammo[i] += p.amount
				hubLat = append(hubLat, l)
			} else {
				var j int
				fmt.Sscanf(p.to, "p%d", &j)
				hits[i][j] += p.amount
				virtLat = append(virtLat, l)
			}
		}
	}
	t.Logf("pays: %d ok; latency all %s; hub %s; virtual %s", len(all), all, hubLat, virtLat)

	states := r.quiesceAndState()
	want := make([][]int64, n) // i's balance with j
	for i := range want {
		want[i] = make([]int64, n)
		for j := range want[i] {
			want[i][j] = uc2Stake - hits[i][j] + hits[j][i]
		}
	}
	r.checkVirtualAgree(states, want)
	for i := range states {
		// Ledger free funds: the five stakes are locked in the virtual channels.
		_, mine, peer, broken := chanState(states[i], "hub")
		wm, wp := int64(uc2Deposit-(n-1)*uc2Stake)-ammo[i], int64(uc2HubDeposit-(n-1)*uc2Stake)+ammo[i]
		if mine != fmt.Sprint(wm) || peer != fmt.Sprint(wp) || broken {
			t.Fatalf("p%d hub: [%s %s] broken=%v, want [%d %d]", i, mine, peer, broken, wm, wp)
		}
	}

	finalMine, finalHub := make([]int64, n), make([]int64, n)
	chain := map[string]int64{}
	var totalAmmo int64
	for i := 0; i < n; i++ {
		var out, in int64 = ammo[i], 0
		for j := 0; j < n; j++ {
			out += hits[i][j]
			in += hits[j][i]
		}
		finalMine[i], finalHub[i] = uc2Deposit-out+in, uc2HubDeposit+ammo[i]
		chain[uc2TestPlayers[i]] = finalMine[i]
		totalAmmo += ammo[i]
	}
	chain[uc2TestHubAddr] = n*uc2HubDeposit + totalAmmo
	closes := r.closeAll(finalMine, finalHub)
	r.logGas(closes)
	total := 0.0
	for _, c := range closes {
		total += c["collisions"].(float64)
	}
	t.Logf("collisions: %v in total (allowed)", total)
	r.checkChain(chain)
	t.Logf("settled: players %v, hub %d (ammo %d)", finalMine, chain[uc2TestHubAddr], totalAmmo)
}

// uc2CollectAllNoFatal collects every player's replies in parallel, from a
// goroutine (so it cannot t.Fatal): nil on any error, which it prints to stderr.
func uc2CollectAllNoFatal(ps []*uc2Proc, counts []int, deadline time.Time) [][]map[string]any {
	out := make([][]map[string]any, len(ps))
	errs := make([]error, len(ps))
	var wg sync.WaitGroup
	for i, p := range ps {
		wg.Add(1)
		go func(i int, p *uc2Proc) {
			defer wg.Done()
			out[i], errs[i] = uc2Collect(p, counts[i], deadline)
		}(i, p)
	}
	wg.Wait()
	for _, err := range errs {
		if err != nil {
			fmt.Fprintln(os.Stderr, "collect:", err)
			return nil
		}
	}
	return out
}

func (r *uc2Run) dumpPlayers() string {
	var sb strings.Builder
	for _, p := range r.ps {
		fmt.Fprintf(&sb, "---- %s ----\n%s\n", p.name, p.stderr.String())
	}
	fmt.Fprintf(&sb, "---- hub ----\n%s\n", r.hubLog.String())
	return sb.String()
}

// TestUC2HeadOnHitsInOneTick is Review Focus 2: two players hit each other in
// the same tick, so both ends of one virtual channel propose at once. 200
// times, 50 ms apart, both pays are written at the same instant. The
// collision policy must resolve every one (the higher index yields), all 400
// pays must succeed and both ends must end with the same version.
func TestUC2HeadOnHitsInOneTick(t *testing.T) {
	url := uc2Gate(t)
	r := startUC2Run(t, url, uc2RunOpts{players: 2, updateTimeoutS: 10, operationS: 180, yieldMS: 40, persist: true})

	const rounds = 200
	counts := []int{rounds, rounds}
	start := time.Now().Add(200 * time.Millisecond)
	var replies [][]map[string]any
	collected := make(chan struct{})
	go func() {
		defer close(collected)
		replies = uc2CollectAllNoFatal(r.ps, counts, start.Add(300*time.Second))
	}()
	for k := 0; k < rounds; k++ {
		time.Sleep(time.Until(start.Add(time.Duration(k) * 50 * time.Millisecond)))
		// One goroutine writes both lines back to back, microseconds apart.
		r.ps[0].send(uc2Pay(fmt.Sprintf("a%d", k), "p1", 5))
		r.ps[1].send(uc2Pay(fmt.Sprintf("b%d", k), "p0", 5))
	}
	<-collected
	if replies == nil {
		t.Fatalf("collecting the pay replies failed\n%s", r.dumpPlayers())
	}
	var lat [2]uc2Latencies
	for i, prefix := range []string{"a", "b"} {
		for k, m := range replies[i] {
			if m["id"] != fmt.Sprintf("%s%d", prefix, k) || m["ok"] != true {
				t.Fatalf("p%d reply %d: %v\n%s", i, k, m, r.ps[i].stderr.String())
			}
			lat[i] = append(lat[i], payLatencyMS(m))
		}
	}
	t.Logf("p0 (priority) latency %s; p1 (yields) latency %s", lat[0], lat[1])
	states := r.quiesceAndState()
	r.checkVirtualAgree(states, [][]int64{{0, uc2Stake}, {uc2Stake, 0}})
	v, _, _, _ := chanState(states[0], "p1")
	t.Logf("final virtual version at both ends: %d", v)

	closes := r.closeAll([]int64{uc2Deposit, uc2Deposit}, []int64{uc2HubDeposit, uc2HubDeposit})
	r.logGas(closes)
	c0, c1 := closes[0]["collisions"].(float64), closes[1]["collisions"].(float64)
	t.Logf("collisions: p0 %v, p1 %v", c0, c1)
	if c0+c1 == 0 {
		t.Fatalf("no collision in %d head-on rounds: the test did not exercise the policy", rounds)
	}
	r.checkChain(map[string]int64{uc2TestPlayers[0]: uc2Deposit, uc2TestPlayers[1]: uc2Deposit, uc2TestHubAddr: 2 * uc2HubDeposit})
}

// TestUC2PlayerTimeoutIsUnresolvedAndCloseStillRuns stalls a peer: p2's
// process is SIGSTOPped for 12 s while p0 keeps paying it, the hub and p1.
// p0's pay in flight to p2 must end "unresolved: update timeout" (p0 has
// priority on that channel, so it waits the update timeout and gives up), its
// later pays to p2 "channel broken", while its pays to the hub and p1, and
// p1's to p0 and the hub, keep succeeding through the stall and after. p0's
// close then fails naming p2 (the run would be incomplete); the others' closes
// fail at the payments-done barrier within the operation timeout, no hang.
func TestUC2PlayerTimeoutIsUnresolvedAndCloseStillRuns(t *testing.T) {
	url := uc2Gate(t)
	const updateTO, opTO = 4, 40
	r := startUC2Run(t, url, uc2RunOpts{players: 3, updateTimeoutS: updateTO, operationS: opTO, yieldMS: 40, persist: true})
	stalled := r.ps[2].cmd.Process
	t.Cleanup(func() { // never leave a stopped process behind
		_ = stalled.Signal(syscall.SIGCONT)
		_ = stalled.Kill()
	})

	const tick = 200 * time.Millisecond
	const stopAt, stall, tail = 3 * time.Second, 12 * time.Second, 3 * time.Second
	end := stopAt + stall + tail
	sched := make([][]uc2Line, 3)
	var to2 []time.Duration // when each of p0's pays to p2 was scheduled
	for k := 0; time.Duration(k)*tick < end; k++ {
		at := time.Duration(k) * tick
		sched[0] = append(sched[0],
			uc2Line{at, uc2Pay(fmt.Sprintf("a%d", k), "hub", 1)},
			uc2Line{at, uc2Pay(fmt.Sprintf("h1-%d", k), "p1", 5)},
			uc2Line{at, uc2Pay(fmt.Sprintf("h2-%d", k), "p2", 5)})
		to2 = append(to2, at)
		sched[1] = append(sched[1],
			uc2Line{at + tick/2, uc2Pay(fmt.Sprintf("h0-%d", k), "p0", 5)},
			uc2Line{at + tick/2, uc2Pay(fmt.Sprintf("a%d", k), "hub", 1)})
		if at < stopAt-time.Second { // p2 pays p1 only well before its stall
			sched[2] = append(sched[2], uc2Line{at, uc2Pay(fmt.Sprintf("h1-%d", k), "p1", 5)})
		}
	}
	counts := []int{len(sched[0]), len(sched[1]), len(sched[2])}
	start := time.Now().Add(200 * time.Millisecond)
	var replies [][]map[string]any
	collected := make(chan struct{})
	go func() {
		defer close(collected)
		replies = uc2CollectAllNoFatal(r.ps, counts, start.Add(end+60*time.Second))
	}()
	go func() {
		time.Sleep(time.Until(start.Add(stopAt)))
		_ = stalled.Signal(syscall.SIGSTOP)
		time.Sleep(stall)
		_ = stalled.Signal(syscall.SIGCONT)
	}()
	for _, err := range uc2Pace(r.ps, sched, start) {
		if err != nil {
			t.Fatal(err)
		}
	}
	<-collected
	if replies == nil {
		t.Fatalf("collecting the pay replies failed\n%s", r.dumpPlayers())
	}

	// p0's pays to p2: ok before the stall, exactly one "unresolved: update
	// timeout", then "channel broken" to the end, also after p2 is back.
	var okTo2, brokenTo2 int
	unresolvedAt := time.Duration(-1)
	k2 := 0
	for _, m := range replies[0] {
		id := m["id"].(string)
		switch {
		case strings.HasPrefix(id, "h2-"):
			at := to2[k2]
			k2++
			errText, _ := m["error"].(string)
			switch {
			case m["ok"] == true:
				if unresolvedAt >= 0 || brokenTo2 > 0 {
					t.Fatalf("p0 pay %s to p2 succeeded after the channel broke: %v", id, m)
				}
				if at > stopAt+tick {
					t.Fatalf("p0 pay %s to p2 (sent %v, p2 stopped at %v) succeeded: %v", id, at, stopAt, m)
				}
				okTo2++
			case errText == "unresolved: update timeout":
				if unresolvedAt >= 0 {
					t.Fatalf("a second unresolved pay to p2: %s %v", id, m)
				}
				if at < stopAt-tick {
					t.Fatalf("p0 pay %s to p2 timed out though sent at %v, before the stall at %v", id, at, stopAt)
				}
				unresolvedAt = at
			case strings.HasPrefix(errText, "channel broken: "):
				if unresolvedAt < 0 {
					t.Fatalf("p0 pay %s to p2 broken before any timeout: %v", id, m)
				}
				brokenTo2++
			default:
				t.Fatalf("p0 pay %s to p2: unexpected outcome %v", id, m)
			}
		default:
			if m["ok"] != true {
				t.Fatalf("p0 pay %s failed during/after the stall: %v\n%s", id, m, r.ps[0].stderr.String())
			}
		}
	}
	if okTo2 < 5 || unresolvedAt < 0 || brokenTo2 < 10 {
		t.Fatalf("p0 to p2: %d ok, unresolved at %v, %d broken", okTo2, unresolvedAt, brokenTo2)
	}
	t.Logf("p0 to p2: %d ok, one unresolved (sent at %v, stall at %v), %d channel broken", okTo2, unresolvedAt, stopAt, brokenTo2)
	for i := 1; i < 3; i++ {
		for _, m := range replies[i] {
			if m["ok"] != true {
				t.Fatalf("p%d pay %v failed: %v\n%s", i, m["id"], m, r.ps[i].stderr.String())
			}
		}
	}

	// quiesce and state: p0 sees p2's channel broken, nothing else.
	for _, p := range r.ps {
		p.send(`{"op":"quiesce"}`, `{"op":"state"}`)
	}
	for i, p := range r.ps {
		p.wantOK(p.recv(30*time.Second), "quiesce")
		st := p.wantOK(p.recv(30*time.Second), "state")
		chans := st["channels"].(map[string]any)
		for target := range chans {
			_, _, _, broken := chanState(chans, target)
			if want := i == 0 && target == "p2"; broken != want {
				t.Fatalf("p%d %s broken=%v, want %v: %v", i, target, broken, want, chans)
			}
		}
	}

	// close: p0 refuses at once, naming p2; p1 and p2 fail at the barrier.
	t0 := time.Now()
	for _, p := range r.ps {
		p.send(`{"op":"close"}`)
	}
	for i, p := range r.ps {
		cl := p.recv(time.Duration(opTO+30)*time.Second - time.Since(t0))
		took := time.Since(t0)
		errText, _ := cl["error"].(string)
		if cl["op"] != "close" || cl["ok"] == true || errText == "" {
			t.Fatalf("p%d close: %v, want a failure", i, cl)
		}
		if i == 0 && (!strings.Contains(errText, "channel broken") || !strings.Contains(errText, "p2:")) {
			t.Fatalf("p0 close does not name the broken channel p2: %v", cl)
		}
		if i == 0 && took > 5*time.Second {
			t.Fatalf("p0 close took %v, want an immediate refusal", took)
		}
		t.Logf("p%d close failed after %.1fs: %s", i, took.Seconds(), errText)
	}
	for i, p := range r.ps {
		p.send(`{"op":"exit"}`)
		p.wantOK(p.recv(30*time.Second), "exit")
		done := make(chan error, 1)
		go func() { done <- p.cmd.Wait() }()
		select {
		case err := <-done:
			if err != nil {
				t.Fatalf("p%d exited with %v\n%s", i, err, p.stderr.String())
			}
		case <-time.After(30 * time.Second):
			t.Fatalf("p%d did not exit within 30 s of exit\n%s", i, p.stderr.String())
		}
	}
}
