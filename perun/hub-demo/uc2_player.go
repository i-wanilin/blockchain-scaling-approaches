// uc2_player.go — -mode uc2-player: one player's Perun sidecar in UC2.
//
// The player holds one ledger channel to the hub (its shots, "ammo", are paid
// to the hub there) and one virtual channel through the hub with every other
// player (hits, paid in both directions). The Python payment client drives it
// exactly as UC1's drives -mode uc1-customer: one JSON request per line on
// stdin, one reply per request on stdout in request order, logs on stderr.
// Stdout carries protocol lines only.
//
//	init     load the cluster config, build the node, register with the hub
//	open     ledger channel (player ledger_deposit, hub hub_deposit), publish
//	         it, wait for every ledger, open the mesh (virtual_stake each
//	         side), the virtual-open barrier; timed, gas-metered
//	pay      one update on the channel named by "to": "hub" = the ledger
//	         channel, "p<k>" = the virtual channel with player k
//	state    every channel's version, balances [mine, peer], broken
//	quiesce  drop the pays not yet started on every channel, wait for those
//	         in flight
//	close    the payments-done barrier, snapshot, the snapshot barrier, then
//	         closeDistributedClient (virtual channels, then the ledger)
//	exit     reply and leave
//
// Every channel has its own FIFO (uc1FIFO: one update in flight, in request
// order), so a slow channel never delays the others. A pay never retries: an
// update timeout leaves the payment unresolved and breaks only that channel,
// and close then refuses, naming it. Who wins a collision is fixed by the
// channel index (update_policy.go): the lower cluster index opened the
// virtual channel and has priority; on the ledger the player (index 0) does.
package main

import (
	"bufio"
	"crypto/ecdsa"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	ethwallet "github.com/perun-network/perun-eth-backend/wallet"

	"perun.network/go-perun/client"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/wire"
	"perun.network/perun-examples/hub-demo/lanwire"
)

// uc2Request is one stdin line. Amounts are decimal strings.
type uc2Request struct {
	Op string `json:"op"`
	// init
	Key             string  `json:"key"`
	Name            string  `json:"name"`
	Config          string  `json:"config"`
	StateDir        string  `json:"state_dir"`
	UpdateTimeoutS  float64 `json:"update_timeout_s"`
	Persist         bool    `json:"persist"`
	StartupTimeoutS float64 `json:"startup_timeout_s"`
	// pay
	ID        string `json:"id"`
	To        string `json:"to"`
	AmountWei string `json:"amount_wei"`
}

type uc2InitReply struct {
	Op      string `json:"op"`
	OK      bool   `json:"ok"`
	Address string `json:"address"`
	Index   int    `json:"index"`
}

// uc2OpenReply times the ledger Open (on-chain) and the virtual Open
// (off-chain) apart. t_virtual_start is when every ledger was ready and this
// player began its mesh; t_virtual_open is when it held all its virtual
// channels (before the barrier that waits for the other players).
type uc2OpenReply struct {
	Op              string            `json:"op"`
	OK              bool              `json:"ok"`
	TStart          float64           `json:"t_start"`
	TLedgerFunded   float64           `json:"t_ledger_funded"`
	TVirtualStart   float64           `json:"t_virtual_start"`
	TVirtualOpen    float64           `json:"t_virtual_open"`
	LedgerChannelID string            `json:"ledger_channel_id"`
	Virtual         map[string]string `json:"virtual"`
	L1GasUsed       uint64            `json:"l1_gas_used"`
	L1FeeWei        string            `json:"l1_fee_wei"`
	Txs             int               `json:"txs"`
}

type uc2ChannelState struct {
	Version     uint64   `json:"version"`
	BalancesWei []string `json:"balances_wei"` // [mine, peer]
	Broken      bool     `json:"broken"`
}

type uc2StateReply struct {
	Op       string                     `json:"op"`
	OK       bool                       `json:"ok"`
	Channels map[string]uc2ChannelState `json:"channels"`
}

type uc2QuiesceReply struct {
	Op       string            `json:"op"`
	OK       bool              `json:"ok"`
	Dropped  int               `json:"dropped"`
	Versions map[string]uint64 `json:"versions"`
}

