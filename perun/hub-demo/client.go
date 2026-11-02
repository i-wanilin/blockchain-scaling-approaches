// client.go — the end-user node.
//
// A Client is a participant (for example "Alice"). Its exported operations form the
// demo's entire API; runScenario in main.go is a sequence of these calls:
//
//	OpenLedgerTo(hub)      open an on-chain ledger channel to the hub (costs gas)
//	OpenVirtualTo(peer)    open an off-chain virtual channel to another client via
//	                       the hub (no gas)
//	pay(channel, amount)   send a payment off-chain (a signed balance update)
//	finalize + settle      close a channel and withdraw funds
//
// These methods do not implement the channel protocol. Each builds a proposal or an
// update and passes it to the go-perun client (the embedded node.perun, configured
// in setup.go); go-perun performs the signing, peer-to-peer messaging, on-chain
// funding, and dispute watching. A Client also runs a handler loop (started in
// NewClient) that defines its response to proposals and updates initiated by peers;
// see proposalHandler and updateHandler.
package main

import (
	"context"
	"fmt"
	"math/big"
	"sync"

	ethwallet "github.com/perun-network/perun-eth-backend/wallet"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/client"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/wire"
	"perun.network/perun-examples/hub-demo/lanwire"
)

// Client is a go-perun client acting as an end user. It opens a ledger channel to
// the hub and virtual channels (through the hub) to other clients. It embeds *node
// (see setup.go) for the shared go-perun wiring and adds the user-specific state
// below.
type Client struct {
	*node

	// ledger is this client's single on-chain channel with the hub. Every virtual
	// channel this client opens is funded out of this ledger channel.
	ledger *client.Channel

	// incomingVirtual receives virtual channels that other clients opened to this
	// client (the payee side). The handler loop pushes them here; the matching
	// OpenVirtualTo on the other side produces one. Buffered so the handler never
	// blocks.
	incomingVirtual chan *client.Channel
	mu              sync.Mutex
	virtuals        map[wire.AddrKey]*client.Channel
	allowedVirtual  map[wire.AddrKey]bool
	requireAllowed  bool
	strictPayments  bool
}

// NewClient builds a client node and starts its handler loop. The go-perun client
// becomes reactive only once Handle is running: from that point go-perun dispatches
// incoming proposals and updates to proposalHandler / updateHandler.
func NewClient(name string, bus wire.Bus, key, nodeURL string, chainID uint64, contracts chainContracts) (*Client, error) {
	return NewClientWithIdentity(name, bus, key, nodeURL, chainID, contracts, nil, "")
}

// NewClientWithIdentity builds a client whose wire identity and channel state
// survive a process restart.
func NewClientWithIdentity(name string, bus wire.Bus, key, nodeURL string, chainID uint64, contracts chainContracts, identity *lanwire.Identity, stateDir string) (*Client, error) {
	n, err := newNode(name, bus, key, nodeURL, chainID, contracts, identity, stateDir)
	if err != nil {
		return nil, err
	}
	c := &Client{node: n, incomingVirtual: make(chan *client.Channel, 64), virtuals: make(map[wire.AddrKey]*client.Channel), allowedVirtual: make(map[wire.AddrKey]bool)}
	// The callback is also invoked by Client.Restore(), making a restarted
	// client usable without rediscovering its locally persisted channels.
	n.perun.OnNewChannel(c.recordChannel)
	go n.perun.Handle(c.proposalHandler(), c.updateHandler())
	return c, nil
}

// RequireRegisteredVirtualPeers enables an explicit client-side admission
// policy for the distributed benchmark.
func (c *Client) RequireRegisteredVirtualPeers(enabled bool) {
	c.mu.Lock()
	c.requireAllowed = enabled
	c.mu.Unlock()
}
func (c *Client) AllowVirtualPeer(peer map[wallet.BackendID]wire.Address) {
	c.mu.Lock()
	c.allowedVirtual[wire.Keys(peer)] = true
	c.mu.Unlock()
}

// RequireSafePaymentUpdates enables application-level payment policy on top
// of go-perun's protocol validation. An actor may decrease only its own
// balance; every counterparty balance must stay equal or increase.
func (c *Client) RequireSafePaymentUpdates(enabled bool) {
	c.mu.Lock()
	c.strictPayments = enabled
	c.mu.Unlock()
}

