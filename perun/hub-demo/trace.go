// trace.go — trace-replay mode (-trace flag).
//
// Instead of the synthetic payment burst, phase 3 replays a recorded tank-game
// trace (JSONL) as off-chain payments, so the state-channel family sees the exact
// same workload that was replayed on the EVM rollups (Arbitrum, zkSync) via
// JSON-RPC. The mapping from game events to channels:
//
//	fire  {from: i, to: "arena", amount: 1}   player i pays the ARENA. The arena is
//	      the hub (Ingrid): a direct update on client i's LEDGER channel, whose
//	      counterparty is the hub. No new machinery — pay() works on any two-party
//	      channel, and the hub's update handler already accepts payments.
//	hit   {from: victim, to: shooter, amount: 5}   victim pays shooter over their
//	      pairwise VIRTUAL channel (the mesh channel between the two clients).
//
// Replay is CLOSED-LOOP in trace order: one payment in flight, the next starts
// only after the previous channel update has completed (both signatures in). This
// mirrors the EVM replay methodology (one in-flight tx, receipt-waited), so the
// latency distributions are comparable.
//
// With -paced the replay is instead OPEN-LOOP: every payment is dispatched at its
// recorded trace timestamp t_ms (relative to replay start), regardless of whether
// earlier payments have completed — the real-time question "can Perun keep up with
// a 20 Hz game?". Concurrency model: go-perun requires updates on ONE channel to
// be sequential, while different channels may update concurrently, so the paced
// replayer runs one FIFO queue + worker goroutine per underlying channel (keyed by
// channel ID — both handles of a virtual channel share the ID, so bidirectional
// traffic on a pair serializes correctly). See replayTracePaced.
//
// Trace amounts are game units, not ETH. In ETH mode they are scaled by traceUnit
// so that a long trace cannot exhaust a channel's stake (129 hits x 5 units as raw
// ETH would overdraw the 10-ETH virtual stakes). Relative payment sizes are
// preserved. In points mode (asset.go) a game unit IS a point and is paid as is.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
)

// traceUnit is the ETH value of one game-trace amount unit in ETH mode
// (fire = 1 unit = 0.001 ETH, hit = 5 units = 0.005 ETH). Points mode pays one
// point per unit instead; see assetKind.traceAmount.
const traceUnit = 0.001

// hubTarget marks a payment whose payee is the hub ("arena") rather than a client.
const hubTarget = -1

// pacedBudget is the per-payment deadline in paced mode: one game tick at 20 Hz.
// A payment completing later than scheduled+pacedBudget is a deadline miss.
const pacedBudget = 50 * time.Millisecond

// tracePayment is one payment event extracted from the trace. Non-payment events
// (for example "move") are dropped at load time.
type tracePayment struct {
	kind   string  // "fire" or "hit"
	from   int     // payer: client index
	to     int     // payee: client index, or hubTarget for the hub ("arena")
	amount float64 // game units; assetKind.traceAmount turns them into the transfer
	tMS    float64 // trace timestamp in ms since game start (drives -paced dispatch)
}

// loadTrace parses the JSONL trace at path, keeping only the payment events
// ("fire", "hit") in file order. It returns the events and the player count
// derived from the highest player id seen (max id + 1), which the caller must
// match against -clients.
func loadTrace(path string) (events []tracePayment, nPlayers int, err error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()

	maxPlayer := 0
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 1<<20), 1<<20)
	for lineNo := 1; sc.Scan(); lineNo++ {
		line := bytes.TrimSpace(sc.Bytes())
		if len(line) == 0 {
			continue
		}
		var raw struct {
			Type    string  `json:"type"`
			TMS     float64 `json:"t_ms"`
			Payload struct {
				From   *int            `json:"from"`
				To     json.RawMessage `json:"to"` // int (client) or "arena"
				Amount float64         `json:"amount"`
			} `json:"payload"`
		}
		if err := json.Unmarshal(line, &raw); err != nil {
			return nil, 0, fmt.Errorf("%s:%d: %w", path, lineNo, err)
		}
		if raw.Type != "fire" && raw.Type != "hit" {
			continue // "move" etc. — no payment attached
		}
		if raw.Payload.From == nil {
			return nil, 0, fmt.Errorf("%s:%d: %s event without payload.from", path, lineNo, raw.Type)
		}
		ev := tracePayment{kind: raw.Type, from: *raw.Payload.From, amount: raw.Payload.Amount, tMS: raw.TMS}
		switch raw.Type {
		case "fire": // target must be the arena (mapped to the hub)
			var to string
			if err := json.Unmarshal(raw.Payload.To, &to); err != nil || to != "arena" {
				return nil, 0, fmt.Errorf("%s:%d: fire target %s, want \"arena\"", path, lineNo, raw.Payload.To)
			}
			ev.to = hubTarget
		case "hit": // target is the shooter (a client index)
			if err := json.Unmarshal(raw.Payload.To, &ev.to); err != nil {
				return nil, 0, fmt.Errorf("%s:%d: hit target %s, want a player id", path, lineNo, raw.Payload.To)
			}
		}
		if ev.from < 0 || (ev.to != hubTarget && ev.to < 0) || ev.amount <= 0 || ev.tMS < 0 {
			return nil, 0, fmt.Errorf("%s:%d: invalid payment event %+v", path, lineNo, ev)
		}
		if ev.from > maxPlayer {
			maxPlayer = ev.from
		}
		if ev.to > maxPlayer {
			maxPlayer = ev.to
		}
		events = append(events, ev)
	}
	if err := sc.Err(); err != nil {
		return nil, 0, fmt.Errorf("%s: %w", path, err)
	}
	if len(events) == 0 {
		return nil, 0, fmt.Errorf("%s: no payment events (fire/hit) found", path)
	}
	return events, maxPlayer + 1, nil
}

