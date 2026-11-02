// uc1_customer.go — -mode uc1-customer: the customer's Perun sidecar and probe.
//
// The Python payment client spawns this process and talks to it in JSON lines:
// one request per line on stdin, one reply per request on stdout in request
// order, logs on stderr. Stdout carries protocol lines only.
//
//	init     build the node, fetch the sensor's manifest, register
//	open     open the ledger channel (customer deposits, sensor 0); timed, gas-metered
//	pay      one channel update paying the sensor, through a FIFO (one in flight)
//	state    the channel's current version and balances
//	quiesce  drop the pays not yet started, wait for the one in flight
//	close    finalize, conclude (primary), withdraw; timed, gas-metered [Q2]
//	exit     reply and leave
//
// A pay never retries [Q6]: an update timeout leaves the payment unresolved
// (the sensor may hold the signed state) and breaks the channel; every later
// pay fails with "channel broken". A rejection or too small a balance fails
// only that pay.
//
// With -probe-rate and -probe-seconds the same process is a Go-only probe: it
// reads the init request from stdin, opens, pays at the given rate through the
// same FIFO, closes, and writes a summary (-probe-out).
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/crypto"
	ethwallet "github.com/perun-network/perun-eth-backend/wallet"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/wire"
	"perun.network/perun-examples/hub-demo/lanwire"
)

// uc1Request is one stdin line. Amounts are decimal strings; a JSON number
// fails to decode into them.
type uc1Request struct {
	Op string `json:"op"`
	// init
	Key             string  `json:"key"`
	Name            string  `json:"name"`
	Chain           string  `json:"chain"`
	ChainID         uint64  `json:"chain_id"`
	SensorControl   string  `json:"sensor_control"`
	Bind            string  `json:"bind"`
	Endpoint        string  `json:"endpoint"`
	StateDir        string  `json:"state_dir"`
	UpdateTimeoutS  float64 `json:"update_timeout_s"`
	ChallengeS      uint64  `json:"challenge_s"`
	Persist         bool    `json:"persist"`
	StartupTimeoutS float64 `json:"startup_timeout_s"`
	// open
	DepositWei string `json:"deposit_wei"`
	// pay
	ID        string `json:"id"`
	AmountWei string `json:"amount_wei"`
}

type uc1ErrorReply struct {
	Op    string `json:"op"`
	OK    bool   `json:"ok"`
	Error string `json:"error"`
}

type uc1InitReply struct {
	Op      string `json:"op"`
	OK      bool   `json:"ok"`
	Address string `json:"address"`
}

type uc1OpenReply struct {
	Op        string  `json:"op"`
	OK        bool    `json:"ok"`
	TStart    float64 `json:"t_start"`
	TFunded   float64 `json:"t_funded"`
	ChannelID string  `json:"channel_id"`
	L1GasUsed uint64  `json:"l1_gas_used"`
	L1FeeWei  string  `json:"l1_fee_wei"`
	Txs       int     `json:"txs"`
}

type uc1PayReply struct {
	Op         string  `json:"op"`
	ID         string  `json:"id"`
	OK         bool    `json:"ok"`
	TSubmitted float64 `json:"t_submitted"`
	TConfirmed float64 `json:"t_confirmed"`
	Version    uint64  `json:"version"`
	Error      string  `json:"error"`
}

type uc1StateReply struct {
	Op          string   `json:"op"`
	OK          bool     `json:"ok"`
	Version     uint64   `json:"version"`
	BalancesWei []string `json:"balances_wei"`
	Broken      bool     `json:"broken"`
}

type uc1QuiesceReply struct {
	Op      string `json:"op"`
	OK      bool   `json:"ok"`
	Dropped int    `json:"dropped"`
	Version uint64 `json:"version"`
}

