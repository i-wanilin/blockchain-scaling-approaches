// uc2.go — UC2 (the six-player tank game) on Perun: contracts, funding, the hub.
//
// Every player holds one ledger channel to the hub and, through the hub, one
// virtual channel per opponent. A shot is paid to the hub in the ledger
// channel (ammo); a hit is paid to the shooter in the virtual channel with
// that player. The game's bound (tank-arena `arena.bound`) fixes the amounts:
// each virtual channel holds virtual_stake per side, a player keeps
// ammo_reserve beside its five stakes, and the hub deposits hub_deposit per
// ledger channel to back the far side of that player's five virtual channels.
// Funding is therefore asymmetric: player ledger_deposit, hub hub_deposit.
//
//	uc2-deploy  deploy the adjudicator, the ERC-20 asset holder and PointsToken,
//	            mint points to the players (one mintBatch) and to the hub (one
//	            mint), write contracts.json
//	uc2-hub     -mode hub on those contracts, with the hub's key from a file and
//	            the hub's payment policy on (a player may only pay the hub)
//	uc2-player  the per-player sidecar on stdin/stdout (uc2_player.go)
//
// The flags are registered here in init() so the global flag.Parse in main()
// sees them; -url, -chainid, -out and -config are shared with the other modes.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"math/big"
	"os"
	"strings"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"

	"perun.network/perun-examples/hub-demo/stdoutguard"
)

type uc2Flags struct {
	mint, hub         string
	points, hubPoints int64
}

var uc2F uc2Flags

func init() {
	flag.StringVar(&uc2F.mint, "mint", "", "uc2-deploy: comma-separated player addresses, each minted -points")
	flag.Int64Var(&uc2F.points, "points", 0, "uc2-deploy: points minted to every -mint address")
	flag.StringVar(&uc2F.hub, "hub", "", "uc2-deploy: the hub's address, minted -hub-points")
	flag.Int64Var(&uc2F.hubPoints, "hub-points", 0, "uc2-deploy: points minted to the hub (its deposit into every ledger channel)")
}

// uc2Main dispatches the UC2 modes; main() calls it right after flag.Parse.
func uc2Main(mode string) error {
	url := flag.Lookup("url").Value.String()
	var chainID uint64
	if _, err := fmt.Sscan(flag.Lookup("chainid").Value.String(), &chainID); err != nil {
		return fmt.Errorf("-chainid: %w", err)
	}
	switch mode {
	case "uc2-deploy":
		players, err := parseAddressList(uc2F.mint)
		if err != nil {
			return fmt.Errorf("-mint: %w", err)
		}
		if !common.IsHexAddress(uc2F.hub) {
			return fmt.Errorf("-hub %q is not an Ethereum address", uc2F.hub)
		}
		return runUC2Deploy(url, chainID, players, uc2F.points, common.HexToAddress(uc2F.hub), uc2F.hubPoints, flag.Lookup("out").Value.String())
	case "uc2-hub":
		return runUC2Hub(flag.Lookup("config").Value.String())
	case "uc2-player":
		return runUC2PlayerMain(os.Stdin, stdoutguard.Protocol)
	default:
		return fmt.Errorf("unknown mode %q (use uc2-deploy, uc2-hub or uc2-player)", mode)
	}
}

// uc2Contracts is contracts.json as uc2-deploy writes it. Minted maps every
// address the deploy minted to (checksummed hex) to its points; the config
// check (validateUC2Funding) reads it so a run that could not fund its
// channels is refused before any node starts.
type uc2Contracts struct {
	ChainID     uint64           `json:"chain_id"`
	Adjudicator string           `json:"adjudicator"`
	AssetHolder string           `json:"asset_holder"`
	Token       string           `json:"token"`
	Asset       string           `json:"asset"`
	Minted      map[string]int64 `json:"minted"`
	DeployGas   uint64           `json:"deploy_gas"`
	DeployTxs   int              `json:"deploy_txs"`
}

func (c uc2Contracts) chain() chainContracts {
	return chainContracts{
		Asset:       assetPoints,
		Adjudicator: common.HexToAddress(c.Adjudicator),
		AssetHolder: common.HexToAddress(c.AssetHolder),
		Token:       common.HexToAddress(c.Token),
	}
}