// traceResults holds the measured outcome of a trace replay.
type traceResults struct {
	path                    string
	total, ok, fail         int
	fireOK, fireFail        int
	hitOK, hitFail          int
	wall                    time.Duration
	latAll, latFire, latHit []time.Duration // per-payment latencies (successful only)

	// Paced (open-loop) mode only:
	paced      bool
	lateness   []time.Duration // completion_time - scheduled_time (successful only)
	misses     int             // payments completing later than scheduled + pacedBudget
	maxBacklog int             // peak number of dispatched-but-uncompleted payments
	traceDur   time.Duration   // time span of the trace (largest t_ms)
}

// replayTrace sends every trace payment closed-loop, in trace order, measuring the
// per-payment latency. mesh[i][j] must be client i's handle of the i<->j virtual
// channel; fire payments use clients[i].ledger (counterparty: the hub). A failed
// payment is counted and reported but does not abort the replay.
func replayTrace(path string, events []tracePayment, clients []*Client, mesh []map[int]*client.Channel) *traceResults {
	tr := &traceResults{path: path, total: len(events)}
	start := time.Now()
	for _, ev := range events {
		payer := clients[ev.from]
		var ch *client.Channel
		if ev.to == hubTarget {
			ch = payer.ledger // fire: pay the hub directly on the ledger channel
		} else {
			ch = mesh[ev.from][ev.to] // hit: pay the shooter on the pairwise virtual channel
		}
		t0 := time.Now()
		err := pay(ch, payer.asset, payer.kind.traceAmount(ev.amount))
		lat := time.Since(t0)
		if err != nil {
			tr.fail++
			if ev.kind == "fire" {
				tr.fireFail++
			} else {
				tr.hitFail++
			}
			fmt.Fprintf(os.Stderr, "trace: %s %d->%d failed: %v\n", ev.kind, ev.from, ev.to, err)
			continue
		}
		tr.ok++
		tr.latAll = append(tr.latAll, lat)
		if ev.kind == "fire" {
			tr.fireOK++
			tr.latFire = append(tr.latFire, lat)
		} else {
			tr.hitOK++
			tr.latHit = append(tr.latHit, lat)
		}
	}
	tr.wall = time.Since(start)
	return tr
}

// replayTracePaced dispatches every trace payment open-loop AT its trace
// timestamp (t_ms relative to replay start), allowing concurrent in-flight
// payments. Correctness under concurrency: go-perun channel updates on one
// channel must be sequential, so each underlying channel (keyed by channel ID)
// gets a dedicated FIFO queue drained by one worker goroutine; updates on
// different channels proceed in parallel. Per payment it records the service
// latency (pay start -> completed) and the lateness (completion - scheduled
// time); a completion later than scheduled+pacedBudget counts as a deadline
// miss. Failed payments are counted and reported but do not abort the replay.
func replayTracePaced(path string, events []tracePayment, clients []*Client, mesh []map[int]*client.Channel) *traceResults {
	tr := &traceResults{path: path, total: len(events), paced: true}

	// Dispatch in timestamp order. Traces are written tick-ordered, so this is
	// normally a no-op; the stable sort keeps file order within a tick.
	evs := make([]tracePayment, len(events))
	copy(evs, events)
	sort.SliceStable(evs, func(i, j int) bool { return evs[i].tMS < evs[j].tMS })
	tr.traceDur = time.Duration(evs[len(evs)-1].tMS * float64(time.Millisecond))

	type job struct {
		ev    tracePayment
		ch    *client.Channel
		asset channel.Asset
		kind  assetKind
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
			err := pay(j.ch, j.asset, j.kind.traceAmount(j.ev.amount))
			done := time.Now()
			backlog.Add(-1)
			mu.Lock()
			if err != nil {
				tr.fail++
				if j.ev.kind == "fire" {
					tr.fireFail++
				} else {
					tr.hitFail++
				}
				fmt.Fprintf(os.Stderr, "trace: %s %d->%d failed: %v\n", j.ev.kind, j.ev.from, j.ev.to, err)
			} else {
				tr.ok++
				late := done.Sub(j.sched)
				tr.lateness = append(tr.lateness, late)
				if late > pacedBudget {
					tr.misses++
				}
				lat := done.Sub(t0)
				tr.latAll = append(tr.latAll, lat)
				if j.ev.kind == "fire" {
					tr.fireOK++
					tr.latFire = append(tr.latFire, lat)
				} else {
					tr.hitOK++
					tr.latHit = append(tr.latHit, lat)
				}
			}
			mu.Unlock()
		}
	}

	// Scheduler (open loop): sleep until each payment's timestamp, then hand it
	// to its channel's queue. Queues are buffered for the whole trace so the
	// scheduler never blocks on a slow channel.
	queues := make(map[channel.ID]chan job) // accessed only by this goroutine
	start := time.Now()
	for _, ev := range evs {
		payer := clients[ev.from]
		var ch *client.Channel
		if ev.to == hubTarget {
			ch = payer.ledger // fire: pay the hub directly on the ledger channel
		} else {
			ch = mesh[ev.from][ev.to] // hit: pay the shooter on the pairwise virtual channel
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
		if b := int(backlog.Add(1)); b > tr.maxBacklog {
			tr.maxBacklog = b // only the scheduler writes maxBacklog
		}
		q <- job{ev: ev, ch: ch, asset: payer.asset, kind: payer.kind, sched: sched}
	}
	for _, q := range queues {
		close(q)
	}
	wg.Wait()
	tr.wall = time.Since(start)
	return tr
}