type uc1CloseReply struct {
	Op               string             `json:"op"`
	OK               bool               `json:"ok"`
	TStart           float64            `json:"t_start"`
	TFinalized       float64            `json:"t_finalized"`
	TWithdrawn       float64            `json:"t_withdrawn"`
	FinalVersion     uint64             `json:"final_version"`
	FinalBalancesWei []string           `json:"final_balances_wei"`
	L1GasUsed        uint64             `json:"l1_gas_used"`
	L1FeeWei         string             `json:"l1_fee_wei"`
	Txs              int                `json:"txs"`
	ByContract       map[string]gasCost `json:"by_contract"`
}

// uc1Backend is the channel the sidecar drives; the tests replace it.
type uc1Backend interface {
	Init(req uc1Request) (address string, err error)
	Open(deposit *big.Int) (uc1OpenReply, error)
	Update(amount *big.Int) (version uint64, err error) // one update paying the sensor
	State() (version uint64, balances []*big.Int, err error)
	Close() (uc1CloseReply, error)
	Shutdown()
}

// ---------------------------------------------------------------------------
// The FIFO: one update in flight, in request order.
// ---------------------------------------------------------------------------

type uc1PayOutcome struct {
	OK                     bool
	TSubmitted, TConfirmed time.Time
	Version                uint64
	Err                    string
}

type uc1Job struct {
	amount *big.Int            // a pay, or
	fn     func()              // any other job that must run after the pays before it
	done   func(uc1PayOutcome) // the pay's result
}

type uc1FIFO struct {
	updater func(*big.Int) (uint64, error)
	balance func() *big.Int

	mu          sync.Mutex
	cond        *sync.Cond
	queue       []uc1Job
	payInFlight bool
	quiesced    bool
	stopped     bool
	broken      string // the first error that broke the channel
}

func newUC1FIFO(updater func(*big.Int) (uint64, error), balance func() *big.Int) *uc1FIFO {
	f := &uc1FIFO{updater: updater, balance: balance}
	f.cond = sync.NewCond(&f.mu)
	go f.loop()
	return f
}

// Pay queues one payment; done is called exactly once with its outcome.
func (f *uc1FIFO) Pay(amount *big.Int, done func(uc1PayOutcome)) {
	f.mu.Lock()
	if f.quiesced || f.stopped {
		f.mu.Unlock()
		done(uc1PayOutcome{Err: "unresolved: after quiesce"})
		return
	}
	f.queue = append(f.queue, uc1Job{amount: amount, done: done})
	f.cond.Signal()
	f.mu.Unlock()
}

// Do queues a job that runs after every pay queued before it (the close).
func (f *uc1FIFO) Do(fn func()) {
	f.mu.Lock()
	f.queue = append(f.queue, uc1Job{fn: fn})
	f.cond.Signal()
	f.mu.Unlock()
}

// Quiesce stops the FIFO: queued pays resolve "dropped at quiesce", later pays
// "after quiesce". It then waits (at most wait) for the pay in flight.
func (f *uc1FIFO) Quiesce(wait time.Duration) (dropped int) {
	f.mu.Lock()
	f.quiesced = true
	var drop []uc1Job
	keep := f.queue[:0]
	for _, j := range f.queue {
		if j.fn == nil {
			drop = append(drop, j)
		} else {
			keep = append(keep, j)
		}
	}
	f.queue = keep
	f.mu.Unlock()
	for _, j := range drop {
		j.done(uc1PayOutcome{Err: "unresolved: dropped at quiesce"})
	}
	deadline := time.Now().Add(wait)
	for time.Now().Before(deadline) {
		f.mu.Lock()
		busy := f.payInFlight
		f.mu.Unlock()
		if !busy {
			break
		}
		time.Sleep(time.Millisecond)
	}
	return len(drop)
}

// Broken returns the first error that broke the channel, or "".
func (f *uc1FIFO) Broken() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.broken
}

