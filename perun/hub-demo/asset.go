// asset.go — which on-chain asset the channels hold: ETH or points.
//
// "eth" (the default) is the original setting: channels hold native ETH in the
// ETH AssetHolder, and every amount the demo handles is a decimal ETH value that
// ethToWei scales by 10^18.
//
// "points" is UC2's game currency (cluster spec §7.3 "Points, not money"): our own
// ERC-20 token, PointsToken (contracts/points; binding in ./pointstoken), with no
// value outside the game — the same contract Arbitrum and zkSync run. One run
// deploys the token, mints a fixed balance to every account with an Anvil key in
// one mintBatch transaction, and binds an ERC-20 AssetHolder to the adjudicator.
// The backend's ERC-20 depositor only needs the standard allowance/approve/
// Approval interface, which PointsToken has. Amounts are whole points and go
// on-chain as they are, one point = one token unit — no 10^18 scaling. A shot costs
// 1 point and a hit 5 points, so a trace amount is already a number of points.
//
// Opening a ledger channel in points mode costs two transactions per depositing
// party instead of one: approve (the asset holder may pull the points) and the
// deposit itself. Both happen inside go-perun's funding step, so they are part of
// the timed and gas-metered Open.
package main

import (
	"fmt"
	"math"
	"math/big"
	"strings"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
)

type assetKind string

const (
	assetETH    assetKind = "eth"
	assetPoints assetKind = "points"
)

const (
	// pointsMint is what the token constructor gives every account with an Anvil
	// key. Only the hub comes near it: it funds its side of every ledger channel.
	pointsMint = 1_000_000
	// Gas limit for approve and for the ERC-20 deposit (the depositor uses one
	// limit for both). The backend's own tests use 100 000; a first deposit into a
	// fresh holding writes several storage slots, so leave room.
	erc20DepositGasHint = uint64(150000)
)

// parseAssetKind accepts "eth", "points" or "" (the default, ETH).
func parseAssetKind(s string) (assetKind, error) {
	switch k := assetKind(strings.ToLower(strings.TrimSpace(s))); k {
	case "", assetETH:
		return assetETH, nil
	case assetPoints:
		return assetPoints, nil
	default:
		return "", fmt.Errorf("unknown asset %q (use eth or points)", s)
	}
}

// units converts an amount as the demo and the config state it (decimal ETH, or
// whole points) into the integer the chain uses (wei, or token units).
func (k assetKind) units(amount float64) *big.Int {
	if k == assetPoints {
		return big.NewInt(int64(math.Round(amount)))
	}
	return ethToWei(amount)
}

// Default channel sizes. ETH keeps the original values. Points follow the game:
// every player starts with 1 000 points (spec §7.3), and a virtual stake of 150
// takes the default 100-payment burst with room to spare while five of them
// (a six-player mesh) still fit one ledger deposit.
func (k assetKind) defaultLedgerDeposit() float64 {
	if k == assetPoints {
		return 1000
	}
	return ledgerDeposit
}

func (k assetKind) defaultVirtualStake() float64 {
	if k == assetPoints {
		return 150
	}
	return virtualStake
}

// Payment sizes. A point cannot be split, so the burst and the cross-payments
// move one point each, and a trace amount is paid in points unscaled.
func (k assetKind) burstAmount() float64 {
	if k == assetPoints {
		return 1
	}
	return burstAmount
}

func (k assetKind) crossAmount() float64 {
	if k == assetPoints {
		return 1
	}
	return crossAmount
}

func (k assetKind) traceAmount(gameUnits float64) *big.Int {
	if k == assetPoints {
		return k.units(gameUnits)
	}
	return k.units(gameUnits * traceUnit)
}

// validateAmount rejects a point amount that is not a whole number: rounding it
// silently would make the channels hold something other than the config says.
func (k assetKind) validateAmount(name string, amount float64) error {
	if k == assetPoints && amount != math.Trunc(amount) {
		return fmt.Errorf("%s %v: points are whole numbers", name, amount)
	}
	return nil
}

// chainContracts are the addresses one run deploys and every node must agree on.
type chainContracts struct {
	Asset       assetKind
	Adjudicator common.Address
	AssetHolder common.Address // ETH or ERC-20 asset holder, per Asset
	Token       common.Address // points mode only; zero for ETH
}

// pointsHolders are the accounts the token mints points to: the hub and every
// client, that is every Anvil key except the deployer's.
func pointsHolders() []common.Address {
	holders := make([]common.Address, 0, len(anvilKeys)-1)
	for _, key := range anvilKeys[1:] {
		k, err := crypto.HexToECDSA(key)
		if err != nil {
			panic(err) // the keys are constants; a bad one is a programming error
		}
		holders = append(holders, crypto.PubkeyToAddress(k.PublicKey))
	}
	return holders
}