// mintedTo looks an address up in Minted whatever the case of the file's keys.
func (c uc2Contracts) mintedTo(a common.Address) (int64, bool) {
	for k, v := range c.Minted {
		if common.IsHexAddress(k) && common.HexToAddress(k) == a {
			return v, true
		}
	}
	return 0, false
}

func loadUC2Contracts(path string) (uc2Contracts, error) {
	var c uc2Contracts
	b, err := os.ReadFile(path)
	if err != nil {
		return c, err
	}
	if err := json.Unmarshal(b, &c); err != nil {
		return c, fmt.Errorf("%s: %w", path, err)
	}
	if !common.IsHexAddress(c.Adjudicator) || !common.IsHexAddress(c.AssetHolder) || !common.IsHexAddress(c.Token) {
		return c, fmt.Errorf("%s: adjudicator, asset_holder or token missing", path)
	}
	if c.Asset != string(assetPoints) {
		return c, fmt.Errorf("%s: asset %q, UC2 runs on points", path, c.Asset)
	}
	return c, nil
}

// validateUC2Funding refuses a UC2 config whose channels cannot carry the
// match (loadClusterConfigAsset calls it when hub_deposit > 0):
//
//   - a player's ledger channel must hold its n-1 virtual stakes plus its
//     ammo reserve: (n-1)*virtual_stake + ammo_reserve <= ledger_deposit;
//   - the hub's side of each ledger channel must back the far side of that
//     player's n-1 virtual channels: (n-1)*virtual_stake <= hub_deposit;
//   - with contracts_file, every client was minted at least ledger_deposit
//     and the hub at least n*hub_deposit, by the addresses in the config.
func (c clusterConfig) validateUC2Funding() error {
	k := c.assetKind()
	if c.HubDeposit <= 0 {
		return fmt.Errorf("hub_deposit must be positive for UC2")
	}
	if c.AmmoReserve < 0 {
		return fmt.Errorf("ammo_reserve %v must not be negative", c.AmmoReserve)
	}
	if err := k.validateAmount("hub_deposit", c.HubDeposit); err != nil {
		return err
	}
	if err := k.validateAmount("ammo_reserve", c.AmmoReserve); err != nil {
		return err
	}
	n := len(c.Clients)
	stakes := float64(n-1) * c.VirtualStake
	if need := stakes + c.AmmoReserve; need > c.LedgerDeposit {
		return fmt.Errorf("ledger_deposit %v is below %d virtual channels x virtual_stake %v + ammo_reserve %v = %v",
			c.LedgerDeposit, n-1, c.VirtualStake, c.AmmoReserve, need)
	}
	if stakes > c.HubDeposit {
		return fmt.Errorf("hub_deposit %v is below %d virtual channels x virtual_stake %v = %v (the hub backs the far side of each)",
			c.HubDeposit, n-1, c.VirtualStake, stakes)
	}
	if c.ContractsFile == "" {
		return nil
	}
	file, err := loadUC2Contracts(c.ContractsFile)
	if err != nil {
		return fmt.Errorf("contracts_file: %w", err)
	}
	if file.ChainID != c.ChainID {
		return fmt.Errorf("contracts_file %s is for chain %d, the config for chain %d", c.ContractsFile, file.ChainID, c.ChainID)
	}
	if k != assetPoints {
		return fmt.Errorf("contracts_file holds points contracts but the config's asset is %q", c.Asset)
	}
	check := func(node clusterNode, need float64) error {
		if !common.IsHexAddress(node.Ethereum) {
			return fmt.Errorf("%s: contracts_file needs the node's \"ethereum\" address to check its mint, got %q", node.Name, node.Ethereum)
		}
		a := common.HexToAddress(node.Ethereum)
		got, _ := file.mintedTo(a)
		if float64(got) < need {
			return fmt.Errorf("%s (%s) was minted %d points in %s but deposits %v", node.Name, a.Hex(), got, c.ContractsFile, need)
		}
		return nil
	}
	if err := check(c.Hub, c.HubDeposit*float64(n)); err != nil {
		return err
	}
	for _, node := range c.Clients {
		if err := check(node, c.LedgerDeposit); err != nil {
			return err
		}
	}
	return nil
}