// Stop ends the loop; queued pays resolve as dropped.
func (f *uc1FIFO) Stop() {
	f.Quiesce(0)
	f.mu.Lock()
	f.stopped = true
	f.cond.Broadcast()
	f.mu.Unlock()
}

func (f *uc1FIFO) loop() {
	for {
		f.mu.Lock()
		for len(f.queue) == 0 && !f.stopped {
			f.cond.Wait()
		}
		if f.stopped && len(f.queue) == 0 {
			f.mu.Unlock()
			return
		}
		j := f.queue[0]
		f.queue[0] = uc1Job{}
		f.queue = f.queue[1:]
		f.payInFlight = j.fn == nil
		broken := f.broken
		f.mu.Unlock()
		if j.fn != nil {
			j.fn()
			continue
		}
		out := f.runPay(j.amount, broken)
		f.mu.Lock()
		f.payInFlight = false
		f.mu.Unlock()
		j.done(out)
	}
}

func (f *uc1FIFO) runPay(amount *big.Int, broken string) uc1PayOutcome {
	if broken != "" {
		return uc1PayOutcome{Err: "channel broken: " + broken}
	}
	if bal := f.balance(); bal == nil || bal.Cmp(amount) < 0 {
		return uc1PayOutcome{Err: "channel empty"}
	}
	tSub := time.Now()
	v, err := f.updater(amount)
	tConf := time.Now()
	if err == nil {
		return uc1PayOutcome{OK: true, TSubmitted: tSub, TConfirmed: tConf, Version: v}
	}
	var rej client.PeerRejectedError
	if errors.As(err, &rej) {
		return uc1PayOutcome{TSubmitted: tSub, Err: "rejected: " + rej.Reason}
	}
	// No answer, or an error we cannot place: the sensor may hold the signed
	// state, so the pay is unresolved and the channel is not used again.
	f.mu.Lock()
	if f.broken == "" {
		f.broken = err.Error()
	}
	f.mu.Unlock()
	if isUpdateTimeout(err) {
		return uc1PayOutcome{TSubmitted: tSub, Err: "unresolved: update timeout"}
	}
	return uc1PayOutcome{TSubmitted: tSub, Err: "unresolved: " + err.Error()}
}

// ---------------------------------------------------------------------------
// The sidecar: requests in, replies out in request order.
// ---------------------------------------------------------------------------

// uc1Replies writes replies in request order: each request reserves a slot,
// a single writer emits the slots in order as they fill.
type uc1Replies struct {
	mu    sync.Mutex
	cond  *sync.Cond
	slots []chan any
	enc   *json.Encoder
	done  chan struct{}
	err   error
}

func newUC1Replies(w io.Writer) *uc1Replies {
	r := &uc1Replies{enc: json.NewEncoder(w), done: make(chan struct{})}
	r.cond = sync.NewCond(&r.mu)
	go r.loop()
	return r
}

func (r *uc1Replies) reserve() chan any {
	slot := make(chan any, 1)
	r.mu.Lock()
	r.slots = append(r.slots, slot)
	r.cond.Signal()
	r.mu.Unlock()
	return slot
}

// close lets the writer finish the slots already reserved, then waits for it.
func (r *uc1Replies) close() error {
	r.mu.Lock()
	r.slots = append(r.slots, nil)
	r.cond.Signal()
	r.mu.Unlock()
	<-r.done
	return r.err
}

func (r *uc1Replies) loop() {
	defer close(r.done)
	for {
		r.mu.Lock()
		for len(r.slots) == 0 {
			r.cond.Wait()
		}
		slot := r.slots[0]
		r.slots = r.slots[1:]
		r.mu.Unlock()
		if slot == nil {
			return
		}
		v := <-slot
		if r.err != nil {
			continue
		}
		// One Encode is one write on an unbuffered writer: the line is out
		// (flushed) when it returns.
		if err := r.enc.Encode(v); err != nil {
			r.err = err
		}
	}
}

