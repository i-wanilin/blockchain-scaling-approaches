package main

// This file contains the runnable, multi-process benchmark mode. It is kept
// deliberately small: the HTTP control plane performs discovery and lifecycle
// barriers; payments remain go-perun messages sent directly between the two
// client processes.

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	ethwallet "github.com/perun-network/perun-eth-backend/wallet"
	"github.com/sirupsen/logrus"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
	perunlog "perun.network/go-perun/log"
	perunlogrus "perun.network/go-perun/log/logrus"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/wire"
	wirenet "perun.network/go-perun/wire/net"
	"perun.network/go-perun/wire/net/simple"
	perunserializer "perun.network/go-perun/wire/perunio/serializer"
	"perun.network/perun-examples/hub-demo/lanwire"
)

var distributedLogger = logrus.New()

func configureDistributedLogging() {
	distributedLogger.SetLevel(logrus.WarnLevel)
	distributedLogger.SetFormatter(&logrus.TextFormatter{})
	perunlog.Set(perunlogrus.FromLogrus(distributedLogger))
}

// clusterConfig is intentionally JSON and stdlib-only so the same repository
// runs without Python/YAML tooling on Linux and native Windows.
type clusterConfig struct {
	ChainURL  string        `json:"chain_url"`
	ChainID   uint64        `json:"chain_id"`
	Hub       clusterNode   `json:"hub"`
	Clients   []clusterNode `json:"clients"`
	StateRoot string        `json:"state_root"`
	ResultDir string        `json:"result_dir"`
	// Asset is "eth" (default) or "points" (asset.go). It sets the unit of the
	// two amounts below: whole ETH (sent as wei, x 10^18) or whole points (sent
	// as they are, one point = one token unit).
	Asset                string  `json:"asset,omitempty"`
	LedgerDeposit        float64 `json:"ledger_deposit"`
	VirtualStake         float64 `json:"virtual_stake"`
	ChallengeSecs        uint64  `json:"challenge_seconds"`
	StartupTimeoutSec    uint64  `json:"startup_timeout_seconds"`
	OperationTimeoutSec  uint64  `json:"operation_timeout_seconds"`
	KeepHubRunning       bool    `json:"keep_hub_running"`
	DisablePaymentPolicy bool    `json:"disable_strict_payment_policy"`
	// Collision policy for channel updates; see update_policy.go.
	YieldTimeoutMS      uint64 `json:"yield_timeout_ms"`
	UpdateTimeoutSec    uint64 `json:"update_timeout_seconds"`
	MaxCollisionRetries int    `json:"max_collision_retries"`
	// UC2 (uc2.go). HubDeposit is the hub's side of every ledger channel; zero
	// keeps the symmetric funding (the hub matches ledger_deposit). AmmoReserve
	// is what a player keeps in its ledger channel, beside its virtual stakes,
	// to pay its shots to the hub. ContractsFile (written by uc2-deploy) makes
	// the hub use those contracts instead of deploying its own; HubKeyFile
	// holds the hub's hex private key (0600) instead of a dev key by name.
	HubDeposit    float64 `json:"hub_deposit"`
	AmmoReserve   float64 `json:"ammo_reserve"`
	ContractsFile string  `json:"contracts_file"`
	HubKeyFile    string  `json:"hub_key_file"`
}

// ledgerHubDeposit is the hub's opening balance in each ledger channel.
func (c clusterConfig) ledgerHubDeposit() float64 {
	if c.HubDeposit > 0 {
		return c.HubDeposit
	}
	return c.LedgerDeposit
}

func (c *clusterConfig) applyUpdatePolicyDefaults() {
	if c.YieldTimeoutMS == 0 {
		c.YieldTimeoutMS = 1000
	}
	if c.UpdateTimeoutSec == 0 {
		c.UpdateTimeoutSec = 10
	}
	if c.MaxCollisionRetries == 0 {
		c.MaxCollisionRetries = 10
	}
}

func (c clusterConfig) updatePolicy() updatePolicy {
	return updatePolicy{
		Yield:      time.Duration(c.YieldTimeoutMS) * time.Millisecond,
		Long:       time.Duration(c.UpdateTimeoutSec) * time.Second,
		MaxRetries: c.MaxCollisionRetries,
	}
}

// validateMeshStake checks that a client can fund its whole share of the mesh.
// Every client holds one virtual channel per other client, and each one locks
// virtual_stake out of that client's ledger channel.
func (c clusterConfig) validateMeshStake() error {
	if n := len(c.Clients) - 1; n > 0 && c.VirtualStake*float64(n) > c.LedgerDeposit {
		return fmt.Errorf("%d virtual channels x virtual_stake %v exceed ledger_deposit %v", n, c.VirtualStake, c.LedgerDeposit)
	}
	return nil
}

// validateUpdatePolicy refuses configurations in which the two timeouts could
// fire close together. If both ends of a channel time out at once, each accepts
// the other's abandoned proposal and the channel forks.
func (c clusterConfig) validateUpdatePolicy() error {
	p := c.updatePolicy()
	if p.Yield <= 0 || p.Long < 4*p.Yield {
		return fmt.Errorf("update_timeout_seconds (%v) must be at least 4x yield_timeout_ms (%v)", p.Long, p.Yield)
	}
	return nil
}

type clusterNode struct {
	Name     string `json:"name"`
	Bind     string `json:"bind"`     // local TCP bind address, e.g. 0.0.0.0:9000
	Endpoint string `json:"endpoint"` // routable host:port peers dial
	Control  string `json:"control"`  // hub: bind address; client: http URL
	// Ethereum is the node's account. Only UC2 with a contracts_file needs it,
	// to check each node against the file's minted amounts.
	Ethereum string `json:"ethereum,omitempty"`
}

func loadClusterConfig(path string) (clusterConfig, error) {
	return loadClusterConfigAsset(path, "")
}

// loadClusterConfigAsset is loadClusterConfig with the config's "asset"
// replaced by asset when that is non-empty (the -asset flag). The asset is
// applied before the defaults, because the default amounts depend on it.
func loadClusterConfigAsset(path, asset string) (clusterConfig, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return clusterConfig{}, err
	}
	var cfg clusterConfig
	if err := json.Unmarshal(b, &cfg); err != nil {
		return cfg, err
	}
	if asset != "" {
		cfg.Asset = asset
	}
	if cfg.ChainURL == "" || cfg.ChainID == 0 || cfg.Hub.Name == "" || cfg.Hub.Bind == "" || cfg.Hub.Endpoint == "" || cfg.Hub.Control == "" || len(cfg.Clients) < 2 {
		return cfg, fmt.Errorf("config requires chain_url, chain_id, hub bind/endpoint/control and at least two clients")
	}
	if cfg.StateRoot == "" {
		cfg.StateRoot = ".state"
	}
	if cfg.ResultDir == "" {
		cfg.ResultDir = "results"
	}
	kind, err := parseAssetKind(cfg.Asset)
	if err != nil {
		return cfg, err
	}
	cfg.Asset = string(kind)
	if cfg.LedgerDeposit == 0 {
		cfg.LedgerDeposit = kind.defaultLedgerDeposit()
	}
	if cfg.VirtualStake == 0 {
		cfg.VirtualStake = kind.defaultVirtualStake()
	}
	if cfg.ChallengeSecs == 0 {
		cfg.ChallengeSecs = challengeDuration
	}
	if cfg.StartupTimeoutSec == 0 {
		cfg.StartupTimeoutSec = 300
	}
	if cfg.OperationTimeoutSec == 0 {
		cfg.OperationTimeoutSec = 180
	}
	if cfg.LedgerDeposit <= 0 || cfg.VirtualStake <= 0 || cfg.VirtualStake > cfg.LedgerDeposit {
		return cfg, fmt.Errorf("ledger_deposit and virtual_stake must be positive, and virtual_stake must not exceed ledger_deposit")
	}
	names := map[string]bool{strings.ToLower(cfg.Hub.Name): true}
	for _, node := range append([]clusterNode{cfg.Hub}, cfg.Clients...) {
		if _, _, err := net.SplitHostPort(node.Bind); err != nil {
			return cfg, fmt.Errorf("%s bind %q: %w", node.Name, node.Bind, err)
		}
		if _, _, err := net.SplitHostPort(node.Endpoint); err != nil {
			return cfg, fmt.Errorf("%s endpoint %q: %w", node.Name, node.Endpoint, err)
		}
	}
	for _, node := range cfg.Clients {
		name := strings.ToLower(node.Name)
		if name == "" || names[name] {
			return cfg, fmt.Errorf("client names must be non-empty and unique: %q", node.Name)
		}
		if node.Control == "" {
			return cfg, fmt.Errorf("client %s requires a control URL", node.Name)
		}
		names[name] = true
	}
	cfg.applyUpdatePolicyDefaults()
	if err := cfg.validateUpdatePolicy(); err != nil {
		return cfg, err
	}
	if err := cfg.validateMeshStake(); err != nil {
		return cfg, err
	}
	if err := cfg.validatePoints(); err != nil {
		return cfg, err
	}
	if cfg.ContractsFile != "" && cfg.HubDeposit <= 0 {
		// Without hub_deposit neither mint check would run: validatePoints
		// leaves a contracts file to validateUC2Funding.
		return cfg, fmt.Errorf("contracts_file %s is set but hub_deposit is not: a UC2 config needs both", cfg.ContractsFile)
	}
	if cfg.HubDeposit > 0 {
		if err := cfg.validateUC2Funding(); err != nil {
			return cfg, err
		}
	}
	return cfg, nil
}

