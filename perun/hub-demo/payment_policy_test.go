package main

import (
	"math/big"
	"testing"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
	"perun.network/go-perun/wallet"
)

func policyState(a, b int64) *channel.State {
	return &channel.State{Allocation: channel.Allocation{
		Balances: channel.Balances{{big.NewInt(a), big.NewInt(b)}},
	}}
}

func TestValidatePaymentUpdate(t *testing.T) {
	current := policyState(10, 0)
	if err := validatePaymentUpdate(current, client.ChannelUpdate{State: policyState(9, 1), ActorIdx: 0}); err != nil {
		t.Fatalf("valid payment rejected: %v", err)
	}
}

func TestValidatePaymentUpdateRejectsCounterpartyLoss(t *testing.T) {
	current := policyState(9, 1)
	if err := validatePaymentUpdate(current, client.ChannelUpdate{State: policyState(10, 0), ActorIdx: 0}); err == nil {
		t.Fatal("expected counterparty loss to be rejected")
	}
}

func TestValidatePaymentUpdateRejectsMint(t *testing.T) {
	current := policyState(10, 0)
	if err := validatePaymentUpdate(current, client.ChannelUpdate{State: policyState(9, 2), ActorIdx: 0}); err == nil {
		t.Fatal("expected changed total to be rejected")
	}
}

// A state built locally has nil Locked/Backends; the same state decoded from
// the wire has empty, non-nil slices. They are the same allocation. The channel
// opener holds the local form and receives the wire form, so treating them as
// different made the opener reject every payment its peer ever proposed.
func TestValidatePaymentUpdateNilAndEmptyAreTheSameStructure(t *testing.T) {
	current := policyState(10, 10)
	proposed := policyState(11, 9)
	proposed.Locked = []channel.SubAlloc{}
	proposed.Backends = []wallet.BackendID{}
	if err := validatePaymentUpdate(current, client.ChannelUpdate{State: proposed, ActorIdx: 1}); err != nil {
		t.Fatalf("payment from the non-opener rejected: %v", err)
	}
}

func TestValidatePaymentUpdateRejectsNewLockedFunds(t *testing.T) {
	current := policyState(10, 10)
	proposed := policyState(11, 9)
	proposed.Locked = []channel.SubAlloc{{Bals: []channel.Bal{big.NewInt(1)}}}
	if err := validatePaymentUpdate(current, client.ChannelUpdate{State: proposed, ActorIdx: 1}); err == nil {
		t.Fatal("a payment must not be able to introduce locked funds")
	}
}