func (c *Client) recordChannel(ch *client.Channel) {
	if ch.IsVirtualChannel() {
		for _, peer := range ch.Peers() {
			if !channel.EqualWireMaps(peer, c.wireAddr) {
				c.mu.Lock()
				c.virtuals[wire.Keys(peer)] = ch
				c.mu.Unlock()
			}
		}
		return
	}
	// This demo owns exactly one ledger channel per client. The guard keeps an
	// existing live handle when a duplicate callback is delivered.
	if c.ledger == nil {
		c.ledger = ch
	}
}

// proposalHandler defines the client's response to an incoming channel proposal. In
// this demo a client receives only virtual-channel proposals (a peer opening a
// payment channel through the hub); the policy is to accept and record the channel.
//
//   - cp.(*client.VirtualChannelProposalMsg) is a type assertion confirming the
//     generic proposal is a virtual-channel proposal.
//   - vcp.Accept(c.wAddr) builds the acceptance carrying this client's address.
func (c *Client) proposalHandler() client.ProposalHandlerFunc {
	return func(cp client.ChannelProposal, r *client.ProposalResponder) {
		vcp, ok := cp.(*client.VirtualChannelProposalMsg)
		if !ok {
			_ = r.Reject(context.TODO(), "client accepts only virtual channels")
			return
		}
		allowed := false
		for _, peer := range vcp.Peers {
			if !channel.EqualWireMaps(peer, c.wireAddr) {
				c.mu.Lock()
				allowed = !c.requireAllowed || c.allowedVirtual[wire.Keys(peer)]
				c.mu.Unlock()
			}
		}
		if !allowed {
			_ = r.Reject(context.TODO(), "client requires registered virtual peer")
			return
		}
		ch, err := r.Accept(context.TODO(), vcp.Accept(c.wAddr))
		if err != nil {
			c.errs <- fmt.Errorf("%s accept virtual: %w", c.name, err)
			return
		}
		c.startWatching(ch) // watch the chain for a stale-state settlement by the peer
		for _, peer := range vcp.Peers {
			if !channel.EqualWireMaps(peer, c.wireAddr) {
				c.mu.Lock()
				c.virtuals[wire.Keys(peer)] = ch
				c.mu.Unlock()
			}
		}
		c.incomingVirtual <- ch // deliver the channel to the waiting caller
	}
}

// updateHandler defines the client's response to an incoming channel update. An
// update is a proposed new channel state; in this demo it is a payment, which the
// payee must accept for it to take effect. The policy here is to accept every
// update. A production client would inspect the proposed balances or payment data
// before deciding.
func (c *Client) updateHandler() client.UpdateHandlerFunc {
	return func(current *channel.State, update client.ChannelUpdate, r *client.UpdateResponder) {
		c.mu.Lock()
		strict := c.strictPayments
		c.mu.Unlock()
		if strict {
			if err := validatePaymentUpdate(current, update); err != nil {
				ctx, cancel := boundedContext()
				_ = r.Reject(ctx, "payment policy: "+err.Error())
				cancel()
				return
			}
		}
		ctx, cancel := boundedContext()
		defer cancel()
		if err := r.Accept(ctx); err != nil {
			c.errs <- fmt.Errorf("%s accept update: %w", c.name, err)
		}
	}
}

func sameBackends(a, b []wallet.BackendID) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func validatePaymentUpdate(current *channel.State, update client.ChannelUpdate) error {
	proposed := update.State
	if current == nil || proposed == nil {
		return fmt.Errorf("missing state")
	}
	// Compare by content, not with reflect.DeepEqual: a locally built state has
	// nil Backends/Locked, the same state decoded from the wire has empty
	// non-nil slices, and DeepEqual calls those different. The channel opener
	// holds the local form, so it rejected every payment its peer proposed.
	if len(current.Balances) != len(proposed.Balances) ||
		!sameBackends(current.Backends, proposed.Backends) ||
		!channel.SubAllocsEqual(current.Locked, proposed.Locked) {
		return fmt.Errorf("allocation structure changed")
	}
	if err := channel.AssertAssetsEqual(current.Assets, proposed.Assets); err != nil {
		return fmt.Errorf("assets changed: %w", err)
	}
	actor := int(update.ActorIdx)
	for asset := range current.Balances {
		if len(current.Balances[asset]) != len(proposed.Balances[asset]) || actor < 0 || actor >= len(current.Balances[asset]) {
			return fmt.Errorf("invalid balance dimensions")
		}
		oldTotal, newTotal := new(big.Int), new(big.Int)
		for participant := range current.Balances[asset] {
			oldBalance, newBalance := current.Balances[asset][participant], proposed.Balances[asset][participant]
			oldTotal.Add(oldTotal, oldBalance)
			newTotal.Add(newTotal, newBalance)
			if participant == actor && newBalance.Cmp(oldBalance) > 0 {
				return fmt.Errorf("actor balance increased")
			}
			if participant != actor && newBalance.Cmp(oldBalance) < 0 {
				return fmt.Errorf("counterparty balance decreased")
			}
		}
		if oldTotal.Cmp(newTotal) != 0 {
			return fmt.Errorf("asset total changed")
		}
	}
	return nil
}

