// Command hub-demo runs a go-perun virtual-payment-hub benchmark against a local
// Anvil EVM: one hub (Ingrid) + N clients, a full mesh of virtual channels over a
// star of ledger channels, an off-chain payment burst, then settlement. It reports
// per-phase on-chain gas and off-chain throughput.
//
// runScenario below is the program's control flow: five phases, each a call into the
// Client/Hub API. The other files implement those calls. The same scenario is
// exercised with assertions in hub_test.go. See ARCHITECTURE.md for the design and
// diagrams.
//
//	deploy contracts    util.deployContracts   (on-chain)
//	phase 1: ledgers     Client.OpenLedgerTo    (on-chain)
//	phase 2: virtuals    Client.OpenVirtualTo   (off-chain)
//	phase 3: payments    pay / Channel.Update   (off-chain)
//	phase 4: settle      finalize + settle      (off-chain)
//	phase 5: settle      finalize + settle      (on-chain)
package main

import (
	"flag"
	"fmt"
	"log"
	"math/big"
	"strings"
	"sync"
	"time"

	"perun.network/go-perun/client"
	"perun.network/go-perun/wire"
)

// Anvil's default, publicly known test mnemonic
// ("test test test test test test test test test test test junk") deterministically
// derives the same accounts on every run; the hex strings below are the private keys
// it produces. They are throwaway keys for a local chain only — never use them on a
// real network, since anyone can regenerate them and move any funds held.
// Index 0 = deployer, 1 = hub (Ingrid), 2.. = clients.
var anvilKeys = []string{
	"ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80", // 0 deployer
	"59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d", // 1 hub
	"5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a", // 2 client
	"7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6", // 3 client
	"47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a", // 4 client
	"8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba", // 5 client
	"92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e", // 6 client
	"4bbbf85ce3377467afe5d46f804f221813b2bb87f24d81f60f1fcdbf7cbf4356", // 7 client
}

var clientNames = []string{"Alice", "Bob", "Carol", "Dave", "Erin", "Frank"}

// Scenario parameters (shared by the demo and the tests) for the ETH asset. Points
// mode uses its own sizes; see the assetKind defaults in asset.go.
const (
	ledgerDeposit = 100.0 // ETH each side deposits into a ledger channel
	virtualStake  = 10.0  // ETH each side puts into a virtual channel
	burstAmount   = 0.001 // ETH per payment in the throughput burst
	crossAmount   = 1.0   // ETH per cross-payment on the rest of the mesh
)

type config struct {
	asset     assetKind // eth (default when empty) or points
	nodeURL   string
	chainID   uint64
	nClients  int
	payments  int
	tracePath string // if set, phase 3 replays this JSONL trace instead of the burst
	paced     bool   // with tracePath: open-loop replay at the trace timestamps
}

type phaseCost struct {
	gas uint64
	txs int
}

type results struct {
	asset              assetKind
	nClients, nVirtual int
	deploy             phaseCost
	deployBy           map[string]phaseCost // contract creations; in points mode also mintBatch ("token")
	ledgerOpen         phaseCost
	virtualOpen        phaseCost
	virtualSettle      phaseCost
	ledgerSettle       phaseCost
	// The two on-chain phases split by the contract each transaction called
	// (gasMeter.labelContracts): open = deposits (+ approves on the token in
	// points mode), settle = adjudicator conclude + asset-holder withdrawals.
	ledgerOpenBy, ledgerSettleBy map[string]phaseCost
	payments                     int
	burst                        time.Duration
	trace                        *traceResults // non-nil in trace-replay mode (-trace)
	// Per-channel wall-clock latencies of the channel-lifecycle sub-processes
	// ("Open/Close sub-process latency"). Ledger operations touch the chain and
	// take seconds (block time bound); virtual operations are off-chain and take
	// milliseconds.
	ledgerOpenLat  []time.Duration // proposal -> funded/ready, one per ledger channel
	ledgerCloseLat []time.Duration // finalize-start -> withdrawn, one per ledger channel
	vOpenLat       []time.Duration // proposal -> both handles ready, one per virtual channel
	vSettleLat     []time.Duration // finalize-start -> defunded, one per virtual channel
	// Balances of the measured virtual channel (first pair) right after the burst.
	payerBal, payeeBal *big.Int
}