func (c clusterConfig) assetKind() assetKind {
	k, _ := parseAssetKind(c.Asset) // validated by loadClusterConfig
	return k
}

// validatePoints checks the points-mode amounts: whole points, and a hub that
// can fund its side of every ledger channel out of the points it was minted.
// With a contracts file the hub did not mint pointsMint itself; the file's
// minted amounts are checked instead (validateUC2Funding).
func (c clusterConfig) validatePoints() error {
	k := c.assetKind()
	if k != assetPoints {
		return nil
	}
	if err := k.validateAmount("ledger_deposit", c.LedgerDeposit); err != nil {
		return err
	}
	if err := k.validateAmount("virtual_stake", c.VirtualStake); err != nil {
		return err
	}
	if c.ContractsFile != "" {
		return nil
	}
	if need := c.ledgerHubDeposit() * float64(len(c.Clients)); need > pointsMint {
		return fmt.Errorf("the hub needs %v points for %d ledger channels but is minted %d", need, len(c.Clients), pointsMint)
	}
	return nil
}

func (c clusterConfig) startupTimeout() time.Duration {
	return time.Duration(c.StartupTimeoutSec) * time.Second
}

func (c clusterConfig) operationTimeout() time.Duration {
	return time.Duration(c.OperationTimeoutSec) * time.Second
}

func clusterClient(cfg clusterConfig, name string) (clusterNode, bool) {
	for _, c := range cfg.Clients {
		if c.Name == name {
			return c, true
		}
	}
	return clusterNode{}, false
}

// networkRuntime owns the TCP bus. TLS encrypts the LAN transport; Perun's
// wire authentication binds every connection to the persistent lanwire key.
type networkRuntime struct {
	bus           wire.Bus
	dialer        *simple.Dialer
	stopListening func()
	close         func()
}

func newNetworkRuntime(id *lanwire.Identity, bind string) (*networkRuntime, error) {
	lanwire.RegisterDecoder()
	tlsCfg, err := selfSignedTLSConfig()
	if err != nil {
		return nil, err
	}
	dialer := simple.NewTCPDialer(8*time.Second, tlsCfg)
	listener, err := simple.NewTCPListener(bind, tlsCfg)
	if err != nil {
		return nil, err
	}
	accounts := map[wallet.BackendID]wire.Account{ethwallet.BackendID: id.Account}
	bus := wirenet.NewBus(accounts, dialer, perunserializer.Serializer())
	go bus.Listen(listener)
	return &networkRuntime{
		bus: bus, dialer: dialer,
		stopListening: func() { _ = listener.Close() },
		close:         func() { _ = listener.Close(); _ = bus.Close() },
	}, nil
}

func selfSignedTLSConfig() (*tls.Config, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	tmpl := x509.Certificate{SerialNumber: big.NewInt(time.Now().UnixNano()), Subject: pkix.Name{CommonName: "perun-hub-demo"}, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(24 * time.Hour), KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth}}
	der, err := x509.CreateCertificate(rand.Reader, &tmpl, &tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, err
	}
	return &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: key}}, MinVersion: tls.VersionTLS12, InsecureSkipVerify: true}, nil // #nosec G402 -- Perun wire signatures authenticate peers; test-LAN only.
}

func encodeWire(a map[wallet.BackendID]wire.Address) (string, error) {
	b, err := a[ethwallet.BackendID].MarshalBinary()
	return base64.RawStdEncoding.EncodeToString(b), err
}
func decodeWire(s string) (map[wallet.BackendID]wire.Address, error) {
	b, err := base64.RawStdEncoding.DecodeString(s)
	if err != nil {
		return nil, err
	}
	a, err := lanwire.ParseAddress(b)
	if err != nil {
		return nil, err
	}
	return map[wallet.BackendID]wire.Address{ethwallet.BackendID: a}, nil
}

type peerInfo struct {
	Name, Endpoint, Wire, Ethereum, Ledger string
}
type manifest struct {
	Hub                      peerInfo
	Asset                    string // "eth" or "points"; must match every client's config
	Adjudicator, AssetHolder string
	Token                    string `json:",omitempty"` // points token, points mode only
	ChainID                  uint64
	RunID                    string
}
type registration struct{ Name, Endpoint, Wire, Ethereum string }
type nodeMark struct{ Name string }
type barrierMark struct{ Name, Key string }
type ledgerSettleRequest struct {
	Name, Ledger string
}

type controlStatus struct {
	Peers                []peerInfo
	LedgersReady         bool
	Closing              bool
	VirtualOpen          []string
	StartAtUnixNano      int64 // set once every client holds its full mesh
	Barriers             map[string][]string
	PaymentsDone         []string
	VirtualSettled       []string
	ClientLedgersSettled []string
	HubLedgersSettled    []string
	Complete             bool
	Error                string
}

// hubControl is a test-only discovery/lifecycle service. It never receives
// payment updates or private channel states.
type hubControl struct {
	mu                   sync.Mutex
	cfg                  clusterConfig
	man                  manifest
	dialer               *simple.Dialer
	hub                  *Hub
	peers                map[string]peerInfo
	virtualOpen          map[string]bool
	virtualFinalized     map[string]bool
	startAt              time.Time
	barriers             map[string]map[string]bool
	paymentsDone         map[string]bool
	virtualSettled       map[string]bool
	clientLedgersSettled map[string]bool
	hubLedgersSettled    map[string]bool
	clientDone           map[string]bool
	results              map[string]distributedResultFile
	closing              bool
	complete             bool
	runErr               string
	done                 chan struct{}
	doneOnce             sync.Once
}

func (h *hubControl) updateCompleteLocked() {
	h.complete = h.runErr == "" &&
		len(h.clientLedgersSettled) == len(h.cfg.Clients) &&
		len(h.hubLedgersSettled) == len(h.cfg.Clients)
	if h.complete {
		suppressExpectedShutdownLogs()
	}
}