// uc2CloseReply: t_start is the close request (before the payments-done
// barrier), t_virtual_settled the moment every virtual channel was settled,
// t_withdrawn the end. virtual_settle_s is the time of this player's own
// settle calls only (the round barriers between them are not counted).
type uc2CloseReply struct {
	Op                     string             `json:"op"`
	OK                     bool               `json:"ok"`
	TStart                 float64            `json:"t_start"`
	TVirtualSettled        float64            `json:"t_virtual_settled"`
	TWithdrawn             float64            `json:"t_withdrawn"`
	VirtualSettleS         float64            `json:"virtual_settle_s"`
	LedgerSettleS          float64            `json:"ledger_settle_s"`
	FinalLedgerVersion     uint64             `json:"final_ledger_version"`
	FinalLedgerBalancesWei []string           `json:"final_ledger_balances_wei"` // [mine, hub]
	VirtualFinal           []channelFinal     `json:"virtual_final"`
	Collisions             int                `json:"collisions"`
	L1GasUsed              uint64             `json:"l1_gas_used"`
	L1FeeWei               string             `json:"l1_fee_wei"`
	Txs                    int                `json:"txs"`
	ByContract             map[string]gasCost `json:"by_contract"`
}

// uc2Backend is the player's channels; the tests replace it.
type uc2Backend interface {
	Init(req uc2Request) (address string, index int, err error)
	Open() (uc2OpenReply, error)
	Targets() []string                                                    // after Open: "hub" and "p<k>" for every other player
	Update(target string, amount *big.Int) (uint64, error)                // one update paying the peer of target
	Balance(target string) *big.Int                                       // this player's own balance in target
	State(target string) (version uint64, balances []*big.Int, err error) // [mine, peer]
	Close() (uc2CloseReply, error)
	Shutdown()
}

// ---------------------------------------------------------------------------
// The sidecar: requests in, replies out in request order.
// ---------------------------------------------------------------------------

type uc2Sidecar struct {
	b        uc2Backend
	replies  *uc1Replies
	updateTO time.Duration
	inited   bool
	opened   bool
	closing  bool
	fifos    map[string]*uc1FIFO
	targets  []string // sorted
}

// runUC2Sidecar serves requests from in until exit or end of input.
func runUC2Sidecar(in io.Reader, out io.Writer, b uc2Backend) error {
	s := &uc2Sidecar{b: b, replies: newUC1Replies(out), updateTO: 10 * time.Second}
	defer func() {
		for _, f := range s.fifos {
			f.Stop()
		}
	}()
	sc := bufio.NewScanner(in)
	sc.Buffer(make([]byte, 64*1024), 1<<20)
	exit := false
	for !exit && sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		exit = s.handle([]byte(line))
	}
	err := s.replies.close()
	b.Shutdown()
	if err == nil && !exit {
		err = sc.Err()
	}
	return err
}

func (s *uc2Sidecar) fail(slot chan any, op, format string, args ...any) {
	slot <- uc1ErrorReply{Op: op, Error: fmt.Sprintf(format, args...)}
}

// brokenChannels lists "target: first error" for every broken channel.
func (s *uc2Sidecar) brokenChannels() []string {
	var out []string
	for _, t := range s.targets {
		if why := s.fifos[t].Broken(); why != "" {
			out = append(out, t+": "+why)
		}
	}
	return out
}