// OpenLedgerTo opens a ledger channel between this client (proposer, index 0) and
// the hub (index 1). This is the one on-chain, gas-bearing step per client.
//
// Arguments:
//   - peers:      the two participants by wire (network) address. Index 0 is this
//     client, index 1 the hub; the ordering fixes the participant indices used
//     throughout the channel.
//   - initAlloc:  the opening balances. SetAssetBalances([myDeposit, hubDeposit])
//     funds index 0 (this client) and index 1 (the hub).
//   - challengeDuration: the on-chain response window in case of a dispute (defined
//     in setup.go).
//
// ProposeChannel sends the proposal, awaits the hub's acceptance, and triggers the
// on-chain deposits through the funder configured in setup.go. On return the channel
// exists and is funded; it is stored in c.ledger.
func (c *Client) OpenLedgerTo(hub *Hub, myDeposit, hubDeposit float64) error {
	peers := []map[wallet.BackendID]wire.Address{c.wireAddr, hub.wireAddr}
	initAlloc := channel.NewAllocation(2, []wallet.BackendID{ethwallet.BackendID}, c.asset)
	initAlloc.SetAssetBalances(c.asset, []channel.Bal{c.kind.units(myDeposit), c.kind.units(hubDeposit)})

	prop, err := client.NewLedgerChannelProposal(challengeDuration, c.wAddr, initAlloc, peers)
	if err != nil {
		return err
	}
	ch, err := c.perun.ProposeChannel(context.TODO(), prop) // go-perun performs the on-chain funding here
	if err != nil {
		return err
	}
	c.startWatching(ch)
	c.ledger = ch
	return nil
}

// OpenVirtualTo opens a virtual channel from this client (payer, index 0) to payee
// (index 1) through the common hub. This is the core scaling mechanism: no on-chain
// transaction occurs, because the stake is reserved off-chain inside the two ledger
// channels that the payer and payee each hold with the hub.
//
// Arguments specific to virtual channels:
//   - parents:   the two ledger channels the virtual channel draws funding from,
//     [payer's ledger with hub, payee's ledger with hub]. go-perun uses them to move
//     the stake off-chain through the hub.
//   - indexMaps: the mapping from the virtual channel's participant indices (0 payer,
//     1 payee) onto each parent ledger channel's indices: {0,1} for the payer's
//     ledger, {1,0} for the payee's. This tells go-perun which balance slot in each
//     ledger channel backs which side of the virtual channel.
//
// The payer reads payee.ledger.ID() directly because both nodes run in one process.
// On a real network the hub would advertise the payee's ledger-channel ID; that
// discovery/routing step is out of scope and does not affect the gas measurements.
func (c *Client) OpenVirtualTo(payee *Client, payerBal, payeeBal float64) (*client.Channel, error) {
	return c.OpenVirtualToPeer(payee.wireAddr, payee.ledger.ID(), payerBal, payeeBal)
}

// OpenVirtualToPeer is the distributed equivalent of OpenVirtualTo. The
// caller obtains the peer's public wire address and ledger ID from the hub's
// control endpoint; no in-process object reference is required.
func (c *Client) OpenVirtualToPeer(payeeWire map[wallet.BackendID]wire.Address, payeeLedger channel.ID, payerBal, payeeBal float64) (*client.Channel, error) {
	return c.OpenVirtualToPeerWithChallenge(payeeWire, payeeLedger, payerBal, payeeBal, challengeDuration)
}

