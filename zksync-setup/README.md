# zkSync Era local chain — recovered setup artifacts

For the thesis, this directory records how the zkSync Era ecosystem was first initialised
(`zkstack`). The configuration as measured is in [`../deploy/stack-config/zksync/`](../deploy/stack-config/zksync/) ([`deploy/README.md`](../deploy/README.md) §5).

Recovered 2026-09-04 from the laptop `laptop` (`~/zksync-ecosystem/`), the host
that ran the 2026-07-20 zkstack spike. These files were **never committed anywhere** —
not on either remote, any branch, GitHub code search, or any gist. This directory
exists so that cannot happen again.

Measurements these produced are in [`../archive/spike-results/RESULTS-zkstack.md`](../archive/spike-results/RESULTS-zkstack.md),
including the 7 distinct failure classes that preceded the one working run.

| File | What it is |
|---|---|
| `clean-init.sh` | The single working `zkstack ecosystem init` sequence, out of 11 attempts |
| `clean-init-desktop.sh` | `clean-init.sh` adapted for the desktop: same sequence, foundry-zksync and node paths auto-detected |
| `bench-bridge/bridge_bench.js` | Spike-era, not used by the thesis measurements: deposit L1→L2 and withdrawal L2→L1 gas + latency on the workstation (the Open/Close phases). The cluster runs measure Open and Close through `measure/zksync.py` |
| `thesis_local/docker-compose.yml` | reth v1.8.2 L1 on `127.0.0.1:8945`, `--dev.block-time 300ms`; postgres 18 on `:5432` |
| `thesis_local/ZkStack.yaml` | Ecosystem config — note `wallet_creation: Localhost` here |
| `thesis_local/chains/thesis_chain/ZkStack.yaml` | Chain config — chain_id 271, EraVM, Rollup, NoProofs, and `wallet_creation: InFile` |

## Gotchas that cost real time

1. **`wallet_creation: InFile` lives in the CHAIN config, not the ecosystem config.**
   `clean-init.sh`'s own comment points at the wrong file. Setting it at the ecosystem
   level does not skip the broken "Distributing eth" step.
2. **`--dev` silently overrides `--l1-rpc-url`** with the compiled-in default
   `http://127.0.0.1:8545`. `clean-init.sh` therefore omits `--dev` entirely. Port 8945
   is deliberate: 8545 belongs to the Arbitrum testnode.
3. **`zkstack` prompts crash with `NotConnected` without a TTY** — hence
   `printf '\n%.0s' {1..60} | script -qec ...`.
4. **`ecosystem init` is not idempotent.** A partial deploy then fails with
   `Ownable: caller is not the owner` / `BridgeHubAlreadyRegistered`.
5. **`restart: unless-stopped`**, not upstream's `always` — otherwise reth resurrects on
   every daemon start and mines empty blocks at ~310 MB/day.

## Not included, deliberately

`configs/wallets.yaml` and `.env` hold 8 generated private keys. Local dev chain only,
but excluded so this repository stays publishable.

## Paths: reproduce the layout, do not rewrite

Every path inside these files is absolute to the laptop
(`/home/i_wanilin/zksync-ecosystem/...`). They are committed **as recovered**.

The desktop has the **same `$HOME`**, so the cheaper and safer route is to
recreate the layout rather than edit five path keys:

```sh
mkdir -p ~/zksync-ecosystem
git clone https://github.com/matter-labs/zksync-era.git ~/zksync-ecosystem/zksync-era
cp -r zksync-setup/thesis_local ~/zksync-ecosystem/
```

The recovered bundle also held `configs/wallets.yaml` and `.env`, which are not
distributed (see above); they have to be supplied anew.

Verified 2026-09-08 with the full recovered bundle: all of `link_to_code`, `chains`, `config`, `configs`,
`artifacts_path`, `contracts_path` and `default_configs_path` then resolve
untouched. Only `rocks_db_path` (`chains/thesis_chain/db/`) is absent — correctly,
since `clean-init.sh` step 2 deletes and regenerates it.

## The exact version pin — recovered, not lost

Earlier notes said the `zksync-era` commit was recorded nowhere and survived only
on the laptop. **That was wrong.** `zkstack` was installed with `cargo install
--git`, so cargo kept the bare repository at `~/.cargo/git/db/`. From it:

```
zkstack     v0.2.1-ff5f519b1     built 2026-09-02 22:31:53
zksync-era  ff5f519b11cff863edcfa0f75af10fea113806b0
contracts   101ab952b2c7b03e7648e30842f081042afc2d9a
```

Confirmed by construction: a clone at that commit checks out `contracts` at
exactly the submodule hash `zkstack --version` prints. The clone even succeeded
from the local cargo objects alone, so the desktop held the full source all
along.

## Rebuilding `zksync_server` — cheaper than feared

> This section is the **x86 (desktop) build**. The setup the thesis measured runs on
> `bath0` (Pi 4) with the arm64 `zksync_server` from `ARM64.md` (SHA-256 `e77b1cc8…`);
> the configuration as measured is in `deploy/stack-config/zksync/`, and how
> `deploy/cluster` starts it is in `deploy/README.md`.

The release binary did not survive and must be rebuilt. Measured on the desktop
(i7-10700K, 16 threads), **not** the ~4 GB/multi-hour job previously assumed:

```
cargo build --manifest-path ./core/Cargo.toml --release --bin zksync_server
  -> 13m 06s wall (175m CPU), binary 190 MB (130 MB stripped), target dir 4.3 GB
```

`libclang-dev`, `clang`, `cmake`, `pkg-config`, `libssl-dev` are prerequisites and
were already present.

⚠️ Built **without** `--features=rocksdb/io-uring`. That matches `zkstack`'s own
command (`zkstack_cli/crates/common/src/server.rs` — uring is opt-in, default
off), so it reproduces the spike, but it differs from the official `server-v2`
Dockerfile, which enables it. `liburing-dev` needs root to install.

How the sequencer was built for the Raspberry Pi 4, and which binary the runs used:
[`ARM64.md`](ARM64.md).

Only local dev credentials appear here: the reth genesis key `0x7726827c…` and
postgres `notsecurepassword`, bound to `127.0.0.1`.
