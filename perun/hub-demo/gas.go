// gas.go — per-phase gas measurement.
//
// runScenario calls mark() before a phase and gasSince() after it. The meter reads
// the chain height at each mark and sums the GasUsed of every transaction mined in
// between; a result of zero gas therefore means no transaction reached the chain
// during the phase, the property the demo verifies for virtual channels.
//
// The meter can also split a phase by the contract each transaction called
// (label + gasSinceByContract): in points mode the ledger open is approve (the
// token) plus deposit (the asset holder), and the thesis reports both. A meter
// made with newGasMeterFrom counts only one sender's transactions, which is how a
// distributed node measures its own share while other nodes transact at the same
// time.
package main

import (
	"context"
	"math/big"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/ethclient"
)

// gasMeter attributes on-chain gas to phases by summing the GasUsed of every
// transaction mined between two marks. Works cleanly on Anvil, which mines one
// block per transaction by default.
type gasMeter struct {
	ec     *ethclient.Client
	last   uint64
	from   *common.Address           // if set, only this sender's transactions count
	labels map[common.Address]string // contract address -> name for the split
}

func newGasMeter(nodeURL string) (*gasMeter, error) {
	ec, err := ethclient.Dial(nodeURL)
	if err != nil {
		return nil, err
	}
	n, err := ec.BlockNumber(context.TODO())
	if err != nil {
		return nil, err
	}
	return &gasMeter{ec: ec, last: n, labels: make(map[common.Address]string)}, nil
}

// newGasMeterFrom is newGasMeter restricted to transactions sent by from.
func newGasMeterFrom(nodeURL string, from common.Address) (*gasMeter, error) {
	g, err := newGasMeter(nodeURL)
	if err != nil {
		return nil, err
	}
	g.from = &from
	return g, nil
}

// label names a contract in the split gasSinceByContract reports.
func (g *gasMeter) label(addr common.Address, name string) {
	if addr != (common.Address{}) {
		g.labels[addr] = name
	}
}

// labelContracts names the contracts of one run: "adjudicator", "asset holder"
// and, in points mode, "token".
func (g *gasMeter) labelContracts(c chainContracts) {
	g.label(c.Adjudicator, "adjudicator")
	g.label(c.AssetHolder, "asset holder")
	g.label(c.Token, "token")
}

// mark resets the measurement window to the current chain head.
func (g *gasMeter) mark() {
	n, err := g.ec.BlockNumber(context.TODO())
	if err == nil {
		g.last = n
	}
}

// gasSince returns total gas used (and number of txs) since the last mark.
func (g *gasMeter) gasSince() (gas uint64, txs int) {
	gas, txs, _, _ = g.gasSinceByContract()
	return gas, txs
}

// gasSinceByContract is gasSince with the total also split by the contract each
// transaction called: its label, "deploy" for a contract creation, or "other".
// fee is the sum of gasUsed x effectiveGasPrice over the same transactions, in
// wei: what the senders actually paid (on Anvil an artefact of its base fee).
func (g *gasMeter) gasSinceByContract() (gas uint64, txs int, by map[string]phaseCost, fee *big.Int) {
	by = make(map[string]phaseCost)
	fee = new(big.Int)
	cur, err := g.ec.BlockNumber(context.TODO())
	if err != nil {
		return 0, 0, by, fee
	}
	for b := g.last + 1; b <= cur; b++ {
		blk, err := g.ec.BlockByNumber(context.TODO(), new(big.Int).SetUint64(b))
		if err != nil {
			continue
		}
		for _, tx := range blk.Transactions() {
			if g.from != nil {
				sender, err := types.Sender(types.LatestSignerForChainID(tx.ChainId()), tx)
				if err != nil || sender != *g.from {
					continue
				}
			}
			r, err := g.ec.TransactionReceipt(context.TODO(), tx.Hash())
			if err != nil {
				continue
			}
			gas += r.GasUsed
			txs++
			if r.EffectiveGasPrice != nil {
				fee.Add(fee, new(big.Int).Mul(new(big.Int).SetUint64(r.GasUsed), r.EffectiveGasPrice))
			}
			name := "other"
			if tx.To() == nil {
				name = "deploy"
			} else if l, ok := g.labels[*tx.To()]; ok {
				name = l
			}
			c := by[name]
			c.gas += r.GasUsed
			c.txs++
			by[name] = c
		}
	}
	g.last = cur
	return gas, txs, by, fee
}