func (h *hubControl) failLocked(err error) {
	if h.runErr == "" {
		h.runErr = err.Error()
	}
	h.doneOnce.Do(func() { close(h.done) })
}

func suppressExpectedShutdownLogs() {
	// go-perun v0.15 logs an orderly remote TCP close as an unexpected recvLoop
	// error. Once every channel is withdrawn there is no protocol work left to
	// diagnose, so silence only this process's teardown interval.
	distributedLogger.SetLevel(logrus.PanicLevel)
}

func sortedMarks(m map[string]bool) []string {
	names := make([]string, 0, len(m))
	for name, marked := range m {
		if marked {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	return names
}

func (h *hubControl) status() controlStatus {
	h.mu.Lock()
	defer h.mu.Unlock()
	ps := make([]peerInfo, 0, len(h.peers))
	ledgersReady := len(h.peers) == len(h.cfg.Clients)
	for _, p := range h.peers {
		ps = append(ps, p)
		if p.Ledger == "" {
			ledgersReady = false
		}
	}
	sort.Slice(ps, func(i, j int) bool { return ps[i].Name < ps[j].Name })
	barriers := make(map[string][]string, len(h.barriers))
	for key, marks := range h.barriers {
		barriers[key] = sortedMarks(marks)
	}
	var startAt int64
	if !h.startAt.IsZero() {
		startAt = h.startAt.UnixNano()
	}
	return controlStatus{
		Peers: ps, LedgersReady: ledgersReady, Closing: h.closing,
		VirtualOpen:          sortedMarks(h.virtualOpen),
		StartAtUnixNano:      startAt,
		Barriers:             barriers,
		PaymentsDone:         sortedMarks(h.paymentsDone),
		VirtualSettled:       sortedMarks(h.virtualSettled),
		ClientLedgersSettled: sortedMarks(h.clientLedgersSettled),
		HubLedgersSettled:    sortedMarks(h.hubLedgersSettled),
		Complete:             h.complete, Error: h.runErr,
	}
}

func (h *hubControl) peersSnapshot() []peerInfo {
	h.mu.Lock()
	defer h.mu.Unlock()
	ps := make([]peerInfo, 0, len(h.peers))
	for _, p := range h.peers {
		ps = append(ps, p)
	}
	sort.Slice(ps, func(i, j int) bool { return ps[i].Name < ps[j].Name })
	return ps
}
func (h *hubControl) readyLedgers() bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.peers) != len(h.cfg.Clients) {
		return false
	}
	for _, p := range h.peers {
		if p.Ledger == "" {
			return false
		}
	}
	return true
}
func (h *hubControl) isComplete() bool { h.mu.Lock(); defer h.mu.Unlock(); return h.complete }

// mark handles a POST carrying a nodeMark from a registered client and applies
// set under the hub lock.
func (h *hubControl) mark(w http.ResponseWriter, r *http.Request, set func(name string)) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST required", http.StatusMethodNotAllowed)
		return
	}
	var mark nodeMark
	if err := json.NewDecoder(r.Body).Decode(&mark); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	h.mu.Lock()
	_, known := h.peers[mark.Name]
	if known {
		set(mark.Name)
	}
	h.mu.Unlock()
	if !known {
		http.Error(w, "unknown client", 400)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (h *hubControl) serve() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/manifest", func(w http.ResponseWriter, _ *http.Request) { json.NewEncoder(w).Encode(h.man) })
	mux.HandleFunc("/v1/register", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var reg registration
		if err := json.NewDecoder(r.Body).Decode(&reg); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		if _, ok := clusterClient(h.cfg, reg.Name); !ok || reg.Endpoint == "" || reg.Wire == "" {
			http.Error(w, "unknown or malformed client", 400)
			return
		}
		wa, err := decodeWire(reg.Wire)
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		h.dialer.Register(wa, reg.Endpoint)
		h.hub.AllowLedgerPeer(wa)
		h.mu.Lock()
		h.peers[reg.Name] = peerInfo{Name: reg.Name, Endpoint: reg.Endpoint, Wire: reg.Wire, Ethereum: reg.Ethereum}
		h.mu.Unlock()
		json.NewEncoder(w).Encode(struct {
			Manifest manifest
			Peers    []peerInfo
		}{h.man, h.peersSnapshot()})
	})
	mux.HandleFunc("/v1/unregister", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var mark nodeMark
		if err := json.NewDecoder(r.Body).Decode(&mark); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		h.mu.Lock()
		delete(h.peers, mark.Name)
		delete(h.virtualOpen, mark.Name)
		delete(h.virtualFinalized, mark.Name)
		delete(h.paymentsDone, mark.Name)
		delete(h.virtualSettled, mark.Name)
		delete(h.clientLedgersSettled, mark.Name)
		delete(h.hubLedgersSettled, mark.Name)
		h.mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/v1/peers", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(h.status())
	})
	mux.HandleFunc("/v1/ledger", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", 405)
			return
		}
		var p peerInfo
		if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		h.mu.Lock()
		known, ok := h.peers[p.Name]
		if ok {
			known.Ledger = p.Ledger
			h.peers[p.Name] = known
		}
		h.mu.Unlock()
		if !ok || p.Ledger == "" {
			http.Error(w, "unknown client or empty ledger", 400)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	// Every client marks the end of its own payment phase. Nobody may finalize
	// a channel while its peer is still paying on it.
	mux.HandleFunc("/v1/payments-done", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var mark nodeMark
		if err := json.NewDecoder(r.Body).Decode(&mark); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		h.mu.Lock()
		_, known := h.peers[mark.Name]
		if known {
			if h.paymentsDone == nil {
				h.paymentsDone = make(map[string]bool)
			}
			h.paymentsDone[mark.Name] = true
		}
		h.mu.Unlock()
		if !known {
			http.Error(w, "unknown client", 400)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	// Generic named barrier: clients mark a key and wait until all have.
	mux.HandleFunc("/v1/barrier", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var mark barrierMark
		if err := json.NewDecoder(r.Body).Decode(&mark); err != nil || mark.Key == "" {
			http.Error(w, "name and key required", 400)
			return
		}
		h.mu.Lock()
		_, known := h.peers[mark.Name]
		if known {
			if h.barriers == nil {
				h.barriers = make(map[string]map[string]bool)
			}
			if h.barriers[mark.Key] == nil {
				h.barriers[mark.Key] = make(map[string]bool)
			}
			h.barriers[mark.Key][mark.Name] = true
		}
		h.mu.Unlock()
		if !known {
			http.Error(w, "unknown client", 400)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/v1/virtual-open", func(w http.ResponseWriter, r *http.Request) {
		h.mark(w, r, func(name string) {
			if h.virtualOpen == nil {
				h.virtualOpen = make(map[string]bool)
			}
			h.virtualOpen[name] = true
			if len(h.virtualOpen) == len(h.cfg.Clients) && h.startAt.IsZero() {
				// Far enough ahead that every client's next status poll sees it.
				h.startAt = time.Now().Add(1500 * time.Millisecond)
			}
		})
	})
	mux.HandleFunc("/v1/close-virtual", func(w http.ResponseWriter, r *http.Request) {
		h.mark(w, r, func(name string) {
			if h.virtualFinalized == nil {
				h.virtualFinalized = make(map[string]bool)
			}
			h.virtualFinalized[name] = true
			h.closing = len(h.virtualFinalized) == len(h.cfg.Clients)
		})
	})
	mux.HandleFunc("/v1/virtual-settled", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var mark nodeMark
		if err := json.NewDecoder(r.Body).Decode(&mark); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		h.mu.Lock()
		_, known := h.peers[mark.Name]
		if known {
			h.virtualSettled[mark.Name] = true
		}
		h.mu.Unlock()
		if !known {
			http.Error(w, "unknown client", 400)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/v1/settle-ledger", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var req ledgerSettleRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		id, err := decodeChannelID(req.Ledger)
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		hubCh, ok := h.hub.ledgerWith(id)
		if !ok {
			http.Error(w, "hub ledger not found", 404)
			return
		}
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), h.cfg.operationTimeout())
			defer cancel()
			err := settleContext(ctx, hubCh, true)
			h.mu.Lock()
			defer h.mu.Unlock()
			if err != nil {
				h.failLocked(fmt.Errorf("settle hub ledger for %s: %w", req.Name, err))
				return
			}
			h.hubLedgersSettled[req.Name] = true
			h.updateCompleteLocked()
		}()
		w.WriteHeader(http.StatusAccepted)
	})
	mux.HandleFunc("/v1/client-ledger-settled", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var mark nodeMark
		if err := json.NewDecoder(r.Body).Decode(&mark); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		h.mu.Lock()
		_, known := h.peers[mark.Name]
		if known {
			h.clientLedgersSettled[mark.Name] = true
		}
		h.updateCompleteLocked()
		h.mu.Unlock()
		if !known {
			http.Error(w, "unknown client", 400)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/v1/client-done", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var mark nodeMark
		if err := json.NewDecoder(r.Body).Decode(&mark); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		h.mu.Lock()
		_, known := h.peers[mark.Name]
		_, resultSaved := h.results[mark.Name]
		if known && h.complete && resultSaved {
			h.clientDone[mark.Name] = true
		}
		allDone := h.complete && len(h.clientDone) == len(h.cfg.Clients)
		h.mu.Unlock()
		if !known {
			http.Error(w, "unknown client", 400)
			return
		}
		if !h.isComplete() {
			http.Error(w, "run is not complete", 409)
			return
		}
		if !resultSaved {
			http.Error(w, "client result has not been uploaded", 409)
			return
		}
		w.WriteHeader(http.StatusNoContent)
		if allDone {
			h.doneOnce.Do(func() { close(h.done) })
		}
	})
	mux.HandleFunc("/v1/result", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST required", http.StatusMethodNotAllowed)
			return
		}
		var result distributedResultFile
		if err := json.NewDecoder(r.Body).Decode(&result); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		h.mu.Lock()
		_, known := h.peers[result.Node]
		valid := known && result.RunID == h.man.RunID && result.Completed
		h.mu.Unlock()
		if !valid {
			http.Error(w, "result does not match this run", 400)
			return
		}
		if err := writeDistributedResultFile(h.cfg.ResultDir, result); err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		h.mu.Lock()
		h.results[result.Node] = result
		h.mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	})
	return mux
}

