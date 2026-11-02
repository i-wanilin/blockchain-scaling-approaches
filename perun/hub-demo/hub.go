// hub.go — the intermediary node ("Ingrid").
//
// The hub implements no protocol of its own. It is an ordinary go-perun client whose
// only distinguishing behaviour is its accept policy: it accepts every ledger channel
// offered to it and records each one. This is sufficient to act as a payment hub
// because, once two clients each hold a ledger channel with the hub, go-perun can
// fund a virtual channel between them through those two ledger channels. That
// intermediary logic resides in go-perun (client/virtual_channel.go), not here.
//
// In summary: a Client opens channels and pays; a Hub accepts and later settles its
// half. Both embed the shared *node wiring from setup.go.
package main

import (
	"context"
	"fmt"
	"math/big"
	"sync"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/wire"
	"perun.network/perun-examples/hub-demo/lanwire"
)

// Hub is a go-perun client used as the payment intermediary ("Ingrid"). It accepts a
// ledger channel from every client; clients then open virtual channels with each
// other through the hub. There is no dedicated hub protocol — the virtual-channel
// intermediary logic resides in go-perun.
type Hub struct {
	*node

	// ledgers holds every ledger channel a client opened to the hub, keyed by channel
	// ID, so the settlement phase can locate and close the hub's half of each one. It
	// is guarded by mu because the handler loop (a goroutine) writes to it while the
	// main flow reads from it.
	mu             sync.Mutex
	ledgers        map[channel.ID]*client.Channel
	allowed        map[wire.AddrKey]bool
	requireAllowed bool
	strictPayments bool
	// wantDeposits, when set (uc2-hub), are the only initial balances the hub
	// funds: [player, hub] in token units.
	wantDeposits []*big.Int
}

// NewHub builds a hub node and starts its handler loop. As with a Client, Handle is
// what lets the hub react to proposals and updates initiated by clients.
func NewHub(name string, bus wire.Bus, key, nodeURL string, chainID uint64, contracts chainContracts) (*Hub, error) {
	return NewHubWithIdentity(name, bus, key, nodeURL, chainID, contracts, nil, "")
}

// NewHubWithIdentity builds a hub whose wire identity and channel state can be
// persisted across process restarts.
func NewHubWithIdentity(name string, bus wire.Bus, key, nodeURL string, chainID uint64, contracts chainContracts, identity *lanwire.Identity, stateDir string) (*Hub, error) {
	n, err := newNode(name, bus, key, nodeURL, chainID, contracts, identity, stateDir)
	if err != nil {
		return nil, err
	}
	h := &Hub{node: n, ledgers: make(map[channel.ID]*client.Channel), allowed: make(map[wire.AddrKey]bool)}
	go n.perun.Handle(h.proposalHandler(), h.updateHandler())
	return h, nil
}

// RequireRegisteredPeers enables the distributed benchmark's admission policy.
// A hub then accepts ledger proposals only from peers registered through its
// control endpoint. It is disabled for the original local teaching demo.
func (h *Hub) RequireRegisteredPeers(enabled bool) {
	h.mu.Lock()
	h.requireAllowed = enabled
	h.mu.Unlock()
}
func (h *Hub) AllowLedgerPeer(peer map[wallet.BackendID]wire.Address) {
	h.mu.Lock()
	h.allowed[wire.Keys(peer)] = true
	h.mu.Unlock()
}

// RequireSafePaymentUpdates enables the hub's payment policy (UC2, R7). Off,
// the hub accepts every update, as the original demo does. On, an update on a
// hub ledger channel must come from the player (index 0; the hub is index 1)
// and may only lower the player's balance in favour of the hub: a shot paid to
// the hub. A finalize with unchanged balances passes, so a player can always
// close. Virtual-channel funding and settlement never reach this policy:
// go-perun handles those proposals itself (client/update.go handleUpdateReq
// dispatches them before the user's UpdateHandler).
func (h *Hub) RequireSafePaymentUpdates(enabled bool) {
	h.mu.Lock()
	h.strictPayments = enabled
	h.mu.Unlock()
}

// RequireLedgerDeposits makes the hub accept a ledger proposal only when its
// initial balances are exactly [player, hub] (uc2-hub: ledger_deposit and
// hub_deposit). Without it (-mode hub) the hub funds whatever is proposed. A
// player asking the hub to fund more would starve the hub's other channels.
func (h *Hub) RequireLedgerDeposits(player, hub *big.Int) {
	h.mu.Lock()
	h.wantDeposits = []*big.Int{new(big.Int).Set(player), new(big.Int).Set(hub)}
	h.mu.Unlock()
}