type uc1Sidecar struct {
	b        uc1Backend
	fifo     *uc1FIFO
	replies  *uc1Replies
	updateTO time.Duration
	inited   bool
	opened   bool
	closing  bool
}

// runUC1Sidecar serves requests from in until exit or end of input.
func runUC1Sidecar(in io.Reader, out io.Writer, b uc1Backend) error {
	s := &uc1Sidecar{b: b, replies: newUC1Replies(out), updateTO: 10 * time.Second}
	s.fifo = newUC1FIFO(b.Update, func() *big.Int {
		_, bals, err := b.State()
		if err != nil || len(bals) == 0 {
			return nil
		}
		return bals[0]
	})
	defer s.fifo.Stop()
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
	// Leave only after every reply is out; then release the node.
	err := s.replies.close()
	b.Shutdown()
	if err == nil && !exit {
		err = sc.Err()
	}
	return err
}

func (s *uc1Sidecar) fail(slot chan any, op, format string, args ...any) {
	slot <- uc1ErrorReply{Op: op, Error: fmt.Sprintf(format, args...)}
}

// handle serves one request line; it returns true on exit.
func (s *uc1Sidecar) handle(line []byte) bool {
	slot := s.replies.reserve()
	var req uc1Request
	if err := json.Unmarshal(line, &req); err != nil {
		// A pay with a bad field still gets its id back, so the caller can
		// resolve that one row.
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
		addr, err := s.b.Init(req)
		if err != nil {
			s.fail(slot, "init", "%v", err)
			return false
		}
		s.inited = true
		slot <- uc1InitReply{Op: "init", OK: true, Address: addr}
	case "open":
		if !s.inited || s.opened {
			s.fail(slot, "open", "open needs init and only one channel per sidecar")
			return false
		}
		dep, err := parseWei(req.DepositWei, false)
		if err != nil {
			s.fail(slot, "open", "deposit_wei: %v", err)
			return false
		}
		rep, err := s.b.Open(dep)
		if err != nil {
			s.fail(slot, "open", "%v", err)
			return false
		}
		s.opened = true
		rep.Op, rep.OK = "open", true
		slot <- rep
	case "pay":
		amount, err := parseWei(req.AmountWei, false)
		switch {
		case err != nil:
			slot <- uc1PayReply{Op: "pay", ID: req.ID, Error: "rejected: amount_wei: " + err.Error()}
		case !s.opened:
			slot <- uc1PayReply{Op: "pay", ID: req.ID, Error: "rejected: no open channel"}
		case s.closing:
			slot <- uc1PayReply{Op: "pay", ID: req.ID, Error: "rejected: channel closing"}
		default:
			id := req.ID
			s.fifo.Pay(amount, func(o uc1PayOutcome) {
				slot <- uc1PayReply{Op: "pay", ID: id, OK: o.OK, TSubmitted: unixSeconds(o.TSubmitted),
					TConfirmed: unixSeconds(o.TConfirmed), Version: o.Version, Error: o.Err}
			})
		}
	case "state":
		if !s.opened {
			s.fail(slot, "state", "no open channel")
			return false
		}
		v, bals, err := s.b.State()
		if err != nil {
			s.fail(slot, "state", "%v", err)
			return false
		}
		slot <- uc1StateReply{Op: "state", OK: true, Version: v, BalancesWei: weiStrings(bals), Broken: s.fifo.Broken() != ""}
	case "quiesce":
		dropped := s.fifo.Quiesce(s.updateTO + time.Second)
		var v uint64
		if s.opened {
			v, _, _ = s.b.State()
		}
		slot <- uc1QuiesceReply{Op: "quiesce", OK: true, Dropped: dropped, Version: v}
	case "close":
		if !s.opened || s.closing {
			s.fail(slot, "close", "close needs an open channel, once")
			return false
		}
		s.closing = true
		// Through the FIFO, so the close follows every pay sent before it.
		s.fifo.Do(func() {
			if broken := s.fifo.Broken(); broken != "" {
				s.fail(slot, "close", "channel broken: %s", broken)
				return
			}
			rep, err := s.b.Close()
			if err != nil {
				s.fail(slot, "close", "%v", err)
				return
			}
			rep.Op, rep.OK = "close", true
			slot <- rep
		})
	case "exit":
		slot <- uc1ErrorReply{Op: "exit", OK: true}
		return true
	default:
		s.fail(slot, req.Op, "unknown op %q", req.Op)
	}
	return false
}

