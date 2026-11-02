package main

import (
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"sort"
	"strings"
	"time"
)

type latencySummary struct {
	Count  int     `json:"count"`
	MeanMS float64 `json:"mean_ms"`
	P50MS  float64 `json:"p50_ms"`
	P90MS  float64 `json:"p90_ms"`
	P99MS  float64 `json:"p99_ms"`
	MaxMS  float64 `json:"max_ms"`
}

type distributedResult struct {
	RunID               string
	Node                string
	Payments            int
	PaymentBurst        time.Duration
	PaymentLatencies    []time.Duration
	LedgerOpen          time.Duration
	LedgerRestored      bool
	VirtualOpen         time.Duration
	VirtualSettle       time.Duration
	LedgerSettle        time.Duration
	Completed           bool
	StrictPaymentPolicy bool
	Trace               *distributedTraceResult
	CleanLatencies      []time.Duration
	Collisions          int
	VirtualFinal        []channelFinal
	Asset               assetKind
	// This node's own on-chain transactions while opening and while settling
	// its ledger channel; nil if not measured (for example a restored ledger).
	LedgerOpenGas, LedgerSettleGas *gasFile
}

// gasFile is one node's on-chain cost of one phase, split by the contract each
// transaction called ("token" = approve, "asset holder" = deposit or withdraw,
// "adjudicator" = conclude).
type gasFile struct {
	Gas        uint64             `json:"gas"`
	Txs        int                `json:"txs"`
	FeeWei     string             `json:"fee_wei"` // sum of gasUsed x effectiveGasPrice
	ByContract map[string]gasCost `json:"by_contract,omitempty"`
}

type gasCost struct {
	Gas uint64 `json:"gas"`
	Txs int    `json:"txs"`
}

func newGasFile(gas uint64, txs int, by map[string]phaseCost, fee *big.Int) *gasFile {
	out := &gasFile{Gas: gas, Txs: txs, FeeWei: "0", ByContract: make(map[string]gasCost, len(by))}
	if fee != nil {
		out.FeeWei = fee.String()
	}
	for name, c := range by {
		out.ByContract[name] = gasCost{Gas: c.gas, Txs: c.txs}
	}
	return out
}

// String prints a phase as "total gas / txs (contract gas, ...)" for the logs.
func (g *gasFile) String() string {
	if g == nil {
		return "n/a"
	}
	names := make([]string, 0, len(g.ByContract))
	for name := range g.ByContract {
		names = append(names, name)
	}
	sort.Strings(names)
	parts := make([]string, 0, len(names))
	for _, name := range names {
		parts = append(parts, fmt.Sprintf("%s %d", name, g.ByContract[name].Gas))
	}
	return fmt.Sprintf("%d gas / %d tx (%s)", g.Gas, g.Txs, strings.Join(parts, ", "))
}

// channelFinal is one virtual channel as seen by this node after all payments.
type channelFinal struct {
	Channel  string   `json:"channel"`
	Version  uint64   `json:"version"`
	Balances []string `json:"balances"`
}

// traceFile is the serialized form of a trace replay. It is omitted entirely
// for synthetic-burst runs, so a reader can tell the two workloads apart.
type traceFile struct {
	Path        string          `json:"path"`
	Mode        string          `json:"mode"` // "closed-loop" or "paced"
	Events      int             `json:"events"`
	OK          int             `json:"ok"`
	Failed      int             `json:"failed"`
	FireOK      int             `json:"fire_ok"`
	HitOK       int             `json:"hit_ok"`
	WallMS      float64         `json:"wall_ms"`
	Throughput  float64         `json:"payments_per_second"`
	Latency     latencySummary  `json:"latency"`
	FireLatency *latencySummary `json:"fire_latency,omitempty"`
	HitLatency  *latencySummary `json:"hit_latency,omitempty"`
	Lateness    *latencySummary `json:"lateness,omitempty"`
	Misses      *int            `json:"deadline_misses,omitempty"`
	BudgetMS    *float64        `json:"deadline_budget_ms,omitempty"`
	MaxBacklog  *int            `json:"max_backlog,omitempty"`
	TraceSpanMS *float64        `json:"trace_span_ms,omitempty"`
}