// checkLedgerProposal is the hub's deposit rule on one ledger proposal.
func (h *Hub) checkLedgerProposal(lcp *client.LedgerChannelProposalMsg) error {
	h.mu.Lock()
	want := h.wantDeposits
	h.mu.Unlock()
	if want == nil {
		return nil
	}
	bals := lcp.InitBals
	if bals == nil || len(bals.Assets) != 1 || len(bals.Balances) != 1 || len(bals.Balances[0]) != 2 || len(bals.Locked) != 0 {
		return fmt.Errorf("want one asset and two balances [player %s, hub %s]", want[0], want[1])
	}
	got := bals.Balances[0]
	if got[0].Cmp(want[0]) != 0 || got[1].Cmp(want[1]) != 0 {
		return fmt.Errorf("proposed deposits [player %s, hub %s], want [player %s, hub %s]", got[0], got[1], want[0], want[1])
	}
	return nil
}

// checkUpdate is the hub's decision on one incoming ledger-channel update.
// The rule is the UC1 sensor's (uc1CheckUpdate), with the hub as the payee.
func (h *Hub) checkUpdate(current *channel.State, up client.ChannelUpdate) error {
	h.mu.Lock()
	strict := h.strictPayments
	h.mu.Unlock()
	if !strict {
		return nil
	}
	return uc1CheckUpdate(current, up, true)
}

// proposalHandler defines the hub's response to an incoming channel proposal: accept
// every ledger-channel proposal and record the resulting channel for settlement.
// This accept-all policy is the entirety of the hub's behaviour.
//
//   - lcp.Accept(h.wAddr, client.WithRandomNonce()) builds the acceptance carrying the
//     hub's address and a random nonce so each channel receives a unique ID.
func (h *Hub) proposalHandler() client.ProposalHandlerFunc {
	return func(cp client.ChannelProposal, r *client.ProposalResponder) {
		lcp, ok := cp.(*client.LedgerChannelProposalMsg)
		if !ok {
			_ = r.Reject(context.TODO(), "hub accepts only ledger channels")
			return
		}
		h.mu.Lock()
		allowed := !h.requireAllowed || (len(lcp.Peers) > 0 && h.allowed[wire.Keys(lcp.Peers[0])])
		h.mu.Unlock()
		if !allowed {
			_ = r.Reject(context.TODO(), "hub requires registered peer")
			return
		}
		if err := h.checkLedgerProposal(lcp); err != nil {
			ctx, cancel := boundedContext()
			_ = r.Reject(ctx, "hub deposits: "+err.Error())
			cancel()
			return
		}
		ch, err := r.Accept(context.TODO(), lcp.Accept(h.wAddr, client.WithRandomNonce()))
		if err != nil {
			h.errs <- fmt.Errorf("hub accept ledger: %w", err)
			return
		}
		h.startWatching(ch)
		h.mu.Lock()
		h.ledgers[ch.ID()] = ch
		h.mu.Unlock()
	}
}

// updateHandler defines the hub's response to an incoming update on a ledger channel.
// In the original demo the only such updates are clients finalizing their ledger
// channels before settlement; in UC2 a player also pays its shots to the hub. The
// policy (checkUpdate) is accept-all unless RequireSafePaymentUpdates is on. The
// virtual-channel funding and defunding that flows through the hub does not surface
// as a user-level update; go-perun handles it internally, so this handler need not
// be aware of virtual channels.
func (h *Hub) updateHandler() client.UpdateHandlerFunc {
	return func(current *channel.State, up client.ChannelUpdate, r *client.UpdateResponder) {
		if err := h.checkUpdate(current, up); err != nil {
			ctx, cancel := boundedContext()
			_ = r.Reject(ctx, "payment policy: "+err.Error())
			cancel()
			return
		}
		if err := r.Accept(context.TODO()); err != nil {
			h.errs <- fmt.Errorf("hub accept update: %w", err)
		}
	}
}

// ledgerWith returns the hub's handle to the ledger channel identified by id (the
// client knows the same channel by the same ID). The settlement phase uses it to
// close the hub's half of each ledger channel.
func (h *Hub) ledgerWith(id channel.ID) (*client.Channel, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	ch, ok := h.ledgers[id]
	return ch, ok
}