// ---------------------------------------------------------------------------
// The real backend: one go-perun ledger channel to the sensor.
// ---------------------------------------------------------------------------

type uc1ChannelBackend struct {
	c          *Client
	netrt      *networkRuntime
	chainURL   string
	sensorWire map[wallet.BackendID]wire.Address
	challenge  uint64
	contracts  chainContracts
	closeTO    time.Duration

	mu sync.Mutex
	ch *client.Channel
}

func (b *uc1ChannelBackend) Init(req uc1Request) (string, error) {
	if req.Key == "" || req.Name == "" || req.Chain == "" || req.SensorControl == "" || req.Bind == "" || req.Endpoint == "" {
		return "", fmt.Errorf("init needs key, name, chain, sensor_control, bind and endpoint")
	}
	if req.Persist && req.StateDir == "" {
		return "", fmt.Errorf("persist needs state_dir")
	}
	if req.UpdateTimeoutS > 0 {
		updatePol.Long = time.Duration(req.UpdateTimeoutS * float64(time.Second))
	}
	b.challenge = req.ChallengeS
	if b.challenge == 0 {
		b.challenge = challengeDuration
	}
	b.chainURL = req.Chain
	startup := time.Duration(req.StartupTimeoutS * float64(time.Second))
	if startup <= 0 {
		startup = 120 * time.Second
	}
	key := strings.TrimPrefix(req.Key, "0x")
	ek, err := crypto.HexToECDSA(key)
	if err != nil {
		return "", fmt.Errorf("key: %w", err)
	}
	ethAddr := crypto.PubkeyToAddress(ek.PublicKey)

	control := strings.TrimRight(req.SensorControl, "/")
	var man uc1Manifest
	if err := uc1Retry(startup, "sensor manifest", func() error {
		return httpJSON(http.MethodGet, control+"/v1/manifest", nil, &man)
	}); err != nil {
		return "", err
	}
	if man.ChainID != req.ChainID {
		return "", fmt.Errorf("sensor is on chain %d, init says %d", man.ChainID, req.ChainID)
	}
	sensorWire, err := decodeWire(man.Wire)
	if err != nil {
		return "", fmt.Errorf("sensor wire: %w", err)
	}
	b.sensorWire = sensorWire

	var id *lanwire.Identity
	if req.StateDir != "" {
		id, err = lanwire.LoadOrCreate(req.StateDir, req.Name)
	} else {
		id, err = lanwire.NewEphemeral(req.Name)
	}
	if err != nil {
		return "", fmt.Errorf("wire identity: %w", err)
	}
	netrt, err := newNetworkRuntime(id, req.Bind)
	if err != nil {
		return "", fmt.Errorf("network bus: %w", err)
	}
	b.netrt = netrt
	netrt.dialer.Register(sensorWire, man.Endpoint)
	persistDir := ""
	if req.Persist {
		persistDir = req.StateDir
	}
	contracts := uc1Contracts{ChainID: man.ChainID, Adjudicator: man.Adjudicator, AssetHolder: man.AssetHolder}.chain()
	b.contracts = contracts
	c, err := NewClientWithIdentity(req.Name, netrt.bus, key, req.Chain, req.ChainID, contracts, id, persistDir)
	if err != nil {
		return "", err
	}
	// The sensor never proposes; if it ever did, it could only pay us.
	c.RequireSafePaymentUpdates(true)
	b.c = c
	wireB64, err := encodeWire(c.wireAddr)
	if err != nil {
		return "", err
	}
	reg := uc1Registration{Name: req.Name, Endpoint: req.Endpoint, Wire: wireB64, Ethereum: ethAddr.Hex()}
	if err := uc1Retry(startup, "register with the sensor", func() error {
		return httpJSON(http.MethodPost, control+"/v1/register", reg, nil)
	}); err != nil {
		return "", err
	}
	return ethAddr.Hex(), nil
}

