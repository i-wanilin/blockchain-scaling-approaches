// uc1_integration_test.go — real processes against a local Anvil (skip without ANVIL_URL).
//
// Start Anvil with `anvil --chain-id 31337 --block-time 1`, then
// ANVIL_URL=ws://127.0.0.1:8545 go test -count=1 -run 'TestUC1.*' -v -timeout 15m
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
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/accounts/abi/bind"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"
)

// Customer keys: indices 101..106 of the public Anvil mnemonic ("test test …
// junk"), from `cast wallet private-key --mnemonic … --mnemonic-index N`.
// Public throwaway keys, local chains only.
var uc1TestCustomerKeys = []string{
	"13986e078393fca89aedc2ecd014df01dfbff153434c04b2e38cfedcbef710f7", // 101
	"7382cc0c1dd9fc4ff87ed969fafac4c040ebd4890d0b8fa35781524df5b15476", // 102
	"360e8f096c6aaec3c922df1a82a7b954b69b42bdc20a6b71b2f50438c13d2ec5", // 103
	"956840865a0d252ee192c683c48befc5d8432aa7b334be6cb79133cfacfcda1a", // 104
	"9dece36dc7cb98e2e521e85efa7417d68744f00ab93caf70ec39dc3d6b16d916", // 105
	"c69422615b990ce3dbed91d6ed41e378ff92f0ebf23b8d18bf3db912c6797fa1", // 106
}

const (
	uc1TestDeposit = "1000000000000000000" // 1 ETH
	uc1TestAmount  = 1_000_000_000_000     // 10^12 wei per payment
)

type uc1Harness struct {
	t       *testing.T
	url     string
	bin     string
	dir     string
	control string // http://127.0.0.1:port
	sensor  *exec.Cmd
	exited  chan error
	mu      sync.Mutex
	logs    map[string]*syncBuffer
}

func uc1Addr(t *testing.T, key string) string {
	k, err := crypto.HexToECDSA(key)
	if err != nil {
		t.Fatal(err)
	}
	return crypto.PubkeyToAddress(k.PublicKey).Hex()
}

