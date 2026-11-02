// uc1_sensor.go — -mode uc1-sensor: the payee end of UC1's direct channels.
//
// The sensor accepts one ledger channel from each admitted customer (customer
// deposit = -deposit-wei, sensor deposit 0, so the sensor sends no transaction
// at Open), accepts the customers' payment updates under the payment policy
// [Q7], and, when a customer finalizes, settles its half as the secondary [Q2]:
// the customer concludes, each party withdraws its own balance. It writes
// sensor-result.json once every channel is settled (exit 0), when the settle
// deadline passes (exit 1) or on SIGTERM (exit 143); only the first is complete.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math/big"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/ethereum/go-ethereum/common"
	ethwallet "github.com/perun-network/perun-eth-backend/wallet"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/wire"
	"perun.network/perun-examples/hub-demo/lanwire"
)

// uc1Admission is the sensor's channel-proposal policy. The Hub's handler
// accepts any ledger channel; the sensor accepts exactly the one it expects.
type uc1Admission struct {
	asset      channel.Asset
	deposit    *big.Int
	challenge  uint64
	self       map[wallet.BackendID]wire.Address
	allow      map[common.Address]bool
	registered map[wire.AddrKey]common.Address // wire identity -> Ethereum address it registered
}

// check returns why lcp is not the expected UC1 channel, or nil: two parties,
// the registered customer at index 0 and the sensor at index 1, the customer's
// participant address is the one it registered and is in -allow, no app,
// our asset only, balances [deposit, 0], nothing locked, challenge as set.
func (a *uc1Admission) check(lcp *client.LedgerChannelProposalMsg) error {
	if len(lcp.Peers) != 2 {
		return fmt.Errorf("want a 2-party channel, got %d parties", len(lcp.Peers))
	}
	if !channel.EqualWireMaps(lcp.Peers[1], a.self) {
		return fmt.Errorf("the sensor must be participant 1")
	}
	regEth, ok := a.registered[wire.Keys(lcp.Peers[0])]
	if !ok {
		return fmt.Errorf("proposer is not registered")
	}
	part, ok := lcp.Participant[ethwallet.BackendID].(*ethwallet.Address)
	if !ok || part == nil {
		return fmt.Errorf("proposer has no Ethereum participant address")
	}
	partEth := ethwallet.AsEthAddr(part)
	if partEth != regEth {
		return fmt.Errorf("participant %s is not the registered address %s", partEth.Hex(), regEth.Hex())
	}
	if !a.allow[partEth] {
		return fmt.Errorf("participant %s is not in -allow", partEth.Hex())
	}
	if lcp.ChallengeDuration != a.challenge {
		return fmt.Errorf("challenge %d s, want %d s", lcp.ChallengeDuration, a.challenge)
	}
	if lcp.App != nil && !channel.IsNoApp(lcp.App) {
		return fmt.Errorf("app channels are not accepted")
	}
	bals := lcp.InitBals
	if bals == nil || len(bals.Assets) != 1 || !bals.Assets[0].Equal(a.asset) {
		return fmt.Errorf("want exactly the sensor's ETH asset")
	}
	if len(bals.Locked) != 0 {
		return fmt.Errorf("locked sub-allocations are not accepted")
	}
	if len(bals.Balances) != 1 || len(bals.Balances[0]) != 2 {
		return fmt.Errorf("invalid balance dimensions")
	}
	if bals.Balances[0][0].Cmp(a.deposit) != 0 || bals.Balances[0][1].Sign() != 0 {
		return fmt.Errorf("balances [%s, %s], want [%s, 0]", bals.Balances[0][0], bals.Balances[0][1], a.deposit)
	}
	if lcp.FundingAgreement != nil && !lcp.FundingAgreement.Equal(bals.Balances) {
		return fmt.Errorf("funding agreement differs from the initial balances")
	}
	return nil
}