// runScenario builds the topology, runs the payment burst, settles, and returns
// the measured costs. It returns an error instead of exiting, so tests can assert.
func runScenario(cfg config) (*results, error) {
	kind, err := parseAssetKind(string(cfg.asset))
	if err != nil {
		return nil, err
	}
	res := &results{asset: kind, nClients: cfg.nClients, payments: cfg.payments}
	deposit, stake := kind.defaultLedgerDeposit(), kind.defaultVirtualStake()

	// Trace-replay mode: load and validate the trace before touching the chain.
	var traceEvents []tracePayment
	if cfg.tracePath != "" {
		var nPlayers int
		var err error
		traceEvents, nPlayers, err = loadTrace(cfg.tracePath)
		if err != nil {
			return nil, fmt.Errorf("trace: %w", err)
		}
		if nPlayers != cfg.nClients {
			return nil, fmt.Errorf("trace %s has %d players (max id + 1); run with -clients %d",
				cfg.tracePath, nPlayers, nPlayers)
		}
	}

	gm, err := newGasMeter(cfg.nodeURL)
	if err != nil {
		return nil, fmt.Errorf("gas meter (is Anvil at %s?): %w", cfg.nodeURL, err)
	}

	// Deploy contracts.
	gm.mark()
	contracts, err := deployContracts(cfg.nodeURL, cfg.chainID, anvilKeys[0], kind)
	if err != nil {
		return nil, fmt.Errorf("deploy: %w", err)
	}
	gm.labelContracts(contracts)
	res.deploy.gas, res.deploy.txs, res.deployBy, _ = gm.gasSinceByContract()

	// Hub + clients on a shared in-process bus.
	bus := wire.NewLocalBus()
	hub, err := NewHub("Ingrid", bus, anvilKeys[1], cfg.nodeURL, cfg.chainID, contracts)
	if err != nil {
		return nil, fmt.Errorf("hub: %w", err)
	}
	clients := make([]*Client, cfg.nClients)
	for i := range clients {
		clients[i], err = NewClient(clientNames[i], bus, anvilKeys[2+i], cfg.nodeURL, cfg.chainID, contracts)
		if err != nil {
			return nil, fmt.Errorf("client %d: %w", i, err)
		}
	}

	// Phase 1: open ledger channels (on-chain).
	gm.mark()
	for _, c := range clients {
		t0 := time.Now()
		if err := c.OpenLedgerTo(hub, deposit, deposit); err != nil {
			return nil, fmt.Errorf("%s open ledger: %w", c.name, err)
		}
		res.ledgerOpenLat = append(res.ledgerOpenLat, time.Since(t0))
	}
	res.ledgerOpen.gas, res.ledgerOpen.txs, res.ledgerOpenBy, _ = gm.gasSinceByContract()

	// Phase 2: open a full mesh of virtual channels via the hub (off-chain).
	type vchan struct {
		payer, payee     *Client
		payerCh, payeeCh *client.Channel
	}
	var vcs []vchan
	// mesh[i][j] = client i's handle of the i<->j virtual channel (used by -trace).
	mesh := make([]map[int]*client.Channel, len(clients))
	for i := range mesh {
		mesh[i] = make(map[int]*client.Channel)
	}
	gm.mark()
	for i := 0; i < len(clients); i++ {
		for j := i + 1; j < len(clients); j++ {
			t0 := time.Now()
			ch, err := clients[i].OpenVirtualTo(clients[j], stake, stake)
			if err != nil {
				return nil, fmt.Errorf("virtual %s->%s: %w", clients[i].name, clients[j].name, err)
			}
			peerCh := <-clients[j].incomingVirtual
			res.vOpenLat = append(res.vOpenLat, time.Since(t0))
			vcs = append(vcs, vchan{clients[i], clients[j], ch, peerCh})
			mesh[i][j], mesh[j][i] = ch, peerCh
		}
	}
	res.virtualOpen.gas, res.virtualOpen.txs = gm.gasSince()
	res.nVirtual = len(vcs)

	// Phase 3: off-chain payments — either the recorded trace or the synthetic burst.
	if cfg.tracePath != "" {
		// Trace replay: fire -> ledger-channel update paying the hub,
		// hit -> virtual-channel update victim->shooter. Either closed-loop in
		// trace order (default) or open-loop at the trace timestamps (-paced).
		if cfg.paced {
			res.trace = replayTracePaced(cfg.tracePath, traceEvents, clients, mesh)
		} else {
			res.trace = replayTrace(cfg.tracePath, traceEvents, clients, mesh)
		}
		res.payments = res.trace.ok
		st := vcs[0].payerCh.State()
		res.payerBal = new(big.Int).Set(st.Balances[0][0])
		res.payeeBal = new(big.Int).Set(st.Balances[0][1])
	} else {
		// Throughput burst on the first virtual channel (off-chain).
		start := time.Now()
		for k := 0; k < cfg.payments; k++ {
			if err := pay(vcs[0].payerCh, vcs[0].payer.asset, kind.units(kind.burstAmount())); err != nil {
				return nil, fmt.Errorf("payment %d: %w", k, err)
			}
		}
		res.burst = time.Since(start)
		st := vcs[0].payerCh.State()
		res.payerBal = new(big.Int).Set(st.Balances[0][0])
		res.payeeBal = new(big.Int).Set(st.Balances[0][1])

		// A few cross-payments to exercise the rest of the mesh (many-to-many).
		for _, v := range vcs[1:] {
			if err := pay(v.payerCh, v.payer.asset, kind.units(kind.crossAmount())); err != nil {
				return nil, fmt.Errorf("cross pay %s->%s: %w", v.payer.name, v.payee.name, err)
			}
		}
	}

	// Phase 4: settle virtual channels (collaborative, off-chain).
	gm.mark()
	for _, v := range vcs {
		t0 := time.Now()
		if err := finalize(v.payerCh); err != nil {
			return nil, fmt.Errorf("finalize virtual: %w", err)
		}
		var wg sync.WaitGroup
		wg.Add(2)
		go func(v vchan) { defer wg.Done(); _ = settle(v.payerCh, false) }(v)
		go func(v vchan) { defer wg.Done(); _ = settle(v.payeeCh, false) }(v)
		wg.Wait()
		res.vSettleLat = append(res.vSettleLat, time.Since(t0))
	}
	res.virtualSettle.gas, res.virtualSettle.txs = gm.gasSince()

	// Phase 5: settle ledger channels (on-chain withdraw).
	gm.mark()
	for _, c := range clients {
		t0 := time.Now()
		if err := finalize(c.ledger); err != nil {
			return nil, fmt.Errorf("finalize ledger: %w", err)
		}
		hubCh, ok := hub.ledgerWith(c.ledger.ID())
		var wg sync.WaitGroup
		wg.Add(1)
		go func(c *Client) { defer wg.Done(); _ = settle(c.ledger, false) }(c)
		if ok {
			wg.Add(1)
			go func(h *client.Channel) { defer wg.Done(); _ = settle(h, true) }(hubCh)
		}
		wg.Wait()
		res.ledgerCloseLat = append(res.ledgerCloseLat, time.Since(t0))
	}
	res.ledgerSettle.gas, res.ledgerSettle.txs, res.ledgerSettleBy, _ = gm.gasSinceByContract()

	for _, c := range clients {
		c.shutdown()
	}
	hub.shutdown()
	return res, nil
}