// newUC1Harness builds the binary, deploys, funds the customers and starts
// the sensor for n customers.
func newUC1Harness(t *testing.T, n int) *uc1Harness {
	t.Helper()
	url := os.Getenv("ANVIL_URL")
	if url == "" {
		t.Skip("set ANVIL_URL (e.g. ws://127.0.0.1:8545) with `anvil --chain-id 31337 --block-time 1` running")
	}
	skipIfNoAnvil(t, url)
	h := &uc1Harness{t: t, url: url, dir: t.TempDir(), logs: map[string]*syncBuffer{}, exited: make(chan error, 1)}
	h.bin = filepath.Join(h.dir, "perun-node")
	if runtime.GOOS == "windows" {
		h.bin += ".exe"
	}
	if out, err := exec.Command("go", "build", "-o", h.bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("build: %v\n%s", err, out)
	}
	contracts := filepath.Join(h.dir, "contracts.json")
	out, err := exec.Command(h.bin, "-mode", "uc1-deploy", "-url", url, "-chainid", "31337", "-out", contracts).Output()
	if err != nil {
		t.Fatalf("uc1-deploy: %v", err)
	}
	var c uc1Contracts
	if err := json.Unmarshal(out, &c); err != nil || c.DeployTxs != 2 {
		t.Fatalf("uc1-deploy stdout %q: %v", out, err)
	}
	t.Logf("deploy: %d gas / %d txs", c.DeployGas, c.DeployTxs)
	var allow []string
	for _, k := range uc1TestCustomerKeys[:n] {
		allow = append(allow, uc1Addr(t, k))
	}
	h.fund(allow)

	keyFile := filepath.Join(h.dir, "sensor.key")
	if err := os.WriteFile(keyFile, []byte(anvilKeys[1]+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	wirePort, ctlPort := freePort(t), freePort(t)
	h.control = fmt.Sprintf("http://127.0.0.1:%d", ctlPort)
	h.sensor = exec.Command(h.bin, "-mode", "uc1-sensor", "-url", url, "-chainid", "31337",
		"-contracts", contracts, "-key-file", keyFile,
		"-bind", fmt.Sprintf("127.0.0.1:%d", wirePort), "-endpoint", fmt.Sprintf("127.0.0.1:%d", wirePort),
		"-control", fmt.Sprintf("127.0.0.1:%d", ctlPort), "-customers", fmt.Sprint(n),
		"-allow", strings.Join(allow, ","), "-deposit-wei", uc1TestDeposit, "-challenge", "10",
		"-update-timeout", "10", "-strict=true", "-persist=true", "-state", filepath.Join(h.dir, "sensor-state"),
		"-settle-deadline", "300", "-out", filepath.Join(h.dir, "sensor-out"))
	h.sensor.Stdout, h.sensor.Stderr = h.log("sensor"), h.log("sensor")
	if err := h.sensor.Start(); err != nil {
		t.Fatal(err)
	}
	go func() { h.exited <- h.sensor.Wait() }()
	t.Cleanup(func() {
		_ = h.sensor.Process.Kill()
		if t.Failed() {
			t.Log(h.dump())
		}
	})
	if err := waitForHTTP(h.control+"/v1/manifest", 30*time.Second); err != nil {
		t.Fatalf("sensor did not start: %v\n%s", err, h.dump())
	}
	return h
}

// syncBuffer is a bytes.Buffer safe for a process's stdout and stderr at once.
type syncBuffer struct {
	mu sync.Mutex
	b  *bytes.Buffer
}

func (s *syncBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *syncBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

// log returns the one writer for label; a process's stdout and stderr share it.
func (h *uc1Harness) log(label string) io.Writer {
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, ok := h.logs[label]; !ok {
		h.logs[label] = &syncBuffer{b: &bytes.Buffer{}}
	}
	return h.logs[label]
}

func (h *uc1Harness) dump() string {
	h.mu.Lock()
	defer h.mu.Unlock()
	var sb strings.Builder
	for label, b := range h.logs {
		fmt.Fprintf(&sb, "---- %s ----\n%s\n", label, b.String())
	}
	return sb.String()
}

// fund sends 2 ETH from the deployer to each address.
func (h *uc1Harness) fund(addrs []string) {
	t := h.t
	ec, err := ethclient.Dial(h.url)
	if err != nil {
		t.Fatal(err)
	}
	defer ec.Close()
	k, _ := crypto.HexToECDSA(anvilKeys[0])
	from := crypto.PubkeyToAddress(k.PublicKey)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	nonce, err := ec.PendingNonceAt(ctx, from)
	if err != nil {
		t.Fatal(err)
	}
	tip := big.NewInt(1_000_000_000)
	head, err := ec.HeaderByNumber(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	feeCap := new(big.Int).Add(new(big.Int).Mul(head.BaseFee, big.NewInt(2)), tip)
	signer := types.LatestSignerForChainID(big.NewInt(31337))
	two := new(big.Int).Mul(big.NewInt(2), big.NewInt(1e18))
	var txs []*types.Transaction
	for i, a := range addrs {
		to := common.HexToAddress(a)
		tx, err := types.SignNewTx(k, signer, &types.DynamicFeeTx{ChainID: big.NewInt(31337), Nonce: nonce + uint64(i),
			GasTipCap: tip, GasFeeCap: feeCap, Gas: 21000, To: &to, Value: two})
		if err != nil {
			t.Fatal(err)
		}
		if err := ec.SendTransaction(ctx, tx); err != nil {
			t.Fatal(err)
		}
		txs = append(txs, tx)
	}
	for _, tx := range txs {
		if r, err := bind.WaitMined(ctx, ec, tx); err != nil || r.Status != types.ReceiptStatusSuccessful {
			t.Fatalf("fund: %v", err)
		}
	}
}

// uc1Sidecar is one customer process driven over stdin/stdout.
type uc1TestSidecar struct {
	t    *testing.T
	name string
	cmd  *exec.Cmd
	in   io.WriteCloser
	out  *bufio.Scanner
	mu   sync.Mutex
}

func (h *uc1Harness) initRequest(name, key string) string {
	ep := fmt.Sprintf("127.0.0.1:%d", freePort(h.t))
	req := map[string]any{"op": "init", "key": key, "name": name, "chain": h.url, "chain_id": 31337,
		"sensor_control": h.control, "bind": ep, "endpoint": ep, "state_dir": filepath.Join(h.dir, "state-"+name),
		"update_timeout_s": 10, "challenge_s": 10, "persist": true, "startup_timeout_s": 60}
	b, _ := json.Marshal(req)
	return string(b)
}

func (h *uc1Harness) startSidecar(name, key string) *uc1TestSidecar {
	t := h.t
	cmd := exec.Command(h.bin, "-mode", "uc1-customer")
	cmd.Stderr = h.log(name)
	in, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill() })
	sc := bufio.NewScanner(out)
	sc.Buffer(make([]byte, 64*1024), 1<<20)
	s := &uc1TestSidecar{t: t, name: name, cmd: cmd, in: in, out: sc}
	if r := s.call(h.initRequest(name, key)); r["ok"] != true || r["address"] != uc1Addr(t, key) {
		t.Fatalf("%s init: %v\n%s", name, r, h.dump())
	}
	return s
}

func (s *uc1TestSidecar) send(line string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := io.WriteString(s.in, line+"\n"); err != nil {
		s.t.Fatalf("%s: write: %v", s.name, err)
	}
}

func (s *uc1TestSidecar) read() map[string]any {
	if !s.out.Scan() {
		s.t.Fatalf("%s: stdout closed (%v)", s.name, s.out.Err())
	}
	var m map[string]any
	if err := json.Unmarshal(s.out.Bytes(), &m); err != nil {
		s.t.Fatalf("%s: stdout line is not a protocol line: %q", s.name, s.out.Text())
	}
	return m
}

func (s *uc1TestSidecar) call(line string) map[string]any {
	s.send(line)
	return s.read()
}

func payLine(name string, w int) string {
	return fmt.Sprintf(`{"op":"pay","id":"uc1/test/%s/w%d","amount_wei":"%d"}`, name, w, uc1TestAmount)
}

func num(v any) float64 { f, _ := v.(float64); return f }

func strs(v any) []string {
	var out []string
	for _, x := range v.([]any) {
		out = append(out, x.(string))
	}
	return out
}

func (h *uc1Harness) waitSensor(timeout time.Duration) uc1SensorResult {
	t := h.t
	select {
	case err := <-h.exited:
		if err != nil {
			t.Fatalf("sensor exit: %v\n%s", err, h.dump())
		}
	case <-time.After(timeout):
		t.Fatalf("sensor did not exit within %v\n%s", timeout, h.dump())
	}
	b, err := os.ReadFile(filepath.Join(h.dir, "sensor-out", "sensor-result.json"))
	if err != nil {
		t.Fatal(err)
	}
	var res uc1SensorResult
	if err := json.Unmarshal(b, &res); err != nil {
		t.Fatal(err)
	}
	if !res.Complete || res.ExitReason != "settled" || len(res.Errors) != 0 {
		t.Fatalf("sensor result not complete: %s", b)
	}
	return res
}

func (h *uc1Harness) sensorChannel(res uc1SensorResult, name string) uc1SensorChannelFile {
	for _, c := range res.Channels {
		if c.Name == name {
			if !c.Settled || !c.Final {
				h.t.Fatalf("%s: not settled at the sensor: %+v", name, c)
			}
			return c
		}
	}
	h.t.Fatalf("sensor has no channel for %s", name)
	return uc1SensorChannelFile{}
}

func TestUC1DirectChannelsProbe(t *testing.T) {
	h := newUC1Harness(t, 2)
	type probeOut struct {
		res uc1ProbeResult
		err error
	}
	results := make([]probeOut, 2)
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			name := fmt.Sprintf("c%d", i+1)
			outPath := filepath.Join(h.dir, name+"-probe.json")
			cmd := exec.Command(h.bin, "-mode", "uc1-customer", "-probe-rate", "50", "-probe-seconds", "10",
				"-probe-out", outPath, "-deposit-wei", uc1TestDeposit, "-probe-amount-wei", fmt.Sprint(uc1TestAmount))
			cmd.Stdin = strings.NewReader(h.initRequest(name, uc1TestCustomerKeys[i]) + "\n")
			var stdout bytes.Buffer
			cmd.Stdout, cmd.Stderr = &stdout, h.log(name)
			if err := cmd.Run(); err != nil {
				results[i].err = fmt.Errorf("%s: %v (stdout %s)", name, err, stdout.String())
				return
			}
			b, err := os.ReadFile(outPath)
			if err == nil {
				err = json.Unmarshal(b, &results[i].res)
			}
			results[i].err = err
		}(i)
	}
	wg.Wait()
	for i, r := range results {
		if r.err != nil {
			t.Fatalf("probe %d: %v\n%s", i+1, r.err, h.dump())
		}
	}
	res := h.waitSensor(120 * time.Second)
	paid := new(big.Int).Mul(big.NewInt(500), big.NewInt(uc1TestAmount)).String()
	for i, r := range results {
		p := r.res
		if p.Sent != 500 || p.OK != 500 || p.Close == nil {
			t.Fatalf("probe %d: %d/%d ok, close %v (%s)", i+1, p.OK, p.Sent, p.Close, p.CloseError)
		}
		c := h.sensorChannel(res, p.Name)
		if c.Payments != 500 || c.BalancesWei[1] != paid {
			t.Fatalf("%s at the sensor: %d payments, balances %v, want 500 and %s", p.Name, c.Payments, c.BalancesWei, paid)
		}
		if strings.Join(p.FinalBalances, ",") != strings.Join(c.BalancesWei, ",") || p.FinalVersion != c.LastVersion {
			t.Fatalf("%s: customer final v%d %v, sensor v%d %v", p.Name, p.FinalVersion, p.FinalBalances, c.LastVersion, c.BalancesWei)
		}
		t.Logf("%s: open %.2f s, %d gas / %d tx; update p50 %.2f ms p99 %.2f ms max %.2f ms; achieved %.1f/s; close %.2f s (finalize %.3f s), %d gas / %d tx %v",
			p.Name, p.Open.TFunded-p.Open.TStart, p.Open.L1GasUsed, p.Open.Txs,
			p.Update.P50MS, p.Update.P99MS, p.Update.MaxMS, p.AchievedRate,
			p.Close.TWithdrawn-p.Close.TStart, p.Close.TFinalized-p.Close.TStart, p.Close.L1GasUsed, p.Close.Txs, p.Close.ByContract)
	}
	t.Logf("sensor: open %v, close %v", res.OpenGas, res.CloseGas)
	if res.OpenGas == nil || res.OpenGas.Txs != 0 {
		t.Fatalf("the sensor must send no transaction at Open: %v", res.OpenGas)
	}
}

