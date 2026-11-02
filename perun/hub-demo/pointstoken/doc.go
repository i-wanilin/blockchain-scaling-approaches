// Package pointstoken is the Go binding of contracts/points/PointsToken.sol, the
// game's points: one ERC-20 (decimals 0) shared by Perun, Arbitrum and zkSync.
// PointsToken.abi and PointsToken.bin are the EVM build of that source (solc
// 0.8.30 --optimize; see contracts/points/README.md). Regenerate the binding
// after rebuilding them with:
//
//	go generate ./pointstoken
package pointstoken

//go:generate go run github.com/ethereum/go-ethereum/cmd/abigen@v1.15.11 --abi PointsToken.abi --bin PointsToken.bin --pkg pointstoken --type PointsToken --out pointstoken.go
