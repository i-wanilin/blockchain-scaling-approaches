// uc2_test.go — UC2 config validation, the hub's payment policy, uc2-deploy.
//
// The amounts are the frozen game's bound (`python3 -m arena.bound --json` in
// tank-arena): S = 336, ammo 336, virtual_stake 1680 per side, hub_deposit
// 8400 per ledger channel, every player deposits its whole 10 000 points.
//
// The addresses and keys are indices 110..116 of the public Anvil mnemonic
// ("test test … junk"), from `cast wallet private-key|address --mnemonic …
// --mnemonic-index N`: 110 the hub, 111..116 players 0..5 (measure/keys.py).
// Public throwaway keys, local chains only. The test hardcodes them rather
// than deriving them because the module has no HD-wallet dependency.
//
// TestUC2DeployWritesContractsFile needs a local Anvil:
// `anvil --chain-id 31337 --block-time 1`, then
// ANVIL_URL=ws://127.0.0.1:8545 go test -count=1 -run 'TestUC2' -v
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/accounts/abi/bind"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"

	ethchannel "github.com/perun-network/perun-eth-backend/channel"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"

	"perun.network/perun-examples/hub-demo/lanwire"
	"perun.network/perun-examples/hub-demo/pointstoken"
)

const uc2TestHubKey = "9e4e3ca5a15203ef569824c74164789921d372c12b83f1aeba7d4e096a8338fd" // 110

var uc2TestPlayers = []string{ // 111..116 = players 0..5
	"0x6813ae1FC15E995230C05D4480d50219Bb635F15",
	"0x11c9CFEc77102a7C903a2D2319c79E7b0BBC9235",
	"0xBE9086f1A38740F297F6347B531732541289b220",
	"0xD4Db664B707353422b1Ffc94038cDD0a7D074D51",
	"0x11Ba29fE987adDFa480FfEcf3d98B26630917a78",
	"0xFFd57510605b4F47A58576ccC059aB8882C7eA00",
}

const uc2TestHubAddr = "0x51953940F874eFa94F92EB2d6Aed023617A07222" // 110

// uc2TestConfig is the UC2 cluster as Task 13 writes it, before defaults.
func uc2TestConfig() clusterConfig {
	cfg := clusterConfig{
		ChainURL: "ws://127.0.0.1:8545", ChainID: 31337, Asset: "points",
		Hub:           clusterNode{Name: "arena", Bind: "0.0.0.0:9000", Endpoint: "127.0.0.1:9000", Control: "0.0.0.0:8091", Ethereum: uc2TestHubAddr},
		LedgerDeposit: 10000, VirtualStake: 1680, HubDeposit: 8400, AmmoReserve: 336,
	}
	for i, a := range uc2TestPlayers {
		cfg.Clients = append(cfg.Clients, clusterNode{
			Name: "p" + string(rune('0'+i)), Bind: "0.0.0.0:9000", Endpoint: "127.0.0.1:9000",
			Control: "http://127.0.0.1:8091", Ethereum: a,
		})
	}
	return cfg
}

func writeUC2Config(t *testing.T, cfg clusterConfig) string {
	t.Helper()
	b, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "cluster.json")
	if err := os.WriteFile(path, b, 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// writeUC2Contracts writes a contracts.json as uc2-deploy would, minting 10 000
// to every player and 6 x 8 400 to the hub, then applying the overrides.
func writeUC2Contracts(t *testing.T, override map[string]int64) string {
	t.Helper()
	c := uc2Contracts{
		ChainID: 31337, Asset: "points",
		Adjudicator: "0x5FbDB2315678afecb367f032d93F642f64180aa3",
		AssetHolder: "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512",
		Token:       "0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
		Minted:      map[string]int64{uc2TestHubAddr: 50400},
	}
	for _, a := range uc2TestPlayers {
		c.Minted[a] = 10000
	}
	for a, v := range override {
		c.Minted[a] = v
	}
	path := filepath.Join(t.TempDir(), "contracts.json")
	if err := writeJSONAtomic(path, c); err != nil {
		t.Fatal(err)
	}
	return path
}

func wantErrNaming(t *testing.T, err error, name string) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected an error naming %s, got none", name)
	}
	if !strings.Contains(err.Error(), name) {
		t.Fatalf("error does not name %s: %v", name, err)
	}
}

func TestUC2FundingValidation(t *testing.T) {
	if err := uc2TestConfig().validateUC2Funding(); err != nil {
		t.Fatalf("the frozen bound was refused: %v", err)
	}
	// The same config through the loader, which must call the check.
	if _, err := loadClusterConfig(writeUC2Config(t, uc2TestConfig())); err != nil {
		t.Fatalf("loader refused the frozen bound: %v", err)
	}

	// A player needs 5 x 1680 for its virtual channels plus 336 of ammo:
	// 8736 is exactly enough, one point less is not. (The brief's 9000 is
	// above 8736 and therefore valid under the brief's own rule.)
	cfg := uc2TestConfig()
	cfg.LedgerDeposit = 8736
	if err := cfg.validateUC2Funding(); err != nil {
		t.Fatalf("ledger_deposit 8736 (exactly enough) refused: %v", err)
	}
	cfg.LedgerDeposit = 8735
	wantErrNaming(t, cfg.validateUC2Funding(), "ledger_deposit")
	_, err := loadClusterConfig(writeUC2Config(t, cfg))
	wantErrNaming(t, err, "ledger_deposit")

	// The hub backs the far side of a player's five virtual channels.
	cfg = uc2TestConfig()
	cfg.HubDeposit = 8000
	wantErrNaming(t, cfg.validateUC2Funding(), "hub_deposit")
	_, err = loadClusterConfig(writeUC2Config(t, cfg))
	wantErrNaming(t, err, "hub_deposit")
}

