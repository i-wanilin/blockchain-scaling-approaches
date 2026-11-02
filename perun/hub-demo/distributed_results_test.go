package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSummarizeLatencies(t *testing.T) {
	got := summarizeLatencies([]time.Duration{
		5 * time.Millisecond,
		1 * time.Millisecond,
		2 * time.Millisecond,
		3 * time.Millisecond,
		4 * time.Millisecond,
	})
	if got.Count != 5 || got.MeanMS != 3 || got.P50MS != 3 || got.P90MS != 5 || got.P99MS != 5 || got.MaxMS != 5 {
		t.Fatalf("unexpected summary: %+v", got)
	}
}

func TestWriteDistributedResult(t *testing.T) {
	dir := t.TempDir()
	cfg := clusterConfig{ResultDir: dir}
	_, err := writeDistributedResult(cfg, distributedResult{
		RunID: "test-run", Node: "alice", Payments: 2,
		PaymentBurst:     4 * time.Millisecond,
		PaymentLatencies: []time.Duration{time.Millisecond, 3 * time.Millisecond},
		Completed:        true,
	})
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(dir, "test-run-alice.json"))
	if err != nil {
		t.Fatal(err)
	}
	var result distributedResultFile
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatal(err)
	}
	if !result.Completed || result.Payments != 2 || result.Throughput != 500 || result.Latency.P50MS != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
}