// handle serves one request line; it returns true on exit.
func (s *uc2Sidecar) handle(line []byte) bool {
	slot := s.replies.reserve()
	var req uc2Request
	if err := json.Unmarshal(line, &req); err != nil {
		var probe struct {
			Op string `json:"op"`
			ID string `json:"id"`
		}
		if json.Unmarshal(line, &probe) == nil && probe.Op == "pay" {
			slot <- uc1PayReply{Op: "pay", ID: probe.ID, Error: "rejected: bad request: " + err.Error()}
			return false
		}
		s.fail(slot, probe.Op, "bad request: %v", err)
		return false
	}
	switch req.Op {
	case "init":
		if s.inited {
			s.fail(slot, "init", "already initialised")
			return false
		}
		if req.UpdateTimeoutS > 0 {
			s.updateTO = time.Duration(req.UpdateTimeoutS * float64(time.Second))
		}
		addr, idx, err := s.b.Init(req)
		if err != nil {
			s.fail(slot, "init", "%v", err)
			return false
		}
		s.inited = true
		slot <- uc2InitReply{Op: "init", OK: true, Address: addr, Index: idx}
	case "open":
		if !s.inited || s.opened {
			s.fail(slot, "open", "open needs init and runs once")
			return false
		}
		rep, err := s.b.Open()
		if err != nil {
			s.fail(slot, "open", "%v", err)
			return false
		}
		s.fifos = make(map[string]*uc1FIFO)
		for _, t := range s.b.Targets() {
			t := t
			s.fifos[t] = newUC1FIFO(
				func(amount *big.Int) (uint64, error) { return s.b.Update(t, amount) },
				func() *big.Int { return s.b.Balance(t) })
			s.targets = append(s.targets, t)
		}
		sort.Strings(s.targets)
		s.opened = true
		rep.Op, rep.OK = "open", true
		slot <- rep
	case "pay":
		amount, err := parseWei(req.AmountWei, false)
		fifo := s.fifos[req.To]
		switch {
		case err != nil:
			slot <- uc1PayReply{Op: "pay", ID: req.ID, Error: "rejected: amount_wei: " + err.Error()}
		case !s.opened:
			slot <- uc1PayReply{Op: "pay", ID: req.ID, Error: "rejected: no open channel"}
		case fifo == nil:
			slot <- uc1PayReply{Op: "pay", ID: req.ID, Error: "rejected: no channel to " + req.To}
		case s.closing:
			slot <- uc1PayReply{Op: "pay", ID: req.ID, Error: "rejected: channel closing"}
		default:
			id := req.ID
			fifo.Pay(amount, func(o uc1PayOutcome) {
				slot <- uc1PayReply{Op: "pay", ID: id, OK: o.OK, TSubmitted: unixSeconds(o.TSubmitted),
					TConfirmed: unixSeconds(o.TConfirmed), Version: o.Version, Error: o.Err}
			})
		}
	case "state":
		if !s.opened {
			s.fail(slot, "state", "no open channel")
			return false
		}
		chans := make(map[string]uc2ChannelState, len(s.targets))
		for _, t := range s.targets {
			v, bals, err := s.b.State(t)
			if err != nil {
				s.fail(slot, "state", "%s: %v", t, err)
				return false
			}
			chans[t] = uc2ChannelState{Version: v, BalancesWei: weiStrings(bals), Broken: s.fifos[t].Broken() != ""}
		}
		slot <- uc2StateReply{Op: "state", OK: true, Channels: chans}
	case "quiesce":
		// Every queue at once: each waits at most the update timeout for its
		// own pay in flight.
		var wg sync.WaitGroup
		var mu sync.Mutex
		dropped := 0
		for _, t := range s.targets {
			wg.Add(1)
			go func(f *uc1FIFO) {
				defer wg.Done()
				n := f.Quiesce(s.updateTO + time.Second)
				mu.Lock()
				dropped += n
				mu.Unlock()
			}(s.fifos[t])
		}
		wg.Wait()
		versions := make(map[string]uint64, len(s.targets))
		for _, t := range s.targets {
			v, _, _ := s.b.State(t)
			versions[t] = v
		}
		slot <- uc2QuiesceReply{Op: "quiesce", OK: true, Dropped: dropped, Versions: versions}
	case "close":
		if !s.opened || s.closing {
			s.fail(slot, "close", "close needs an open channel, once")
			return false
		}
		s.closing = true
		// The close follows every pay sent before it, on every channel.
		var wg sync.WaitGroup
		for _, t := range s.targets {
			wg.Add(1)
			s.fifos[t].Do(wg.Done)
		}
		go func() {
			wg.Wait()
			if broken := s.brokenChannels(); len(broken) > 0 {
				s.fail(slot, "close", "channel broken: %s", strings.Join(broken, "; "))
				return
			}
			rep, err := s.b.Close()
			if err != nil {
				s.fail(slot, "close", "%v", err)
				return
			}
			rep.Op, rep.OK = "close", true
			slot <- rep
		}()
	case "exit":
		slot <- uc1ErrorReply{Op: "exit", OK: true}
		return true
	default:
		s.fail(slot, req.Op, "unknown op %q", req.Op)
	}
	return false
}