func TestUC2MintCheckFromContractsFile(t *testing.T) {
	cfg := uc2TestConfig()
	cfg.ContractsFile = writeUC2Contracts(t, nil)
	if _, err := loadClusterConfig(writeUC2Config(t, cfg)); err != nil {
		t.Fatalf("a full mint was refused: %v", err)
	}

	short := uc2TestPlayers[3]
	cfg.ContractsFile = writeUC2Contracts(t, map[string]int64{short: 9999})
	_, err := loadClusterConfig(writeUC2Config(t, cfg))
	wantErrNaming(t, err, short)

	// The hub must be minted its deposit into every ledger channel: 6 x 8400.
	cfg.ContractsFile = writeUC2Contracts(t, map[string]int64{uc2TestHubAddr: 50399})
	_, err = loadClusterConfig(writeUC2Config(t, cfg))
	wantErrNaming(t, err, uc2TestHubAddr)

	// A node without an address cannot be checked against the file.
	cfg.ContractsFile = writeUC2Contracts(t, nil)
	cfg.Clients[2].Ethereum = ""
	_, err = loadClusterConfig(writeUC2Config(t, cfg))
	wantErrNaming(t, err, cfg.Clients[2].Name)
}

// The built-in mint (pointsMint per Anvil key) is what -mode hub deploys. With
// a contracts file the file's minted amounts are the truth instead.
func TestUC2PointsMintCheckOnlyWithoutContractsFile(t *testing.T) {
	cfg := uc2TestConfig()
	cfg.LedgerDeposit, cfg.HubDeposit = 200000, 200000 // 6 x 200000 > pointsMint
	_, err := loadClusterConfig(writeUC2Config(t, cfg))
	wantErrNaming(t, err, "minted")

	over := map[string]int64{uc2TestHubAddr: 1_200_000}
	for _, a := range uc2TestPlayers {
		over[a] = 200000
	}
	cfg.ContractsFile = writeUC2Contracts(t, over)
	if _, err := loadClusterConfig(writeUC2Config(t, cfg)); err != nil {
		t.Fatalf("pointsMint applied despite a contracts file: %v", err)
	}
}

// A ledger channel as the hub sees it: the player opened it (index 0), the hub
// is index 1.
func hubLedgerState(player, hub int64, final bool) *channel.State {
	s := policyState(player, hub)
	s.IsFinal = final
	return s
}

func TestUC2HubRejectsPaymentToPlayer(t *testing.T) {
	cur := hubLedgerState(10000, 8400, false)
	h := &Hub{}
	h.RequireSafePaymentUpdates(true)

	raise := client.ChannelUpdate{State: hubLedgerState(10001, 8399, false), ActorIdx: 0}
	if err := h.checkUpdate(cur, raise); err == nil {
		t.Fatal("an update raising the player's balance was accepted")
	}
	// The same payment claimed by the hub's index is not the player's to make.
	if err := h.checkUpdate(cur, client.ChannelUpdate{State: hubLedgerState(10001, 8399, false), ActorIdx: 1}); err == nil {
		t.Fatal("an update in the hub's name was accepted")
	}
	lower := client.ChannelUpdate{State: hubLedgerState(9999, 8401, false), ActorIdx: 0}
	if err := h.checkUpdate(cur, lower); err != nil {
		t.Fatalf("a shot (player pays the hub) was rejected: %v", err)
	}
	final := client.ChannelUpdate{State: hubLedgerState(10000, 8400, true), ActorIdx: 0}
	if err := h.checkUpdate(cur, final); err != nil {
		t.Fatalf("the finalize with unchanged balances was rejected: %v", err)
	}

	// With the policy off the hub behaves as before: it accepts anything.
	h.RequireSafePaymentUpdates(false)
	if err := h.checkUpdate(cur, raise); err != nil {
		t.Fatalf("policy off but the update was rejected: %v", err)
	}
}

func TestUC2ParseMintList(t *testing.T) {
	got, err := parseAddressList(strings.Join(uc2TestPlayers, ", "))
	if err != nil || len(got) != 6 || got[5] != common.HexToAddress(uc2TestPlayers[5]) {
		t.Fatalf("parse: %v %v", got, err)
	}
	// A duplicate would be minted twice by mintBatch.
	if _, err := parseAddressList(uc2TestPlayers[0] + "," + strings.ToLower(uc2TestPlayers[0])); err == nil {
		t.Fatal("duplicate address accepted")
	}
	if _, err := parseAddressList("0x1234"); err == nil {
		t.Fatal("bad address accepted")
	}
	if _, err := parseAddressList(""); err == nil {
		t.Fatal("empty list accepted")
	}
}

func TestUC2DeployWritesContractsFile(t *testing.T) {
	url := os.Getenv("ANVIL_URL")
	if url == "" {
		t.Skip("ANVIL_URL not set")
	}
	players, err := parseAddressList(strings.Join(uc2TestPlayers, ","))
	if err != nil {
		t.Fatal(err)
	}
	hk, _ := crypto.HexToECDSA(uc2TestHubKey)
	hub := crypto.PubkeyToAddress(hk.PublicKey)
	if hub != common.HexToAddress(uc2TestHubAddr) {
		t.Fatalf("hub key/address mismatch: %s", hub.Hex())
	}
	out := filepath.Join(t.TempDir(), "contracts.json")
	if err := runUC2Deploy(url, 31337, players, 10000, hub, 50400, out); err != nil {
		t.Fatal(err)
	}
	c, err := loadUC2Contracts(out)
	if err != nil {
		t.Fatal(err)
	}
	if c.Asset != "points" || c.ChainID != 31337 {
		t.Fatalf("asset/chain: %+v", c)
	}
	// adjudicator, token, mintBatch, asset holder, mint(hub)
	if c.DeployTxs != 5 || c.DeployGas == 0 {
		t.Fatalf("deploy_txs %d (want 5), deploy_gas %d", c.DeployTxs, c.DeployGas)
	}
	t.Logf("uc2-deploy: deploy_gas=%d deploy_txs=%d", c.DeployGas, c.DeployTxs)
	if len(c.Minted) != 7 {
		t.Fatalf("minted has %d entries, want 7: %v", len(c.Minted), c.Minted)
	}
	ec, err := ethclient.Dial(url)
	if err != nil {
		t.Fatal(err)
	}
	defer ec.Close()
	tok, err := pointstoken.NewPointsTokenCaller(common.HexToAddress(c.Token), ec)
	if err != nil {
		t.Fatal(err)
	}
	want := map[common.Address]int64{hub: 50400}
	for _, p := range players {
		want[p] = 10000
	}
	for a, v := range want {
		if c.Minted[a.Hex()] != v {
			t.Errorf("minted[%s] = %d, want %d", a.Hex(), c.Minted[a.Hex()], v)
		}
		bal, err := tok.BalanceOf(&bind.CallOpts{}, a)
		if err != nil {
			t.Fatal(err)
		}
		if bal.Cmp(big.NewInt(v)) != 0 {
			t.Errorf("balanceOf(%s) = %s, want %d", a.Hex(), bal, v)
		}
	}
	// The file the deploy wrote is one a UC2 config accepts.
	cfg := uc2TestConfig()
	cfg.ContractsFile = out
	if _, err := loadClusterConfig(writeUC2Config(t, cfg)); err != nil {
		t.Fatalf("config refused the deployed contracts file: %v", err)
	}
}