// latStats summarizes a latency sample.
type latStats struct{ mean, median, p10, p90, max time.Duration }

func computeLatStats(lats []time.Duration) latStats {
	if len(lats) == 0 {
		return latStats{}
	}
	s := make([]time.Duration, len(lats))
	copy(s, lats)
	sort.Slice(s, func(i, j int) bool { return s[i] < s[j] })
	var sum time.Duration
	for _, l := range s {
		sum += l
	}
	q := func(p float64) time.Duration { // nearest-rank on the sorted sample
		i := int(p*float64(len(s)-1) + 0.5)
		return s[i]
	}
	return latStats{
		mean:   sum / time.Duration(len(s)),
		median: q(0.50),
		p10:    q(0.10),
		p90:    q(0.90),
		max:    s[len(s)-1],
	}
}

func fmtLatStats(label string, n int, lats []time.Duration) string {
	if n == 0 {
		return fmt.Sprintf("  latency (%s)%*s n=0", label, 5-len(label), "")
	}
	st := computeLatStats(lats)
	r := func(d time.Duration) time.Duration { return d.Round(time.Microsecond) }
	return fmt.Sprintf("  latency (%s)%*s mean=%v median=%v p10=%v p90=%v max=%v",
		label, 5-len(label), "", r(st.mean), r(st.median), r(st.p10), r(st.p90), r(st.max))
}

// printTraceResults prints the trace-replay block of the results table (in place
// of the synthetic-burst line).
func printTraceResults(tr *traceResults) {
	if tr.paced {
		fmt.Printf("Paced trace replay (open-loop at trace timestamps): %s\n", tr.path)
	} else {
		fmt.Printf("Trace replay: %s\n", tr.path)
	}
	fmt.Printf("  transfers: %d total = %d fire (client->hub, ledger) + %d hit (victim->shooter, virtual)\n",
		tr.total, tr.fireOK+tr.fireFail, tr.hitOK+tr.hitFail)
	fmt.Printf("  ok/fail:   %d / %d (fire %d/%d, hit %d/%d)\n",
		tr.ok, tr.fail, tr.fireOK, tr.fireFail, tr.hitOK, tr.hitFail)
	rate := float64(tr.ok) / tr.wall.Seconds()
	if tr.paced {
		budget := tr.traceDur + tr.traceDur/10
		fmt.Printf("  wall time: %v for a %v trace => finished within +10%%: %v (%.0f payments/s)\n",
			tr.wall.Round(time.Millisecond), tr.traceDur.Round(time.Millisecond), tr.wall <= budget, rate)
		if tr.ok > 0 {
			st := computeLatStats(tr.lateness)
			r := func(d time.Duration) time.Duration { return d.Round(time.Microsecond) }
			fmt.Printf("  lateness (completion - scheduled): mean=%v median=%v p90=%v max=%v\n",
				r(st.mean), r(st.median), r(st.p90), r(st.max))
			fmt.Printf("  deadline misses (> %v after schedule): %d / %d = %.2f%%\n",
				pacedBudget, tr.misses, tr.ok, 100*float64(tr.misses)/float64(tr.ok))
		}
		fmt.Printf("  max backlog: %d payment(s) dispatched but not yet completed\n", tr.maxBacklog)
	} else {
		fmt.Printf("  wall time: %v => %.0f payments/s (closed-loop, one in flight)\n",
			tr.wall.Round(time.Microsecond), rate)
	}
	fmt.Println(fmtLatStats("all", tr.ok, tr.latAll))
	fmt.Println(fmtLatStats("fire", tr.fireOK, tr.latFire))
	fmt.Println(fmtLatStats("hit", tr.hitOK, tr.latHit))
}
