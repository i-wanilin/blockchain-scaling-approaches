package main

import (
	"context"
	"math/big"
	"os"
	"testing"

	"github.com/ethereum/go-ethereum/ethclient"
)

// anvilURL returns the node URL for the tests (override with ANVIL_URL).
func anvilURL() string {
	if u := os.Getenv("ANVIL_URL"); u != "" {
		return u
	}
	return "ws://127.0.0.1:8545"
}

// skipIfNoAnvil skips the test when no reachable Anvil is found, so `go test`
// never hangs in environments without a local chain.
func skipIfNoAnvil(t *testing.T, url string) {
	t.Helper()
	ec, err := ethclient.Dial(url)
	if err != nil {
		t.Skipf("no Anvil at %s (%v) — start it with: anvil --block-time 1", url, err)
	}
	if _, err := ec.BlockNumber(context.Background()); err != nil {
		t.Skipf("Anvil unreachable at %s (%v) — start it with: anvil --block-time 1", url, err)
	}
}

// TestHubManyToMany runs the full lifecycle (open ledgers, open a virtual-channel
// mesh, pay, settle) and asserts the properties that make the hub a scaling win.
//
// Requires a local Anvil with interval mining (anvil --block-time 1); otherwise it
// is skipped. It is an integration test, so it is slow (on-chain settlement).
func TestHubManyToMany(t *testing.T) { runHubManyToMany(t, assetETH) }

// TestHubManyToManyPoints is the same lifecycle on the points token (asset.go).
func TestHubManyToManyPoints(t *testing.T) { runHubManyToMany(t, assetPoints) }

func runHubManyToMany(t *testing.T, kind assetKind) {
	url := anvilURL()
	skipIfNoAnvil(t, url)

	const nClients, payments = 3, 100
	res, err := runScenario(config{asset: kind, nodeURL: url, chainID: 31337, nClients: nClients, payments: payments})
	if err != nil {
		t.Fatalf("scenario failed: %v", err)
	}
	t.Logf("%s: deploy %d gas / %d tx %v; ledger open %d gas / %d tx %v; ledger settle %d gas / %d tx %v",
		kind, res.deploy.gas, res.deploy.txs, res.deployBy, res.ledgerOpen.gas, res.ledgerOpen.txs, res.ledgerOpenBy,
		res.ledgerSettle.gas, res.ledgerSettle.txs, res.ledgerSettleBy)

	t.Run("mesh is many-to-many", func(t *testing.T) {
		want := nClients * (nClients - 1) / 2 // C(n,2)
		if res.nVirtual != want {
			t.Errorf("virtual channels = %d, want %d (full mesh of %d clients)", res.nVirtual, want, nClients)
		}
	})

	t.Run("opening virtual channels costs no gas", func(t *testing.T) {
		if res.virtualOpen.gas != 0 || res.virtualOpen.txs != 0 {
			t.Errorf("opening %d virtual channels: got %d gas / %d tx, want 0 / 0",
				res.nVirtual, res.virtualOpen.gas, res.virtualOpen.txs)
		}
	})

	t.Run("settling virtual channels costs no gas", func(t *testing.T) {
		if res.virtualSettle.gas != 0 {
			t.Errorf("settling virtual channels: got %d gas, want 0", res.virtualSettle.gas)
		}
	})

	t.Run("ledger channels cost on-chain gas", func(t *testing.T) {
		if res.ledgerOpen.gas == 0 {
			t.Error("opening ledger channels should cost gas, got 0")
		}
		if res.ledgerSettle.gas == 0 {
			t.Error("settling ledger channels should cost gas, got 0")
		}
	})

	// Points are minted once, at setup, in a single mintBatch to the token.
	t.Run("setup mints in one transaction", func(t *testing.T) {
		wantMints := 0
		if kind == assetPoints {
			wantMints = 1
		}
		if got := res.deployBy["token"].txs; got != wantMints {
			t.Errorf("mint transactions = %d, want %d", got, wantMints)
		}
	})

	// Both parties of every ledger channel deposit: one deposit each, and in
	// points mode one approve each before it.
	t.Run("open is deposit (plus approve for points)", func(t *testing.T) {
		deposits := 2 * nClients
		if got := res.ledgerOpenBy["asset holder"].txs; got != deposits {
			t.Errorf("deposits = %d, want %d", got, deposits)
		}
		wantApproves := 0
		if kind == assetPoints {
			wantApproves = deposits
		}
		if got := res.ledgerOpenBy["token"].txs; got != wantApproves {
			t.Errorf("approves = %d, want %d", got, wantApproves)
		}
	})

	t.Run("payment balances are exact", func(t *testing.T) {
		moved := new(big.Int).Mul(kind.units(kind.burstAmount()), big.NewInt(payments))
		wantPayer := new(big.Int).Sub(kind.units(kind.defaultVirtualStake()), moved)
		wantPayee := new(big.Int).Add(kind.units(kind.defaultVirtualStake()), moved)
		if res.payerBal.Cmp(wantPayer) != 0 {
			t.Errorf("payer balance = %s, want %s (%s units)", res.payerBal, wantPayer, kind)
		}
		if res.payeeBal.Cmp(wantPayee) != 0 {
			t.Errorf("payee balance = %s, want %s (%s units)", res.payeeBal, wantPayee, kind)
		}
	})
}