func httpJSON(method, url string, in, out any) error {
	var body io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return err
		}
		body = strings.NewReader(string(b))
	}
	req, err := http.NewRequest(method, url, body)
	if err != nil {
		return err
	}
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := (&http.Client{Timeout: 8 * time.Second}).Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("%s: %s", url, resp.Status)
	}
	if out != nil {
		return json.NewDecoder(resp.Body).Decode(out)
	}
	return nil
}

func ethKeyForDevNode(name string) string {
	for i, n := range append([]string{"deployer", "ingrid"}, clientNames...) {
		if strings.EqualFold(n, name) {
			return anvilKeys[i]
		}
	}
	return ""
}

func newRunID() string {
	var suffix [4]byte
	if _, err := rand.Read(suffix[:]); err != nil {
		return time.Now().UTC().Format("20060102T150405Z")
	}
	return fmt.Sprintf("%s-%x", time.Now().UTC().Format("20060102T150405Z"), suffix)
}

func distributedStateDir(cfg clusterConfig, name string) string {
	return filepath.Join(cfg.StateRoot, name)
}

func runHub(cfg clusterConfig) error {
	return runHubWith(cfg, false)
}

// runHubWith is runHub. paymentPolicy turns on the hub's update policy on its
// ledger channels (uc2-hub, unless the config disables it): a player may only
// pay the hub, never be paid by it. Without contracts_file / hub_key_file the
// hub deploys its own contracts and uses the dev key of its name, as -mode
// hub always did.
func runHubWith(cfg clusterConfig, paymentPolicy bool) error {
	return runHubWithDeposits(cfg, paymentPolicy, nil)
}

// runHubWithDeposits is runHubWith; deposits, when non-nil ([player, hub] in
// token units, uc2-hub), are the only ledger funding the hub accepts.
func runHubWithDeposits(cfg clusterConfig, paymentPolicy bool, deposits []*big.Int) error {
	key, err := hubKey(cfg)
	if err != nil {
		return err
	}
	id, err := lanwire.LoadOrCreate(distributedStateDir(cfg, cfg.Hub.Name), cfg.Hub.Name)
	if err != nil {
		return err
	}
	netrt, err := newNetworkRuntime(id, cfg.Hub.Bind)
	if err != nil {
		return fmt.Errorf("network bus: %w", err)
	}
	graceful := false
	defer func() {
		if graceful {
			netrt.stopListening()
			return
		}
		netrt.close()
	}()
	contracts, err := hubContracts(cfg)
	if err != nil {
		return err
	}
	adj := contracts.Adjudicator
	hub, err := NewHubWithIdentity(cfg.Hub.Name, netrt.bus, key, cfg.ChainURL, cfg.ChainID, contracts, id, distributedStateDir(cfg, cfg.Hub.Name))
	if err != nil {
		return err
	}
	defer hub.shutdown()
	// The hub's own on-chain cost: its deposit (approve + deposit for points)
	// into every client's ledger channel, then its withdrawals.
	hubGas, err := newGasMeterFrom(cfg.ChainURL, hub.ethAddr)
	if err != nil {
		return fmt.Errorf("gas meter: %w", err)
	}
	hubGas.labelContracts(contracts)
	hub.RequireRegisteredPeers(true)
	hub.RequireSafePaymentUpdates(paymentPolicy)
	if deposits != nil {
		hub.RequireLedgerDeposits(deposits[0], deposits[1])
	}
	wh, _ := encodeWire(hub.wireAddr)
	ctl := &hubControl{
		cfg: cfg, dialer: netrt.dialer, hub: hub,
		peers:                make(map[string]peerInfo),
		virtualSettled:       make(map[string]bool),
		clientLedgersSettled: make(map[string]bool),
		hubLedgersSettled:    make(map[string]bool),
		clientDone:           make(map[string]bool),
		results:              make(map[string]distributedResultFile),
		done:                 make(chan struct{}),
		man: manifest{
			Hub:         peerInfo{Name: cfg.Hub.Name, Endpoint: cfg.Hub.Endpoint, Wire: wh, Ethereum: hub.ethAddr.Hex()},
			Asset:       cfg.Asset,
			Adjudicator: adj.Hex(), AssetHolder: contracts.AssetHolder.Hex(), ChainID: cfg.ChainID, RunID: newRunID(),
		},
	}
	if contracts.Asset == assetPoints {
		ctl.man.Token = contracts.Token.Hex()
	}
	server := &http.Server{Addr: cfg.Hub.Control, Handler: ctl.serve(), ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = server.ListenAndServe() }()
	fmt.Printf("hub ready: run=%s control=http://%s, p2p=%s, adjudicator=%s, asset=%s\n", ctl.man.RunID, cfg.Hub.Control, cfg.Hub.Endpoint, adj.Hex(), cfg.Asset)
	// Split the hub's gas at the moment every ledger channel is funded: before
	// it the hub only deposits, after it only withdraws (virtual channels never
	// touch the chain).
	var hubOpenGas *gasFile
	hubOpenDone := make(chan struct{})
	go func() {
		defer close(hubOpenDone)
		for !ctl.readyLedgers() {
			select {
			case <-ctl.done:
				return
			case <-time.After(200 * time.Millisecond):
			}
		}
		hubOpenGas = newGasFile(hubGas.gasSinceByContract())
	}()
	if cfg.KeepHubRunning {
		waitForSignal()
	} else {
		select {
		case <-ctl.done:
			status := ctl.status()
			if status.Error == "" {
				<-hubOpenDone
				fmt.Printf("hub complete: run=%s all virtual and ledger channels settled\n", ctl.man.RunID)
				fmt.Printf("hub gas: ledger_open=%s ledger_settle=%s\n", hubOpenGas, newGasFile(hubGas.gasSinceByContract()))
				// Both clients have already written their result files and acknowledged
				// completion. Give their processes time to exit before Ingrid releases
				// the final TCP streams.
				time.Sleep(2 * time.Second)
			}
		case <-signalChannel():
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), cfg.operationTimeout())
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		return err
	}
	if status := ctl.status(); status.Error != "" {
		return fmt.Errorf("distributed run failed: %s", status.Error)
	}
	graceful = ctl.isComplete()
	return nil
}