func (c *Client) OpenVirtualToPeerWithChallenge(payeeWire map[wallet.BackendID]wire.Address, payeeLedger channel.ID, payerBal, payeeBal float64, challenge uint64) (*client.Channel, error) {
	initAlloc := &channel.Allocation{
		Assets:   []channel.Asset{c.asset},
		Balances: [][]channel.Bal{{c.kind.units(payerBal), c.kind.units(payeeBal)}}, // [asset 0] = {payer, payee}
		Backends: []wallet.BackendID{ethwallet.BackendID},
	}
	peers := []map[wallet.BackendID]wire.Address{c.wireAddr, payeeWire}
	parents := []channel.ID{c.ledger.ID(), payeeLedger}
	indexMaps := [][]channel.Index{{0, 1}, {1, 0}}

	vcp, err := client.NewVirtualChannelProposal(challenge, c.wAddr, initAlloc, peers, parents, indexMaps)
	if err != nil {
		return nil, err
	}
	ch, err := c.perun.ProposeChannel(context.TODO(), vcp) // funded off-chain through the hub; no transaction
	if err != nil {
		return nil, err
	}
	c.startWatching(ch)
	c.mu.Lock()
	c.virtuals[wire.Keys(payeeWire)] = ch
	c.mu.Unlock()
	return ch, nil
}

// VirtualWith returns the accepted or proposed virtual channel for peer.
func (c *Client) VirtualWith(peer map[wallet.BackendID]wire.Address) (*client.Channel, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	ch, ok := c.virtuals[wire.Keys(peer)]
	return ch, ok
}

// VirtualChannels returns a stable snapshot of the virtual channels currently
// owned by this client. The distributed close protocol uses it to defund every
// child channel before attempting to settle the parent ledger channel.
func (c *Client) VirtualChannels() []*client.Channel {
	c.mu.Lock()
	defer c.mu.Unlock()
	seen := make(map[channel.ID]bool)
	channels := make([]*client.Channel, 0, len(c.virtuals))
	for _, ch := range c.virtuals {
		if !seen[ch.ID()] {
			seen[ch.ID()] = true
			channels = append(channels, ch)
		}
	}
	return channels
}

// ----------------------------------------------------------------------------
// Channel operations shared by payments and settlement. These are functions rather
// than methods because they act on a *client.Channel handle, which both the payer
// and the payee hold. A payment and a settlement are both a change to the channel
// state that both parties sign.
// ----------------------------------------------------------------------------

// pay transfers amount from the local party to the peer in a two-party channel.
// ch.Update applies a state change that go-perun then has both parties sign: move
// amount from this party's balance slot to the peer's and increment the version. No
// transaction reaches the chain.
//
//   - ch.Idx() is this party's index in the channel (0 or 1).
//   - 1 - from is the other party.
//   - TransferBalance(from, to, asset, amount) shifts the balance in the draft state.
//
// amount is already in on-chain units (wei or points); callers convert with
// node.kind.units, so the same pay serves both assets.
func pay(ch *client.Channel, asset channel.Asset, amount *big.Int) error {
	_, err := payCounted(ch, asset, amount)
	return err
}

// payCounted is pay, additionally reporting how many times this party gave way
// to a simultaneous proposal from its peer (see update_policy.go). A payment
// that gave way carries the yield timeout in its latency, so callers that
// report latency keep those samples apart.
func payCounted(ch *client.Channel, asset channel.Asset, amount *big.Int) (gaveWay int, err error) {
	return updateWithPolicy(ch, func(s *channel.State) {
		from := ch.Idx()
		to := 1 - from
		s.Allocation.TransferBalance(from, to, asset, amount)
	})
}

// finalize marks a channel's state final. A final state can be settled without
// waiting out the on-chain challenge period — the cooperative close. Settling a
// non-final state instead requires waiting out challengeDuration, which is the path
// taken if a peer becomes unresponsive.
func finalize(ch *client.Channel) error {
	if ch.State().IsFinal {
		return nil
	}
	_, err := updateWithPolicy(ch, func(s *channel.State) { s.IsFinal = true })
	return err
}

// settle concludes a channel and withdraws its funds, then releases its resources.
// The secondary flag selects which party drives the on-chain settlement transaction:
// false means primary (this party sends it), true means secondary (this party
// follows the other's transaction). For a ledger channel the client is primary and
// the hub secondary, so exactly one settlement transaction is sent per channel.
// Virtual channels produce no transaction; they defund off-chain back into the
// ledger channels.
func settle(ch *client.Channel, secondary bool) error {
	return settleContext(context.TODO(), ch, secondary)
}

func settleContext(ctx context.Context, ch *client.Channel, secondary bool) error {
	if err := ch.Settle(ctx, secondary); err != nil {
		return err
	}
	return ch.Close()
}