// TestUC2HubMeshUnderPolicy is the six-player mesh of runDistributedMesh with
// the UC2 hub: contracts and mint from uc2-deploy, the hub's key from a file,
// asymmetric funding (player 10 000, hub 8 400 per ledger channel) and the
// hub's payment policy on. It pins R7: the policy must leave go-perun's
// virtual-channel funding and settlement through the hub alone, accept every
// shot paid to the hub and every finalize. Each player fires 4 x 1 point at
// the hub and trades 4 x 5-point hits with every opponent in both directions,
// so after settlement each player holds 9 996 points and the hub 50 424.
func TestUC2HubMeshUnderPolicy(t *testing.T) {
	if os.Getenv("RUN_DISTRIBUTED_TEST") != "1" {
		t.Skip("set RUN_DISTRIBUTED_TEST=1 with Anvil running")
	}
	url := anvilURL()
	skipIfNoAnvil(t, url)
	bin := filepath.Join(t.TempDir(), "perun-node")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("build: %v\n%s", err, out)
	}
	const players, rounds = 6, 4
	state := t.TempDir()

	// The clients run as `-mode client`, which takes the dev key of its name
	// (anvilKeys[2..7]); the hub's key (anvilKeys[1]) comes from a file.
	addr := func(key string) common.Address {
		k, _ := crypto.HexToECDSA(key)
		return crypto.PubkeyToAddress(k.PublicKey)
	}
	hubAddr := addr(anvilKeys[1])
	var playerAddrs []common.Address
	for i := 0; i < players; i++ {
		playerAddrs = append(playerAddrs, addr(anvilKeys[2+i]))
	}
	contractsPath := filepath.Join(state, "contracts.json")
	if err := runUC2Deploy(url, 31337, playerAddrs, 10000, hubAddr, 50400, contractsPath); err != nil {
		t.Fatal(err)
	}
	keyPath := filepath.Join(state, "hub.key")
	if err := os.WriteFile(keyPath, []byte(anvilKeys[1]+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

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
		StartupTimeoutSec: 60, OperationTimeoutSec: 120, Asset: "points",
		YieldTimeoutMS: 40, UpdateTimeoutSec: 20, MaxCollisionRetries: 10000,
		LedgerDeposit: 10000, VirtualStake: 1680, HubDeposit: 8400, AmmoReserve: 336,
		ContractsFile: contractsPath, HubKeyFile: keyPath,
		Hub: clusterNode{Name: "arena", Bind: fmt.Sprintf("127.0.0.1:%d", hubPort),
			Endpoint: fmt.Sprintf("127.0.0.1:%d", hubPort), Control: fmt.Sprintf("127.0.0.1:%d", controlPort),
			Ethereum: hubAddr.Hex()},
	}
	for i := 0; i < players; i++ {
		ep := fmt.Sprintf("127.0.0.1:%d", freePort(t))
		cfg.Clients = append(cfg.Clients, clusterNode{Name: strings.ToLower(clientNames[i]), Bind: ep, Endpoint: ep,
			Control: ctl, Ethereum: playerAddrs[i].Hex()})
	}
	configPath := writeUC2Config(t, cfg)

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
	hub := start("hub", "-mode", "uc2-hub", "-config", configPath)
	hubDone := make(chan error, 1)
	go func() { hubDone <- hub.Wait() }()
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
	select {
	case err := <-hubDone:
		if err != nil {
			t.Fatalf("hub failed: %v\n%s", err, dump())
		}
	case <-ctx.Done():
		t.Fatalf("hub did not finish\n%s", dump())
	}
	if strings.Contains(dump(), "payment policy") {
		t.Fatalf("an update was refused by a payment policy\n%s", dump())
	}

	for _, node := range cfg.Clients {
		raw, err := os.ReadFile(resultPath(t, cfg.ResultDir, node.Name))
		if err != nil {
			t.Fatal(err)
		}
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
			if f.Balances[0] != "1680" || f.Balances[1] != "1680" {
				t.Fatalf("%s: virtual channel %s ended at %v, want 1680 each", node.Name, f.Channel[:8], f.Balances)
			}
		}
	}

	// The settled points on chain: asymmetric funding withdrew correctly.
	c, err := loadUC2Contracts(contractsPath)
	if err != nil {
		t.Fatal(err)
	}
	ec, err := ethclient.Dial(url)
	if err != nil {
		t.Fatal(err)
	}
	defer ec.Close()
	tok, err := pointstoken.NewPointsTokenCaller(common.HexToAddress(c.Token), ec)
	if err != nil {
		t.Fatal(err)
	}
	want := map[common.Address]int64{hubAddr: 50400 + players*rounds}
	for _, a := range playerAddrs {
		want[a] = 10000 - rounds
	}
	for a, v := range want {
		bal, err := tok.BalanceOf(&bind.CallOpts{}, a)
		if err != nil {
			t.Fatal(err)
		}
		if bal.Cmp(big.NewInt(v)) != 0 {
			t.Errorf("balanceOf(%s) = %s after settlement, want %d", a.Hex(), bal, v)
		}
	}
}

// A contracts file without hub_deposit would skip both mint checks:
// validatePoints leaves the file to validateUC2Funding, which runs only for
// hub_deposit > 0. The loader refuses such a config.
func TestUC2ContractsFileNeedsHubDeposit(t *testing.T) {
	cfg := uc2TestConfig()
	cfg.ContractsFile = writeUC2Contracts(t, nil)
	cfg.HubDeposit, cfg.AmmoReserve = 0, 0
	_, err := loadClusterConfig(writeUC2Config(t, cfg))
	wantErrNaming(t, err, "hub_deposit")
	wantErrNaming(t, err, "contracts_file")
}

