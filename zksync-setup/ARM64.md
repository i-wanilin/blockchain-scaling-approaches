# zkSync Era on the Raspberry Pi 4: the arm64 build of `zksync_server`

zkSync Era ran every zkSync measurement on `bath0`, the Pi 4 with 8 GB: `zksync_server`,
PostgreSQL and its L1 (reth), all on the board (cluster spec §8 step 1, `deploy/README.md`
§5). Matter Labs does not publish the sequencer for arm64, the Pi's processor
architecture, so we built it ourselves. This document says why that was needed, how the
binary was built and which binary the runs used.

## The binary on `bath0`

- Built on 2026-09-08 with Matter Labs' own, unmodified `docker/server-v2/Dockerfile`
  (below); `zksync_server --version` → `31.5.0-non-semver-compat`.
- Installed as `~/zksync/bin/zksync_server`; 185 065 688 bytes; SHA-256
  `e77b1cc838a0bb459509b5f7067b02ebdb318758f14061e08339a2740b2ad8a6`. The binary is not
  in this repository; every run's manifest records its SHA-256 (`images.zk_bin_sha256`).
- It needs the system library `liburing2` on the board.

Memory was not a limit on the Pi 4: the stack used at most 2.8 GB in the sustained test
run (cluster spec §8 step 1).

## Why we had to build it

| Image | Role | Architectures |
|---|---|---|
| `matterlabs/server-v2` | **sequencer (main node)** | **amd64 only** |
| `matterlabs/external-node` | read replica | amd64 **+ arm64** |

Checked over the 400 most recent `server-v2` tags (of 7 221): every one amd64, none with
a `-linux-arm64` variant, while `external-node` publishes `…-linux-arm64` for every build.

This is a release choice, not a technical limit. The exclusion is one line of the CI
matrix, in `.github/workflows/build-core-template.yml`:

```yaml
components:  [server-v2, external-node, snapshots-creator]
platforms:   [linux/amd64]
include:
  - components: external-node        # the ONLY arm64 exception
    platforms:  linux/arm64
```

- `docker/server-v2/Dockerfile` contains nothing specific to one architecture. It
  differs from the `external-node` Dockerfile only in which binaries it builds and
  copies.
- Both binaries are built from the same workspace, `./core/Cargo.toml`, and
  `zksync_external_node` is compiled on an arm64 runner (`matterlabs-ci-runner-arm`).
  So the shared code (RocksDB, the EraVM and zksync-protocol crates, the whole of core)
  compiles on arm64.
- Both base images, `ghcr.io/matter-labs/zksync-build-base` and `…/zksync-runtime-base`,
  are published for arm64 (checked with `docker manifest inspect`).

## How it was built

With the official Dockerfile, in Matter Labs' own arm64 base image, under QEMU on the
workstation:

```sh
docker buildx build --platform linux/arm64 --target builder \
  -f docker/server-v2/Dockerfile .
```

`--target builder` stops before the runtime stage, which only copies prebuilt contract
artifacts.

```
rustup: nightly-2026-08-09-aarch64-unknown-linux-gnu   installed OK
Finished `release` profile [optimized] target(s) in 304m 13s
rc=0

$ file zksync_server
ELF 64-bit LSB pie executable, ARM aarch64, version 1 (GNU/Linux),
dynamically linked, interpreter /lib/ld-linux-aarch64.so.1

$ docker run --platform linux/arm64 ... /zs --version
--- arch: aarch64 ---
zksync_server 31.5.0-non-semver-compat
```

The official Dockerfile builds with `--features=rocksdb/io-uring`; that is why the binary
needs `liburing2` (without it, it stops with `liburing.so.2: cannot open shared object
file`; the official runtime stage installs the library, `--target builder` does not).

The build took 5 h 04 min under emulation against 13 min 06 s for a native amd64 build.
This is the cost of emulating arm64 on an x86 processor and says nothing about the speed
of the binary on the Pi. Cross-compiling would be much faster if the build has to be
repeated.

## Source versions

Recovered from `~/.cargo/git/db/` on the workstation: `zkstack` was installed with
`cargo install --git`, so cargo kept the repository.

```
zkstack     v0.2.1-ff5f519b1     built 2026-09-02 22:31:53
zksync-era  ff5f519b11cff863edcfa0f75af10fea113806b0
contracts   101ab952b2c7b03e7648e30842f081042afc2d9a
```

A fresh clone at that commit checks out `contracts` at exactly the submodule hash that
`zkstack --version` reports.

`zkstack`'s own command builds `zksync_server` without `--features=rocksdb/io-uring`
(`zkstack_cli/crates/common/src/server.rs`, where io-uring is off by default). The arm64
binary on `bath0` follows the official Dockerfile instead and has io-uring on.
