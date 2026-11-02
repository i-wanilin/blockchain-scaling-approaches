# Stack configuration on `bath0` (as measured)

Copied read-only from `bath0` on 2026-10-12 after the final series. Every run folder records the SHA-256
of these files in `stack-config.sha256`; the copies here match the hashes of the final UC1 (`5mx7`) and
UC2 runs byte for byte:

| File here | On `bath0` | SHA-256 (start) |
|---|---|---|
| `arbitrum/docker-compose.yaml` | `~/arbitrum/docker-compose.yaml` | `22391604…` |
| `arbitrum/SHA256SUMS` | `~/arbitrum/SHA256SUMS` (the snapshot tarballs) | `15bf6539…` |
| `arbitrum/sequencer_config.json` | `sequencer_config.json` inside `~/arbitrum/config.tgz` | `939ab077…` |
| `zksync/docker-compose.yml` | `~/zksync-ecosystem/thesis_local/docker-compose.yml` | `4e89b725…` |
| `zksync/general.yaml` | `~/zksync-ecosystem/thesis_local/chains/thesis_chain/configs/general.yaml` | `c3d1aedd…` |
| `zksync/SHA256SUMS` | `~/snap/SHA256SUMS` (the snapshot) | `fda6b8fd…` |

Not copied: the snapshot tarballs themselves (several GB; their hashes are in the two `SHA256SUMS`) and
the `zksync_server` arm64 binary (`~/zksync/bin/zksync_server`, SHA-256 `e77b1cc8…`, also recorded per
run). Perun needs no stack file: a fresh Anvil (`~/bin/anvil`, version in each manifest) per run.

The passwords and keys in these files (`passphrase`, `notsecurepassword`, the `0123…cdef` signing key)
are the public development defaults of the Arbitrum test node and of zkSync's local setup; the chains
are local to the cluster.

How the stacks were installed by hand and how `deploy/cluster` starts them from the snapshots:
`deploy/README.md` and `zksync-setup/`.
