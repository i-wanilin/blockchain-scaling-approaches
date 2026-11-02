// setup.go — the shared go-perun-on-Ethereum wiring.
//
// newNode turns a private key and a node URL into a configured go-perun client able
// to open channels on the local Ethereum chain. A go-perun client requires five
// components, which newNode constructs in order:
//
//  1. a wallet — holds the private key; signs channel states and transactions.
//  2. a contract backend — the connection to the node and a transactor for sending
//     transactions.
//  3. a funder — performs the on-chain deposit when a ledger channel opens.
//  4. an adjudicator — the client of the on-chain referee contract (disputes and
//     settlement).
//  5. a watcher — observes the chain and reacts to disputes.
//
// client.New assembles these into the node.perun client that client.go and hub.go
// drive. Hub and Client both embed the resulting *node, so this wiring is defined
// once. See ARCHITECTURE.md for how the components relate.
package main

import (
	"context"
	"fmt"
	"math/big"
	"path/filepath"

	"github.com/ethereum/go-ethereum/accounts"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	ethchannel "github.com/perun-network/perun-eth-backend/channel"
	ethwallet "github.com/perun-network/perun-eth-backend/wallet"
	swallet "github.com/perun-network/perun-eth-backend/wallet/simple"
	"polycry.pt/poly-go/sortedkv/leveldb"

	"perun.network/go-perun/channel"
	"perun.network/go-perun/channel/persistence/keyvalue"
	"perun.network/go-perun/client"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/watcher/local"
	"perun.network/go-perun/wire"
	"perun.network/perun-examples/hub-demo/lanwire"
)

const (
	challengeDuration = uint64(10)      // If a dispute happens, seconds the other side has to respond on-chain.
	ethDepositGasHint = uint64(50000)   // Gas hint the funder uses for an ETH deposit.
	adjMaxGas         = uint64(1000000) // Gas cap for adjudicator (settlement) calls.
)

// node is the go-perun client + Ethereum backend wiring that a Hub and a Client have
// in common. Hub and Client embed it (`*node`) and add their role-specific state and
// handlers. The fields:
//
//	perun    — the go-perun client we drive (propose/update/settle live on it)
//	asset    — which on-chain asset (ETH or points, via its AssetHolder) the channels use
//	kind     — eth or points; how amounts convert to on-chain units (asset.go)
//	ethAddr  — this node's Ethereum address (its on-chain identity)
//	wireAddr — this node's messaging address (how peers reach it on the bus)
//	wAddr    — this node's wallet address inside channels (its in-channel identity)
//	errs     — background handlers report errors here instead of crashing
type node struct {
	name             string
	perun            *client.Client
	asset            channel.Asset
	kind             assetKind
	ethAddr          common.Address
	wireAddr         map[wallet.BackendID]wire.Address
	wAddr            map[wallet.BackendID]wallet.Address
	errs             chan error
	closePersistence func() error
}

