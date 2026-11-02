// uc1.go — UC1 (pay-per-data) on direct Perun ledger channels.
//
// UC1 has no hub. A sensor streams data and every customer pays one
// micro-payment per data window. On Perun each customer opens one ledger
// channel straight to the sensor: the customer deposits, the sensor deposits
// nothing, every payment is one off-chain update, and the customer closes
// cooperatively at the end. Three process modes implement this:
//
//	uc1-deploy    deploy the Adjudicator and the ETH AssetHolder, write contracts.json
//	uc1-sensor    accept one channel per admitted customer, accept their payments,
//	              settle as secondary, write sensor-result.json (uc1_sensor.go)
//	uc1-customer  the per-customer sidecar driven over stdin/stdout by the Python
//	              payment client, or a Go-only probe (uc1_customer.go)
//
// The UC1 modes never read the cluster config. Their flags are registered here
// in init() so the global flag.Parse in main() sees them. Their machine-readable
// output goes to stdoutguard.Protocol (the real stdout); anything else a
// dependency prints lands on stderr (see package stdoutguard).
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"

	"perun.network/perun-examples/hub-demo/stdoutguard"
)

// The deployer (anvilKeys[0]) creates the two contracts at nonces 0 and 1 on a
// fresh Anvil, so their addresses are the same in every run (verified on two
// fresh Anvils, 2026-10-03). `up perun` checks them.
var (
	uc1ExpectedAdjudicator = common.HexToAddress("0x5FbDB2315678afecb367f032d93F642f64180aa3")
	uc1ExpectedAssetHolder = common.HexToAddress("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512")
)

// uc1Flags are the flags of the UC1 modes. -url and -chainid are main()'s.
type uc1Flags struct {
	out, contracts, keyFile, bind, endpoint, control, allow, depositWei, state string
	customers                                                                  int
	challenge                                                                  uint64
	updateTimeout, settleDeadline                                              float64
	strict, persist, clockCheck                                                bool
	probeRate, probeSeconds                                                    float64
	probeOut, probeAmountWei                                                   string
}

var uc1F uc1Flags

func init() {
	flag.StringVar(&uc1F.out, "out", "", "uc1-deploy: contracts.json path; uc1-sensor: result directory")
	flag.StringVar(&uc1F.contracts, "contracts", "contracts.json", "uc1-sensor: contracts.json written by uc1-deploy")
	flag.StringVar(&uc1F.keyFile, "key-file", "", "uc1-sensor: file holding the sensor's hex private key (0600)")
	flag.StringVar(&uc1F.bind, "bind", "0.0.0.0:9000", "uc1-sensor: Perun wire listen address")
	flag.StringVar(&uc1F.endpoint, "endpoint", "", "uc1-sensor: Perun wire address customers dial")
	flag.StringVar(&uc1F.control, "control", "0.0.0.0:8090", "uc1-sensor: control HTTP listen address")
	flag.IntVar(&uc1F.customers, "customers", 6, "uc1-sensor: number of customer channels to expect")
	flag.StringVar(&uc1F.allow, "allow", "", "uc1-sensor: comma-separated customer Ethereum addresses")
	flag.StringVar(&uc1F.depositWei, "deposit-wei", "1000000000000000000", "customer deposit per channel, in wei")
	flag.Uint64Var(&uc1F.challenge, "challenge", 10, "uc1-sensor: channel challenge duration, seconds")
	flag.Float64Var(&uc1F.updateTimeout, "update-timeout", 10, "uc1-sensor: update response timeout, seconds")
	flag.BoolVar(&uc1F.strict, "strict", true, "uc1-sensor: payment policy (customer may only lower its own balance)")
	flag.BoolVar(&uc1F.persist, "persist", true, "uc1-sensor: persist channel state (LevelDB) under -state")
	flag.StringVar(&uc1F.state, "state", "", "uc1-sensor: state directory (wire identity, channel DB)")
	flag.Float64Var(&uc1F.settleDeadline, "settle-deadline", 600, "uc1-sensor: give up this many seconds after the first finalize")
	flag.Float64Var(&uc1F.probeRate, "probe-rate", 0, "uc1-customer: probe mode, payments per second")
	flag.Float64Var(&uc1F.probeSeconds, "probe-seconds", 0, "uc1-customer: probe mode, seconds of payments")
	flag.StringVar(&uc1F.probeOut, "probe-out", "", "uc1-customer: probe mode, summary JSON path")
	flag.StringVar(&uc1F.probeAmountWei, "probe-amount-wei", "1000000000000", "uc1-customer: probe mode, wei per payment")
	flag.BoolVar(&uc1F.clockCheck, "clock-check", false, "uc1-customer: answer each stdin line with {\"now\": time.Now()} (clock-domain test)")
}