// ---------------------------------------------------------------------------
// The real backend: runClient's steps, driven one request at a time.
// ---------------------------------------------------------------------------

type uc2PlayerBackend struct {
	cfg       clusterConfig
	nc        clusterNode
	name      string
	index     int
	ethAddr   common.Address
	netrt     *networkRuntime
	c         *Client
	man       manifest
	hubWire   map[wallet.BackendID]wire.Address
	contracts chainContracts

	mu        sync.Mutex
	channels  map[string]*client.Channel // "hub" and "p<k>"
	mesh      map[int]*client.Channel
	completed bool
}

func uc2Target(index int) string { return fmt.Sprintf("p%d", index) }

func (b *uc2PlayerBackend) Init(req uc2Request) (string, int, error) {
	if req.Key == "" || req.Name == "" || req.Config == "" {
		return "", 0, fmt.Errorf("init needs key, name and config")
	}
	if req.Persist && req.StateDir == "" {
		return "", 0, fmt.Errorf("persist needs state_dir")
	}
	cfg, err := loadClusterConfig(req.Config)
	if err != nil {
		return "", 0, fmt.Errorf("config: %w", err)
	}
	if cfg.HubDeposit <= 0 {
		return "", 0, fmt.Errorf("%s is not a UC2 config (no hub_deposit)", req.Config)
	}
	nc, ok := clusterClient(cfg, req.Name)
	if !ok {
		return "", 0, fmt.Errorf("%q is not a client in %s", req.Name, req.Config)
	}
	b.cfg, b.nc, b.name, b.index = cfg, nc, req.Name, clusterClientIndex(cfg, req.Name)
	updatePol = cfg.updatePolicy()
	if req.UpdateTimeoutS > 0 {
		updatePol.Long = time.Duration(req.UpdateTimeoutS * float64(time.Second))
	}
	startup := time.Duration(req.StartupTimeoutS * float64(time.Second))
	if startup <= 0 {
		startup = cfg.startupTimeout()
	}
	key := strings.TrimPrefix(req.Key, "0x")
	var ek *ecdsa.PrivateKey
	if ek, err = crypto.HexToECDSA(key); err != nil {
		return "", 0, fmt.Errorf("key: %w", err)
	}
	b.ethAddr = crypto.PubkeyToAddress(ek.PublicKey)
	// The mint check was made against the config's address for this player.
	if nc.Ethereum != "" && common.HexToAddress(nc.Ethereum) != b.ethAddr {
		return "", 0, fmt.Errorf("the init key is %s's, but %s says %s is %s", b.ethAddr.Hex(), req.Config, req.Name, nc.Ethereum)
	}

	var id *lanwire.Identity
	if req.StateDir != "" {
		id, err = lanwire.LoadOrCreate(req.StateDir, req.Name)
	} else {
		id, err = lanwire.NewEphemeral(req.Name)
	}
	if err != nil {
		return "", 0, fmt.Errorf("wire identity: %w", err)
	}
	if b.netrt, err = newNetworkRuntime(id, nc.Bind); err != nil {
		return "", 0, fmt.Errorf("network bus: %w", err)
	}
	enc, err := encodeWire(map[wallet.BackendID]wire.Address{ethwallet.BackendID: id.Address})
	if err != nil {
		return "", 0, err
	}
	// The hub may still be starting: retry until startup passes (a 4xx refusal is final).
	var regResp struct {
		Manifest manifest
		Peers    []peerInfo
	}
	control := strings.TrimRight(nc.Control, "/")
	if err := uc1Retry(startup, "register with the hub", func() error {
		return httpJSON(http.MethodPost, control+"/v1/register", registration{Name: req.Name, Endpoint: nc.Endpoint, Wire: enc, Ethereum: b.ethAddr.Hex()}, &regResp)
	}); err != nil {
		return "", 0, err
	}
	b.man = regResp.Manifest
	hubWire, contracts, err := joinHub(cfg, b.netrt.dialer, regResp.Manifest)
	if err != nil {
		return "", 0, err
	}
	if cfg.ContractsFile != "" {
		file, err := loadUC2Contracts(cfg.ContractsFile)
		if err != nil {
			return "", 0, fmt.Errorf("contracts_file: %w", err)
		}
		if file.chain() != contracts {
			return "", 0, fmt.Errorf("the hub runs contracts %+v, %s says %+v", contracts, cfg.ContractsFile, file.chain())
		}
	}
	b.hubWire, b.contracts = hubWire, contracts
	persistDir := ""
	if req.Persist {
		persistDir = req.StateDir
	}
	c, err := NewClientWithIdentity(req.Name, b.netrt.bus, key, cfg.ChainURL, cfg.ChainID, contracts, id, persistDir)
	if err != nil {
		return "", 0, err
	}
	b.c = c
	return b.ethAddr.Hex(), b.index, nil
}