// uc1CheckUpdate is the sensor's update policy: only the customer (index 0)
// proposes, and with strict it may only lower its own balance [Q7]. A final
// state with unchanged balances passes, so the customer can always close.
func uc1CheckUpdate(current *channel.State, up client.ChannelUpdate, strict bool) error {
	if up.State == nil {
		return fmt.Errorf("missing state")
	}
	if up.ActorIdx != 0 {
		return fmt.Errorf("only the customer (index 0) may propose, actor is %d", up.ActorIdx)
	}
	if strict {
		return validatePaymentUpdate(current, up)
	}
	return nil
}

// uc1SensorChannel is the sensor's record of one customer channel.
type uc1SensorChannel struct {
	ch                                  *client.Channel
	name, ethereum                      string
	accepted, payments, rejected        int
	version                             uint64
	balances                            []*big.Int
	final, settled                      bool
	tOpened, tFinal, tSettle0, tSettle1 time.Time
	settleErr                           string
}

type uc1SensorChannelFile struct {
	Name            string   `json:"name"`
	Ethereum        string   `json:"ethereum"`
	ChannelID       string   `json:"channel_id"`
	AcceptedUpdates int      `json:"accepted_updates"` // payments + the finalize
	Payments        int      `json:"payments"`         // accepted non-final updates
	RejectedUpdates int      `json:"rejected_updates"`
	LastVersion     uint64   `json:"last_version"`
	BalancesWei     []string `json:"balances_wei"` // [customer, sensor] of the last accepted state
	Final           bool     `json:"final"`
	TOpened         float64  `json:"t_opened"`
	TFinal          float64  `json:"t_final"`
	TSettleStart    float64  `json:"t_settle_start"`
	TSettleEnd      float64  `json:"t_settle_end"`
	Settled         bool     `json:"settled"`
	SettleError     string   `json:"settle_error,omitempty"`
}

type uc1SensorResult struct {
	Complete          bool                   `json:"complete"`
	ExitReason        string                 `json:"exit_reason"` // settled, settle-deadline, signal
	Sensor            string                 `json:"sensor"`
	Adjudicator       string                 `json:"adjudicator"`
	AssetHolder       string                 `json:"asset_holder"`
	ChainID           uint64                 `json:"chain_id"`
	CustomersExpected int                    `json:"customers_expected"`
	DepositWei        string                 `json:"deposit_wei"`
	Strict            bool                   `json:"strict"`
	Persist           bool                   `json:"persist"`
	Channels          []uc1SensorChannelFile `json:"channels"`
	// The sensor's own transactions, split at the first finalize: before it
	// (expected none, it deposits 0) and from it on (the secondary withdrawals,
	// plus a conclude only if a customer failed to conclude). The settles of
	// several channels overlap, so the close cost is the sum over all of them;
	// the backend does not hand out per-channel receipts.
	OpenGas     *gasFile `json:"open_gas"`
	CloseGas    *gasFile `json:"close_gas"`
	Errors      []string `json:"errors"`
	TStarted    float64  `json:"t_started"`
	TFirstFinal float64  `json:"t_first_final"`
	TWritten    float64  `json:"t_written"`
}

type uc1Status struct {
	ChannelsOpen    int      `json:"channels_open"`
	ChannelsSettled int      `json:"channels_settled"`
	Errors          []string `json:"errors"`
}

type uc1Sensor struct {
	n      *node
	dialer interface {
		Register(map[wallet.BackendID]wire.Address, string)
	}
	strict   bool
	expected int
	updateTO time.Duration
	settleTO time.Duration
	man      uc1Manifest
	gm       *gasMeter
	tStarted time.Time

	mu         sync.Mutex
	adm        *uc1Admission
	names      map[wire.AddrKey]string
	admitted   map[wire.AddrKey]bool
	chans      map[channel.ID]*uc1SensorChannel
	order      []channel.ID
	errs       []string
	firstFinal time.Time
	openGas    *gasFile
	openGasMu  sync.Mutex
	finalOnce  sync.Once
	finalSeen  chan struct{}
	allDone    chan struct{}
	doneOnce   sync.Once
}

