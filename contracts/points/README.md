# PointsToken

The game's points (cluster spec §7.3 "Points, not money"): one ERC-20 with
`decimals() = 0`, no value outside the game, and a single minter (the deployer)
who gives every player the same balance with one `mintBatch` call. Perun,
Arbitrum and zkSync all run this one source.

## EVM build (Anvil for Perun, Arbitrum)

solc 0.8.30+commit.73712a01, optimizer on with default runs:

```sh
cd contracts/points
~/.local/zksync-tools/solc --optimize --abi --bin -o build/evm PointsToken.sol
```

The committed EVM artifacts are
`perun/hub-demo/pointstoken/PointsToken.{abi,bin}`; they must be byte-identical
to this build. The Go binding next to them is generated from them with
`go generate ./pointstoken` inside `perun/hub-demo` (abigen from go-ethereum
v1.15.11, the version pinned in its `go.mod`).

## zkSync build (EraVM; the rollup only, not Perun)

zksolc 1.5.18 with the zkSync fork of solc, era-solc 0.8.30-1.0.2:

```sh
cd contracts/points
~/.local/zksync-tools/zksolc --solc ~/.local/zksync-tools/era-solc \
  -O3 --codegen evmla --bin -o build/zksync PointsToken.sol
```

This writes `build/zksync/PointsToken.sol/PointsToken.zbin`. The committed copy
is `zksync/PointsToken.zbin` (hex text, as zksolc writes it), with its SHA-256 in
`zksync/PointsToken.zbin.sha256`; `measure/uc2_setup.py` refuses a zbin whose
hash differs, and `measure/tests/test_uc2_setup.py` checks the pair. `build/` is
not committed.

## Deployment: anew in every run

Each rollup chain starts every run from a snapshot: its saved state, created once and
restored byte for byte (`deploy/README.md`). The token is not in that snapshot. It is
deployed anew at the start of every run, as setup before the measured part, by
`python -m measure.uc2_setup --stack arbitrum|zksync …`: fund the arena, the
players and the deployer (account 119) on the L1, bridge 0.5 ETH to the
deployer, deploy, `mintBatch` every player the same points, check the balances.
It writes `token.json` and `recipients.json` into the run folder. On Arbitrum the
deployment is a type-2 contract creation of the EVM artifact above; on zkSync it
is `deploy/zk-token/deploy.js` (zksync-ethers 6.21.2, EIP-712 deploy of the
zbin through the ContractDeployer). The comment in the contract's source still says the token is "part of each chain's
snapshot", the first plan; it is out of date but left unchanged, because editing the
source would change the committed EVM bytecode (metadata hash). The gas of the deployment
is recorded in each run's `token.json` but not counted in the costs: in real use the token
is deployed once, not for every match.