func printResults(res *results) {
	fmt.Println()
	fmt.Println("================ go-perun hub demo: results ================")
	fmt.Printf("Topology: 1 hub + %d clients; %d ledger channels (star), %d virtual channels (mesh)\n",
		res.nClients, res.nClients, res.nVirtual)
	fmt.Printf("Asset: %s\n", res.asset)
	fmt.Println("------------------------------------------------------------")
	fmt.Printf("%-30s %12s %6s\n", "phase (on-chain)", "gas", "txs")
	fmt.Printf("%-30s %12d %6d\n", "deploy contracts", res.deploy.gas, res.deploy.txs)
	if res.asset == assetPoints {
		printGasSplit(res.deployBy, map[string]string{"deploy": "contract creations", "token": "mintBatch (token)"})
	}
	fmt.Printf("%-30s %12d %6d\n", "open ledger channels", res.ledgerOpen.gas, res.ledgerOpen.txs)
	printGasSplit(res.ledgerOpenBy, map[string]string{"token": "approve (token)", "asset holder": "deposit (asset holder)"})
	fmt.Printf("%-30s %12d %6d  <- headline\n", "open virtual channels", res.virtualOpen.gas, res.virtualOpen.txs)
	fmt.Printf("%-30s %12d %6d\n", "settle virtual channels", res.virtualSettle.gas, res.virtualSettle.txs)
	fmt.Printf("%-30s %12d %6d\n", "settle ledger channels", res.ledgerSettle.gas, res.ledgerSettle.txs)
	printGasSplit(res.ledgerSettleBy, map[string]string{"adjudicator": "conclude (adjudicator)", "asset holder": "withdraw (asset holder)"})
	if res.nClients > 0 {
		fmt.Printf("%-30s %12d\n", "gas / ledger-channel open", res.ledgerOpen.gas/uint64(res.nClients))
	}
	fmt.Println("------------------------------------------------------------")
	fmt.Println("Sub-process latency (wall clock, per channel):")
	fmt.Println(fmtDurSummary("ledger open   (propose->funded, on-chain)", res.ledgerOpenLat))
	fmt.Println(fmtDurSummary("ledger close  (finalize->withdrawn, on-chain)", res.ledgerCloseLat))
	fmt.Println(fmtDurSummary("virtual open  (propose->ready, off-chain)", res.vOpenLat))
	fmt.Println(fmtDurSummary("virtual settle (finalize->defunded, off-chain)", res.vSettleLat))
	fmt.Println("------------------------------------------------------------")
	if res.trace != nil {
		printTraceResults(res.trace)
	} else {
		tps := float64(res.payments) / res.burst.Seconds()
		fmt.Printf("Off-chain: %d payments in %v => %.0f payments/s, %.3f ms/payment\n",
			res.payments, res.burst.Round(time.Microsecond), tps,
			float64(res.burst.Microseconds())/float64(res.payments)/1000.0)
	}
	fmt.Println("============================================================")
}