// OpenLedgerWei is OpenLedgerToWire with amounts in wei: kind.units(float64)
// would round 10^18.
func (c *Client) OpenLedgerWei(peerWire map[wallet.BackendID]wire.Address, myDeposit, peerDeposit *big.Int, challenge uint64) (*client.Channel, error) {
	peers := []map[wallet.BackendID]wire.Address{c.wireAddr, peerWire}
	initAlloc := channel.NewAllocation(2, []wallet.BackendID{ethwallet.BackendID}, c.asset)
	initAlloc.SetAssetBalances(c.asset, []channel.Bal{new(big.Int).Set(myDeposit), new(big.Int).Set(peerDeposit)})
	prop, err := client.NewLedgerChannelProposal(challenge, c.wAddr, initAlloc, peers)
	if err != nil {
		return nil, err
	}
	ch, err := c.perun.ProposeChannel(context.Background(), prop)
	if err != nil {
		return nil, err
	}
	c.startWatching(ch)
	c.ledger = ch
	return ch, nil
}

func (b *uc1ChannelBackend) meter() (*gasMeter, error) {
	gm, err := newGasMeterFrom(b.chainURL, b.c.ethAddr)
	if err != nil {
		return nil, err
	}
	gm.labelContracts(b.contracts)
	return gm, nil
}

func (b *uc1ChannelBackend) Open(deposit *big.Int) (uc1OpenReply, error) {
	gm, err := b.meter()
	if err != nil {
		return uc1OpenReply{}, fmt.Errorf("gas meter: %w", err)
	}
	t0 := time.Now()
	ch, err := b.c.OpenLedgerWei(b.sensorWire, deposit, new(big.Int), b.challenge)
	t1 := time.Now()
	if err != nil {
		return uc1OpenReply{}, err
	}
	b.mu.Lock()
	b.ch = ch
	b.mu.Unlock()
	gas, txs, _, fee := gm.gasSinceByContract()
	id := ch.ID()
	return uc1OpenReply{TStart: unixSeconds(t0), TFunded: unixSeconds(t1), ChannelID: fmt.Sprintf("0x%x", id[:]),
		L1GasUsed: gas, L1FeeWei: fee.String(), Txs: txs}, nil
}

func (b *uc1ChannelBackend) channel() *client.Channel {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.ch
}

func (b *uc1ChannelBackend) Update(amount *big.Int) (uint64, error) {
	ch := b.channel()
	if ch == nil {
		return 0, fmt.Errorf("no open channel")
	}
	// The customer is index 0: updateWithPolicy waits Long and never yields.
	if _, err := updateWithPolicy(ch, func(s *channel.State) {
		s.Allocation.TransferBalance(0, 1, b.c.asset, amount)
	}); err != nil {
		return 0, err
	}
	return ch.State().Version, nil
}

func (b *uc1ChannelBackend) State() (uint64, []*big.Int, error) {
	ch := b.channel()
	if ch == nil {
		return 0, nil, fmt.Errorf("no open channel")
	}
	st := ch.State()
	return st.Version, copyBals(st.Balances), nil
}