func summarizeTrace(tr *distributedTraceResult) *traceFile {
	if tr == nil {
		return nil
	}
	mode := "closed-loop"
	if tr.Paced {
		mode = "paced"
	}
	throughput := 0.0
	if tr.Wall > 0 {
		throughput = float64(tr.OK) / tr.Wall.Seconds()
	}
	out := &traceFile{
		Path: tr.Path, Mode: mode, Events: tr.Total, OK: tr.OK, Failed: tr.Fail,
		FireOK: tr.FireOK, HitOK: tr.HitOK,
		WallMS: durationMS(tr.Wall), Throughput: throughput,
		Latency: summarizeLatencies(tr.LatAll),
	}
	if len(tr.LatFire) > 0 {
		s := summarizeLatencies(tr.LatFire)
		out.FireLatency = &s
	}
	if len(tr.LatHit) > 0 {
		s := summarizeLatencies(tr.LatHit)
		out.HitLatency = &s
	}
	if tr.Paced {
		s := summarizeLatencies(tr.Lateness)
		budget := durationMS(pacedBudget)
		span := durationMS(tr.TraceDur)
		misses, backlog := tr.Misses, tr.MaxBacklog
		out.Lateness, out.Misses, out.BudgetMS = &s, &misses, &budget
		out.MaxBacklog, out.TraceSpanMS = &backlog, &span
	}
	return out
}

type distributedResultFile struct {
	SchemaVersion   int            `json:"schema_version"`
	RunID           string         `json:"run_id"`
	TimestampUTC    string         `json:"timestamp_utc"`
	Node            string         `json:"node"`
	Hostname        string         `json:"hostname"`
	OS              string         `json:"os"`
	Arch            string         `json:"arch"`
	Commit          string         `json:"commit,omitempty"`
	CommitDirty     bool           `json:"commit_dirty"`
	Payments        int            `json:"payments"`
	PaymentFailures int            `json:"payment_failures"`
	BurstMS         float64        `json:"burst_ms"`
	Throughput      float64        `json:"payments_per_second"`
	Latency         latencySummary `json:"payment_latency"`
	// Collisions counts how often this node gave way to a simultaneous
	// proposal. CleanLatency excludes those payments, whose latency contains
	// the yield timeout rather than Perun's own cost.
	Collisions          int             `json:"collisions"`
	CleanLatency        *latencySummary `json:"payment_latency_clean,omitempty"`
	VirtualFinal        []channelFinal  `json:"virtual_final,omitempty"`
	LedgerOpenMS        float64         `json:"ledger_open_ms"`
	LedgerRestored      bool            `json:"ledger_restored"`
	VirtualOpenMS       float64         `json:"virtual_open_ms"`
	VirtualSettleMS     float64         `json:"virtual_settle_ms"`
	LedgerSettleMS      float64         `json:"ledger_settle_ms"`
	Completed           bool            `json:"completed"`
	StrictPaymentPolicy bool            `json:"strict_payment_policy"`
	Trace               *traceFile      `json:"trace,omitempty"`
	// Asset tells the unit of every balance above: wei ("eth") or points.
	Asset           string   `json:"asset"`
	LedgerOpenGas   *gasFile `json:"ledger_open_gas,omitempty"`
	LedgerSettleGas *gasFile `json:"ledger_settle_gas,omitempty"`
}

func durationMS(d time.Duration) float64 {
	return float64(d) / float64(time.Millisecond)
}

