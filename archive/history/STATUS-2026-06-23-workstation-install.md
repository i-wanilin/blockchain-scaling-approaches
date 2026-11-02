> **HISTORICAL** — install log of the June 2026 workstation environment (WSL2). Not the thesis setup; see the root README.

# Benchmarking Environment — Install & Build Status

BA thesis: use-case-driven comparison of L2 scaling families.
Environment: WSL2 Ubuntu 24.04, kernel 6.18, Docker Desktop backend.
Session scope: **install + verify-build only.** No long-running services were started.
Date: 2026-06-23.

> **Reproduce (2026-06-29):** run `./setup.sh` (or `make setup`) to reconstruct this
> environment from scratch at the pinned versions/commits below. **go-nitro is intentionally
> excluded** from the reproducible setup — it was evaluated as a contrast case (dormant since
> 2023) and dropped; the go-nitro notes below are kept for the record only. The go-perun hub
> demo is tracked separately under `perun/hub-demo/` (not part of `setup.sh`).

---

## TL;DR — what's ready

| # | Component | Build | Ready to run? |
|---|-----------|-------|---------------|
| 1 | Foundry / Anvil (global) | ✅ PASS | ✅ yes |
| 2 | Perun (go-perun + eth-backend + examples) | ✅ PASS | ✅ yes — incl. **EVM virtual-channel test passing** |
| 3 | go-nitro (Go + hardhat contracts) | ✅ PASS | ✅ yes (needs `mkcert` TLS cert before running the node) |
| 4 | Arbitrum nitro-testnode | ✅ all images pulled | ✅ yes — launch with `./test-node.bash --init` (not done this session) |
| 5 | zkSync anvil-zksync | ✅ binary runs | ✅ yes |

**Nothing is blocked.** All four frameworks + the local EVM build/verify cleanly. The only deferred items are *launching* services, which you asked me not to do unprompted.

---

## Host prerequisites (verified, not reinstalled)

| Tool | Version | Note |
|------|---------|------|
| Docker | 29.1.2 | + Compose v2.40.3 |
| Go | 1.26.4 | ≥1.21 ✓ |
| Node | v24.15.0 | ⚠️ newer than some tools support (see go-nitro) |
| Yarn | 4.17 global / 3.5.1 per-repo | go-nitro pins yarn 3.5.1 via corepack |
| npm | 11.12.1 | |
| Rust / cargo | **absent** | only needed for zkSync `zkstack`; not installed (not required for `anvil-zksync`) |
| gcc / make / jq / git / curl / build-essential | present | |

### Working-folder speed — GOOD ✅
`/home/i_wanilin/Blockchain-scaling-approaches` sits on `/dev/sdd`, the **native WSL2 ext4 virtual disk** — not a `/mnt/c` drvfs Windows mount. This is the correct, fast location for git/Go/Docker/npm I/O (a `/mnt/c` path would be ~5–20× slower for many-small-file ops). Measured write throughput: **4.0 GB/s** (`dd` O_DIRECT, 512 MiB). 933 GB free. No change recommended.

Disk used by this install: perun 20M · go-nitro 1.7G · nitro-testnode 87M (+ ~5 GB Docker images) · zksync 67M.

---

## 1. Foundry / Anvil — GLOBAL

- **Version:** forge / cast / anvil / chisel **1.7.1** (`4072e48705`, 2026-05-08)
- **Location:** `~/.foundry/bin` (was already installed; `foundryup` confirmed up to date)
- **Build:** ✅ `anvil --version`, `forge --version`, `cast --version` all OK
- **Role:** local EVM L1 that everything deploys against.
- **Friction:** none.

## 2. Perun (state channels) — `./perun/`

| Repo | Source | Pin | Build |
|------|--------|-----|-------|
| go-perun | hyperledger-labs | tag **v0.15.0** (`1204ffc`) | ✅ `go build ./...` + `go vet ./...` |
| perun-eth-backend | perun-network | tag **v0.6.0** (`3106abe`) | ✅ `go build ./...` |
| perun-examples | perun-network | HEAD (`6e975e7`) | ✅ `payment-channel` + `app-channel` build |