// The uc2-hub funds only the deposits the config names: the player
// ledger_deposit (index 0), the hub hub_deposit (index 1). A proposal asking
// the hub to fund more would starve its other ledger channels.
func TestUC2HubChecksProposedDeposits(t *testing.T) {
	lanwire.RegisterDecoder()
	asset := ethchannel.NewAsset(big.NewInt(31337), common.HexToAddress("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"))
	player := common.HexToAddress(uc2TestPlayers[0])
	pw, hw := uc1TestWire(t, "p0"), uc1TestWire(t, "arena")
	prop := func(bals ...int64) *client.LedgerChannelProposalMsg {
		return uc1TestProposal(t, asset, player, pw, hw, bals, 10)
	}
	h := &Hub{}
	// Off (-mode hub): any deposits, as before.
	if err := h.checkLedgerProposal(prop(10000, 50000)); err != nil {
		t.Fatalf("no deposit rule set, but the proposal was refused: %v", err)
	}
	h.RequireLedgerDeposits(big.NewInt(10000), big.NewInt(8400))
	if err := h.checkLedgerProposal(prop(10000, 8400)); err != nil {
		t.Fatalf("the configured deposits were refused: %v", err)
	}
	for name, p := range map[string]*client.LedgerChannelProposalMsg{
		"hub over-funds":  prop(10000, 8401),
		"hub under-funds": prop(10000, 8399),
		"player short":    prop(9999, 8400),
		"player more":     prop(10001, 8400),
		"sides swapped":   prop(8400, 10000),
	} {
		err := h.checkLedgerProposal(p)
		if err == nil {
			t.Errorf("%s: accepted", name)
			continue
		}
		if !strings.Contains(err.Error(), "8400") {
			t.Errorf("%s: the reason does not name the expected deposits: %v", name, err)
		}
	}
}

// ---------------------------------------------------------------------------
// -mode uc2-player on a fake backend (no chain).
// ---------------------------------------------------------------------------

// fakeUC2Channel is one channel of the fake player: its own balance, the
// peer's, a version, and every amount that reached it in order.
type fakeUC2Channel struct {
	mine, peer *big.Int
	version    uint64
	amounts    []int64
	updateFn   func(amount *big.Int) error // optional, runs before the update lands
}

// fakeUC2Backend is player 2 of six: a ledger channel to the hub (10 000 /
// 8 400) and a virtual channel with each other player (1 680 each side).
type fakeUC2Backend struct {
	mu        sync.Mutex
	chans     map[string]*fakeUC2Channel
	active    int
	maxActive int
	closed    bool
}

func newFakeUC2Backend() *fakeUC2Backend {
	f := &fakeUC2Backend{chans: map[string]*fakeUC2Channel{
		"hub": {mine: big.NewInt(10000), peer: big.NewInt(8400)},
	}}
	for _, p := range []string{"p0", "p1", "p3", "p4", "p5"} {
		f.chans[p] = &fakeUC2Channel{mine: big.NewInt(1680), peer: big.NewInt(1680)}
	}
	return f
}

func (f *fakeUC2Backend) Init(req uc2Request) (string, int, error) {
	if req.Key == "" || req.Config == "" {
		return "", 0, fmt.Errorf("init needs key and config")
	}
	return "0x00000000000000000000000000000000000000c2", 2, nil
}

func (f *fakeUC2Backend) Open() (uc2OpenReply, error) {
	return uc2OpenReply{TStart: 1, TLedgerFunded: 2, TVirtualOpen: 3, LedgerChannelID: "0x01",
		Virtual:   map[string]string{"p0": "0x10", "p1": "0x11", "p3": "0x13", "p4": "0x14", "p5": "0x15"},
		L1GasUsed: 90000, L1FeeWei: "1", Txs: 2}, nil
}

func (f *fakeUC2Backend) Targets() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []string
	for t := range f.chans {
		out = append(out, t)
	}
	return out
}

func (f *fakeUC2Backend) channel(target string) *fakeUC2Channel {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.chans[target]
}

