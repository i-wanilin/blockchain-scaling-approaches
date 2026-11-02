// util.go — low-level Ethereum plumbing, kept out of the way of the main logic.
//
// Three helpers:
//
//	createContractBackend — open a connection to Anvil and wrap it so go-perun can
//	                        send signed transactions through it.
//	deployContracts       — deploy the on-chain contracts the channels rely on: the
//	                        Adjudicator (referee) and the AssetHolder (vault) — for
//	                        ETH, or for points together with the points token itself
//	                        (see asset.go). Called once at the start of runScenario.
//	ethToWei              — convert a human "1.5 ETH" into the integer Wei amount the
//	                        chain actually uses (1 ETH = 10^18 Wei).
package main

import (
	"context"
	"fmt"
	"math/big"

	"github.com/ethereum/go-ethereum/accounts"
	"github.com/ethereum/go-ethereum/accounts/abi/bind"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"
	ethchannel "github.com/perun-network/perun-eth-backend/channel"
	swallet "github.com/perun-network/perun-eth-backend/wallet/simple"

	"perun.network/perun-examples/hub-demo/pointstoken"
)

const txFinalityDepth = 1 // Blocks to wait for tx confirmation (Anvil instamines).

// createContractBackend creates a go-perun eth-backend contract backend bound to nodeURL.
func createContractBackend(nodeURL string, chainID uint64, w *swallet.Wallet) (ethchannel.ContractBackend, error) {
	signer := types.LatestSignerForChainID(new(big.Int).SetUint64(chainID))
	transactor := swallet.NewTransactor(w, signer)
	ec, err := ethclient.Dial(nodeURL)
	if err != nil {
		return ethchannel.ContractBackend{}, err
	}
	return ethchannel.NewContractBackend(
		ec,
		ethchannel.MakeChainID(new(big.Int).SetUint64(chainID)),
		transactor,
		txFinalityDepth,
	), nil
}

// deployContracts deploys the Perun adjudicator and the asset holder for kind. In
// points mode it first deploys our PointsToken (contracts/points, the same
// contract the rollups use), mints pointsMint points to every account in
// pointsHolders with one mintBatch call, and binds an ERC-20 asset holder to
// that token.
func deployContracts(nodeURL string, chainID uint64, privateKey string, kind assetKind) (chainContracts, error) {
	return deployContractsMinting(nodeURL, chainID, privateKey, kind, mintToAnvilKeys)
}

// pointsMinter mints the opening points once the token is deployed. It sends
// its transactions with send, which waits for each to be mined.
type pointsMinter func(token *pointstoken.PointsToken, send func(what string, tx func(*bind.TransactOpts) (*types.Transaction, error)) error) error

// mintToAnvilKeys is the original mint: pointsMint to every points holder
// (every Anvil key but the deployer's) in one mintBatch.
func mintToAnvilKeys(token *pointstoken.PointsToken, send func(string, func(*bind.TransactOpts) (*types.Transaction, error)) error) error {
	return send("mint points", func(o *bind.TransactOpts) (*types.Transaction, error) {
		return token.MintBatch(o, pointsHolders(), big.NewInt(pointsMint))
	})
}

// mintToList is UC2's mint (uc2-deploy): perPlayer to each player in one
// mintBatch, then hubPoints to the hub in one mint. Two transactions, because
// mintBatch gives every address the same amount.
func mintToList(players []common.Address, perPlayer *big.Int, hub common.Address, hubPoints *big.Int) pointsMinter {
	return func(token *pointstoken.PointsToken, send func(string, func(*bind.TransactOpts) (*types.Transaction, error)) error) error {
		if err := send("mint points to players", func(o *bind.TransactOpts) (*types.Transaction, error) {
			return token.MintBatch(o, players, perPlayer)
		}); err != nil {
			return err
		}
		return send("mint points to the hub", func(o *bind.TransactOpts) (*types.Transaction, error) {
			return token.Mint(o, hub, hubPoints)
		})
	}
}

// deployContractsMinting is deployContracts with the points mint chosen by the
// caller (ignored for ETH).
func deployContractsMinting(nodeURL string, chainID uint64, privateKey string, kind assetKind, mint pointsMinter) (chainContracts, error) {
	out := chainContracts{Asset: kind}
	k, err := crypto.HexToECDSA(privateKey)
	if err != nil {
		return out, err
	}
	w := swallet.NewWallet(k)
	cb, err := createContractBackend(nodeURL, chainID, w)
	if err != nil {
		return out, err
	}
	acc := accounts.Account{Address: crypto.PubkeyToAddress(k.PublicKey)}

	out.Adjudicator, err = ethchannel.DeployAdjudicator(context.TODO(), cb, acc)
	if err != nil {
		return out, err
	}
	if kind != assetPoints {
		out.AssetHolder, err = ethchannel.DeployETHAssetholder(context.TODO(), cb, out.Adjudicator, acc)
		return out, err
	}
	out.Token, err = deployPointsToken(cb, acc, mint)
	if err != nil {
		return out, err
	}
	out.AssetHolder, err = ethchannel.DeployERC20Assetholder(context.TODO(), cb, out.Adjudicator, out.Token, acc)
	return out, err
}

// deployPointsToken deploys PointsToken (the deployer becomes its minter) and
// mints the opening points with mint. Gas limits are left at zero, so
// go-ethereum estimates them.
func deployPointsToken(cb ethchannel.ContractBackend, acc accounts.Account, mint pointsMinter) (common.Address, error) {
	ctx := context.TODO()
	opts, err := cb.NewTransactor(ctx, 0, acc)
	if err != nil {
		return common.Address{}, err
	}
	addr, tx, token, err := pointstoken.DeployPointsToken(opts, cb)
	if err != nil {
		return addr, fmt.Errorf("deploy points token: %w", err)
	}
	if _, err := cb.ConfirmTransaction(ctx, tx, acc); err != nil {
		return addr, fmt.Errorf("deploy points token: %w", err)
	}
	send := func(what string, call func(*bind.TransactOpts) (*types.Transaction, error)) error {
		opts, err := cb.NewTransactor(ctx, 0, acc)
		if err != nil {
			return err
		}
		tx, err := call(opts)
		if err != nil {
			return fmt.Errorf("%s: %w", what, err)
		}
		if _, err := cb.ConfirmTransaction(ctx, tx, acc); err != nil {
			return fmt.Errorf("%s: %w", what, err)
		}
		return nil
	}
	return addr, mint(token, send)
}

// ethToWei converts an ETH amount to Wei.
func ethToWei(eth float64) *big.Int {
	weiPerEth := new(big.Float).SetInt(new(big.Int).Exp(big.NewInt(10), big.NewInt(18), nil))
	wei, _ := new(big.Float).Mul(big.NewFloat(eth), weiPerEth).Int(nil)
	return wei
}