// Close is the customer's close [Q2: primary]: finalize (one off-chain
// update), then conclude and withdraw its own balance.
func (b *uc1ChannelBackend) Close() (uc1CloseReply, error) {
	ch := b.channel()
	if ch == nil {
		return uc1CloseReply{}, fmt.Errorf("no open channel")
	}
	gm, err := b.meter()
	if err != nil {
		return uc1CloseReply{}, fmt.Errorf("gas meter: %w", err)
	}
	t0 := time.Now()
	if err := finalize(ch); err != nil {
		return uc1CloseReply{}, fmt.Errorf("finalize: %w", err)
	}
	t1 := time.Now()
	st := ch.State()
	ctx, cancel := context.WithTimeout(context.Background(), b.closeTO)
	err = settleContext(ctx, ch, false)
	cancel()
	t2 := time.Now()
	if err != nil {
		return uc1CloseReply{}, fmt.Errorf("settle: %w", err)
	}
	gas, txs, by, fee := gm.gasSinceByContract()
	return uc1CloseReply{TStart: unixSeconds(t0), TFinalized: unixSeconds(t1), TWithdrawn: unixSeconds(t2),
		FinalVersion: st.Version, FinalBalancesWei: weiStrings(copyBals(st.Balances)),
		L1GasUsed: gas, L1FeeWei: fee.String(), Txs: txs, ByContract: newGasFile(gas, txs, by, fee).ByContract}, nil
}

func (b *uc1ChannelBackend) Shutdown() {
	suppressExpectedShutdownLogs()
	if b.c != nil {
		b.c.shutdown()
	}
	if b.netrt != nil {
		b.netrt.close()
	}
}

func newUC1ChannelBackend() *uc1ChannelBackend {
	return &uc1ChannelBackend{closeTO: 300 * time.Second}
}

func runUC1SidecarMain(in io.Reader, out io.Writer) error {
	configureDistributedLogging()
	return runUC1Sidecar(in, out, newUC1ChannelBackend())
}

// runUC1ClockCheck answers every input line with {"now": time.Now()} as the
// sidecar's timestamps encode it. t_intended is taken by Python's time.time()
// and t_submitted/t_confirmed by Go's time.Now(); a Python test brackets this
// reply between two time.time() reads to check the two clocks agree.
func runUC1ClockCheck(in io.Reader, out io.Writer) error {
	enc := json.NewEncoder(out)
	sc := bufio.NewScanner(in)
	for sc.Scan() {
		if err := enc.Encode(struct {
			Now float64 `json:"now"`
		}{unixSeconds(time.Now())}); err != nil {
			return err
		}
	}
	return sc.Err()
}

// ---------------------------------------------------------------------------
// The probe: a Go-only open-loop load on one channel.
// ---------------------------------------------------------------------------

type uc1ProbeResult struct {
	Name          string         `json:"name"`
	Address       string         `json:"address"`
	Rate          float64        `json:"rate_per_s"`
	Seconds       float64        `json:"seconds"`
	AmountWei     string         `json:"amount_wei"`
	Sent          int            `json:"sent"`
	OK            int            `json:"ok"`
	Failed        map[string]int `json:"failed"`
	AchievedRate  float64        `json:"achieved_per_s"`     // ok / (last confirm - first intended)
	Update        latencySummary `json:"update_latency"`     // t_confirmed - t_submitted
	EndToEnd      latencySummary `json:"end_to_end_latency"` // t_confirmed - t_intended (includes FIFO wait)
	Open          uc1OpenReply   `json:"open"`
	Close         *uc1CloseReply `json:"close,omitempty"`
	CloseError    string         `json:"close_error,omitempty"`
	FinalVersion  uint64         `json:"final_version"`
	FinalBalances []string       `json:"final_balances_wei"`
}