func (b *uc2PlayerBackend) meter() (*gasMeter, error) {
	gm, err := newGasMeterFrom(b.cfg.ChainURL, b.ethAddr)
	if err != nil {
		return nil, err
	}
	gm.labelContracts(b.contracts)
	return gm, nil
}

func (b *uc2PlayerBackend) Open() (uc2OpenReply, error) {
	if b.c == nil {
		return uc2OpenReply{}, fmt.Errorf("not initialised")
	}
	gm, err := b.meter()
	if err != nil {
		return uc2OpenReply{}, fmt.Errorf("gas meter: %w", err)
	}
	t0 := time.Now()
	if err := b.c.OpenLedgerToWire(b.hubWire, b.cfg.LedgerDeposit, b.cfg.ledgerHubDeposit(), b.cfg.ChallengeSecs); err != nil {
		return uc2OpenReply{}, fmt.Errorf("open ledger: %w", err)
	}
	t1 := time.Now()
	gas, txs, _, fee := gm.gasSinceByContract()
	b.c.RequireRegisteredVirtualPeers(true)
	b.c.RequireSafePaymentUpdates(!b.cfg.DisablePaymentPolicy)
	if err := publishLedgerAndWait(b.cfg, b.nc, b.netrt.dialer, b.c, b.name); err != nil {
		return uc2OpenReply{}, err
	}
	t2 := time.Now()
	control := strings.TrimRight(b.nc.Control, "/")
	mesh, took, _, err := openMeshAtBarrier(b.cfg, control, b.c, b.netrt.dialer, b.name)
	if err != nil {
		return uc2OpenReply{}, err
	}
	chans := map[string]*client.Channel{"hub": b.c.ledger}
	virtual := make(map[string]string, len(mesh))
	for k, ch := range mesh {
		chans[uc2Target(k)] = ch
		id := ch.ID()
		virtual[uc2Target(k)] = "0x" + hex.EncodeToString(id[:])
	}
	b.mu.Lock()
	b.channels, b.mesh = chans, mesh
	b.mu.Unlock()
	lid := b.c.ledger.ID()
	return uc2OpenReply{TStart: unixSeconds(t0), TLedgerFunded: unixSeconds(t1),
		TVirtualStart: unixSeconds(t2), TVirtualOpen: unixSeconds(t2.Add(took)),
		LedgerChannelID: "0x" + hex.EncodeToString(lid[:]), Virtual: virtual,
		L1GasUsed: gas, L1FeeWei: fee.String(), Txs: txs}, nil
}

func (b *uc2PlayerBackend) Targets() []string {
	b.mu.Lock()
	defer b.mu.Unlock()
	out := make([]string, 0, len(b.channels))
	for t := range b.channels {
		out = append(out, t)
	}
	return out
}

func (b *uc2PlayerBackend) channel(target string) *client.Channel {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.channels[target]
}

func (b *uc2PlayerBackend) Update(target string, amount *big.Int) (uint64, error) {
	ch := b.channel(target)
	if ch == nil {
		return 0, fmt.Errorf("no channel to %s", target)
	}
	if _, err := payCounted(ch, b.c.asset, amount); err != nil {
		return 0, err
	}
	return ch.State().Version, nil
}