// uc1Main dispatches the UC1 modes; main() calls it right after flag.Parse.
func uc1Main(mode string) error {
	url := flag.Lookup("url").Value.String()
	var chainID uint64
	if _, err := fmt.Sscan(flag.Lookup("chainid").Value.String(), &chainID); err != nil {
		return fmt.Errorf("-chainid: %w", err)
	}
	switch mode {
	case "uc1-deploy":
		return runUC1Deploy(url, chainID, uc1F.out)
	case "uc1-sensor":
		code, err := runUC1Sensor(url, chainID, uc1F)
		if err != nil {
			return err
		}
		if code != 0 {
			os.Exit(code)
		}
		return nil
	case "uc1-customer":
		if uc1F.clockCheck {
			return runUC1ClockCheck(os.Stdin, stdoutguard.Protocol)
		}
		if uc1F.probeRate > 0 || uc1F.probeSeconds > 0 {
			return runUC1Probe(os.Stdin, stdoutguard.Protocol, uc1F)
		}
		return runUC1SidecarMain(os.Stdin, stdoutguard.Protocol)
	default:
		return fmt.Errorf("unknown mode %q (use uc1-deploy, uc1-sensor or uc1-customer)", mode)
	}
}

// uc1Contracts is contracts.json: what uc1-deploy wrote and every UC1 process reads.
type uc1Contracts struct {
	ChainID     uint64 `json:"chain_id"`
	Adjudicator string `json:"adjudicator"`
	AssetHolder string `json:"asset_holder"`
	DeployGas   uint64 `json:"deploy_gas"`
	DeployTxs   int    `json:"deploy_txs"`
}

func (c uc1Contracts) chain() chainContracts {
	return chainContracts{Asset: assetETH, Adjudicator: common.HexToAddress(c.Adjudicator), AssetHolder: common.HexToAddress(c.AssetHolder)}
}