// hubKey is the hub's Ethereum key: from hub_key_file when the config names
// one, else the Anvil dev key of the hub's name. With an "ethereum" address
// for the hub in the config, the key must match it: the mint check was made
// against that address.
func hubKey(cfg clusterConfig) (string, error) {
	if cfg.HubKeyFile == "" {
		key := ethKeyForDevNode(cfg.Hub.Name)
		if key == "" {
			return "", fmt.Errorf("hub name %q has no Anvil development key (and no hub_key_file)", cfg.Hub.Name)
		}
		return key, nil
	}
	key, err := readKeyFile(cfg.HubKeyFile)
	if err != nil {
		return "", fmt.Errorf("hub_key_file: %w", err)
	}
	k, err := crypto.HexToECDSA(key)
	if err != nil {
		return "", fmt.Errorf("hub_key_file %s: %w", cfg.HubKeyFile, err)
	}
	if cfg.Hub.Ethereum != "" && crypto.PubkeyToAddress(k.PublicKey) != common.HexToAddress(cfg.Hub.Ethereum) {
		return "", fmt.Errorf("hub_key_file %s holds the key of %s, but the config says the hub is %s",
			cfg.HubKeyFile, crypto.PubkeyToAddress(k.PublicKey).Hex(), cfg.Hub.Ethereum)
	}
	return key, nil
}

// hubContracts are the contracts the hub runs on: those in contracts_file
// (deployed and minted by uc2-deploy, whose gas that file records), or a
// fresh deployment whose setup cost is printed apart from Open.
func hubContracts(cfg clusterConfig) (chainContracts, error) {
	if cfg.ContractsFile != "" {
		c, err := loadUC2Contracts(cfg.ContractsFile)
		if err != nil {
			return chainContracts{}, err
		}
		fmt.Printf("contracts from %s (deploy gas %d in %d txs, paid by uc2-deploy)\n", cfg.ContractsFile, c.DeployGas, c.DeployTxs)
		return c.chain(), nil
	}
	// Setup cost, reported apart from Open: the deployer's contract creations
	// and, in points mode, its one mintBatch.
	deployerKey, err := crypto.HexToECDSA(anvilKeys[0])
	if err != nil {
		return chainContracts{}, err
	}
	setupGas, err := newGasMeterFrom(cfg.ChainURL, crypto.PubkeyToAddress(deployerKey.PublicKey))
	if err != nil {
		return chainContracts{}, fmt.Errorf("gas meter: %w", err)
	}
	contracts, err := deployContracts(cfg.ChainURL, cfg.ChainID, anvilKeys[0], cfg.assetKind())
	if err != nil {
		return contracts, err
	}
	setupGas.labelContracts(contracts)
	fmt.Printf("setup gas: %s (deploy = contract creations, token = mintBatch)\n", newGasFile(setupGas.gasSinceByContract()))
	return contracts, nil
}

