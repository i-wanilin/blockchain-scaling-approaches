package main

import (
	"fmt"
	"os"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
)

// distributedTraceResult is the measured outcome of a trace replay executed by
// one physical node. Field-for-field it mirrors traceResults (local, in-process
// mode) so LAN figures stay directly comparable to the single-process battery
// numbers in bench/DISPERSION.md.
type distributedTraceResult struct {
	Path             string
	Total            int // events this node was responsible for sending
	OK, Fail         int
	FireOK, FireFail int
	HitOK, HitFail   int
	Wall             time.Duration
	LatAll           []time.Duration
	LatFire          []time.Duration
	LatHit           []time.Duration

	// Paced (open-loop) mode only:
	Paced      bool
	Lateness   []time.Duration // completion_time - scheduled_time (successful only)
	Misses     int             // completions later than scheduled + pacedBudget
	MaxBacklog int             // peak dispatched-but-uncompleted payments
	TraceDur   time.Duration   // time span of the trace (largest t_ms)
}

// traceChannels is the set of Perun channels one distributed node can pay over:
// its ledger channel to the hub (used by "fire" events, which target the arena)
// and its virtual channels to peers, keyed by the peer's index in the cluster
// config (used by "hit" events, which target a player id).
type traceChannels struct {
	ledger  *client.Channel
	virtual map[int]*client.Channel
	asset   channel.Asset
	kind    assetKind // converts trace amounts: game units -> wei or points
}

func (tc traceChannels) channelFor(ev tracePayment) (*client.Channel, error) {
	if ev.to == hubTarget {
		if tc.ledger == nil {
			return nil, fmt.Errorf("fire event needs a ledger channel to the hub")
		}
		return tc.ledger, nil
	}
	ch, ok := tc.virtual[ev.to]
	if !ok {
		return nil, fmt.Errorf("no virtual channel to client index %d", ev.to)
	}
	return ch, nil
}

// selectTraceEvents returns the payments this node makes itself. Every client
// is given the same trace and replays only its own slice, so together the
// cluster replays the whole trace. A trace that needs more players than the
// cluster defines is rejected: part of it would silently never be sent. A
// player with no payments of its own is legitimate; it still receives.
func selectTraceEvents(events []tracePayment, nPlayers, myIndex, nClients int) ([]tracePayment, error) {
	if nPlayers > nClients {
		return nil, fmt.Errorf("trace needs %d players but the cluster defines %d clients", nPlayers, nClients)
	}
	var mine []tracePayment
	for _, ev := range events {
		if ev.from == myIndex {
			mine = append(mine, ev)
		}
	}
	return mine, nil
}

func (tr *distributedTraceResult) record(ev tracePayment, lat time.Duration, err error) {
	if err != nil {
		tr.Fail++
		if ev.kind == "fire" {
			tr.FireFail++
		} else {
			tr.HitFail++
		}
		fmt.Fprintf(os.Stderr, "trace: %s %d->%d failed: %v\n", ev.kind, ev.from, ev.to, err)
		return
	}
	tr.OK++
	tr.LatAll = append(tr.LatAll, lat)
	if ev.kind == "fire" {
		tr.FireOK++
		tr.LatFire = append(tr.LatFire, lat)
	} else {
		tr.HitOK++
		tr.LatHit = append(tr.LatHit, lat)
	}
}

// replayDistributedTrace sends every event closed-loop, in trace order,
// measuring per-payment latency. A failed payment is counted and reported but
// does not abort the replay.
func replayDistributedTrace(path string, events []tracePayment, tc traceChannels) *distributedTraceResult {
	tr := &distributedTraceResult{Path: path, Total: len(events)}
	start := time.Now()
	for _, ev := range events {
		ch, err := tc.channelFor(ev)
		if err != nil {
			tr.record(ev, 0, err)
			continue
		}
		t0 := time.Now()
		err = pay(ch, tc.asset, tc.kind.traceAmount(ev.amount))
		tr.record(ev, time.Since(t0), err)
	}
	tr.Wall = time.Since(start)
	return tr
}