func (f *fakeUC2Backend) Update(target string, amount *big.Int) (uint64, error) {
	ch := f.channel(target)
	if ch == nil {
		return 0, fmt.Errorf("fake: no channel %s", target)
	}
	f.mu.Lock()
	f.active++
	if f.active > f.maxActive {
		f.maxActive = f.active
	}
	f.mu.Unlock()
	defer func() { f.mu.Lock(); f.active--; f.mu.Unlock() }()
	if ch.updateFn != nil {
		if err := ch.updateFn(amount); err != nil {
			return 0, err
		}
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	ch.mine.Sub(ch.mine, amount)
	ch.peer.Add(ch.peer, amount)
	ch.version++
	ch.amounts = append(ch.amounts, amount.Int64())
	return ch.version, nil
}

func (f *fakeUC2Backend) Balance(target string) *big.Int {
	ch := f.channel(target)
	if ch == nil {
		return nil
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	return new(big.Int).Set(ch.mine)
}

func (f *fakeUC2Backend) State(target string) (uint64, []*big.Int, error) {
	ch := f.channel(target)
	if ch == nil {
		return 0, nil, fmt.Errorf("fake: no channel %s", target)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	return ch.version, []*big.Int{new(big.Int).Set(ch.mine), new(big.Int).Set(ch.peer)}, nil
}

func (f *fakeUC2Backend) Close() (uc2CloseReply, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.closed = true
	h := f.chans["hub"]
	return uc2CloseReply{TStart: 1, TVirtualSettled: 2, TWithdrawn: 3,
		FinalLedgerBalancesWei: []string{h.mine.String(), h.peer.String()}, Txs: 2}, nil
}

func (f *fakeUC2Backend) Shutdown() {}

func (f *fakeUC2Backend) amounts(target string) []int64 {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]int64(nil), f.chans[target].amounts...)
}

// uc2Session drives a sidecar asynchronously: send writes request lines, recv
// reads the next reply (replies come in request order, whenever they are ready).
type uc2Session struct {
	t     *testing.T
	in    *io.PipeWriter
	lines chan map[string]any
	done  chan error
}

func startUC2Sidecar(t *testing.T, b uc2Backend) *uc2Session {
	t.Helper()
	inR, inW := io.Pipe()
	outR, outW := io.Pipe()
	s := &uc2Session{t: t, in: inW, lines: make(chan map[string]any, 256), done: make(chan error, 1)}
	go func() { s.done <- runUC2Sidecar(inR, outW, b); _ = outW.Close() }()
	go func() {
		defer close(s.lines)
		sc := bufio.NewScanner(outR)
		for sc.Scan() {
			var m map[string]any
			if err := json.Unmarshal(sc.Bytes(), &m); err != nil {
				m = map[string]any{"not_json": sc.Text()}
			}
			s.lines <- m
		}
	}()
	return s
}

func (s *uc2Session) send(lines ...string) {
	s.t.Helper()
	for _, l := range lines {
		if _, err := io.WriteString(s.in, l+"\n"); err != nil {
			s.t.Fatalf("write %q: %v", l, err)
		}
	}
}

func (s *uc2Session) recv() map[string]any {
	s.t.Helper()
	select {
	case m, ok := <-s.lines:
		if !ok {
			s.t.Fatal("sidecar closed stdout")
		}
		if _, bad := m["not_json"]; bad {
			s.t.Fatalf("stdout line is not JSON: %v", m)
		}
		return m
	case <-time.After(10 * time.Second):
		s.t.Fatal("no reply within 10 s")
	}
	return nil
}

// finish sends exit, checks its reply and that nothing else was written.
func (s *uc2Session) finish() {
	s.t.Helper()
	s.send(`{"op":"exit"}`)
	if m := s.recv(); m["op"] != "exit" || m["ok"] != true {
		s.t.Fatalf("exit: %v", m)
	}
	_ = s.in.Close()
	select {
	case err := <-s.done:
		if err != nil {
			s.t.Fatalf("sidecar: %v", err)
		}
	case <-time.After(10 * time.Second):
		s.t.Fatal("sidecar did not exit")
	}
	for m := range s.lines {
		s.t.Fatalf("unexpected extra stdout line: %v", m)
	}
}

const uc2InitLine = `{"op":"init","key":"ab","name":"p2","config":"/tmp/m-RUN/cluster.json","state_dir":"/tmp/x","update_timeout_s":10,"persist":true,"startup_timeout_s":120}`

// openUC2 sends init and open and checks both replies.
func (s *uc2Session) open(initLine string) {
	s.t.Helper()
	s.send(initLine, `{"op":"open"}`)
	if m := s.recv(); m["op"] != "init" || m["ok"] != true || m["index"] != float64(2) || m["address"] == "" {
		s.t.Fatalf("init: %v", m)
	}
	if m := s.recv(); m["op"] != "open" || m["ok"] != true || m["txs"] != float64(2) || m["ledger_channel_id"] != "0x01" {
		s.t.Fatalf("open: %v", m)
	}
}

func uc2Pay(id, to string, amount int) string {
	return fmt.Sprintf(`{"op":"pay","id":%q,"to":%q,"amount_wei":"%d"}`, id, to, amount)
}

func wantPay(t *testing.T, m map[string]any, id string, ok bool, errPrefix string) {
	t.Helper()
	if m["op"] != "pay" || m["id"] != id {
		t.Fatalf("want the reply to pay %s (replies in request order), got %v", id, m)
	}
	if m["ok"] != ok {
		t.Fatalf("pay %s: ok %v, want %v: %v", id, m["ok"], ok, m)
	}
	if e, _ := m["error"].(string); !strings.HasPrefix(e, errPrefix) || (errPrefix == "" && e != "") {
		t.Fatalf("pay %s: error %q, want prefix %q", id, e, errPrefix)
	}
}

func TestUC2PlayerRoutesByTarget(t *testing.T) {
	b := newFakeUC2Backend()
	s := startUC2Sidecar(t, b)
	s.open(uc2InitLine)
	s.send(uc2Pay("a1", "hub", 1), uc2Pay("h1", "p0", 5), uc2Pay("h2", "p3", 5),
		uc2Pay("a2", "hub", 1), uc2Pay("h3", "p0", 7))
	for _, w := range []struct {
		id      string
		version float64
	}{{"a1", 1}, {"h1", 1}, {"h2", 1}, {"a2", 2}, {"h3", 2}} {
		m := s.recv()
		wantPay(t, m, w.id, true, "")
		if m["version"] != w.version {
			t.Fatalf("pay %s: version %v, want %v (each channel counts its own versions)", w.id, m["version"], w.version)
		}
		if m["t_submitted"].(float64) <= 0 || m["t_confirmed"].(float64) < m["t_submitted"].(float64) {
			t.Fatalf("pay %s: timestamps %v", w.id, m)
		}
	}
	s.finish()
	for target, want := range map[string][]int64{"hub": {1, 1}, "p0": {5, 7}, "p3": {5}, "p1": nil, "p4": nil, "p5": nil} {
		if got := b.amounts(target); fmt.Sprint(got) != fmt.Sprint(want) {
			t.Errorf("%s received %v, want %v", target, got, want)
		}
	}
}

func TestUC2PlayerChannelsRunInParallel(t *testing.T) {
	b := newFakeUC2Backend()
	release := make(chan struct{})
	b.chans["p3"].updateFn = func(*big.Int) error { <-release; return nil }
	s := startUC2Sidecar(t, b)
	s.open(uc2InitLine)
	s.send(uc2Pay("h1", "p3", 5), uc2Pay("a1", "hub", 1), uc2Pay("a2", "hub", 1), uc2Pay("a3", "hub", 1))
	// The hub pays land while the p3 update is still in flight.
	deadline := time.Now().Add(5 * time.Second)
	for len(b.amounts("hub")) < 3 {
		if time.Now().After(deadline) {
			close(release)
			t.Fatalf("a slow p3 channel delayed the hub pays: hub got %v", b.amounts("hub"))
		}
		time.Sleep(time.Millisecond)
	}
	if len(b.amounts("p3")) != 0 {
		t.Fatal("the p3 update finished before it was released")
	}
	close(release)
	wantPay(t, s.recv(), "h1", true, "")
	for _, id := range []string{"a1", "a2", "a3"} {
		wantPay(t, s.recv(), id, true, "")
	}
	s.finish()
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.maxActive != 2 {
		t.Fatalf("max concurrent updates %d, want 2 (p3 in flight beside one hub pay at a time)", b.maxActive)
	}
}

func TestUC2PlayerUnknownTargetRejected(t *testing.T) {
	b := newFakeUC2Backend()
	s := startUC2Sidecar(t, b)
	s.send(uc2InitLine, uc2Pay("early", "hub", 1))
	s.recv()
	wantPay(t, s.recv(), "early", false, "rejected: no open channel")
	s.send(`{"op":"open"}`)
	s.recv()
	for _, to := range []string{"p9", "p2", "arena", ""} { // p2 is this player
		s.send(uc2Pay("x-"+to, to, 1))
		m := s.recv()
		wantPay(t, m, "x-"+to, false, "rejected:")
		if m["error"] != "rejected: no channel to "+to {
			t.Fatalf("pay to %q: error %q", to, m["error"])
		}
	}
	s.send(`{"op":"pay","id":"num","to":"hub","amount_wei":5}`)
	wantPay(t, s.recv(), "num", false, "rejected: bad request")
	s.send(uc2Pay("ok", "hub", 1))
	wantPay(t, s.recv(), "ok", true, "")
	s.finish()
	if got := b.amounts("hub"); len(got) != 1 {
		t.Fatalf("only the valid pay may reach a channel: hub got %v", got)
	}
}

func TestUC2PlayerTimeoutBreaksOnlyThatChannel(t *testing.T) {
	b := newFakeUC2Backend()
	b.chans["p0"].updateFn = func(*big.Int) error {
		return fmt.Errorf("updating: %w", client.RequestTimedOutError("peer did not respond"))
	}
	s := startUC2Sidecar(t, b)
	s.open(uc2InitLine)
	s.send(uc2Pay("h1", "p0", 5), uc2Pay("h2", "p0", 5), uc2Pay("a1", "hub", 1), uc2Pay("h3", "p1", 5))
	wantPay(t, s.recv(), "h1", false, "unresolved: update timeout")
	wantPay(t, s.recv(), "h2", false, "channel broken:")
	wantPay(t, s.recv(), "a1", true, "")
	wantPay(t, s.recv(), "h3", true, "")
	s.send(`{"op":"state"}`)
	st := s.recv()
	chans := st["channels"].(map[string]any)
	for target, broken := range map[string]bool{"p0": true, "hub": false, "p1": false, "p3": false} {
		if chans[target].(map[string]any)["broken"] != broken {
			t.Fatalf("%s broken = %v, want %v: %v", target, chans[target], broken, st)
		}
	}
	// The run is incomplete: close refuses and names the broken channel.
	s.send(`{"op":"close"}`)
	cl := s.recv()
	if cl["op"] != "close" || cl["ok"] == true || !strings.Contains(cl["error"].(string), "p0") ||
		!strings.Contains(cl["error"].(string), "channel broken") {
		t.Fatalf("close with a broken channel: %v", cl)
	}
	s.finish()
	if b.closed {
		t.Fatal("the backend closed despite a broken channel")
	}
}

func TestUC2PlayerQuiesceDropsAllQueues(t *testing.T) {
	b := newFakeUC2Backend()
	release := make(chan struct{})
	block := func(*big.Int) error { <-release; return nil }
	b.chans["hub"].updateFn, b.chans["p1"].updateFn = block, block
	s := startUC2Sidecar(t, b)
	s.open(strings.Replace(uc2InitLine, `"update_timeout_s":10`, `"update_timeout_s":2`, 1))
	s.send(uc2Pay("a1", "hub", 1), uc2Pay("a2", "hub", 1), uc2Pay("a3", "hub", 1),
		uc2Pay("h1", "p1", 5), uc2Pay("h2", "p1", 5), uc2Pay("h3", "p1", 5), uc2Pay("h4", "p4", 5))
	deadline := time.Now().Add(5 * time.Second)
	for {
		b.mu.Lock()
		ready := b.active == 2 && len(b.chans["p4"].amounts) == 1
		b.mu.Unlock()
		if ready {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the two blocking pays never went in flight")
		}
		time.Sleep(time.Millisecond)
	}
	// The quiesce waits for the pays in flight; let them land meanwhile. (The
	// goroutine starts first: the sidecar reads no further line while it
	// serves the quiesce, so a second write would block until it is done.)
	go func() { time.Sleep(300 * time.Millisecond); close(release) }()
	s.send(`{"op":"quiesce"}`, uc2Pay("late", "p3", 5))
	wantPay(t, s.recv(), "a1", true, "")
	wantPay(t, s.recv(), "a2", false, "unresolved: dropped at quiesce")
	wantPay(t, s.recv(), "a3", false, "unresolved: dropped at quiesce")
	wantPay(t, s.recv(), "h1", true, "")
	wantPay(t, s.recv(), "h2", false, "unresolved: dropped at quiesce")
	wantPay(t, s.recv(), "h3", false, "unresolved: dropped at quiesce")
	wantPay(t, s.recv(), "h4", true, "")
	q := s.recv()
	if q["op"] != "quiesce" || q["ok"] != true || q["dropped"] != float64(4) {
		t.Fatalf("quiesce: %v", q)
	}
	versions := q["versions"].(map[string]any)
	want := map[string]float64{"hub": 1, "p0": 0, "p1": 1, "p3": 0, "p4": 1, "p5": 0}
	if len(versions) != len(want) {
		t.Fatalf("quiesce versions %v, want every channel", versions)
	}
	for target, v := range want {
		if versions[target] != v {
			t.Fatalf("quiesce versions %v, want %v", versions, want)
		}
	}
	wantPay(t, s.recv(), "late", false, "unresolved: after quiesce")
	s.finish()
}

func TestUC2PlayerStateListsEveryChannel(t *testing.T) {
	b := newFakeUC2Backend()
	s := startUC2Sidecar(t, b)
	s.send(uc2InitLine, `{"op":"state"}`)
	s.recv()
	if m := s.recv(); m["op"] != "state" || m["ok"] == true {
		t.Fatalf("state before open must fail: %v", m)
	}
	// state reads the channels when it is served, not after the pays queued
	// before it, so wait for their replies first.
	s.send(`{"op":"open"}`, uc2Pay("h1", "p0", 5), uc2Pay("a1", "hub", 1))
	s.recv()
	wantPay(t, s.recv(), "h1", true, "")
	wantPay(t, s.recv(), "a1", true, "")
	s.send(`{"op":"state"}`)
	st := s.recv()
	if st["op"] != "state" || st["ok"] != true {
		t.Fatalf("state: %v", st)
	}
	chans := st["channels"].(map[string]any)
	want := map[string][2]string{"hub": {"9999", "8401"}, "p0": {"1675", "1685"},
		"p1": {"1680", "1680"}, "p3": {"1680", "1680"}, "p4": {"1680", "1680"}, "p5": {"1680", "1680"}}
	if len(chans) != len(want) {
		t.Fatalf("state lists %d channels, want %d: %v", len(chans), len(want), chans)
	}
	for target, bals := range want {
		c, ok := chans[target].(map[string]any)
		if !ok {
			t.Fatalf("state misses %s: %v", target, chans)
		}
		got := c["balances_wei"].([]any)
		if got[0] != bals[0] || got[1] != bals[1] || c["broken"] != false {
			t.Fatalf("%s: %v, want balances [mine, peer] = %v", target, c, bals)
		}
	}
	if v := chans["p0"].(map[string]any)["version"]; v != float64(1) {
		t.Fatalf("p0 version %v", v)
	}
	s.send(`{"op":"close"}`)
	cl := s.recv()
	if cl["op"] != "close" || cl["ok"] != true || cl["final_ledger_balances_wei"].([]any)[0] != "9999" {
		t.Fatalf("close: %v", cl)
	}
	s.send(uc2Pay("after", "hub", 1))
	wantPay(t, s.recv(), "after", false, "rejected: channel closing")
	s.finish()
}

// uc2Proc is one uc2-player process driven over its stdin/stdout.
type uc2Proc struct {
	t      *testing.T
	name   string
	cmd    *exec.Cmd
	in     io.WriteCloser
	lines  chan map[string]any
	stderr *syncBuffer
}

func startUC2PlayerProc(t *testing.T, bin, name string) *uc2Proc {
	t.Helper()
	p := &uc2Proc{t: t, name: name, cmd: exec.Command(bin, "-mode", "uc2-player"), lines: make(chan map[string]any, 64), stderr: &syncBuffer{b: &bytes.Buffer{}}}
	var err error
	if p.in, err = p.cmd.StdinPipe(); err != nil {
		t.Fatal(err)
	}
	out, err := p.cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	p.cmd.Stderr = p.stderr
	if err := p.cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = p.cmd.Process.Kill() })
	go func() {
		defer close(p.lines)
		sc := bufio.NewScanner(out)
		for sc.Scan() {
			var m map[string]any
			if err := json.Unmarshal(sc.Bytes(), &m); err != nil {
				m = map[string]any{"not_json": sc.Text()}
			}
			p.lines <- m
		}
	}()
	return p
}