func runClient(cfg clusterConfig, name string, payments int, tracePath string, paced bool) error {
	nc, ok := clusterClient(cfg, name)
	if !ok {
		return fmt.Errorf("unknown client %q", name)
	}
	key := ethKeyForDevNode(name)
	if key == "" {
		return fmt.Errorf("client name %q has no Anvil development key", name)
	}
	// Every client is given the SAME trace and replays only the payments it
	// makes itself. Load and validate it before any chain or peer work, so an
	// unusable trace costs nothing instead of failing after a full ledger open.
	myIdx := clusterClientIndex(cfg, name)
	var traceEvents []tracePayment
	if tracePath != "" {
		events, nPlayers, err := loadTrace(tracePath)
		if err != nil {
			return fmt.Errorf("trace: %w", err)
		}
		traceEvents, err = selectTraceEvents(events, nPlayers, myIdx, len(cfg.Clients))
		if err != nil {
			return fmt.Errorf("trace %s: %w", tracePath, err)
		}
	}
	id, err := lanwire.LoadOrCreate(distributedStateDir(cfg, name), name)
	if err != nil {
		return err
	}
	netrt, err := newNetworkRuntime(id, nc.Bind)
	if err != nil {
		return err
	}
	completed := false
	defer func() {
		if completed {
			// This process exits immediately after returning. Closing the listener
			// releases the port; letting process exit release established streams
			// avoids go-perun logging its deliberate bus close as a recvLoop error.
			netrt.stopListening()
			return
		}
		netrt.close()
	}()
	localWire := map[wallet.BackendID]wire.Address{ethwallet.BackendID: id.Address}
	enc, _ := encodeWire(localWire)
	ethKey, _ := crypto.HexToECDSA(key)
	ethAddr := crypto.PubkeyToAddress(ethKey.PublicKey)
	var regResp struct {
		Manifest manifest
		Peers    []peerInfo
	}
	if err := httpJSON(http.MethodPost, strings.TrimRight(nc.Control, "/")+"/v1/register", registration{Name: name, Endpoint: nc.Endpoint, Wire: enc, Ethereum: ethAddr.Hex()}, &regResp); err != nil {
		return fmt.Errorf("register with hub: %w", err)
	}
	defer func() {
		if !completed {
			_ = httpJSON(http.MethodPost, strings.TrimRight(nc.Control, "/")+"/v1/unregister", nodeMark{Name: name}, nil)
		}
	}()
	hubWire, contracts, err := joinHub(cfg, netrt.dialer, regResp.Manifest)
	if err != nil {
		return err
	}
	hubAsset := contracts.Asset
	c, err := NewClientWithIdentity(name, netrt.bus, key, cfg.ChainURL, cfg.ChainID, contracts, id, distributedStateDir(cfg, name))
	if err != nil {
		return err
	}
	defer c.shutdown()
	// This node's own on-chain cost of Open (deposit; approve + deposit for
	// points) and of Close (conclude + withdraw), apart from other nodes' txs.
	gm, err := newGasMeterFrom(cfg.ChainURL, ethAddr)
	if err != nil {
		return fmt.Errorf("gas meter: %w", err)
	}
	gm.labelContracts(contracts)
	var ledgerOpenGas, ledgerSettleGas *gasFile
	if err := c.perun.Restore(context.Background()); err != nil {
		return fmt.Errorf("restore: %w", err)
	}
	ledgerRestored := c.ledger != nil
	var ledgerOpenDuration time.Duration
	if c.ledger == nil {
		gm.mark()
		start := time.Now()
		if err := c.OpenLedgerToWire(hubWire, cfg.LedgerDeposit, cfg.ledgerHubDeposit(), cfg.ChallengeSecs); err != nil {
			return fmt.Errorf("open ledger: %w", err)
		}
		ledgerOpenDuration = time.Since(start)
		ledgerOpenGas = newGasFile(gm.gasSinceByContract())
	}
	c.RequireRegisteredVirtualPeers(true)
	c.RequireSafePaymentUpdates(!cfg.DisablePaymentPolicy)
	if err := publishLedgerAndWait(cfg, nc, netrt.dialer, c, name); err != nil {
		return err
	}
	var paymentLatencies, cleanLatencies []time.Duration
	var paymentBurst time.Duration
	var traceResult *distributedTraceResult
	control := strings.TrimRight(nc.Control, "/")

	// Full mesh of virtual channels, one per pair of clients, then the
	// virtual-open barrier. The hub names one start instant, so a paced replay
	// begins at the same moment everywhere (as closely as the clocks agree).
	virtualByIndex, virtualOpenDuration, startAt, err := openMeshAtBarrier(cfg, control, c, netrt.dialer, name)
	if err != nil {
		return err
	}
	time.Sleep(time.Until(startAt))

	switch {
	case tracePath != "":
		tc := traceChannels{ledger: c.ledger, virtual: virtualByIndex, asset: c.asset, kind: c.kind}
		switch {
		case len(traceEvents) == 0:
			traceResult = &distributedTraceResult{Path: tracePath, Paced: paced}
		case paced:
			traceResult = replayDistributedTracePaced(tracePath, traceEvents, tc)
		default:
			traceResult = replayDistributedTrace(tracePath, traceEvents, tc)
		}
		printDistributedTraceSummary(traceResult)
		// Report the trace replay through the same fields as the burst so
		// throughput/percentiles stay comparable across both workloads.
		paymentLatencies = traceResult.LatAll
		paymentBurst = traceResult.Wall
		payments = traceResult.OK

	case payments > 0:
		// Synthetic burst: the first client pays the second, every other client
		// pays the first. With -payments on both ends of one channel this is the
		// head-on collision workload (update_policy.go).
		target := 0
		if myIdx == 0 {
			target = 1
		}
		ch, ok := virtualByIndex[target]
		if !ok {
			return fmt.Errorf("virtual channel to %s missing", cfg.Clients[target].Name)
		}
		if paymentLatencies, cleanLatencies, paymentBurst, err = runBurst(ch, c.asset, c.kind.units(c.kind.burstAmount()), payments); err != nil {
			return err
		}
		printDistributedPaymentSummary(payments, paymentBurst, paymentLatencies)
	}
	virtualFinal, err := endPaymentsAndSnapshot(cfg, control, c, name)
	if err != nil {
		return err
	}
	gm.mark()
	virtualSettleDuration, ledgerSettleDuration, err := closeDistributedClient(cfg, nc, c, name, virtualByIndex)
	if err != nil {
		return err
	}
	ledgerSettleGas = newGasFile(gm.gasSinceByContract())
	// Every Perun channel is now settled. A peer may legitimately exit while
	// this process writes and uploads its result, and go-perun v0.15 otherwise
	// labels that orderly TCP close as an unexpected recvLoop error.
	suppressExpectedShutdownLogs()
	resultFile, err := writeDistributedResult(cfg, distributedResult{
		RunID: regResp.Manifest.RunID, Node: name, Payments: payments,
		PaymentBurst: paymentBurst, PaymentLatencies: paymentLatencies,
		CleanLatencies: cleanLatencies, Collisions: int(gaveWayTotal.Load()), VirtualFinal: virtualFinal,
		LedgerOpen: ledgerOpenDuration, LedgerRestored: ledgerRestored, VirtualOpen: virtualOpenDuration,
		VirtualSettle: virtualSettleDuration, LedgerSettle: ledgerSettleDuration,
		Completed: true, StrictPaymentPolicy: !cfg.DisablePaymentPolicy, Trace: traceResult,
		Asset: hubAsset, LedgerOpenGas: ledgerOpenGas, LedgerSettleGas: ledgerSettleGas,
	})
	if err != nil {
		return fmt.Errorf("write result: %w", err)
	}
	if err := httpJSON(http.MethodPost, strings.TrimRight(nc.Control, "/")+"/v1/result", resultFile, nil); err != nil {
		return fmt.Errorf("upload result to hub: %w", err)
	}
	fmt.Printf("client complete: run=%s node=%s ledger_open=%s virtual_open=%s virtual_settle=%s ledger_settle=%s\n",
		regResp.Manifest.RunID, name, ledgerOpenDuration.Round(time.Millisecond), virtualOpenDuration.Round(time.Millisecond),
		virtualSettleDuration.Round(time.Millisecond), ledgerSettleDuration.Round(time.Millisecond))
	fmt.Printf("client gas: node=%s asset=%s ledger_open=%s ledger_settle=%s\n", name, hubAsset, ledgerOpenGas, ledgerSettleGas)
	if err := httpJSON(http.MethodPost, strings.TrimRight(nc.Control, "/")+"/v1/client-done", nodeMark{Name: name}, nil); err != nil {
		return fmt.Errorf("acknowledge completed run: %w", err)
	}
	completed = true
	return nil
}

// joinHub reads the manifest a client got back from POST /v1/register: the
// hub's wire address (registered with the dialer) and the contracts. Every
// process reads the asset from its own config; it refuses to join a hub that
// deployed the other one, rather than fail later inside funding.
func joinHub(cfg clusterConfig, dialer *simple.Dialer, man manifest) (map[wallet.BackendID]wire.Address, chainContracts, error) {
	hubWire, err := decodeWire(man.Hub.Wire)
	if err != nil {
		return nil, chainContracts{}, err
	}
	dialer.Register(hubWire, man.Hub.Endpoint)
	hubAsset, err := parseAssetKind(man.Asset)
	if err != nil {
		return nil, chainContracts{}, fmt.Errorf("hub manifest: %w", err)
	}
	if hubAsset != cfg.assetKind() {
		return nil, chainContracts{}, fmt.Errorf("hub runs asset %q but this node's config says %q", hubAsset, cfg.Asset)
	}
	return hubWire, chainContracts{
		Asset:       hubAsset,
		Adjudicator: common.HexToAddress(man.Adjudicator),
		AssetHolder: common.HexToAddress(man.AssetHolder),
		Token:       common.HexToAddress(man.Token),
	}, nil
}

// publishLedgerAndWait runs once this client's ledger channel is open: it
// learns every other client from the hub, publishes its ledger ID, and waits
// until every client has published one.
func publishLedgerAndWait(cfg clusterConfig, nc clusterNode, dialer *simple.Dialer, c *Client, name string) error {
	if err := registerKnownPeers(nc.Control, dialer, c, name, len(cfg.Clients), cfg.startupTimeout()); err != nil {
		return err
	}
	ledgerID := c.ledger.ID()
	if err := httpJSON(http.MethodPost, strings.TrimRight(nc.Control, "/")+"/v1/ledger", peerInfo{Name: name, Ledger: hex.EncodeToString(ledgerID[:])}, nil); err != nil {
		return err
	}
	return waitForLedgers(nc.Control, len(cfg.Clients), cfg.startupTimeout())
}

// openMeshAtBarrier is openMesh followed by the virtual-open barrier: nobody
// pays until every client holds every one of its channels. It returns the mesh,
// the time openMesh itself took, and the start instant the hub named.
func openMeshAtBarrier(cfg clusterConfig, control string, c *Client, dialer *simple.Dialer, name string) (map[int]*client.Channel, time.Duration, time.Time, error) {
	start := time.Now()
	mesh, err := openMesh(cfg, control, c, dialer, name)
	if err != nil {
		return nil, 0, time.Time{}, err
	}
	took := time.Since(start)
	if err := httpJSON(http.MethodPost, control+"/v1/virtual-open", nodeMark{Name: name}, nil); err != nil {
		return nil, 0, time.Time{}, fmt.Errorf("publish open virtual channels: %w", err)
	}
	ready, err := waitForStatus(control, "every client to open its virtual channels", cfg.startupTimeout(), func(s controlStatus) bool {
		return s.StartAtUnixNano != 0
	})
	if err != nil {
		return nil, 0, time.Time{}, err
	}
	return mesh, took, time.Unix(0, ready.StartAtUnixNano), nil
}

