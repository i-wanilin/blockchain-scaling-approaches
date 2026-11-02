package main

import (
	"strings"
	"testing"

	"perun.network/go-perun/client"
)

func ev(kind string, from, to int, tMS float64) tracePayment {
	return tracePayment{kind: kind, from: from, to: to, amount: 1, tMS: tMS}
}

// UC-B is the workload the two-user LAN campaign actually replays: every hit
// goes 0 -> 1, so the primary client sends all of them.
func TestSelectTraceEventsSingleSender(t *testing.T) {
	events := []tracePayment{
		ev("hit", 0, 1, 0),
		ev("hit", 0, 1, 100),
		ev("hit", 0, 1, 200),
	}
	mine, err := selectTraceEvents(events, 2, 0, 2)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(mine) != 3 {
		t.Fatalf("got %d events, want 3", len(mine))
	}
}

// UC-A / UC-C need more players than a two-user cluster provides.
func TestSelectTraceEventsRejectsOversizedTrace(t *testing.T) {
	events := []tracePayment{ev("hit", 0, 1, 0)}
	_, err := selectTraceEvents(events, 6, 0, 2)
	if err == nil {
		t.Fatal("expected an error when the trace needs more players than the cluster has")
	}
	if !strings.Contains(err.Error(), "6 players") || !strings.Contains(err.Error(), "2 clients") {
		t.Errorf("error %q should report both counts", err.Error())
	}
}

func TestTraceChannelsRouting(t *testing.T) {
	// channelFor only selects a channel, it never pays, so an empty map and a
	// nil ledger are enough to exercise both failure branches.
	tc := traceChannels{virtual: map[int]*client.Channel{}}
	if _, err := tc.channelFor(ev("fire", 0, hubTarget, 0)); err == nil {
		t.Error("fire without a ledger channel should error")
	}
	if _, err := tc.channelFor(ev("hit", 0, 7, 0)); err == nil {
		t.Error("hit to an unknown peer index should error")
	}
}
