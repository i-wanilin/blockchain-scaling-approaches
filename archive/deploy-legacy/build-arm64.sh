#!/usr/bin/env bash
# build-arm64.sh — cross-compile / fetch everything the Raspberry Pis need,
# from the x86 laptop. Output lands in deploy/dist/arm64/ (git-ignored).
#
# Usage:
#   ./build-arm64.sh            # cross-compile the Go pieces (hub-demo)
#   ./build-arm64.sh --fetch    # additionally download the prebuilt arm64
#                               # release tarballs for foundry/anvil and
#                               # anvil-zksync (exact pinned versions)
#
# Verified on the dev laptop 2026-07-18:
#   GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build   -> PASS
#   hub-demo binary: ~14.3 MB (static aarch64 ELF, "not stripped";
#   -ldflags "-s -w" trims it to ~10 MB)
set -euo pipefail
cd "$(dirname "$0")"

REPO_ROOT="$(cd .. && pwd)"
DIST="dist/arm64"
mkdir -p "$DIST"

# Pinned versions — keep in sync with STATUS.md.
FOUNDRY_VERSION="v1.7.1"
ANVIL_ZKSYNC_VERSION="v0.6.11"

echo "== cross-compiling perun hub-demo (linux/arm64) =="
# CGO_ENABLED=0 -> fully static binary, no cross C toolchain needed;
# go-perun + eth-backend are pure Go, so this is safe.
( cd "$REPO_ROOT/perun/hub-demo" &&
  GOOS=linux GOARCH=arm64 CGO_ENABLED=0 \
    go build -ldflags "-s -w" -o "$OLDPWD/$DIST/hub-demo" . )
file "$DIST/hub-demo"
ls -lh "$DIST/hub-demo"

if [[ "${1:-}" == "--fetch" ]]; then
  echo "== fetching prebuilt arm64 release binaries =="

  # Foundry (anvil/forge/cast/chisel) — official linux_arm64 build exists
  # at exactly our pinned version:
  FOUNDRY_URL="https://github.com/foundry-rs/foundry/releases/download/${FOUNDRY_VERSION}/foundry_${FOUNDRY_VERSION}_linux_arm64.tar.gz"
  echo "-- $FOUNDRY_URL"
  curl -fSL --retry 3 -o "$DIST/foundry_${FOUNDRY_VERSION}_linux_arm64.tar.gz" "$FOUNDRY_URL"

  # anvil-zksync — official aarch64-unknown-linux-gnu build exists at
  # exactly our pinned version:
  AZK_URL="https://github.com/matter-labs/anvil-zksync/releases/download/${ANVIL_ZKSYNC_VERSION}/anvil-zksync-${ANVIL_ZKSYNC_VERSION}-aarch64-unknown-linux-gnu.tar.gz"
  echo "-- $AZK_URL"
  curl -fSL --retry 3 -o "$DIST/anvil-zksync-${ANVIL_ZKSYNC_VERSION}-aarch64-linux.tar.gz" "$AZK_URL"

  ls -lh "$DIST"
fi

echo "== done — artifacts in deploy/$DIST/ =="
echo "   (the ansible player-node role copies them to the Pis if present)"