func (s *uc1Sensor) addError(format string, args ...any) {
	msg := fmt.Sprintf(format, args...)
	fmt.Fprintln(os.Stderr, "uc1-sensor:", msg)
	s.mu.Lock()
	s.errs = append(s.errs, msg)
	s.mu.Unlock()
}

func (s *uc1Sensor) proposalHandler() client.ProposalHandlerFunc {
	return func(cp client.ChannelProposal, r *client.ProposalResponder) {
		ctx, cancel := context.WithTimeout(context.Background(), s.updateTO)
		defer cancel()
		lcp, ok := cp.(*client.LedgerChannelProposalMsg)
		if !ok {
			_ = r.Reject(ctx, "uc1 sensor accepts only ledger channels")
			return
		}
		s.mu.Lock()
		err := s.adm.check(lcp)
		key := wire.Keys(lcp.Peers[0])
		if err == nil && s.admitted[key] {
			err = fmt.Errorf("this customer already has a channel")
		}
		if err == nil && len(s.admitted) >= s.expected {
			err = fmt.Errorf("the sensor already has %d channels", s.expected)
		}
		if err == nil {
			s.admitted[key] = true
		}
		s.mu.Unlock()
		if err != nil {
			s.addError("rejected channel proposal: %v", err)
			_ = r.Reject(ctx, "uc1 sensor: "+err.Error())
			return
		}
		// Funding waits for the customer's deposit, so the context must outlive
		// a few blocks and the challenge duration; it never limits a payment.
		fctx, fcancel := context.WithTimeout(context.Background(), s.settleTO)
		defer fcancel()
		ch, err := r.Accept(fctx, lcp.Accept(s.n.wAddr, client.WithRandomNonce()))
		if err != nil {
			s.mu.Lock()
			delete(s.admitted, key)
			s.mu.Unlock()
			s.addError("accept channel: %v", err)
			return
		}
		s.n.startWatching(ch)
	}
}

// onNewChannel records a channel as soon as go-perun registers it (before
// Accept returns), so the first payment always finds its record.
func (s *uc1Sensor) onNewChannel(ch *client.Channel) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.chans[ch.ID()]; ok {
		return
	}
	rec := &uc1SensorChannel{ch: ch, tOpened: time.Now()}
	if peers := ch.Peers(); len(peers) > 0 {
		key := wire.Keys(peers[0])
		rec.name = s.names[key]
		rec.ethereum = s.adm.registered[key].Hex()
	}
	st := ch.State()
	rec.version = st.Version
	rec.balances = copyBals(st.Balances)
	s.chans[ch.ID()] = rec
	s.order = append(s.order, ch.ID())
}

func copyBals(b channel.Balances) []*big.Int {
	if len(b) == 0 {
		return nil
	}
	out := make([]*big.Int, len(b[0]))
	for i, v := range b[0] {
		out[i] = new(big.Int).Set(v)
	}
	return out
}

func (s *uc1Sensor) updateHandler() client.UpdateHandlerFunc {
	return func(cur *channel.State, up client.ChannelUpdate, r *client.UpdateResponder) {
		ctx, cancel := context.WithTimeout(context.Background(), s.updateTO)
		defer cancel()
		s.mu.Lock()
		rec := s.chans[cur.ID]
		s.mu.Unlock()
		if rec == nil {
			s.addError("update on unknown channel %x", cur.ID)
			_ = r.Reject(ctx, "unknown channel")
			return
		}
		if err := uc1CheckUpdate(cur, up, s.strict); err != nil {
			s.mu.Lock()
			rec.rejected++
			s.mu.Unlock()
			_ = r.Reject(ctx, "payment policy: "+err.Error())
			return
		}
		if err := r.Accept(ctx); err != nil {
			s.addError("%s: accept update v%d: %v", rec.name, up.State.Version, err)
			return
		}
		s.mu.Lock()
		rec.accepted++
		rec.version = up.State.Version
		rec.balances = copyBals(up.State.Balances)
		startSettle := false
		if up.State.IsFinal {
			if !rec.final {
				rec.final, rec.tFinal, startSettle = true, time.Now(), true
				if s.firstFinal.IsZero() {
					s.firstFinal = rec.tFinal
				}
			}
		} else {
			rec.payments++
		}
		s.mu.Unlock()
		if startSettle {
			s.finalOnce.Do(func() { close(s.finalSeen) })
			// The machine lock is held while this handler runs; the settle
			// needs it, so it runs in its own goroutine after Accept returned.
			go s.settle(rec)
		}
	}
}