**Version note:** go-perun and perun-eth-backend are versioned *independently*. go-perun **v0.15.0** pairs with perun-eth-backend **v0.6.0** — confirmed against `perun-examples/payment-channel/go.mod`, which pins exactly `perun.network/go-perun v0.15.0` + `github.com/perun-network/perun-eth-backend v0.6.0`.

**Org note:** go-perun cloned from hyperledger-labs (as requested); eth-backend + examples from perun-network (the examples only exist there and their import paths point at `github.com/perun-network/...`). The two orgs are mirrors and Go module resolution (vanity path `perun.network/go-perun`) is identical either way.

### ⭐ Virtual-channel / EVM end-goal — DOABLE, already proven
- There is **no standalone virtual-channel *example app*** in perun-examples (the EVM examples are `payment-channel` (2-party direct) and `app-channel`; the rest target non-EVM chains: ckb/dot/icp/xlm).
- **BUT** the real EVM backend ships a working 3-party virtual-channel **test**: `perun-eth-backend/client/virtualchannel_test.go`.
  - `setupVirtualChannelClients` builds `[3]RoleSetup` named **`{"Alice","Bob","Ingrid"}`** — i.e. **Ingrid is the hub**.
  - Balances are structured as `InitBalsAliceIngrid`, `InitBalsBobIngrid`, `InitBalsAliceBob` → Alice⇄Ingrid and Bob⇄Ingrid ledger channels with a virtual Alice⇄Bob channel on top. Exactly the Alice–Ingrid–Bob hub topology.
  - Tests: `TestVirtualChannelOptimistic` and `TestVirtualChannelDispute`.
  - **I ran them: `ok github.com/perun-network/perun-eth-backend/client 1.477s` — PASS.** (Uses go-ethereum's in-process simulated backend, not Anvil.)
- Core API lives in `go-perun/client/virtual_channel*.go`.
- **Implication for the thesis:** your Alice–Ingrid–Bob virtual payment channel on the EVM backend is achievable. Starting point = adapt `virtualchannel_test.go` / the `payment-channel` example into a 3-node runnable demo, and point the eth backend at Anvil instead of the simulated backend.
- **Friction:** none. The misleading `go build ./...` "PASS" I first saw on the examples root was a check bug on my side (perun-examples is a multi-module repo with no root module); building each example dir individually works.

## 3. go-nitro (state channels) — `./go-nitro/`

- **Source:** statechannels/go-nitro `main` (`64c4ee9`). go.mod declares `go 1.21`, go-ethereum v1.12.0.
- **Go build:** ✅ `go build ./...` PASS; main binary `go build -o go-nitro .` PASS (~64 MB).
- **Contracts:** ✅ Yarn-Berry monorepo (`packages/nitro-protocol`, hardhat + typechain). `yarn install` PASS; its `afterInstall` hook auto-ran hardhat → **42 Solidity files compiled, 67 typings generated**. Artifacts in `packages/nitro-protocol/artifacts/` + `typechain-types/`.

### Friction / workarounds (thesis data)
1. **Build is barely documented.** The node readme only shows `go test ./...` and a TLS-cert prerequisite; no build steps. Had to derive the build from the `Makefile`, root `main.go`, and the `packages/*` Yarn workspaces.
2. **`go vet` fails (build does not).** Under Go 1.26, `go vet ./...` flags `node/engine/chainservice/eth_chainservice.go:495`: `slog.Logger.Error arg "err" should be a string or a slog.Attr`. This is a newer vet check catching an slog key/value misuse the original authors' Go didn't enforce. It is a lint diagnostic only — compilation and the binary are unaffected.
3. **Hardhat warns Node v24 is unsupported** ("currently using Node.js v24.15.0, which is not supported by Hardhat"). Compilation still succeeded. If it ever breaks, pin Node 18/20 via nvm for the contracts build.
4. **Yarn version:** repo pins `yarn@3.5.1`; `corepack enable` makes the right version activate automatically. No manual yarn downgrade needed.
5. **To *run* (not build) the node/tests:** a TLS cert is required — `tls/readme.md` says install `mkcert` and generate key+cert into `./tls/`. Not needed for the build verification done this session.
- **Net:** the expected "dormant-repo dependency hell" did **not** materialize — both the Go side and the contracts built on the first serious attempt. Worth stating explicitly in the thesis as a (mild) surprise.

## 4. Arbitrum Nitro — `./nitro-testnode/`

- **Source:** OffchainLabs/nitro-testnode `release` branch, `--recurse-submodules` (blockscout submodule present at `v1.0.0-2638-g74ada713b`).
- **Architecture:** Docker-orchestrated, **no local compile in simple mode.** `docker-compose.yaml` has ~24 services on image `nitro-node-dev-testnode`, which `test-node.bash` produces by **pulling** `offchainlabs/nitro-node:v3.9.6-91bf578` and retagging it (the `--dev` flag would instead build nitro + blockscout from source).
- **Verification (no launch):** pulled every required image to prove availability —
  - `offchainlabs/nitro-node:v3.9.6-91bf578` ✅ (3.57 GB) — the node image
  - `ethereum/client-go:v1.16.4`, `gcr.io/prysmaticlabs/prysm/{prysmctl,beacon-chain,validator}:v6.1.1`, `postgres:13.6`, `redis:6.2.6`, `minio`, `softwaremill/elasticmq-native:1.6.9` ✅ all OK
- **Friction:** a raw `docker compose pull` aborts with `pull access denied for nitro-node-dev-testnode` — *expected*, because that tag is created locally by `test-node.bash`, not published. Not a real problem; pulling the upstream `offchainlabs/nitro-node` image directly (as the script does) works.
- **To launch later (separate, will prompt first):**
  ```bash
  cd nitro-testnode && ./test-node.bash --init      # pulls + deploys + LAUNCHES full local L1+L2
  ```
  Sequencer RPC will be at `http://localhost:8547` (HTTP) / `:8548` (WS). ⚠️ `--init` wipes chain data and redeploys.

## 5. zkSync Era — `./zksync/`

- **Binary:** `anvil-zksync` **v0.6.11** (prebuilt `x86_64-unknown-linux-gnu` release; formerly `era-test-node`).
- **Location:** `./zksync/anvil-zksync`
- **Build/verify:** ✅ `./zksync/anvil-zksync --version` → `anvil-zksync 0.6.11`
- **Install choice:** used the standalone prebuilt binary rather than the `foundryup-zksync` installer, to avoid shadowing the global Foundry (forge/cast 1.7.1) with `forge-zksync`/`cast-zksync`. No Rust required.
- **Friction:** none.
- **Full local chain (optional, not installed):** a complete local zkSync (sequencer + contracts) via `zkstack` would require installing Rust + the `zkstack` CLI (`zkstackup`) and is heavier than Anvil-equivalent needs. `anvil-zksync` alone is the local-EVM-equivalent node and is sufficient for use-case benchmarks. Document only.

---

## What was deliberately NOT done
- No long-running services launched (no `anvil` daemon, no `./test-node.bash --init`, `anvil-zksync` only invoked with `--version`).
- Rust not installed (only `zkstack` would need it).
- Global Foundry untouched beyond `foundryup` (confirmed already latest).

## Suggested next steps
1. **Perun:** clone/adapt `virtualchannel_test.go` into a runnable 3-node Alice–Ingrid–Bob demo and repoint the eth backend from the simulated backend to a live **Anvil** instance.
2. **go-nitro:** generate the `mkcert` TLS cert into `./tls/`, then run the node / RPC client (`packages/nitro-rpc-client`).
3. **Arbitrum:** when ready, `./test-node.bash --init` and confirm the sequencer at `localhost:8547`.
4. **zkSync:** `./zksync/anvil-zksync run` to start the local node (default RPC `http://localhost:8011`) when benchmarking.