// uc2LedgerDeposits are the initial balances of every UC2 ledger channel in
// token units: [the player's ledger_deposit, the hub's hub_deposit].
func (c clusterConfig) uc2LedgerDeposits() []*big.Int {
	k := c.assetKind()
	return []*big.Int{k.units(c.LedgerDeposit), k.units(c.HubDeposit)}
}

// parseAddressList parses a comma-separated list of distinct Ethereum
// addresses. A duplicate is refused: mintBatch would mint to it twice.
func parseAddressList(s string) ([]common.Address, error) {
	var out []common.Address
	seen := make(map[common.Address]bool)
	for _, part := range strings.Split(s, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if !common.IsHexAddress(part) {
			return nil, fmt.Errorf("%q is not an Ethereum address", part)
		}
		a := common.HexToAddress(part)
		if seen[a] {
			return nil, fmt.Errorf("%s is listed twice", a.Hex())
		}
		seen[a] = true
		out = append(out, a)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no addresses")
	}
	return out, nil
}

// runUC2Deploy deploys UC2's contracts from anvilKeys[0] and writes
// contracts.json. Its gas is the run's setup cost, recorded in the file
// (deploy_gas, deploy_txs) and kept apart from any Open.
func runUC2Deploy(url string, chainID uint64, players []common.Address, points int64, hub common.Address, hubPoints int64, out string) error {
	if out == "" {
		return fmt.Errorf("uc2-deploy needs -out contracts.json")
	}
	if points <= 0 || hubPoints <= 0 {
		return fmt.Errorf("uc2-deploy needs -points and -hub-points above zero")
	}
	for _, p := range players {
		if p == hub {
			return fmt.Errorf("the hub %s is also listed in -mint", hub.Hex())
		}
	}
	configureDistributedLogging()
	deployer, err := crypto.HexToECDSA(anvilKeys[0])
	if err != nil {
		return err
	}
	gm, err := newGasMeterFrom(url, crypto.PubkeyToAddress(deployer.PublicKey))
	if err != nil {
		return fmt.Errorf("gas meter (is Anvil at %s?): %w", url, err)
	}
	mint := mintToList(players, big.NewInt(points), hub, big.NewInt(hubPoints))
	contracts, err := deployContractsMinting(url, chainID, anvilKeys[0], assetPoints, mint)
	if err != nil {
		return fmt.Errorf("deploy: %w", err)
	}
	gas, txs, _, _ := gm.gasSinceByContract()
	c := uc2Contracts{
		ChainID: chainID, Adjudicator: contracts.Adjudicator.Hex(), AssetHolder: contracts.AssetHolder.Hex(),
		Token: contracts.Token.Hex(), Asset: string(assetPoints),
		Minted:    map[string]int64{hub.Hex(): hubPoints},
		DeployGas: gas, DeployTxs: txs,
	}
	for _, p := range players {
		c.Minted[p.Hex()] = points
	}
	if err := writeJSONAtomic(out, c); err != nil {
		return err
	}
	b, _ := json.Marshal(c)
	fmt.Fprintln(stdoutguard.Protocol, string(b))
	return nil
}

// runUC2Hub is -mode hub on uc2-deploy's contracts with the key from
// hub_key_file and, unless disable_strict_payment_policy, the hub's payment
// policy. It refuses a config that is not a UC2 one rather than fall back to
// deploying its own contracts.
func runUC2Hub(configPath string) error {
	configureDistributedLogging()
	cfg, err := loadClusterConfig(configPath)
	if err != nil {
		return err
	}
	if cfg.HubDeposit <= 0 || cfg.ContractsFile == "" || cfg.HubKeyFile == "" {
		return fmt.Errorf("uc2-hub needs hub_deposit, contracts_file and hub_key_file in %s", configPath)
	}
	updatePol = cfg.updatePolicy()
	return runHubWithDeposits(cfg, !cfg.DisablePaymentPolicy, cfg.uc2LedgerDeposits())
}