func TestUC1SidecarProtocolEndToEnd(t *testing.T) {
	h := newUC1Harness(t, 1)
	s := h.startSidecar("c1", uc1TestCustomerKeys[0])
	open := s.call(`{"op":"open","deposit_wei":"` + uc1TestDeposit + `"}`)
	if open["ok"] != true || num(open["txs"]) != 1 || num(open["t_funded"]) < num(open["t_start"]) {
		t.Fatalf("open: %v\n%s", open, h.dump())
	}
	t.Logf("open: %.2f s, %v gas / %v tx, fee %v wei", num(open["t_funded"])-num(open["t_start"]), open["l1_gas_used"], open["txs"], open["l1_fee_wei"])
	st := s.call(`{"op":"state"}`)
	if st["ok"] != true || num(st["version"]) != 0 {
		t.Fatalf("the funded state is version 0: %v", st)
	}
	// Pipelined: all 100 requests go out before any reply is read.
	for w := 1; w <= 100; w++ {
		s.send(payLine("c1", w))
	}
	for w := 1; w <= 100; w++ {
		r := s.read()
		if r["op"] != "pay" || r["id"] != fmt.Sprintf("uc1/test/c1/w%d", w) || r["ok"] != true || r["error"] != "" {
			t.Fatalf("reply %d: %v", w, r)
		}
		if num(r["version"]) != float64(w) || num(r["t_confirmed"]) < num(r["t_submitted"]) {
			t.Fatalf("pay %d: version %v (want %d), times %v..%v", w, r["version"], w, r["t_submitted"], r["t_confirmed"])
		}
	}
	paid := new(big.Int).Mul(big.NewInt(100), big.NewInt(uc1TestAmount))
	left := new(big.Int).Sub(new(big.Int).Mul(big.NewInt(1), big.NewInt(1e18)), paid)
	st = s.call(`{"op":"state"}`)
	if num(st["version"]) != 100 || st["broken"] != false || strings.Join(strs(st["balances_wei"]), ",") != left.String()+","+paid.String() {
		t.Fatalf("state after 100 pays: %v", st)
	}
	q := s.call(`{"op":"quiesce"}`)
	if q["ok"] != true || num(q["dropped"]) != 0 || num(q["version"]) != 100 {
		t.Fatalf("quiesce: %v", q)
	}
	if r := s.call(payLine("c1", 101)); r["ok"] != false || r["error"] != "unresolved: after quiesce" {
		t.Fatalf("pay after quiesce: %v", r)
	}
	cl := s.call(`{"op":"close"}`)
	if cl["ok"] != true || num(cl["final_version"]) != 101 || strings.Join(strs(cl["final_balances_wei"]), ",") != left.String()+","+paid.String() {
		t.Fatalf("close: %v\n%s", cl, h.dump())
	}
	if !(num(cl["t_start"]) <= num(cl["t_finalized"]) && num(cl["t_finalized"]) <= num(cl["t_withdrawn"])) {
		t.Fatalf("close times out of order: %v", cl)
	}
	t.Logf("close: %.2f s (finalize %.3f s), %v gas / %v tx %v, fee %v wei", num(cl["t_withdrawn"])-num(cl["t_start"]),
		num(cl["t_finalized"])-num(cl["t_start"]), cl["l1_gas_used"], cl["txs"], cl["by_contract"], cl["l1_fee_wei"])
	if r := s.call(`{"op":"exit"}`); r["ok"] != true {
		t.Fatalf("exit: %v", r)
	}
	if err := s.cmd.Wait(); err != nil {
		t.Fatalf("sidecar exit: %v", err)
	}
	res := h.waitSensor(120 * time.Second)
	c := h.sensorChannel(res, "c1")
	if c.LastVersion != 101 || c.Payments != 100 || c.BalancesWei[1] != paid.String() {
		t.Fatalf("sensor view: %+v", c)
	}
	t.Logf("sensor: open %v, close %v; settle %.2f s", res.OpenGas, res.CloseGas, c.TSettleEnd-c.TSettleStart)
}