func (b *uc2PlayerBackend) Balance(target string) *big.Int {
	ch := b.channel(target)
	if ch == nil {
		return nil
	}
	st := ch.State()
	if len(st.Balances) == 0 || int(ch.Idx()) >= len(st.Balances[0]) {
		return nil
	}
	return new(big.Int).Set(st.Balances[0][ch.Idx()])
}

func (b *uc2PlayerBackend) State(target string) (uint64, []*big.Int, error) {
	ch := b.channel(target)
	if ch == nil {
		return 0, nil, fmt.Errorf("no channel to %s", target)
	}
	st := ch.State()
	if len(st.Balances) == 0 || len(st.Balances[0]) != 2 {
		return 0, nil, fmt.Errorf("%s: unexpected allocation", target)
	}
	me := ch.Idx()
	return st.Version, []*big.Int{new(big.Int).Set(st.Balances[0][me]), new(big.Int).Set(st.Balances[0][1-me])}, nil
}

func (b *uc2PlayerBackend) Close() (uc2CloseReply, error) {
	control := strings.TrimRight(b.nc.Control, "/")
	t0 := time.Now()
	virtualFinal, err := endPaymentsAndSnapshot(b.cfg, control, b.c, b.name)
	if err != nil {
		return uc2CloseReply{}, err
	}
	gm, err := b.meter()
	if err != nil {
		return uc2CloseReply{}, fmt.Errorf("gas meter: %w", err)
	}
	b.mu.Lock()
	mesh := b.mesh
	b.mu.Unlock()
	virtualDur, ledgerDur, err := closeDistributedClient(b.cfg, b.nc, b.c, b.name, mesh)
	if err != nil {
		return uc2CloseReply{}, err
	}
	t2 := time.Now()
	gas, txs, by, fee := gm.gasSinceByContract()
	st := b.c.ledger.State()
	collisions := int(gaveWayTotal.Load())
	// The hub leaves only when every client has uploaded a result and said done.
	result := makeDistributedResultFile(distributedResult{
		RunID: b.man.RunID, Node: b.name, Collisions: collisions, VirtualFinal: virtualFinal,
		VirtualSettle: virtualDur, LedgerSettle: ledgerDur, Completed: true,
		StrictPaymentPolicy: !b.cfg.DisablePaymentPolicy, Asset: b.cfg.assetKind(),
		LedgerSettleGas: newGasFile(gas, txs, by, fee),
	})
	if err := httpJSON(http.MethodPost, control+"/v1/result", result, nil); err != nil {
		return uc2CloseReply{}, fmt.Errorf("upload result to hub: %w", err)
	}
	if err := httpJSON(http.MethodPost, control+"/v1/client-done", nodeMark{Name: b.name}, nil); err != nil {
		return uc2CloseReply{}, fmt.Errorf("acknowledge completed run: %w", err)
	}
	b.mu.Lock()
	b.completed = true
	b.mu.Unlock()
	return uc2CloseReply{TStart: unixSeconds(t0), TVirtualSettled: unixSeconds(t2.Add(-ledgerDur)), TWithdrawn: unixSeconds(t2),
		VirtualSettleS: virtualDur.Seconds(), LedgerSettleS: ledgerDur.Seconds(),
		FinalLedgerVersion: st.Version, FinalLedgerBalancesWei: weiStrings(copyBals(st.Balances)),
		VirtualFinal: virtualFinal, Collisions: collisions,
		L1GasUsed: gas, L1FeeWei: fee.String(), Txs: txs, ByContract: newGasFile(gas, txs, by, fee).ByContract}, nil
}

func (b *uc2PlayerBackend) Shutdown() {
	suppressExpectedShutdownLogs()
	if b.c != nil {
		b.c.shutdown()
	}
	if b.netrt != nil {
		b.mu.Lock()
		done := b.completed
		b.mu.Unlock()
		if done {
			// As runClient: release the port, let process exit close the streams.
			b.netrt.stopListening()
		} else {
			b.netrt.close()
		}
	}
}

func runUC2PlayerMain(in io.Reader, out io.Writer) error {
	configureDistributedLogging()
	return runUC2Sidecar(in, out, &uc2PlayerBackend{})
}