// printGasSplit prints the per-contract lines under an on-chain phase, in a fixed
// order so ETH and points runs line up. names maps a contract label to the
// operation it stands for in this phase; anything else is printed as labelled.
func printGasSplit(by map[string]phaseCost, names map[string]string) {
	for _, label := range []string{"token", "asset holder", "adjudicator", "deploy", "other"} {
		c, ok := by[label]
		if !ok {
			continue
		}
		name := label
		if n, ok := names[label]; ok {
			name = n
		}
		fmt.Printf("  %-28s %12d %6d\n", name, c.gas, c.txs)
	}
}

// fmtDurSummary formats one line of the per-channel latency block: sample size
// plus mean/min/max. On-chain samples are seconds-scale, off-chain ones
// milliseconds-scale, so everything is rounded to 100µs.
func fmtDurSummary(label string, ds []time.Duration) string {
	if len(ds) == 0 {
		return fmt.Sprintf("  %-46s n=0", label)
	}
	lo, hi := ds[0], ds[0]
	var sum time.Duration
	for _, d := range ds {
		sum += d
		if d < lo {
			lo = d
		}
		if d > hi {
			hi = d
		}
	}
	r := func(d time.Duration) time.Duration { return d.Round(100 * time.Microsecond) }
	return fmt.Sprintf("  %-46s n=%-2d mean=%v min=%v max=%v",
		label, len(ds), r(sum/time.Duration(len(ds))), r(lo), r(hi))
}

func main() {
	mode, configPath, distributedNode := distributedFlags()
	nodeURL := flag.String("url", "ws://127.0.0.1:8545", "Ethereum (Anvil) node URL")
	asset := flag.String("asset", "eth", "channel asset: eth, or points (our ERC-20; see asset.go)")
	chainID := flag.Uint64("chainid", 31337, "chain ID (Anvil default 31337)")
	nClients := flag.Int("clients", 3, "number of client nodes (2..6)")
	payments := flag.Int("payments", 100, "off-chain payments in the throughput burst")
	tracePath := flag.String("trace", "", "JSONL game trace to replay instead of the burst (fire->hub, hit->virtual)")
	paced := flag.Bool("paced", false, "with -trace: open-loop replay, dispatching each payment at its trace timestamp")
	flag.Parse()
	if strings.HasPrefix(*mode, "uc1-") {
		if err := uc1Main(*mode); err != nil {
			log.Fatal(err)
		}
		return
	}
	if strings.HasPrefix(*mode, "uc2-") {
		if err := uc2Main(*mode); err != nil {
			log.Fatal(err)
		}
		return
	}
	if *mode != "local" {
		// -asset overrides the config's "asset" only when given explicitly, so
		// its default ("eth") never silently replaces "points" in a config.
		assetOverride := ""
		flag.Visit(func(f *flag.Flag) {
			if f.Name == "asset" {
				assetOverride = *asset
			}
		})
		if err := distributedMain(*mode, *configPath, *distributedNode, *payments, *tracePath, *paced, assetOverride); err != nil {
			log.Fatal(err)
		}
		return
	}

	if *nClients < 2 || *nClients > len(clientNames) {
		log.Fatalf("clients must be in [2,%d]", len(clientNames))
	}
	if *paced && *tracePath == "" {
		log.Fatal("-paced requires -trace")
	}

	kind, err := parseAssetKind(*asset)
	if err != nil {
		log.Fatal(err)
	}
	res, err := runScenario(config{
		asset:     kind,
		nodeURL:   *nodeURL,
		chainID:   *chainID,
		nClients:  *nClients,
		payments:  *payments,
		tracePath: *tracePath,
		paced:     *paced,
	})
	if err != nil {
		log.Fatal(err)
	}
	printResults(res)
}