func TestUC1SixCustomersCloseTogether(t *testing.T) {
	const n = 6
	h := newUC1Harness(t, n)
	sides := make([]*uc1TestSidecar, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			name := fmt.Sprintf("c%d", i+1)
			s := h.startSidecar(name, uc1TestCustomerKeys[i])
			if r := s.call(`{"op":"open","deposit_wei":"` + uc1TestDeposit + `"}`); r["ok"] != true || num(r["txs"]) != 1 {
				t.Errorf("%s open: %v", name, r)
				return
			}
			sides[i] = s
		}(i)
	}
	wg.Wait()
	if t.Failed() {
		t.FailNow()
	}
	// 10 pays per second for 5 s on every channel at once.
	for i, s := range sides {
		wg.Add(1)
		go func(i int, s *uc1TestSidecar) {
			defer wg.Done()
			t0 := time.Now()
			for w := 1; w <= 50; w++ {
				time.Sleep(time.Until(t0.Add(time.Duration(w-1) * 100 * time.Millisecond)))
				s.send(payLine(s.name, w))
			}
		}(i, s)
	}
	wg.Wait()
	for _, s := range sides {
		for w := 1; w <= 50; w++ {
			if r := s.read(); r["ok"] != true || num(r["version"]) != float64(w) {
				t.Fatalf("%s pay %d: %v", s.name, w, r)
			}
		}
	}
	// All six close at once: the sensor withdraws from one account six times
	// concurrently as the secondary.
	closes := make([]map[string]any, n)
	for i, s := range sides {
		wg.Add(1)
		go func(i int, s *uc1TestSidecar) {
			defer wg.Done()
			closes[i] = s.call(`{"op":"close"}`)
		}(i, s)
	}
	wg.Wait()
	for i, c := range closes {
		if c["ok"] != true {
			t.Fatalf("c%d close: %v\n%s", i+1, c, h.dump())
		}
		t.Logf("c%d close: %.2f s, %v gas / %v tx %v", i+1, num(c["t_withdrawn"])-num(c["t_start"]), c["l1_gas_used"], c["txs"], c["by_contract"])
	}
	res := h.waitSensor(180 * time.Second)
	if len(res.Channels) != n {
		t.Fatalf("sensor has %d channels", len(res.Channels))
	}
	for _, s := range sides {
		c := h.sensorChannel(res, s.name)
		if c.Payments != 50 {
			t.Fatalf("%s: %d payments at the sensor", s.name, c.Payments)
		}
	}
	h.mu.Lock()
	sensorBuf := h.logs["sensor"]
	h.mu.Unlock()
	sensorLog := sensorBuf.String()
	if strings.Contains(strings.ToLower(sensorLog), "nonce") {
		t.Fatalf("nonce error at the sensor:\n%s", sensorLog)
	}
	if res.CloseGas == nil || res.CloseGas.Txs != n {
		t.Fatalf("the sensor must withdraw once per channel: %v", res.CloseGas)
	}
	t.Logf("sensor close: %v", res.CloseGas)
	for _, s := range sides {
		s.call(`{"op":"exit"}`)
	}
}