// settle is the sensor's half of the close [Q2: secondary]: it waits for the
// customer's conclude, then withdraws its own (non-zero) balance.
func (s *uc1Sensor) settle(rec *uc1SensorChannel) {
	s.snapshotOpenGas()
	ctx, cancel := context.WithTimeout(context.Background(), s.settleTO)
	defer cancel()
	t0 := time.Now()
	err := settleContext(ctx, rec.ch, true)
	t1 := time.Now()
	s.mu.Lock()
	rec.tSettle0, rec.tSettle1 = t0, t1
	if err != nil {
		rec.settleErr = err.Error()
	} else {
		rec.settled = true
	}
	all := len(s.chans) >= s.expected
	for _, c := range s.chans {
		all = all && c.settled
	}
	s.mu.Unlock()
	if err != nil {
		s.addError("%s: settle: %v", rec.name, err)
	}
	if all {
		s.doneOnce.Do(func() { close(s.allDone) })
	}
}

// snapshotOpenGas records the sensor's transactions before the first close
// started (expected: none).
func (s *uc1Sensor) snapshotOpenGas() {
	s.openGasMu.Lock()
	defer s.openGasMu.Unlock()
	if s.openGas == nil {
		s.openGas = newGasFile(s.gm.gasSinceByContract())
	}
}

func (s *uc1Sensor) status() uc1Status {
	s.mu.Lock()
	defer s.mu.Unlock()
	st := uc1Status{ChannelsOpen: len(s.chans), Errors: append([]string{}, s.errs...)}
	for _, c := range s.chans {
		if c.settled {
			st.ChannelsSettled++
		}
	}
	return st
}

func (s *uc1Sensor) serve() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/manifest", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(s.man)
	})
	mux.HandleFunc("/v1/status", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(s.status())
	})
	mux.HandleFunc("/v1/register", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var reg uc1Registration
		if err := json.NewDecoder(r.Body).Decode(&reg); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if reg.Name == "" || reg.Endpoint == "" || reg.Wire == "" || !common.IsHexAddress(reg.Ethereum) {
			http.Error(w, "name, endpoint, wire and ethereum are required", http.StatusBadRequest)
			return
		}
		eth := common.HexToAddress(reg.Ethereum)
		s.mu.Lock()
		allowed := s.adm.allow[eth]
		s.mu.Unlock()
		if !allowed {
			s.addError("refused registration of %s (%s): not in -allow", reg.Name, eth.Hex())
			http.Error(w, "ethereum address not in -allow", http.StatusForbidden)
			return
		}
		wa, err := decodeWire(reg.Wire)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		s.dialer.Register(wa, reg.Endpoint)
		s.mu.Lock()
		s.adm.registered[wire.Keys(wa)] = eth
		s.names[wire.Keys(wa)] = reg.Name
		s.mu.Unlock()
		fmt.Fprintf(os.Stderr, "uc1-sensor: registered %s %s at %s\n", reg.Name, eth.Hex(), reg.Endpoint)
		w.WriteHeader(http.StatusNoContent)
	})
	return mux
}