// runUC1Probe reads the init request from in, then opens, pays at f.probeRate
// for f.probeSeconds, closes, and writes the summary to f.probeOut and out.
func runUC1Probe(in io.Reader, out io.Writer, f uc1Flags) error {
	configureDistributedLogging()
	if f.probeRate <= 0 || f.probeSeconds <= 0 {
		return fmt.Errorf("the probe needs -probe-rate and -probe-seconds")
	}
	amount, err := parseWei(f.probeAmountWei, false)
	if err != nil {
		return fmt.Errorf("-probe-amount-wei: %w", err)
	}
	deposit, err := parseWei(f.depositWei, false)
	if err != nil {
		return fmt.Errorf("-deposit-wei: %w", err)
	}
	line, err := bufio.NewReader(in).ReadBytes('\n')
	if err != nil && len(line) == 0 {
		return fmt.Errorf("probe: read init from stdin: %w", err)
	}
	var req uc1Request
	if err := json.Unmarshal(line, &req); err != nil || req.Op != "init" {
		return fmt.Errorf("probe: stdin must start with an init request (%v)", err)
	}
	b := newUC1ChannelBackend()
	defer b.Shutdown()
	addr, err := b.Init(req)
	if err != nil {
		return fmt.Errorf("probe init: %w", err)
	}
	open, err := b.Open(deposit)
	if err != nil {
		return fmt.Errorf("probe open: %w", err)
	}
	open.Op, open.OK = "open", true
	fifo := newUC1FIFO(b.Update, func() *big.Int {
		_, bals, err := b.State()
		if err != nil || len(bals) == 0 {
			return nil
		}
		return bals[0]
	})
	defer fifo.Stop()

	n := int(f.probeRate*f.probeSeconds + 0.5)
	type sample struct {
		intended time.Time
		out      uc1PayOutcome
	}
	samples := make([]sample, n)
	var wg sync.WaitGroup
	t0 := time.Now()
	for k := 0; k < n; k++ {
		at := t0.Add(time.Duration(float64(k) / f.probeRate * float64(time.Second)))
		time.Sleep(time.Until(at))
		samples[k].intended = time.Now()
		wg.Add(1)
		k := k
		fifo.Pay(amount, func(o uc1PayOutcome) { samples[k].out = o; wg.Done() })
	}
	wg.Wait()
	res := uc1ProbeResult{Name: req.Name, Address: addr, Rate: f.probeRate, Seconds: f.probeSeconds,
		AmountWei: amount.String(), Sent: n, Failed: map[string]int{}, Open: open}
	var upd, e2e []time.Duration
	var last time.Time
	for _, s := range samples {
		if !s.out.OK {
			res.Failed[s.out.Err]++
			continue
		}
		res.OK++
		upd = append(upd, s.out.TConfirmed.Sub(s.out.TSubmitted))
		e2e = append(e2e, s.out.TConfirmed.Sub(s.intended))
		if s.out.TConfirmed.After(last) {
			last = s.out.TConfirmed
		}
	}
	if res.OK > 0 {
		res.AchievedRate = float64(res.OK) / last.Sub(samples[0].intended).Seconds()
	}
	res.Update, res.EndToEnd = summarizeLatencies(upd), summarizeLatencies(e2e)
	fifo.Quiesce(updatePol.Long + time.Second)
	if fifo.Broken() == "" {
		cl, err := b.Close()
		if err != nil {
			res.CloseError = err.Error()
		} else {
			cl.Op, cl.OK = "close", true
			res.Close = &cl
			res.FinalVersion, res.FinalBalances = cl.FinalVersion, cl.FinalBalancesWei
		}
	} else {
		res.CloseError = "channel broken: " + fifo.Broken()
	}
	if res.Close == nil {
		if v, bals, err := b.State(); err == nil {
			res.FinalVersion, res.FinalBalances = v, weiStrings(bals)
		}
	}
	if f.probeOut != "" {
		if err := writeJSONAtomic(f.probeOut, res); err != nil {
			return err
		}
	}
	if err := json.NewEncoder(out).Encode(res); err != nil {
		return err
	}
	if res.OK != n || res.Close == nil {
		return fmt.Errorf("probe: %d/%d ok, close error %q", res.OK, n, res.CloseError)
	}
	return nil
}