// replayDistributedTracePaced dispatches every event open-loop AT its trace
// timestamp, allowing concurrent in-flight payments. go-perun requires updates
// on a single channel to be sequential, so each channel gets a dedicated FIFO
// queue drained by one worker; different channels proceed in parallel. This is
// the same scheduling discipline as replayTracePaced in local mode.
func replayDistributedTracePaced(path string, events []tracePayment, tc traceChannels) *distributedTraceResult {
	tr := &distributedTraceResult{Path: path, Total: len(events), Paced: true}

	evs := make([]tracePayment, len(events))
	copy(evs, events)
	sort.SliceStable(evs, func(i, j int) bool { return evs[i].tMS < evs[j].tMS })
	tr.TraceDur = time.Duration(evs[len(evs)-1].tMS * float64(time.Millisecond))

	type job struct {
		ev    tracePayment
		ch    *client.Channel
		sched time.Time
	}
	var (
		wg      sync.WaitGroup
		mu      sync.Mutex // guards the tr.* aggregates
		backlog atomic.Int64
	)
	worker := func(q chan job) {
		defer wg.Done()
		for j := range q {
			t0 := time.Now()
			err := pay(j.ch, tc.asset, tc.kind.traceAmount(j.ev.amount))
			done := time.Now()
			backlog.Add(-1)
			mu.Lock()
			if err == nil {
				late := done.Sub(j.sched)
				tr.Lateness = append(tr.Lateness, late)
				if late > pacedBudget {
					tr.Misses++
				}
			}
			tr.record(j.ev, done.Sub(t0), err)
			mu.Unlock()
		}
	}

	queues := make(map[channel.ID]chan job) // accessed only by this goroutine
	start := time.Now()
	for _, ev := range evs {
		ch, err := tc.channelFor(ev)
		if err != nil {
			mu.Lock()
			tr.record(ev, 0, err)
			mu.Unlock()
			continue
		}
		sched := start.Add(time.Duration(ev.tMS * float64(time.Millisecond)))
		if d := time.Until(sched); d > 0 {
			time.Sleep(d)
		}
		q, ok := queues[ch.ID()]
		if !ok {
			q = make(chan job, len(evs))
			queues[ch.ID()] = q
			wg.Add(1)
			go worker(q)
		}
		if b := int(backlog.Add(1)); b > tr.MaxBacklog {
			tr.MaxBacklog = b // only the scheduler writes MaxBacklog
		}
		q <- job{ev: ev, ch: ch, sched: sched}
	}
	for _, q := range queues {
		close(q)
	}
	wg.Wait()
	tr.Wall = time.Since(start)
	return tr
}

func printDistributedTraceSummary(tr *distributedTraceResult) {
	mode := "closed-loop"
	if tr.Paced {
		mode = "paced (open-loop)"
	}
	throughput := 0.0
	if tr.Wall > 0 {
		throughput = float64(tr.OK) / tr.Wall.Seconds()
	}
	fmt.Printf("distributed trace: %s [%s] %d/%d ok (%d failed) in %v => %.1f payment/s\n",
		tr.Path, mode, tr.OK, tr.Total, tr.Fail, tr.Wall.Round(time.Millisecond), throughput)
	s := summarizeLatencies(tr.LatAll)
	fmt.Printf("distributed trace latency: mean=%.3f ms p50=%.3f ms p90=%.3f ms p99=%.3f ms max=%.3f ms\n",
		s.MeanMS, s.P50MS, s.P90MS, s.P99MS, s.MaxMS)
	if tr.Paced {
		l := summarizeLatencies(tr.Lateness)
		fmt.Printf("distributed trace pacing: lateness mean=%.3f ms p90=%.3f ms max=%.3f ms | misses=%d/%d (budget %v) | max_backlog=%d | trace_span=%v\n",
			l.MeanMS, l.P90MS, l.MaxMS, tr.Misses, tr.OK, pacedBudget, tr.MaxBacklog, tr.TraceDur.Round(time.Millisecond))
	}
}