// endPaymentsAndSnapshot is the end of the payment phase: the payments-done
// barrier (no channel may be finalized while its peer is still paying on it),
// the snapshot of the virtual channels, and the snapshot barrier. Finalizing is
// itself an update (version + 1), so it waits until every client has recorded
// its snapshot, or the two ends of a channel would report different versions
// depending on who polled the barrier first.
func endPaymentsAndSnapshot(cfg clusterConfig, control string, c *Client, name string) ([]channelFinal, error) {
	if err := httpJSON(http.MethodPost, control+"/v1/payments-done", nodeMark{Name: name}, nil); err != nil {
		return nil, fmt.Errorf("publish end of payments: %w", err)
	}
	if _, err := waitForStatus(control, "all clients to finish paying", cfg.operationTimeout(), func(s controlStatus) bool {
		return len(s.PaymentsDone) == len(cfg.Clients)
	}); err != nil {
		return nil, err
	}
	virtualFinal := snapshotVirtualChannels(c)
	if err := httpJSON(http.MethodPost, control+"/v1/barrier", barrierMark{Name: name, Key: "snapshot"}, nil); err != nil {
		return nil, fmt.Errorf("publish snapshot: %w", err)
	}
	if _, err := waitForStatus(control, "every client to record its channel snapshot", cfg.operationTimeout(), func(s controlStatus) bool {
		return len(s.Barriers["snapshot"]) == len(cfg.Clients)
	}); err != nil {
		return nil, err
	}
	return virtualFinal, nil
}

func (c *Client) OpenLedgerToWire(hubWire map[wallet.BackendID]wire.Address, myDeposit, hubDeposit float64, challenge uint64) error {
	peers := []map[wallet.BackendID]wire.Address{c.wireAddr, hubWire}
	initAlloc := channel.NewAllocation(2, []wallet.BackendID{ethwallet.BackendID}, c.asset)
	initAlloc.SetAssetBalances(c.asset, []channel.Bal{c.kind.units(myDeposit), c.kind.units(hubDeposit)})
	prop, err := client.NewLedgerChannelProposal(challenge, c.wAddr, initAlloc, peers)
	if err != nil {
		return err
	}
	ch, err := c.perun.ProposeChannel(context.Background(), prop)
	if err != nil {
		return err
	}
	c.startWatching(ch)
	c.ledger = ch
	return nil
}

// runBurst sends n closed-loop payments. clean holds only the samples that did
// not give way to a simultaneous proposal, so the yield timeout never
// masquerades as Perun's latency.
func runBurst(ch *client.Channel, asset channel.Asset, amount *big.Int, n int) (all, clean []time.Duration, burst time.Duration, err error) {
	start := time.Now()
	for i := 0; i < n; i++ {
		t0 := time.Now()
		gaveWay, err := payCounted(ch, asset, amount)
		if err != nil {
			return all, clean, time.Since(start), fmt.Errorf("payment %d: %w", i, err)
		}
		lat := time.Since(t0)
		all = append(all, lat)
		if gaveWay == 0 {
			clean = append(clean, lat)
		}
	}
	return all, clean, time.Since(start), nil
}

// settleRounds and settlePartner define the order in which the mesh is settled.
// go-perun's hub waits a fixed 10 s for the matching settlement proposal from
// the other end of a virtual channel, and a client's settlement holds its
// ledger channel until the hub answers. If every client settled in its own
// order, A would wait on B while B waits on C, and all of them time out. The
// schedule is a round-robin tournament (circle method): each round is a set of
// disjoint pairs, so both ends of a channel always settle it together. For an
// odd number of clients one client sits each round out (partner -1).
func settleRounds(n int) int {
	if n%2 == 1 {
		return n
	}
	return n - 1
}

func settlePartner(n, round, me int) int {
	m := n + n%2 // pad odd n with a dummy client m-1
	partner := round
	if me != m-1 {
		partner = ((2*round-me)%(m-1) + (m - 1)) % (m - 1)
		if partner == me {
			partner = m - 1
		}
	}
	if partner >= n {
		return -1
	}
	return partner
}

// openMesh gives this client one virtual channel with every other client. For
// each pair the client with the LOWER cluster index opens the channel, which
// also makes it index 0 of that channel and therefore the priority side of the
// collision policy. It returns the channels keyed by the peer's cluster index.
func openMesh(cfg clusterConfig, control string, c *Client, dialer *simple.Dialer, name string) (map[int]*client.Channel, error) {
	var ps struct{ Peers []peerInfo }
	if err := httpJSON(http.MethodGet, control+"/v1/peers", nil, &ps); err != nil {
		return nil, err
	}
	myIdx := clusterClientIndex(cfg, name)
	type remote struct {
		idx  int
		name string
		wire map[wallet.BackendID]wire.Address
	}
	var remotes []remote
	for _, peer := range ps.Peers {
		idx := clusterClientIndex(cfg, peer.Name)
		if peer.Name == name || idx < 0 {
			continue
		}
		pw, err := decodeWire(peer.Wire)
		if err != nil {
			return nil, err
		}
		dialer.Register(pw, peer.Endpoint)
		c.AllowVirtualPeer(pw)
		remotes = append(remotes, remote{idx, peer.Name, pw})
		if idx < myIdx {
			continue // the lower index opens; we wait for its proposal below
		}
		ledger, err := decodeChannelID(peer.Ledger)
		if err != nil {
			return nil, err
		}
		if _, err := c.OpenVirtualToPeerWithChallenge(pw, ledger, cfg.VirtualStake, cfg.VirtualStake, cfg.ChallengeSecs); err != nil {
			return nil, fmt.Errorf("open virtual %s: %w", peer.Name, err)
		}
	}
	mesh := make(map[int]*client.Channel, len(remotes))
	deadline := time.Now().Add(cfg.startupTimeout())
	for _, r := range remotes {
		for {
			if ch, ok := c.VirtualWith(r.wire); ok {
				mesh[r.idx] = ch
				break
			}
			if time.Now().After(deadline) {
				return nil, fmt.Errorf("virtual channel from %s never arrived", r.name)
			}
			time.Sleep(10 * time.Millisecond)
		}
	}
	return mesh, nil
}

// snapshotVirtualChannels records where each virtual channel stands once every
// client has stopped paying. Both ends must report the same version and
// balances; a difference means the channel forked.
func snapshotVirtualChannels(c *Client) []channelFinal {
	var out []channelFinal
	for _, ch := range c.VirtualChannels() {
		st := ch.State()
		id := ch.ID()
		f := channelFinal{Channel: hex.EncodeToString(id[:]), Version: st.Version}
		if len(st.Allocation.Balances) > 0 {
			for _, b := range st.Allocation.Balances[0] {
				f.Balances = append(f.Balances, b.String())
			}
		}
		out = append(out, f)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Channel < out[j].Channel })
	return out
}

func decodeChannelID(s string) (channel.ID, error) {
	var id channel.ID
	b, err := hex.DecodeString(s)
	if err != nil || len(b) != len(id) {
		return id, fmt.Errorf("invalid channel ID")
	}
	copy(id[:], b)
	return id, nil
}
func waitForLedgers(control string, n int, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		var p struct {
			Peers        []peerInfo
			LedgersReady bool
		}
		if err := httpJSON(http.MethodGet, strings.TrimRight(control, "/")+"/v1/peers", nil, &p); err == nil && p.LedgersReady && len(p.Peers) == n {
			return nil
		}
		time.Sleep(400 * time.Millisecond)
	}
	return fmt.Errorf("timed out waiting for ledger channels")
}