func (s *uc1Sensor) result(reason string, complete bool, closeGas *gasFile, persist bool, deposit *big.Int) uc1SensorResult {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.openGasMu.Lock()
	openGas := s.openGas
	s.openGasMu.Unlock()
	out := uc1SensorResult{
		Complete: complete, ExitReason: reason, Sensor: s.man.Ethereum,
		Adjudicator: s.man.Adjudicator, AssetHolder: s.man.AssetHolder, ChainID: s.man.ChainID,
		CustomersExpected: s.expected, DepositWei: deposit.String(), Strict: s.strict, Persist: persist,
		Channels: []uc1SensorChannelFile{}, OpenGas: openGas, CloseGas: closeGas,
		Errors: append([]string{}, s.errs...), TStarted: unixSeconds(s.tStarted),
		TFirstFinal: unixSeconds(s.firstFinal), TWritten: unixSeconds(time.Now()),
	}
	for _, id := range s.order {
		c := s.chans[id]
		out.Channels = append(out.Channels, uc1SensorChannelFile{
			Name: c.name, Ethereum: c.ethereum, ChannelID: "0x" + fmt.Sprintf("%x", id[:]),
			AcceptedUpdates: c.accepted, Payments: c.payments, RejectedUpdates: c.rejected,
			LastVersion: c.version, BalancesWei: weiStrings(c.balances), Final: c.final,
			TOpened: unixSeconds(c.tOpened), TFinal: unixSeconds(c.tFinal),
			TSettleStart: unixSeconds(c.tSettle0), TSettleEnd: unixSeconds(c.tSettle1),
			Settled: c.settled, SettleError: c.settleErr,
		})
	}
	return out
}

