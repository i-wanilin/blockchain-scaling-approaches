// Collision handling for channel updates.
//
// A go-perun channel allows ONE update in flight. Either party may propose the
// next state, but if both propose the same version at the same moment, each
// holds its channel lock while waiting for the other's signature, and each
// side's copy of the other's proposal is parked behind that lock. go-perun
// v0.15 does not resolve this; with context.TODO() both sides wait forever.
//
// A symmetric timeout does not fix this. Tried as a negative control on
// 2026-09-21 (both ends give way after the same timeout and retry): within five
// payments both sides had abandoned their proposal at the same version, neither
// side's update ever landed, and the channel was dead. Reading go-perun's
// accept path suggests the outcome can be worse still - each parked handler
// accepting the other's abandoned proposal, leaving two fully signed states
// with one version - but that fork was NOT observed; the stall was.
//
// So the two ends are given different roles, fixed by the channel index
// (index 0 is the party that opened the channel):
//
//	index 0  priority: waits Long. Hitting it means the channel is broken.
//	index 1  yielder:  waits Yield, then discards its proposal and unlocks, so
//	         its parked handler accepts index 0's proposal. It then re-proposes
//	         on top of the new version.
//
// Long must be far above Yield so the two can never fire together. A collision
// costs about Yield of extra latency on both payments and is counted, never
// hidden. Residual risk: if an ordinary, non-colliding round trip ever exceeds
// Yield, the yielder abandons an update its peer accepted and the channel
// desynchronises. That is detected (the expected version never arrives) and
// fails the run; it is never silently wrong. Set Yield well above the worst
// normal round trip of the link.
package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync/atomic"
	"time"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
)

type updatePolicy struct {
	Yield      time.Duration
	Long       time.Duration
	MaxRetries int
}

// updatePol is process-wide: one node runs one role set. Distributed mode
// overwrites it from the cluster config.
var updatePol = updatePolicy{Yield: time.Second, Long: 10 * time.Second, MaxRetries: 10}

// gaveWayTotal counts every give-way in this process, so workloads that call
// plain pay() (the trace replays) still report their collisions.
var gaveWayTotal atomic.Int64

func (p updatePolicy) timeoutFor(idx channel.Index) time.Duration {
	if idx == 0 {
		return p.Long
	}
	return p.Yield
}

// isUpdateTimeout reports whether err means "no answer in time": either the
// peer did not respond to our proposal, or we could not even take the channel
// lock because the peer's updates kept it busy.
func isUpdateTimeout(err error) bool {
	var timedOut client.RequestTimedOutError
	if errors.As(err, &timedOut) {
		return true
	}
	return errors.Is(err, context.DeadlineExceeded) ||
		strings.Contains(err.Error(), "locking machine mutex in time")
}

// boundedContext replaces the context.TODO() calls that let a lost message
// hang a node forever.
func boundedContext() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), updatePol.Long)
}

// updateWithPolicy applies one update under the role-based policy and reports
// how many times this party gave way before it succeeded.
func updateWithPolicy(ch *client.Channel, apply func(*channel.State)) (gaveWay int, err error) {
	yielder := ch.Idx() != 0
	for {
		before := ch.State().Version
		ctx, cancel := context.WithTimeout(context.Background(), updatePol.timeoutFor(ch.Idx()))
		err = ch.Update(ctx, apply)
		cancel()
		if err == nil {
			return gaveWay, nil
		}
		if !yielder || !isUpdateTimeout(err) {
			return gaveWay, err
		}
		gaveWay++
		gaveWayTotal.Add(1)
		if gaveWay > updatePol.MaxRetries {
			return gaveWay, fmt.Errorf("gave way %d times without getting an update through: %w", gaveWay, err)
		}
		// We have unlocked, so the parked proposal from index 0 is being
		// accepted now. Re-propose only once it has landed; if it never does,
		// this was not a collision and the two ends no longer agree.
		if err := waitForVersionAbove(ch, before, updatePol.Long); err != nil {
			return gaveWay, err
		}
	}
}

func waitForVersionAbove(ch *client.Channel, version uint64, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if ch.State().Version > version {
			return nil
		}
		time.Sleep(time.Millisecond)
	}
	return fmt.Errorf("gave way at version %d but no update from the peer arrived within %v: "+
		"the peer may have accepted the abandoned proposal, so the channel is desynchronised "+
		"(yield timeout too short for this link?)", version, timeout)
}