func runUC1Deploy(url string, chainID uint64, out string) error {
	if out == "" {
		return fmt.Errorf("uc1-deploy needs -out contracts.json")
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
	contracts, err := deployContracts(url, chainID, anvilKeys[0], assetETH)
	if err != nil {
		return fmt.Errorf("deploy: %w", err)
	}
	gas, txs, _, _ := gm.gasSinceByContract()
	c := uc1Contracts{ChainID: chainID, Adjudicator: contracts.Adjudicator.Hex(), AssetHolder: contracts.AssetHolder.Hex(), DeployGas: gas, DeployTxs: txs}
	if contracts.Adjudicator != uc1ExpectedAdjudicator || contracts.AssetHolder != uc1ExpectedAssetHolder {
		fmt.Fprintf(os.Stderr, "uc1-deploy: addresses differ from the fresh-Anvil ones (%s, %s): was the deployer's nonce not 0?\n",
			uc1ExpectedAdjudicator.Hex(), uc1ExpectedAssetHolder.Hex())
	}
	if err := writeJSONAtomic(out, c); err != nil {
		return err
	}
	b, _ := json.Marshal(c)
	fmt.Fprintln(stdoutguard.Protocol, string(b))
	return nil
}

func loadUC1Contracts(path string) (uc1Contracts, error) {
	var c uc1Contracts
	b, err := os.ReadFile(path)
	if err != nil {
		return c, err
	}
	if err := json.Unmarshal(b, &c); err != nil {
		return c, fmt.Errorf("%s: %w", path, err)
	}
	if !common.IsHexAddress(c.Adjudicator) || !common.IsHexAddress(c.AssetHolder) {
		return c, fmt.Errorf("%s: adjudicator or asset_holder missing", path)
	}
	return c, nil
}

// uc1Manifest is the sensor's GET /v1/manifest.
type uc1Manifest struct {
	Wire        string `json:"wire"`
	Endpoint    string `json:"endpoint"` // the sensor's Perun wire address; customers dial it
	Ethereum    string `json:"ethereum"`
	Adjudicator string `json:"adjudicator"`
	AssetHolder string `json:"asset_holder"`
	ChainID     uint64 `json:"chain_id"`
}

// uc1Registration is a customer's POST /v1/register.
type uc1Registration struct {
	Name     string `json:"name"`
	Endpoint string `json:"endpoint"`
	Wire     string `json:"wire"`
	Ethereum string `json:"ethereum"`
}

// parseWei parses a decimal amount in wei. Amounts are strings on every UC1
// interface: a float would round 10^18.
func parseWei(s string, allowZero bool) (*big.Int, error) {
	if s == "" {
		return nil, fmt.Errorf("missing amount")
	}
	v, ok := new(big.Int).SetString(s, 10)
	if !ok {
		return nil, fmt.Errorf("amount %q is not a decimal integer", s)
	}
	if v.Sign() < 0 || (!allowZero && v.Sign() == 0) {
		return nil, fmt.Errorf("amount %s must be positive", s)
	}
	return v, nil
}

// unixSeconds is a wall-clock instant as the Python side's time.time() reads it.
func unixSeconds(t time.Time) float64 {
	if t.IsZero() {
		return 0
	}
	return float64(t.UnixNano()) / 1e9
}

func weiStrings(bals []*big.Int) []string {
	out := make([]string, len(bals))
	for i, b := range bals {
		out[i] = b.String()
	}
	return out
}

// writeJSONAtomic writes v to path through a temporary file and a rename, so a
// reader never sees half a file.
func writeJSONAtomic(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	b = append(b, '\n')
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp*")
	if err != nil {
		return err
	}
	if _, err := f.Write(b); err != nil {
		f.Close()
		os.Remove(f.Name())
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		os.Remove(f.Name())
		return err
	}
	if err := f.Close(); err != nil {
		os.Remove(f.Name())
		return err
	}
	return os.Rename(f.Name(), path)
}

// uc1Retry calls fn until it succeeds or timeout passes, with capped
// exponential backoff. The first dial on the Pis once failed with "no route to
// host" (spec §8), so start-up never trusts a single attempt. A refusal by the
// sensor (HTTP 4xx, e.g. an address not in -allow) is final and not retried.
func uc1Retry(timeout time.Duration, what string, fn func() error) error {
	deadline := time.Now().Add(timeout)
	wait := 200 * time.Millisecond
	for {
		err := fn()
		if err == nil {
			return nil
		}
		if uc1Refused(err) {
			return fmt.Errorf("%s: %w", what, err)
		}
		if time.Now().Add(wait).After(deadline) {
			return fmt.Errorf("%s: gave up after %v: %w", what, timeout, err)
		}
		time.Sleep(wait)
		if wait *= 2; wait > 5*time.Second {
			wait = 5 * time.Second
		}
	}
}

// uc1Refused reports whether err is an HTTP 4xx answer from httpJSON, whose
// errors read "<url>: <status>".
func uc1Refused(err error) bool {
	msg := err.Error()
	i := strings.LastIndex(msg, ": ")
	return i >= 0 && len(msg) >= i+5 && msg[i+2] == '4' && msg[i+5] == ' '
}

// parseAllow parses -allow (comma-separated Ethereum addresses).
func parseAllow(s string) (map[common.Address]bool, error) {
	out := make(map[common.Address]bool)
	for _, part := range strings.Split(s, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if !common.IsHexAddress(part) {
			return nil, fmt.Errorf("-allow: %q is not an Ethereum address", part)
		}
		out[common.HexToAddress(part)] = true
	}
	return out, nil
}