// newNode builds (but does NOT start the handler loop for) a go-perun client from an
// Anvil private key, wired to the real Ethereum backend at nodeURL. NewHub/NewClient
// call this, then start the appropriate handler loop on top.
func newNode(name string, bus wire.Bus, privateKey, nodeURL string, chainID uint64, contracts chainContracts, identity *lanwire.Identity, stateDir string) (*node, error) {
	lanwire.RegisterDecoder()
	// (1) WALLET: derive the key pair and Ethereum address from the hex private key.
	k, err := crypto.HexToECDSA(privateKey)
	if err != nil {
		return nil, err
	}
	w := swallet.NewWallet(k)
	ethAddr := crypto.PubkeyToAddress(k.PublicKey)
	walletAddr := ethwallet.AsWalletAddr(ethAddr) // same key, expressed as a perun wallet address

	// (2) CONTRACT BACKEND: the connection to Anvil (see util.go). Then sanity-check
	// that the adjudicator and asset-holder addresses we were given really are those
	// contracts on this chain — fail fast if someone passed the wrong address.
	cb, err := createContractBackend(nodeURL, chainID, w)
	if err != nil {
		return nil, fmt.Errorf("contract backend: %w", err)
	}
	adj, assetHolder := contracts.Adjudicator, contracts.AssetHolder
	if err := ethchannel.ValidateAdjudicator(context.TODO(), cb, adj); err != nil {
		return nil, fmt.Errorf("validating adjudicator: %w", err)
	}
	// The ERC-20 holder's bytecode embeds the token address, so validating it
	// also proves it holds the token we were told about.
	var depositor ethchannel.Depositor
	switch contracts.Asset {
	case assetETH:
		if err := ethchannel.ValidateAssetHolderETH(context.TODO(), cb, assetHolder, adj); err != nil {
			return nil, fmt.Errorf("validating asset holder: %w", err)
		}
		depositor = ethchannel.NewETHDepositor(ethDepositGasHint)
	case assetPoints:
		if err := ethchannel.ValidateAssetHolderERC20(context.TODO(), cb, assetHolder, adj, contracts.Token); err != nil {
			return nil, fmt.Errorf("validating points asset holder: %w", err)
		}
		depositor = ethchannel.NewERC20Depositor(contracts.Token, erc20DepositGasHint)
	default:
		return nil, fmt.Errorf("unknown asset %q", contracts.Asset)
	}

	// (3) FUNDER: deposits stake on-chain when a ledger channel opens. We register
	// our asset with its depositor so the funder knows HOW to deposit it: an ETH
	// deposit is one payable call; a points deposit is approve, then deposit.
	ethAcc := accounts.Account{Address: ethAddr}
	asset := ethchannel.NewAsset(new(big.Int).SetUint64(chainID), assetHolder)
	funder := ethchannel.NewFunder(cb)
	funder.RegisterAsset(*asset, depositor, ethAcc)

	// (4) + (5) Adjudicator (the client of the on-chain referee contract) and a local
	// watcher that uses it to react to disputes. No dispute arises in this demo, but a
	// production client must always run a watcher.
	adjudicator := ethchannel.NewAdjudicator(cb, adj, ethAddr, ethAcc, adjMaxGas)
	watcher, err := local.NewWatcher(adjudicator)
	if err != nil {
		return nil, fmt.Errorf("watcher: %w", err)
	}

	// The network identity is intentionally distinct from the Ethereum wallet.
	// It is a persistent RSA key used by the TCP wire bus; the wallet address is
	// still the identity that owns funds in the channel.
	if identity == nil {
		var idErr error
		identity, idErr = lanwire.NewEphemeral(name)
		if idErr != nil {
			return nil, fmt.Errorf("wire identity: %w", idErr)
		}
	}
	wireAddrs := map[wallet.BackendID]wire.Address{ethwallet.BackendID: identity.Address}
	wAddrs := map[wallet.BackendID]wallet.Address{ethwallet.BackendID: walletAddr}
	perunWallet := map[wallet.BackendID]wallet.Wallet{ethwallet.BackendID: w}

	// Assemble everything into the go-perun client. `bus` is the shared messaging
	// layer all nodes use to talk to each other (here in-process; see main.go).
	pc, err := client.New(wireAddrs, bus, funder, adjudicator, perunWallet, watcher)
	if err != nil {
		return nil, fmt.Errorf("client: %w", err)
	}
	var closePersistence func() error
	if stateDir != "" {
		db, err := leveldb.LoadDatabase(filepath.Join(stateDir, "channels"))
		if err != nil {
			return nil, fmt.Errorf("open channel persistence: %w", err)
		}
		pr := keyvalue.NewPersistRestorer(db)
		pc.EnablePersistence(pr)
		closePersistence = pr.Close
	}

	return &node{
		name:             name,
		perun:            pc,
		asset:            asset,
		kind:             contracts.Asset,
		ethAddr:          ethAddr,
		wireAddr:         wireAddrs,
		wAddr:            wAddrs,
		errs:             make(chan error, 64),
		closePersistence: closePersistence,
	}, nil
}

// startWatching runs the dispute watcher for one channel in the background. If the
// peer ever tried to settle an old (stale) state on-chain, the watcher would notice
// and respond. In this cooperative demo it simply runs quietly until the channel
// closes.
func (n *node) startWatching(ch *client.Channel) {
	go func() { _ = ch.Watch(adjEventHandler{n.name}) }()
}

// shutdown closes the go-perun client and releases its resources.
func (n *node) shutdown() {
	_ = n.perun.Close()
	if n.closePersistence != nil {
		_ = n.closePersistence()
	}
}

// adjEventHandler receives on-chain adjudicator events (disputes). None occur in this
// demo, so the handler is a no-op; a production client would react here, for example
// by refuting a stale state.
type adjEventHandler struct{ name string }

func (adjEventHandler) HandleAdjudicatorEvent(channel.AdjudicatorEvent) {}