func readKeyFile(path string) (string, error) {
	if path == "" {
		return "", fmt.Errorf("-key-file is required")
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimPrefix(strings.TrimSpace(string(b)), "0x"), nil
}

// runUC1Sensor runs the sensor and returns its exit code: 0 complete, 1 the
// settle deadline passed, 143 SIGTERM (130 SIGINT).
func runUC1Sensor(url string, chainID uint64, f uc1Flags) (int, error) {
	configureDistributedLogging()
	tStarted := time.Now()
	if f.out == "" {
		return 0, fmt.Errorf("uc1-sensor needs -out DIR")
	}
	if f.endpoint == "" {
		return 0, fmt.Errorf("uc1-sensor needs -endpoint host:port")
	}
	if f.customers < 1 {
		return 0, fmt.Errorf("-customers must be at least 1")
	}
	if f.persist && f.state == "" {
		return 0, fmt.Errorf("-persist needs -state DIR")
	}
	allow, err := parseAllow(f.allow)
	if err != nil {
		return 0, err
	}
	if len(allow) == 0 {
		return 0, fmt.Errorf("-allow is empty: no customer could open a channel")
	}
	deposit, err := parseWei(f.depositWei, false)
	if err != nil {
		return 0, fmt.Errorf("-deposit-wei: %w", err)
	}
	contractsFile, err := loadUC1Contracts(f.contracts)
	if err != nil {
		return 0, err
	}
	if contractsFile.ChainID != chainID {
		return 0, fmt.Errorf("%s is for chain %d, -chainid is %d", f.contracts, contractsFile.ChainID, chainID)
	}
	key, err := readKeyFile(f.keyFile)
	if err != nil {
		return 0, err
	}
	updatePol.Long = time.Duration(f.updateTimeout * float64(time.Second))

	var id *lanwire.Identity
	if f.state != "" {
		id, err = lanwire.LoadOrCreate(f.state, "sensor")
	} else {
		id, err = lanwire.NewEphemeral("sensor")
	}
	if err != nil {
		return 0, fmt.Errorf("wire identity: %w", err)
	}
	netrt, err := newNetworkRuntime(id, f.bind)
	if err != nil {
		return 0, fmt.Errorf("network bus: %w", err)
	}
	defer netrt.close()
	persistDir := ""
	if f.persist {
		persistDir = f.state
	}
	contracts := contractsFile.chain()
	n, err := newNode("sensor", netrt.bus, key, url, chainID, contracts, id, persistDir)
	if err != nil {
		return 0, err
	}
	defer n.shutdown()
	gm, err := newGasMeterFrom(url, n.ethAddr)
	if err != nil {
		return 0, fmt.Errorf("gas meter: %w", err)
	}
	gm.labelContracts(contracts)
	wireB64, err := encodeWire(n.wireAddr)
	if err != nil {
		return 0, err
	}
	settleTO := time.Duration(f.settleDeadline * float64(time.Second))
	s := &uc1Sensor{
		n: n, dialer: netrt.dialer, strict: f.strict, expected: f.customers,
		updateTO: updatePol.Long, settleTO: settleTO, gm: gm, tStarted: tStarted,
		man: uc1Manifest{Wire: wireB64, Endpoint: f.endpoint, Ethereum: n.ethAddr.Hex(),
			Adjudicator: contracts.Adjudicator.Hex(), AssetHolder: contracts.AssetHolder.Hex(), ChainID: chainID},
		adm: &uc1Admission{asset: n.asset, deposit: deposit, challenge: f.challenge, self: n.wireAddr,
			allow: allow, registered: make(map[wire.AddrKey]common.Address)},
		names: make(map[wire.AddrKey]string), admitted: make(map[wire.AddrKey]bool),
		chans: make(map[channel.ID]*uc1SensorChannel), finalSeen: make(chan struct{}), allDone: make(chan struct{}),
	}
	n.perun.OnNewChannel(s.onNewChannel)
	go func() {
		for err := range n.errs {
			s.addError("%v", err)
		}
	}()
	go n.perun.Handle(s.proposalHandler(), s.updateHandler())

	server := &http.Server{Addr: f.control, Handler: s.serve(), ReadHeaderTimeout: 5 * time.Second}
	serveErr := make(chan error, 1)
	go func() { serveErr <- server.ListenAndServe() }()
	fmt.Fprintf(os.Stderr, "uc1-sensor ready: %s control=http://%s wire=%s customers=%d strict=%v persist=%v\n",
		n.ethAddr.Hex(), f.control, f.endpoint, f.customers, f.strict, f.persist)

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM, os.Interrupt)
	deadline := make(chan struct{})
	go func() {
		<-s.finalSeen
		time.Sleep(settleTO)
		close(deadline)
	}()
	reason, code := "settled", 0
	select {
	case <-s.allDone:
	case <-deadline:
		reason, code = "settle-deadline", 1
		s.addError("settle deadline (%v after the first finalize) passed", settleTO)
	case sig := <-sigs:
		reason, code = "signal", 143
		if sig == os.Interrupt {
			code = 130
		}
	case err := <-serveErr:
		return 0, fmt.Errorf("control server: %w", err)
	}
	// The closing gas, bounded so a dead chain cannot keep the sensor from
	// writing its result.
	var closeGas *gasFile
	gasDone := make(chan *gasFile, 1)
	go func() {
		s.snapshotOpenGas()
		gasDone <- newGasFile(gm.gasSinceByContract())
	}()
	select {
	case closeGas = <-gasDone:
	case <-time.After(30 * time.Second):
		s.addError("close gas: chain did not answer in 30 s")
	}
	res := s.result(reason, code == 0, closeGas, f.persist, deposit)
	path := filepath.Join(f.out, "sensor-result.json")
	if err := writeJSONAtomic(path, res); err != nil {
		return 0, fmt.Errorf("write %s: %w", path, err)
	}
	fmt.Fprintf(os.Stderr, "uc1-sensor: %s, wrote %s (complete=%v)\n", reason, path, res.Complete)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	_ = server.Shutdown(ctx)
	cancel()
	suppressExpectedShutdownLogs()
	return code, nil
}