func registerKnownPeers(control string, dialer *simple.Dialer, c *Client, self string, n int, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		var p struct{ Peers []peerInfo }
		if err := httpJSON(http.MethodGet, strings.TrimRight(control, "/")+"/v1/peers", nil, &p); err == nil && len(p.Peers) == n {
			for _, peer := range p.Peers {
				if peer.Name == self {
					continue
				}
				wa, err := decodeWire(peer.Wire)
				if err != nil {
					return err
				}
				dialer.Register(wa, peer.Endpoint)
				c.AllowVirtualPeer(wa)
			}
			return nil
		}
		time.Sleep(300 * time.Millisecond)
	}
	return fmt.Errorf("timed out waiting for registered peers")
}

func waitForStatus(control, description string, timeout time.Duration, ready func(controlStatus) bool) (controlStatus, error) {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		var status controlStatus
		if err := httpJSON(http.MethodGet, strings.TrimRight(control, "/")+"/v1/peers", nil, &status); err == nil {
			if status.Error != "" {
				return status, fmt.Errorf("hub reported failure: %s", status.Error)
			}
			if ready(status) {
				return status, nil
			}
		}
		time.Sleep(400 * time.Millisecond)
	}
	return controlStatus{}, fmt.Errorf("timed out waiting for %s after %s", description, timeout)
}

func closeDistributedClient(cfg clusterConfig, nc clusterNode, c *Client, name string, mesh map[int]*client.Channel) (time.Duration, time.Duration, error) {
	control := strings.TrimRight(nc.Control, "/")
	timeout := cfg.operationTimeout()
	// Each virtual channel is finalized by the end that opened it (index 0).
	// Nobody settles until every client has finalized its share.
	for _, ch := range c.VirtualChannels() {
		if ch.Idx() != 0 {
			continue
		}
		if err := finalize(ch); err != nil {
			return 0, 0, fmt.Errorf("finalize virtual channel: %w", err)
		}
	}
	if err := httpJSON(http.MethodPost, control+"/v1/close-virtual", nodeMark{Name: name}, nil); err != nil {
		return 0, 0, fmt.Errorf("publish finalized virtual channels: %w", err)
	}
	if _, err := waitForStatus(control, "every client to finalize its virtual channels", timeout, func(s controlStatus) bool {
		return s.Closing
	}); err != nil {
		return 0, 0, err
	}

	// Settle round by round (see settlePartner). The barrier after each round
	// keeps a fast client from running ahead of its next partner; its polling
	// wait is a harness artefact, so only the settle calls themselves are timed.
	var virtualDuration time.Duration
	n, me := len(cfg.Clients), clusterClientIndex(cfg, name)
	for round := 0; round < settleRounds(n); round++ {
		if partner := settlePartner(n, round, me); partner >= 0 {
			ch, ok := mesh[partner]
			if !ok {
				return 0, 0, fmt.Errorf("no virtual channel with %s to settle", cfg.Clients[partner].Name)
			}
			start := time.Now()
			ctx, cancel := context.WithTimeout(context.Background(), timeout)
			err := settleContext(ctx, ch, false)
			cancel()
			if err != nil {
				return 0, 0, fmt.Errorf("settle virtual channel with %s: %w", cfg.Clients[partner].Name, err)
			}
			virtualDuration += time.Since(start)
		}
		key := fmt.Sprintf("settle-round-%d", round)
		if err := httpJSON(http.MethodPost, control+"/v1/barrier", barrierMark{Name: name, Key: key}, nil); err != nil {
			return 0, 0, fmt.Errorf("publish %s: %w", key, err)
		}
		if _, err := waitForStatus(control, "every client to finish "+key, timeout, func(s controlStatus) bool {
			return len(s.Barriers[key]) == n
		}); err != nil {
			return 0, 0, err
		}
	}
	if err := httpJSON(http.MethodPost, control+"/v1/virtual-settled", nodeMark{Name: name}, nil); err != nil {
		return 0, 0, fmt.Errorf("publish virtual settlement: %w", err)
	}
	if _, err := waitForStatus(control, "all clients to settle virtual channels", timeout, func(s controlStatus) bool {
		return len(s.VirtualSettled) == len(cfg.Clients)
	}); err != nil {
		return 0, 0, err
	}

	ledgerStart := time.Now()
	if err := finalize(c.ledger); err != nil {
		return virtualDuration, 0, fmt.Errorf("finalize ledger channel: %w", err)
	}
	ledgerID := c.ledger.ID()
	if err := httpJSON(http.MethodPost, control+"/v1/settle-ledger", ledgerSettleRequest{Name: name, Ledger: hex.EncodeToString(ledgerID[:])}, nil); err != nil {
		return virtualDuration, 0, fmt.Errorf("request hub ledger settlement: %w", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	err := settleContext(ctx, c.ledger, false)
	cancel()
	if err != nil {
		return virtualDuration, 0, fmt.Errorf("settle client ledger: %w", err)
	}
	if err := httpJSON(http.MethodPost, control+"/v1/client-ledger-settled", nodeMark{Name: name}, nil); err != nil {
		return virtualDuration, 0, fmt.Errorf("publish client ledger settlement: %w", err)
	}
	// This client has completed all of its Perun operations. Other clients may
	// finish and close their TCP streams while we wait for the hub-side
	// settlements; go-perun v0.15 reports those orderly closes at error level.
	suppressExpectedShutdownLogs()
	if _, err := waitForStatus(control, "all ledger channels to settle", timeout, func(s controlStatus) bool {
		return s.Complete
	}); err != nil {
		return virtualDuration, 0, err
	}
	return virtualDuration, time.Since(ledgerStart), nil
}
func waitForSignal() {
	<-signalChannel()
}

func signalChannel() <-chan os.Signal {
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	return sig
}

// distributedMain is called by main.go after flag parsing.
// asset, if non-empty, overrides the config's "asset" (see loadClusterConfigAsset).
func distributedMain(mode, configPath, node string, payments int, tracePath string, paced bool, asset string) error {
	configureDistributedLogging()
	if paced && tracePath == "" {
		return fmt.Errorf("-paced requires -trace")
	}
	cfg, err := loadClusterConfigAsset(configPath, asset)
	if err != nil {
		return err
	}
	updatePol = cfg.updatePolicy()
	switch mode {
	case "hub":
		return runHub(cfg)
	case "client":
		return runClient(cfg, node, payments, tracePath, paced)
	case "init":
		_, err := lanwire.LoadOrCreate(distributedStateDir(cfg, node), node)
		return err
	case "preflight":
		return runPreflight(cfg, node)
	default:
		return fmt.Errorf("unknown mode %q (use local, hub, client, init, or preflight)", mode)
	}
}

func distributedFlags() (*string, *string, *string) {
	mode := flag.String("mode", "local", "local benchmark, or distributed hub/client/init/preflight")
	config := flag.String("config", "config/cluster.local.json", "distributed cluster JSON config")
	node := flag.String("node", "", "distributed client name (for -mode client/init)")
	return mode, config, node
}

// clusterClientIndex maps a client name to its index in the cluster config.
// Trace events address players by index, so this is what ties a trace's player
// ids to physical nodes. Returns -1 if the name is not a configured client.
func clusterClientIndex(cfg clusterConfig, name string) int {
	for i, c := range cfg.Clients {
		if c.Name == name {
			return i
		}
	}
	return -1
}