func summarizeLatencies(ds []time.Duration) latencySummary {
	if len(ds) == 0 {
		return latencySummary{}
	}
	sorted := append([]time.Duration(nil), ds...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	var total time.Duration
	for _, d := range sorted {
		total += d
	}
	percentile := func(p float64) float64 {
		index := int(math.Ceil(p*float64(len(sorted)))) - 1
		if index < 0 {
			index = 0
		}
		return durationMS(sorted[index])
	}
	return latencySummary{
		Count: len(sorted), MeanMS: durationMS(total) / float64(len(sorted)),
		P50MS: percentile(0.50), P90MS: percentile(0.90),
		P99MS: percentile(0.99), MaxMS: durationMS(sorted[len(sorted)-1]),
	}
}

func printDistributedPaymentSummary(payments int, burst time.Duration, latencies []time.Duration) {
	s := summarizeLatencies(latencies)
	throughput := 0.0
	if burst > 0 {
		throughput = float64(payments) / burst.Seconds()
	}
	fmt.Printf("distributed payments: %d in %v => %.1f payment/s, %.3f ms/payment\n",
		payments, burst.Round(time.Microsecond), throughput, s.MeanMS)
	fmt.Printf("distributed latency: p50=%.3f ms p90=%.3f ms p99=%.3f ms max=%.3f ms\n",
		s.P50MS, s.P90MS, s.P99MS, s.MaxMS)
}

func vcsInfo() (string, bool) {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "", false
	}
	var revision string
	var dirty bool
	for _, setting := range info.Settings {
		switch setting.Key {
		case "vcs.revision":
			revision = setting.Value
		case "vcs.modified":
			dirty = setting.Value == "true"
		}
	}
	return revision, dirty
}

func safeResultName(value string) string {
	return strings.Map(func(r rune) rune {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '-', r == '_':
			return r
		default:
			return '_'
		}
	}, value)
}

func makeDistributedResultFile(result distributedResult) distributedResultFile {
	hostname, _ := os.Hostname()
	revision, dirty := vcsInfo()
	summary := summarizeLatencies(result.PaymentLatencies)
	throughput := 0.0
	if result.PaymentBurst > 0 {
		throughput = float64(result.Payments) / result.PaymentBurst.Seconds()
	}
	var clean *latencySummary
	if result.Collisions > 0 && len(result.CleanLatencies) > 0 {
		s := summarizeLatencies(result.CleanLatencies)
		clean = &s
	}
	return distributedResultFile{
		Collisions: result.Collisions, CleanLatency: clean, VirtualFinal: result.VirtualFinal,
		SchemaVersion: 3, RunID: result.RunID, TimestampUTC: time.Now().UTC().Format(time.RFC3339Nano),
		Node: result.Node, Hostname: hostname, OS: runtime.GOOS, Arch: runtime.GOARCH,
		Commit: revision, CommitDirty: dirty,
		Payments: result.Payments, BurstMS: durationMS(result.PaymentBurst), Throughput: throughput,
		Latency: summary, LedgerOpenMS: durationMS(result.LedgerOpen),
		LedgerRestored: result.LedgerRestored, VirtualOpenMS: durationMS(result.VirtualOpen),
		VirtualSettleMS: durationMS(result.VirtualSettle),
		LedgerSettleMS:  durationMS(result.LedgerSettle), Completed: result.Completed,
		StrictPaymentPolicy: result.StrictPaymentPolicy,
		Trace:               summarizeTrace(result.Trace),
		Asset:               string(result.Asset),
		LedgerOpenGas:       result.LedgerOpenGas, LedgerSettleGas: result.LedgerSettleGas,
	}
}

func writeDistributedResultFile(resultDir string, out distributedResultFile) error {
	if err := os.MkdirAll(resultDir, 0o755); err != nil {
		return err
	}
	path := filepath.Join(resultDir, fmt.Sprintf("%s-%s.json", safeResultName(out.RunID), safeResultName(out.Node)))
	data, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return err
	}
	fmt.Printf("result written: %s\n", path)
	return nil
}

func writeDistributedResult(cfg clusterConfig, result distributedResult) (distributedResultFile, error) {
	out := makeDistributedResultFile(result)
	return out, writeDistributedResultFile(cfg.ResultDir, out)
}
