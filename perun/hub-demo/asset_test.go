package main

import (
	"encoding/json"
	"math/big"
	"os"
	"path/filepath"
	"testing"
)

func TestAssetUnits(t *testing.T) {
	if got := assetETH.units(1.5); got.Cmp(big.NewInt(1_500_000_000_000_000_000)) != 0 {
		t.Fatalf("1.5 ETH = %s wei", got)
	}
	// A point goes on-chain as one token unit, and a trace amount is points.
	if got := assetPoints.units(1000); got.Cmp(big.NewInt(1000)) != 0 {
		t.Fatalf("1000 points = %s units", got)
	}
	if got := assetPoints.traceAmount(5); got.Cmp(big.NewInt(5)) != 0 {
		t.Fatalf("hit of 5 = %s points", got)
	}
	if got, want := assetETH.traceAmount(5), ethToWei(5*traceUnit); got.Cmp(want) != 0 {
		t.Fatalf("ETH hit of 5 = %s wei, want %s", got, want)
	}
	for _, s := range []string{"", "eth", "ETH", " points "} {
		if _, err := parseAssetKind(s); err != nil {
			t.Fatalf("%q: %v", s, err)
		}
	}
	if _, err := parseAssetKind("usdc"); err == nil {
		t.Fatal("an unknown asset must be rejected")
	}
	if n := len(pointsHolders()); n != len(anvilKeys)-1 {
		t.Fatalf("points minted to %d accounts, want every key but the deployer's (%d)", n, len(anvilKeys)-1)
	}
}

func TestLoadClusterConfigAsset(t *testing.T) {
	write := func(cfg clusterConfig) string {
		cfg.ChainURL, cfg.ChainID = "ws://127.0.0.1:8545", 31337
		cfg.Hub = clusterNode{Name: "ingrid", Bind: "0.0.0.0:9000", Endpoint: "127.0.0.1:9000", Control: "0.0.0.0:8080"}
		cfg.Clients = []clusterNode{
			{Name: "alice", Bind: "0.0.0.0:9000", Endpoint: "127.0.0.1:9000", Control: "http://127.0.0.1:8080"},
			{Name: "bob", Bind: "0.0.0.0:9000", Endpoint: "127.0.0.1:9000", Control: "http://127.0.0.1:8080"},
		}
		data, err := json.Marshal(cfg)
		if err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(t.TempDir(), "cluster.json")
		if err := os.WriteFile(path, data, 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}

	// No "asset": ETH with the original amounts.
	got, err := loadClusterConfig(write(clusterConfig{}))
	if err != nil {
		t.Fatal(err)
	}
	if got.Asset != "eth" || got.LedgerDeposit != ledgerDeposit || got.VirtualStake != virtualStake {
		t.Fatalf("ETH defaults not applied: %+v", got)
	}

	// Points: whole-point defaults.
	got, err = loadClusterConfig(write(clusterConfig{Asset: "points"}))
	if err != nil {
		t.Fatal(err)
	}
	if got.Asset != "points" || got.LedgerDeposit != 1000 || got.VirtualStake != 150 {
		t.Fatalf("points defaults not applied: %+v", got)
	}

	// The -asset flag replaces the config's asset before the defaults apply.
	got, err = loadClusterConfigAsset(write(clusterConfig{Asset: "eth"}), "points")
	if err != nil || got.Asset != "points" || got.LedgerDeposit != 1000 {
		t.Fatalf("override: %+v, %v", got, err)
	}

	if _, err := loadClusterConfig(write(clusterConfig{Asset: "points", LedgerDeposit: 100.5, VirtualStake: 10})); err == nil {
		t.Fatal("a fractional point deposit must be rejected")
	}
	if _, err := loadClusterConfig(write(clusterConfig{Asset: "points", LedgerDeposit: pointsMint, VirtualStake: 10})); err == nil {
		t.Fatal("a hub cannot fund two ledgers of its whole mint")
	}
	if _, err := loadClusterConfig(write(clusterConfig{Asset: "gold"})); err == nil {
		t.Fatal("an unknown asset must be rejected")
	}
}