func (p *uc2Proc) send(lines ...string) {
	p.t.Helper()
	for _, l := range lines {
		if _, err := io.WriteString(p.in, l+"\n"); err != nil {
			p.t.Fatalf("%s: write: %v", p.name, err)
		}
	}
}

func (p *uc2Proc) recv(timeout time.Duration) map[string]any {
	p.t.Helper()
	select {
	case m, ok := <-p.lines:
		if !ok {
			p.t.Fatalf("%s closed stdout\n%s", p.name, p.stderr.String())
		}
		if _, bad := m["not_json"]; bad {
			p.t.Fatalf("%s: stdout carries a non-protocol line: %v", p.name, m)
		}
		return m
	case <-time.After(timeout):
		p.t.Fatalf("%s: no reply within %v\n%s", p.name, timeout, p.stderr.String())
	}
	return nil
}

func (p *uc2Proc) wantOK(m map[string]any, op string) map[string]any {
	p.t.Helper()
	if m["op"] != op || m["ok"] != true {
		p.t.Fatalf("%s: %s failed: %v\n%s", p.name, op, m, p.stderr.String())
	}
	return m
}

// TestUC2PlayerSmokeAnvil runs the real sidecar: uc2-deploy, a uc2-hub and two
// uc2-player processes driven over stdin/stdout, a few pays each way on the
// virtual channel and to the hub, then quiesce, state, close, exit, and the
// settled points on chain.
func TestUC2PlayerSmokeAnvil(t *testing.T) {
	if os.Getenv("RUN_DISTRIBUTED_TEST") != "1" {
		t.Skip("set RUN_DISTRIBUTED_TEST=1 with Anvil running")
	}
	url := anvilURL()
	skipIfNoAnvil(t, url)
	bin := filepath.Join(t.TempDir(), "perun-node")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("build: %v\n%s", err, out)
	}
	state := t.TempDir()
	addr := func(key string) common.Address {
		k, _ := crypto.HexToECDSA(key)
		return crypto.PubkeyToAddress(k.PublicKey)
	}
	hubAddr := addr(anvilKeys[1])
	keys := []string{anvilKeys[2], anvilKeys[3]}
	players := []common.Address{addr(keys[0]), addr(keys[1])}
	contractsPath := filepath.Join(state, "contracts.json")
	if err := runUC2Deploy(url, 31337, players, 10000, hubAddr, 2*8400, contractsPath); err != nil {
		t.Fatal(err)
	}
	keyPath := filepath.Join(state, "hub.key")
	if err := os.WriteFile(keyPath, []byte(anvilKeys[1]+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	hubPort, controlPort := freePort(t), freePort(t)
	cfg := clusterConfig{
		ChainURL: url, ChainID: 31337, StateRoot: state, ResultDir: filepath.Join(state, "results"),
		StartupTimeoutSec: 60, OperationTimeoutSec: 120, Asset: "points",
		YieldTimeoutMS: 40, UpdateTimeoutSec: 20, MaxCollisionRetries: 10000,
		LedgerDeposit: 10000, VirtualStake: 1680, HubDeposit: 8400, AmmoReserve: 336,
		ContractsFile: contractsPath, HubKeyFile: keyPath,
		Hub: clusterNode{Name: "arena", Bind: fmt.Sprintf("127.0.0.1:%d", hubPort),
			Endpoint: fmt.Sprintf("127.0.0.1:%d", hubPort), Control: fmt.Sprintf("127.0.0.1:%d", controlPort),
			Ethereum: hubAddr.Hex()},
	}
	for i := range players {
		ep := fmt.Sprintf("127.0.0.1:%d", freePort(t))
		cfg.Clients = append(cfg.Clients, clusterNode{Name: fmt.Sprintf("p%d", i), Bind: ep, Endpoint: ep,
			Control: fmt.Sprintf("http://127.0.0.1:%d", controlPort), Ethereum: players[i].Hex()})
	}
	configPath := writeUC2Config(t, cfg)

	var hubLog bytes.Buffer
	hub := exec.Command(bin, "-mode", "uc2-hub", "-config", configPath)
	hub.Stdout, hub.Stderr = &hubLog, &hubLog
	if err := hub.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = hub.Process.Kill() })
	hubDone := make(chan error, 1)
	go func() { hubDone <- hub.Wait() }()

	// The players start before the hub is up: init retries the registration.
	ps := make([]*uc2Proc, len(players))
	for i := range ps {
		ps[i] = startUC2PlayerProc(t, bin, cfg.Clients[i].Name)
		ps[i].send(fmt.Sprintf(`{"op":"init","key":%q,"name":"p%d","config":%q,"state_dir":%q,"update_timeout_s":20,"persist":false,"startup_timeout_s":60}`,
			keys[i], i, configPath, filepath.Join(state, fmt.Sprintf("p%d", i))))
	}
	for i, p := range ps {
		m := p.wantOK(p.recv(90*time.Second), "init")
		if m["index"] != float64(i) || common.HexToAddress(m["address"].(string)) != players[i] {
			t.Fatalf("p%d init: %v", i, m)
		}
	}
	for _, p := range ps {
		p.send(`{"op":"open"}`)
	}
	for i, p := range ps {
		m := p.wantOK(p.recv(120*time.Second), "open")
		virtual := m["virtual"].(map[string]any)
		other := fmt.Sprintf("p%d", 1-i)
		if len(virtual) != 1 || virtual[other] == nil || m["txs"] != float64(2) || m["l1_gas_used"].(float64) == 0 {
			t.Fatalf("p%d open: %v", i, m)
		}
		t.Logf("p%d open: ledger %.2fs, virtual %.3fs, gas %v in %v txs", i,
			m["t_ledger_funded"].(float64)-m["t_start"].(float64), m["t_virtual_open"].(float64)-m["t_virtual_start"].(float64),
			m["l1_gas_used"], m["txs"])
	}
	// p0: two shots and two hits on p1; p1: one shot and three hits on p0.
	ps[0].send(uc2Pay("p0a1", "hub", 1), uc2Pay("p0h1", "p1", 5), uc2Pay("p0a2", "hub", 1), uc2Pay("p0h2", "p1", 5))
	ps[1].send(uc2Pay("p1h1", "p0", 5), uc2Pay("p1a1", "hub", 1), uc2Pay("p1h2", "p0", 5), uc2Pay("p1h3", "p0", 5), uc2Pay("p1x", "p1", 5))
	for _, id := range []string{"p0a1", "p0h1", "p0a2", "p0h2"} {
		wantPay(t, ps[0].recv(30*time.Second), id, true, "")
	}
	for _, id := range []string{"p1h1", "p1a1", "p1h2", "p1h3"} {
		wantPay(t, ps[1].recv(30*time.Second), id, true, "")
	}
	wantPay(t, ps[1].recv(30*time.Second), "p1x", false, "rejected: no channel to p1")

	for _, p := range ps {
		p.send(`{"op":"quiesce"}`, `{"op":"state"}`)
	}
	// A ledger channel's balances are its free funds: each side's 1 680 for
	// the open virtual channel is locked out of them until it settles.
	want := []map[string][2]string{
		{"hub": {"8318", "6722"}, "p1": {"1685", "1675"}},
		{"hub": {"8319", "6721"}, "p0": {"1675", "1685"}},
	}
	for i, p := range ps {
		q := p.wantOK(p.recv(30*time.Second), "quiesce")
		if q["dropped"] != float64(0) {
			t.Fatalf("p%d quiesce: %v", i, q)
		}
		st := p.wantOK(p.recv(30*time.Second), "state")
		chans := st["channels"].(map[string]any)
		for target, bals := range want[i] {
			c := chans[target].(map[string]any)
			got := c["balances_wei"].([]any)
			if got[0] != bals[0] || got[1] != bals[1] || c["broken"] != false {
				t.Fatalf("p%d %s: %v, want [mine, peer] %v", i, target, c, bals)
			}
		}
	}
	for _, p := range ps {
		p.send(`{"op":"close"}`)
	}
	finalLedger := [][2]string{{"10003", "8397"}, {"9994", "8406"}} // virtual 1685/1675 settled back
	for i, p := range ps {
		cl := p.wantOK(p.recv(240*time.Second), "close")
		got := cl["final_ledger_balances_wei"].([]any)
		if got[0] != finalLedger[i][0] || got[1] != finalLedger[i][1] {
			t.Fatalf("p%d close: final ledger %v, want %v: %v", i, got, finalLedger[i], cl)
		}
		if vf := cl["virtual_final"].([]any); len(vf) != 1 {
			t.Fatalf("p%d close: virtual_final %v", i, vf)
		}
		t.Logf("p%d close: gas %v in %v txs, collisions %v, virtual settle %.3fs, ledger settle %.2fs", i,
			cl["l1_gas_used"], cl["txs"], cl["collisions"], cl["virtual_settle_s"], cl["ledger_settle_s"])
		p.send(`{"op":"exit"}`)
		p.wantOK(p.recv(30*time.Second), "exit")
		if err := p.cmd.Wait(); err != nil {
			t.Fatalf("p%d exited with %v\n%s", i, err, p.stderr.String())
		}
	}
	select {
	case err := <-hubDone:
		if err != nil {
			t.Fatalf("hub: %v\n%s", err, hubLog.String())
		}
	case <-time.After(60 * time.Second):
		t.Fatalf("hub did not finish\n%s", hubLog.String())
	}

	c, err := loadUC2Contracts(contractsPath)
	if err != nil {
		t.Fatal(err)
	}
	ec, err := ethclient.Dial(url)
	if err != nil {
		t.Fatal(err)
	}
	defer ec.Close()
	tok, err := pointstoken.NewPointsTokenCaller(common.HexToAddress(c.Token), ec)
	if err != nil {
		t.Fatal(err)
	}
	for a, v := range map[common.Address]int64{players[0]: 10003, players[1]: 9994, hubAddr: 2*8400 + 3} {
		bal, err := tok.BalanceOf(&bind.CallOpts{}, a)
		if err != nil {
			t.Fatal(err)
		}
		if bal.Cmp(big.NewInt(v)) != 0 {
			t.Errorf("balanceOf(%s) = %s after settlement, want %d", a.Hex(), bal, v)
		}
	}
}
