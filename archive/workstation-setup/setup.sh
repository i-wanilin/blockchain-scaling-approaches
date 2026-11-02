#!/usr/bin/env bash
#
# Reproduce the L2-benchmark environment for the thesis (WSL2 Ubuntu, x86_64).
#
# Reconstructs everything from scratch at the exact versions/commits we validated
# (see STATUS.md). It does NOT include:
#   - the go-perun hub demo  (lives in perun/hub-demo/, tracked separately)
#   - go-nitro               (evaluated as a contrast case, then dropped — see STATUS.md)
#
# Idempotent: existing components are skipped. Run from the repo root:
#   ./setup.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Pinned versions / commits (validated 2026-06).
GOPERUN_TAG="v0.15.0"
ETHBACKEND_TAG="v0.6.0"
EXAMPLES_COMMIT="6e975e7d2f61a80d1d13b71330a2415086b82242"
TESTNODE_COMMIT="62f6cae30942f82958695697d3de8b4e1447ea7f"
ZKSYNC_VERSION="v0.6.11"
FOUNDRY_VERSION="${FOUNDRY_VERSION:-v1.7.1}"   # override with env; validated on v1.7.1

log() { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

clone_tag() {  # dir url tag
  if [ -d "$1/.git" ]; then echo "  $1 already present, skipping"; return; fi
  git clone --branch "$3" --depth 1 "$2" "$1"
}
clone_commit() {  # dir url commit
  if [ -d "$1/.git" ]; then echo "  $1 already present, skipping"; return; fi
  git clone "$2" "$1"
  git -C "$1" checkout --quiet "$3"
}

# --- Prerequisites (checked, not installed — see STATUS.md for the full list) ---
log "Checking prerequisites"
missing=0
for c in git go curl tar; do
  if have "$c"; then echo "  ok: $c"; else echo "  MISSING: $c"; missing=1; fi
done
have docker && echo "  ok: docker" || echo "  note: docker needed only for nitro-testnode"
[ "$missing" -eq 0 ] || { echo "Install the missing prerequisites and re-run."; exit 1; }

# --- 1. Foundry / Anvil (global, in ~/.foundry) ---
log "Foundry / Anvil"
if ! have foundryup && [ ! -x "$HOME/.foundry/bin/foundryup" ]; then
  curl -L https://foundry.paradigm.xyz | bash
fi
export PATH="$HOME/.foundry/bin:$PATH"
# Install the pinned toolchain. foundryup >=1.8 uses `--install <ver>`; older
# builds used `--version <ver>`. Try the new flag first, fall back to the old
# one, and finally to a plain (latest) install so this works on any foundryup.
foundryup --install "$FOUNDRY_VERSION" \
  || foundryup --version "$FOUNDRY_VERSION" \
  || foundryup
# Verify each binary on its own line: under `set -e` a missing binary aborts the
# script here instead of being swallowed by an `&&` chain (see git history).
anvil --version
forge --version
cast --version

# --- 2. Perun (go-perun + eth-backend + examples) ---
log "Perun (go-perun $GOPERUN_TAG, eth-backend $ETHBACKEND_TAG, examples)"
mkdir -p perun
clone_tag    perun/go-perun          https://github.com/hyperledger-labs/go-perun.git      "$GOPERUN_TAG"
clone_tag    perun/perun-eth-backend https://github.com/perun-network/perun-eth-backend.git "$ETHBACKEND_TAG"
clone_commit perun/perun-examples    https://github.com/perun-network/perun-examples.git    "$EXAMPLES_COMMIT"
( cd perun/go-perun          && go build ./... )
( cd perun/perun-eth-backend && go build ./... )
( cd perun/perun-examples/payment-channel && go build ./... )
echo "  perun builds OK"

# --- 3. Arbitrum Nitro testnode (Docker; not launched here) ---
log "Arbitrum nitro-testnode (pinned $TESTNODE_COMMIT)"
if [ -d nitro-testnode/.git ]; then
  echo "  nitro-testnode already present, skipping"
else
  git clone -b release --recurse-submodules https://github.com/OffchainLabs/nitro-testnode.git
  git -C nitro-testnode checkout --quiet "$TESTNODE_COMMIT"
  git -C nitro-testnode submodule update --init --recursive
fi
echo "  launch later (NOT now) with:  cd nitro-testnode && ./test-node.bash --init   # needs --block-time-style interval mining on the embedded chain"

# --- 4. zkSync anvil-zksync (standalone binary) ---
log "zkSync anvil-zksync $ZKSYNC_VERSION"
mkdir -p zksync
if [ -x zksync/anvil-zksync ]; then
  echo "  zksync/anvil-zksync already present, skipping"
else
  url="https://github.com/matter-labs/anvil-zksync/releases/download/${ZKSYNC_VERSION}/anvil-zksync-${ZKSYNC_VERSION}-x86_64-unknown-linux-gnu.tar.gz"
  curl -L -o /tmp/anvil-zksync.tgz "$url"
  tar -xzf /tmp/anvil-zksync.tgz -C zksync && rm -f /tmp/anvil-zksync.tgz
  chmod +x zksync/anvil-zksync
fi
./zksync/anvil-zksync --version

log "Done. Environment reconstructed (go-nitro intentionally omitted; hub demo separate)."
echo "Reminder: run a local L1 with interval mining for go-perun, e.g.  anvil --block-time 1"
