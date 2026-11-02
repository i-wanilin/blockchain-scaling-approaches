# perun-node: the Perun program of the measurements

This folder holds the Go program that runs Perun in the thesis measurements, built on
go-perun v0.15.0 and its Ethereum backend (perun-eth-backend v0.6.0). The cluster
builds it as `perun-node` (`deploy/cluster sync`). On the cluster, every Perun node runs
as its own process on its own board, and the nodes send their channel messages to each
other over the Wi-Fi (TCP, encrypted with TLS). The chain is Anvil on `bath0`.

- **UC1** (pay per data window): no hub. Each customer opens one ledger channel
  straight to the sensor on `bath1` and pays one update per window.
- **UC2** (the six-player tank game): a **hub** on `bath1`. Every player opens one
  ledger channel to the hub and, through it, one virtual channel with each of the
  other players.

How the UC1 and UC2 modes work, their ports and their tests:
[`DISTRIBUTED.md`](DISTRIBUTED.md), sections *UC1* and *UC2*. How the architecture
fits together and how to read the source: [`ARCHITECTURE.md`](ARCHITECTURE.md).

The folder is named `hub-demo` because it began as a demo of the hub: one program in
which a hub and its clients ran together, without a network. That early demo is still
here as the `local` mode, described in [`LOCAL-DEMO.md`](LOCAL-DEMO.md); no thesis number comes
from it.

## Modes and files (`-mode`)

One binary, several modes. Only the `uc1-*` and `uc2-*` modes are used by the
thesis measurements; `deploy/cluster` starts them.

| Mode | Files | Used by |
|---|---|---|
| `uc1-deploy`, `uc1-sensor`, `uc1-customer` | `uc1.go`, `uc1_sensor.go`, `uc1_customer.go` | **UC1 measurements** (direct channels, no hub) |
| `uc2-deploy`, `uc2-hub`, `uc2-player` | `uc2.go`, `uc2_player.go`, plus `distributed.go` (`uc2-hub` runs the distributed hub with its policies) and `pointstoken/` | **UC2 measurements** (hub, virtual channels, points) |
| `hub`, `client`, `preflight`, `init` | `distributed.go`, `distributed_results.go`, `distributed_trace.go`, `preflight.go` | pre-cluster version of the hub over TCP (`DISTRIBUTED.md`, top sections and *Legacy*); not used by the thesis measurements |
| `local` (default) | `main.go`, `trace.go` | the early demo without a network ([`LOCAL-DEMO.md`](LOCAL-DEMO.md)); not used by the thesis measurements |

Shared by all modes: `setup.go`, `hub.go`, `client.go`, `update_policy.go`,
`asset.go`, `gas.go`, `util.go`, `lanwire/`, `stdoutguard/`.

The pre-cluster `hub` and `client` modes were the step between the early demo and the
UC2 modes: each node a separate process, talking over TCP, with channel state kept on
disk and a small discovery endpoint so one identical binary can run the hub or any
client (`alice` … `frank`). They were verified once on the Raspberry Pi cluster
(2026-09-29; Anvil on `bath0`, the hub on `bath1`, clients on the other boards).
`uc2-hub` reuses their hub code. See [`DISTRIBUTED.md`](DISTRIBUTED.md) for the
procedure, the automated three-process, head-on and six-process mesh validation, and,
under *Legacy*, the earlier three-laptop beta.

## Files

- `main.go`      — flags, mode dispatch, and the `local` demo: `runScenario` (build topology → pay → settle) + metrics table.
- `setup.go`     — `node`: the go-perun client + Ethereum backend wiring shared by hub and client.
- `hub.go`       — `Hub`: the intermediary — accepts ledger channels and settles its side; optional admission (registered peers), deposit (`RequireLedgerDeposits`) and payment (`RequireSafePaymentUpdates`) policies used by the distributed and UC2 hub.
- `client.go`    — `Client`: an end user — `OpenLedgerTo`, `OpenVirtualTo`, `pay`, `finalize`, `settle`.
- `update_policy.go` — collision handling for concurrent channel updates (priority by channel index).
- `trace.go`     — `-trace` mode of the `local` demo: JSONL game-trace loading, closed-loop and paced replay, latency stats.
- `asset.go`     — `-asset eth|points`: amounts, defaults and the contracts each asset needs.
- `gas.go`       — per-phase gas meter (sums `GasUsed` over mined blocks).
- `util.go`      — contract backend, contract deployment, unit conversion.
- `distributed.go`, `distributed_results.go`, `distributed_trace.go` — `hub`/`client`/`init` modes over TCP, result JSON, trace replay per node; the hub part is reused by `uc2-hub`.
- `preflight.go` — `preflight` mode: configuration and connectivity checks.
- `uc1.go`, `uc1_sensor.go`, `uc1_customer.go` — UC1 modes (see `DISTRIBUTED.md`, *UC1*).
- `uc2.go`, `uc2_player.go` — UC2 modes (see `DISTRIBUTED.md`, *UC2*).
- `lanwire/`     — persistent wire identity for the TCP modes.
- `stdoutguard/` — keeps the sidecar's stdout for protocol lines only.
- `pointstoken/` — Go binding and bytecode of `PointsToken` (`contracts/points/`).
- `config/cluster.example.json` — template for the distributed config.
- `scripts/`     — older helper scripts from before the cluster, for building, resetting, starting, checking and recording the environment of the pre-cluster `hub`/`client` modes (`.sh` for Linux, `.ps1` for the Windows PC used in June). The measurements do not use them: `deploy/cluster` builds `perun-node` and starts it on the boards.
- `*_test.go`    — `hub_test.go` (the `local` scenario), `distributed_test.go`, `mesh_test.go`, `headon_test.go`, `payment_policy_test.go`, `preflight_test.go`, `uc1_*test.go`, `uc2_*test.go`, `asset_test.go`, `distributed_*_test.go`.

## The early demo without a network (`local` mode)

The `local` mode is the demo this folder began with, before the Raspberry Pi cluster
existed: one program on one computer holds the hub and all clients, and their messages
pass inside the program's memory, not over a network. It is kept but not used by the
thesis measurements; no thesis number comes from it. What it does and how to run it:
[`LOCAL-DEMO.md`](LOCAL-DEMO.md).
