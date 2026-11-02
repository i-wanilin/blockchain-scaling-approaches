# zkSync UC1 measurement path — verified facts (Task 1, 2026-10-01)

Plan: `docs/superpowers/plans/2026-10-01-uc1-zksync-measurement-path.md`, Task 1.
Part A read `bath0` over SSH, **read-only** (no file edits, no container start/stop, no sudo).
Part B ran on the workstation (PC, WSL2) against the chain in `~/zksync-ecosystem/thesis_local`
(`block_commit_deadline_ms: 2000` there, so its latencies are not the Pi's).

## Facts

| # | Fact | Value found | Shown by | Verdict |
|---|---|---|---|---|
| F1 | Ecosystem directory, binary on `bath0` | Ecosystem `~/zksync-ecosystem` = `/home/i_wanilin/zksync-ecosystem` (`thesis_local/` + `zksync-era/`, the latter holding **only** `contracts/` and `etc/`, no source, no `.git`, no `core/target`). Binary **`/home/i_wanilin/zksync/bin/zksync_server`**, SHA-256 `e77b1cc838a0bb459509b5f7067b02ebdb318758f14061e08339a2740b2ad8a6` (185 065 688 bytes). Next to it `/home/i_wanilin/zksync/bin/zksync_server.a72`, SHA-256 `ba35cf80…dbf0` (the cortex-a72 build; not to be used). `liburing2` installed, 2.5-1build1 | `ssh bath0 'find ~ -maxdepth 4 -name "zksync_server*" -type f -exec sha256sum {} \;'`; `ls -la ~/zksync/bin ~/zksync-ecosystem/zksync-era`; `dpkg -s liburing2` | as expected (SHA matches; path is outside the ecosystem dir — Task 5 sets `ZK_BIN=/home/i_wanilin/zksync/bin/zksync_server`) |
| F2 | Server command line, working directory, environment | Working directory **`/home/i_wanilin/zksync-ecosystem/zksync-era`** (the server reads the compiled contracts from it). Command, with `C=/home/i_wanilin/zksync-ecosystem/thesis_local/chains/thesis_chain/configs`:<br>`/home/i_wanilin/zksync/bin/zksync_server --genesis-path $C/genesis.yaml --config-path $C/general.yaml --wallets-path $C/wallets.yaml --secrets-path $C/secrets.yaml --contracts-config-path $C/contracts.yaml`<br>**No `--components`** (default set), **no environment variables** (no `RAYON_NUM_THREADS`; the `RAYON_NUM_THREADS=1/2` runs of 2026-09-30 were one-off tests). Started with `setsid nohup … > ~/zk-*.log 2>&1 < /dev/null &`, after `docker compose start reth postgres` in `thesis_local`. Same form used for the workstation in Part B (workstation binary), up after 3 s | `bath0` holds no record (`~/.bash_history` has only the setup line; no `*zk*.sh`). Source: the Claude session transcript of 2026-09-29/30 (`~/.claude/projects/-home-i-wanilin-Blockchain-scaling-approaches/64050b3f-….jsonl`), every bath0 start uses exactly this line (2026-09-29T10:01Z … 2026-09-29T23:24Z). Cross-checked: (a) `bath0`'s server logs read config from exactly these five files (`sed … ~/zk-generic-ctrl2.log \| grep -oE "YAML file .[^ ]+" \| sort \| uniq -c`); (b) zkstack's own `server` command at pinned `ff5f519b11cff863edcfa0f75af10fea113806b0` (`zkstack_cli/crates/common/src/server.rs` lines 61–113: `push_dir(code_path)`, the same five flags in this order, `--components=` only when given) | as expected; **recorded from the transcript, to be confirmed by Task 5 Step 5** |
| F3 | Compose project, services, ports, volumes on `bath0` | Project **`thesis_local`**, file `/home/i_wanilin/zksync-ecosystem/thesis_local/docker-compose.yml` (identical in substance to the committed `zksync-setup/thesis_local/docker-compose.yml`; `docker compose ls -a`: `thesis_local exited(2)`). Services `reth` (`ghcr.io/paradigmxyz/reth:v1.8.2`, restart `unless-stopped`), `postgres` (`postgres:18`, `max_connections=1000`, `PGDATA=/var/lib/postgresql/data`), `zk` (profile `runner`, never started). Containers `thesis_local-reth-1`, `thesis_local-postgres-1`, both `Exited (0)` 29 h ago. Ports: **reth `127.0.0.1:8945→8545`** (yes, localhost only), postgres `127.0.0.1:5432→5432`. Volumes **`thesis_local_postgres-data`, `thesis_local_reth-data`** (+ bind `/home/i_wanilin/zksync-ecosystem/zksync-era/etc/reth/chaindata` → `/chaindata`) | `cat ~/zksync-ecosystem/thesis_local/docker-compose.yml`; `docker volume ls`; `docker ps -a`; `docker inspect thesis_local-reth-1 --format "{{json .HostConfig.PortBindings}} …"` | as expected |
| F3b | reth RPC flags on `bath0` (Q7 hardening) | Container command (`docker inspect … .Config.Cmd`): `node --dev --datadir /rethdata --http --http.addr 0.0.0.0 --http.port 8545 --http.corsdomain "*" --chain /chaindata/reth_config --dev.block-time 300ms`. So: `--http.addr 0.0.0.0` (inside the container; host binding limits it to 127.0.0.1), **`--http.corsdomain "*"` present**, **no `--http.api`** (reth's default HTTP API set applies), **no `--ws*` flags** (WS off). Task 5 must, when opening 8945 to the LAN: add `--http.api eth,net,web3`, drop `--http.corsdomain "*"`, change the port line to `0.0.0.0:8945:8545` (or the LAN address) | compose file above; `docker inspect thesis_local-reth-1 --format "{{json .Config.Cmd}}"` | as expected (needs the hardening in Task 5) |
| F4 | Server config on `bath0` | `general.yaml` (sha256 `c3d1aedd…a5a5`): `block_commit_deadline_ms: 20000`, `miniblock_commit_deadline_ms: 1000`, `max_nonce_ahead: 2500`, **`pubdata_sending_mode: BLOBS`**, `use_fusaka_blob_format: true` (restored after the pre-Fusaka test), `commitment_generator.max_parallelism: 10`, `http_port: 3050`, `ws_port: 3051`, debug `log_directives` as in the feasibility runs; no `l1_batch_commit_data_generator_mode` key. `general.yaml` is byte-identical to `general.yaml.before-da-tests` and `.before-par-tests`; `diff` against **`general.yaml.orig-2000ms` (present)** differs only in line 51 (`20000` vs `2000`). `.bak-nonce200` also kept. **No `RAYON_NUM_THREADS`** in `env`, `~/.bashrc`, `~/.profile`, `~/bin`, `thesis_local/.env` (empty), `/etc/environment` | `grep -nE "block_commit_deadline_ms\|…" general.yaml*`; `sha256sum general.yaml*`; `diff general.yaml general.yaml.orig-2000ms`; `env \| grep -i rayon; grep -rn RAYON …` | as expected |
| F5 | Snapshot files on `bath0` | **`/home/i_wanilin/snap/`**: `SHA256SUMS`, `chain.tgz`, `era-artifacts.tgz`, `postgres.dump`, `reth-data.tgz`. `sha256sum -c SHA256SUMS`: `postgres.dump: OK`, `chain.tgz: OK`, `reth-data.tgz: OK`. **`bath0`'s `SHA256SUMS` lists only these three**; `era-artifacts.tgz` is not in it, but its hash `bd2ffe0cda8e526f81fc9c46d914eb12fcd506f9d3ac7d4ad4ef9f7adb71afc6` equals the 4th line of the workstation's `deploy/dist/snapshots/zksync-feas-20260929/SHA256SUMS` | `ls -la ~/snap; cd ~/snap && sha256sum -c SHA256SUMS; sha256sum era-artifacts.tgz` | as expected, with a note: Task 5 should verify `era-artifacts.tgz` against the workstation's 4-line `SHA256SUMS` (bath0's copy is the older 3-line one) |
| F6 | Interface of the L2 API | **All interfaces (`0.0.0.0`)** for HTTP 3050, WS 3051 and health 3071. Config has only ports (`api.web3_json_rpc.http_port/ws_port`, `http_url: http://127.0.0.1:3050` is a URL hint, not a bind). Source default: `core/node/api_server/src/web3/mod.rs:175/180` bind `([0,0,0,0], port)`; `core/lib/config/src/configs/api.rs:82` (health) `0.0.0.0`. Observed: on `bath0` 2026-09-29 16:24 Z `ss -ltnp` showed `0.0.0.0:3050` (from the transcript); on the workstation today with the same config form, `ss -ltn` showed `0.0.0.0:3050`, `0.0.0.0:3051`, `0.0.0.0:3071` | source grep; workstation `ss -ltn \| grep -E ":(3050\|3051\|3071) "` | as expected (3050/3051 need no change for Q7); confirm on bath0 in Task 5 Step 5 |
| F7 | `bath0` throttle flags, last boot | `throttled=0x80000` (soft temperature limit **occurred** since boot; nothing current, no undervoltage, no arm-capped `0x20000`, no throttled-occurred `0x40000`). 45.2 °C now, load 0.07. Booted **2026-09-29 16:19:13** (up 1 d 19 h 34 min at 11:54 local) | `vcgencmd get_throttled; vcgencmd measure_temp; uptime; uptime -s` | as expected (`0x80000` is the bit the pre-flight already excepts) |
| F8 | Balances | L1 deployer `0x36615Cf3…c049`: 99999999999844999998892907461892907462 wei, i.e. **≈ 10²⁰ ETH**, not the 100 ETH the plan's fact table states; deployer L2: 0.999725 ETH at the first run (0.5 ETH less after `bridge_bench.js`'s final transfer, +3 ETH −0.3 ETH from its deposits/withdrawals). Accounts 100…106 (`0x8C32…d43D` … `0x4548…13f0`): **0 on L1 and 0 on L2** at the first probe run. The probe itself sends 1 wei transfers to index 100, so on the **workstation** chain index 100 now holds 16 wei+ on L2 (8 wei after run 1, 16 after run 2, more after run 3 and the trigger tx). The `bath0` snapshot is unaffected | `bench/zksync_uc1_probe.py`, F8 lines (run 1) | accounts as expected; **differs (harmless)**: the L1 funder holds ≈ 10²⁰ ETH, not 100 ETH. **Note**: the workstation chain's index 100 is no longer 0 on L2 |
| F9 | Chain IDs | L1 **9**, L2 **271** | probe `F9  L1 9 L2 271` | as expected |
| F10 | `eth_sendRawTransaction` answers before sequencing | Reply 0.017–0.035 s; receipts 1.07–1.86 s after the last of 5 sends (one L2 block per ~1 s, `miniblock_commit_deadline_ms: 1000`); funding: reply 0.024–0.076 s, receipt 1.15–1.26 s | probe F10 lines (3 runs) | as expected |
| F11 | tx hash = keccak(raw) for type 2 | `True` in all 3 runs (e.g. `0x58d2cec9…fb51`) | probe F11 | as expected |
| F12 | Error texts (all JSON-RPC `code: 3`, `data: '0x'`) | duplicate: **`known transaction. transaction with hash 0x32f6…d60e is already in the system`**; nonce too low: **`nonce too low. allowed nonce range: 8 - 2508, actual: 5`**; nonce too high: **`nonce too high. allowed nonce range: 8 - 2508, actual: 5005`**; fee below base: **`max fee per gas less than block base fee`**; insufficient funds: **`insufficient funds for gas + value. balance: 99924900799999992, fee: 100000000000000, value: 1000000000000000000000000`**. Stable prefixes for Task 2: `known transaction`, `nonce too low`, `nonce too high`, `max fee per gas less than block base fee`, `insufficient funds` | probe F12 lines (identical wording in all 3 runs, only numbers/hash differ) | as expected (texts new; the hash in the duplicate text is abbreviated with `…`) |
| F13 | `eth_getTransactionCount(pending)` counts the mempool | `latest 5 pending 8 sent up to 7` (run 1); `13/16/15`, `21/24/23` in runs 2–3 → pending = highest sent + 1 | probe F13 | as expected |
| F14 | WS `newHeads`, `eth_getBlockReceipts` | `eth_subscribe newHeads` returns a subscription id; a head arrives; `eth_getBlockReceipts` over WS: **ok**. **But an idle chain seals no L2 block**: run 1 waited 30 s for a head and timed out (`eth_blockNumber` stayed at `0xb0d` over 10 s with no traffic). The probe now sends one transfer after subscribing (deviation from the plan's script, see below) | probe F14 lines (runs 2–3); run 1 traceback | **differs (minor)**: no heads without traffic. For UC1 there is always traffic during the stream; anything that waits for a head on an idle chain (e.g. a readiness check) must not rely on it |
| F15 | Gas | `eth_gasPrice` **100 000 000** (10⁸ wei = `minimal_l2_gas_price`); `effectiveGasPrice` **100 000 000** with `maxPriorityFeePerGas=0`, `maxFeePerGas=2·gp` → effective = base fee, no tip. A transfer's `gasUsed` 131 557–132 274 (0.1 ETH from deployer to a fresh account). `eth_estimateGas` for a 1 wei transfer to index 100: **175 462–175 690** over 10 samples (runs 1, 3; run 2 175 462 flat) | probe F15 lines | as expected; suggests `PAYMENT_GAS` ≈ 2× 175 690 ≈ **350 000** (Task 3 decides) |
| F16 | Deposit and withdrawal calls (zksync-ethers 6.21.2, ethers 6.17.0) | Deposit (3×, from `0x36615Cf3…c049`): L1 type-2 tx **to `0xE965F94Cd784d274E6970F603576265B17CfA3f4`** (Bridgehub; `cast code` non-empty `0x60806040…`), selector **`0xd52471c1`**, decoded tuple `(271, 1000119263462500000, 0x36615Cf3…c049, 1000000000000000000, 0x, 454337, 800, [], 0x36615Cf3…c049)` → chain ID 271, `mintValue` 1.0001192634625 ETH, `l2Contract` = sender, `l2Value` = 1 ETH = amount, empty calldata, **`l2GasLimit` 454 337** (= `zks_estimateGasL1ToL2` result `0x6eec1`, checked with curl for sender = recipient, value 1 ETH, `gasPerPubdata` 800; same value for index 100), `l2GasPerPubdataByteLimit` **800**, no factory deps, `refundRecipient` = sender. `tx.value` = `mintValue`. L1 `maxFeePerGas` 1 000 000 001, tip 1 000 000 000, gas limit 360 844–432 057, gasUsed 283 742–341 321, status 1. Withdrawal (3×): L2 tx **to `0x…800A`** (cast prints `32778`), selector **`0x51cff8d9`**, input `withdraw(0x36615Cf3…c049)` (to self), **value 0.1 ETH** = amount, status 1, gasUsed 115 536–236 182. **zksync-ethers sent it as type `0x71` (EIP-712)**; a **type-2** `withdraw(address)` signed with `measure.txsign.Eip1559Signer` (index 195, 0.001 ETH, gas = 2× estimate 317 570) was also accepted: status 1, gasUsed 243 040, 1 L2→L1 log, 1.15 s. `cast sig`: `0xd52471c1`, `0x71623274`, `0x51cff8d9` (cast 1.7.1) | `node bridge_bench.js` (hashes printed); `cast tx <h> --rpc-url …8945`; `cast calldata-decode '<sig>' <input>`; `cast tx <h> --rpc-url …3050`; `cast sig …`; `cast code 0xe965… \| head -c 20`; type-2 check in Python (see report) | as expected; note: the reference client uses EIP-712 for the withdrawal, Task 3's type-2 form is verified to work |
| F17 | `mintValue − l2Value` vs base cost | `mintValue − l2Value` = **119 263 462 500 000** wei. `cast call <Bridgehub> 'l2TransactionBaseCost(uint256,uint256,uint256,uint256)(uint256)' 271 1000000001 454337 800 --block 39974` = **119 263 462 500 000** → **equal**. zksync-ethers prices with `overrides.maxFeePerGas \|\| overrides.gasPrice` (`adapters.js:578`), here 1 000 000 001. The result is the same for gas price 10⁹; it differs for 1 (113 584 250 000 000) and 10¹¹ (1 438 733 833 484 779), so the gas price argument matters and must be ≤ the tx's `maxFeePerGas` as in the plan | `cast call` as shown; `cast base-fee 39975` = 1, `cast gas-price` = 1 000 000 001 | as expected |
| F18 | Workstation latencies | Deposit: L1 inclusion seen 4.46–4.49 s, L2 credit seen 4.47–4.51 s after send (credit seen 0.01–0.02 s after the L1 receipt was seen; **both bounded by ethers' 4 s default polling**, so the true L1→L2 credit time is not resolved by this script — Task 3's own timing polls faster). Withdrawal receipt latency 1.93 / 0.82 / 1.92 s | `node bridge_bench.js` output | as expected (workstation figures, 2 s batch window) |

## Deviations from the plan's procedure (workstation only)

1. **`npm ci` fails**: `zksync-setup/bench-bridge/` has no `package-lock.json` (npm: "can only install with an existing package-lock.json"). Used `npm install` in a scratch copy of `package.json` + `bridge_bench.js` (resolved zksync-ethers 6.21.2, ethers 6.17.0); nothing written into the repo besides the two `console.log` lines.
2. **Docker Desktop could not publish host ports 8945 and 5432** on the workstation today (`ports are not available: exposing port TCP 127.0.0.1:8945 -> 127.0.0.1:0: /forwards/expose returned unexpected status: 500`, same for 5432, repeatable; other ports such as 18945/15432 worked). Workaround, nothing in `~/zksync-ecosystem` changed: a compose override in the session scratchpad published reth on `127.0.0.1:18945` and postgres on `127.0.0.1:15432`, and a small Python TCP forwarder in WSL listened on `127.0.0.1:8945 → 18945` and `127.0.0.1:5432 → 15432`. The server, probe, `bridge_bench.js` and `cast` therefore used the usual ports unchanged. Behaviour is unaffected (plain TCP relay); latencies include one extra local hop on the L1/DB path. This is a workstation issue only (bath0 publishes these ports natively).
3. **Probe script**: the WS check sends one transfer after subscribing, because an idle chain seals no block (F14). Otherwise the script is the plan's verbatim. Run from the worktree root with `PYTHONPATH=.` (the script imports `measure`).

## Raw probe output, run 1 (fresh chain state)

```
F9  L1 9 L2 271
F8  deployer L1 99999999999844999998892907461892907462 deployer L2 999725000000000000
F8  100 0x8C3229EC621644789d7F61FAa82c6d0E5F97d43D L1 0 L2 0
F8  101 0x9586A4833970847aef259aD5BFB7aa8901DDf746 L1 0 L2 0
F8  102 0x0e9971c0005D91336c1441b8F03c1C4fe5FB4584 L1 0 L2 0
F8  103 0xC4c81D5C1851702d27d602aA8ff830A7689F17cc L1 0 L2 0
F8  104 0x9c79357189d6aF261691ECF48de9A6bbF30438Fc L1 0 L2 0
F8  105 0xd96Eb0f2e106eA7c0a939e9C460a17ace65FeCFF L1 0 L2 0
F8  106 0x4548774216F19914493d051481FEb56246bC13f0 L1 0 L2 0
F10 funding: reply 0.076s, receipt 1.263s after the send
F11 hash==keccak(raw): True 58d2cec9def35c371b06e574c37aa518a74ba2cfc54d453f2db7e47aa211fb51 0x58d2cec9def35c371b06e574c37aa518a74ba2cfc54d453f2db7e47aa211fb51
F15 effectiveGasPrice 100000000 eth_gasPrice 100000000 gasUsed 132274
F10 reply 0.035s  receipt 1.071s after the last send  block 2826
F10 reply 0.028s  receipt 1.074s after the last send  block 2826
F10 reply 0.029s  receipt 1.078s after the last send  block 2826
F10 reply 0.025s  receipt 1.081s after the last send  block 2826
F10 reply 0.022s  receipt 1.084s after the last send  block 2826
F13 latest 5 pending 8 sent up to 7
F12 duplicate    : {'code': 3, 'message': 'known transaction. transaction with hash 0x32f6…d60e is already in the system', 'data': '0x'}
F12 nonce too low: {'code': 3, 'message': 'nonce too low. allowed nonce range: 8 - 2508, actual: 5', 'data': '0x'}
F12 nonce too high: {'code': 3, 'message': 'nonce too high. allowed nonce range: 8 - 2508, actual: 5005', 'data': '0x'}
F12 fee < base   : {'code': 3, 'message': 'max fee per gas less than block base fee', 'data': '0x'}
F12 no funds     : {'code': 3, 'message': 'insufficient funds for gas + value. balance: 99924900799999992, fee: 100000000000000, value: 1000000000000000000000000', 'data': '0x'}
F15 estimate min/max 175462 175690
F14 subscribe: {"jsonrpc":"2.0","id":1,"result":"0x1a90c20423db260536b0eecbc4d973b1"}
(TimeoutError waiting 30 s for the first newHeads message: idle chain)
```

## Raw probe output, run 3 (script as committed)

```
F9  L1 9 L2 271
F8  deployer L1 99999999999844999998892907461892907462 deployer L2 799698616900000000
F8  100 0x8C3229EC621644789d7F61FAa82c6d0E5F97d43D L1 0 L2 16
F8  101..106 L1 0 L2 0
F10 funding: reply 0.030s, receipt 1.149s after the send
F11 hash==keccak(raw): True de7b1639…43e9 0xde7b1639…43e9
F15 effectiveGasPrice 100000000 eth_gasPrice 100000000 gasUsed 131557
F10 reply 0.024s  receipt 0.004s after the last send  block 2836
F10 reply 0.020s  receipt 1.850s after the last send  block 2838
F10 reply 0.023s  receipt 1.854s after the last send  block 2838
F10 reply 0.019s  receipt 1.857s after the last send  block 2838
F10 reply 0.020s  receipt 1.861s after the last send  block 2838
F13 latest 21 pending 24 sent up to 23
F12 (same texts as run 1; ranges 24 - 2524)
F15 estimate min/max 175462 175690
F14 subscribe: {"jsonrpc":"2.0","id":1,"result":"0x9caedd0c9bbed52644f703bc5c0b768a"}
F14 trigger tx: sent
F14 eth_getBlockReceipts: ok
```

## Bridge benchmark output (workstation)

```
=== DEPOSIT L1->L2 (3 runs, 1 ETH each) ===
deposit tx 0x7c203949f9207c557735e3532a8a758f63a40113e474fe86e5874b39de5241ab
run 1: L1 gas=283742  L1 inclusion=4.49s  L1->L2 credited e2e=4.51s
deposit tx 0x014ce2ec4a8a78e54b70e25dc1619c0ee7913064c3576676482c528907fd6f1a
run 2: L1 gas=341321  L1 inclusion=4.46s  L1->L2 credited e2e=4.47s
deposit tx 0xfef5996a9e7722d2d5af5de9fe69f932d8811e544c5d92ca2e97f303d0f8398c
run 3: L1 gas=291390  L1 inclusion=4.48s  L1->L2 credited e2e=4.50s
=== WITHDRAWAL INITIATION L2->L1 (3 runs, 0.1 ETH) ===
withdraw tx 0xb985f5bb5e964329ce28dd3e209fc0ba613265c611c98291e8c3c2381993f161
run 1: L2 gas=236182  receipt latency=1.93s
withdraw tx 0xb2a5972d5db4a07796d9bc5105b1fb4dc5c1fb24bf7955abc504c74ce7696358
run 2: L2 gas=115536  receipt latency=0.82s
withdraw tx 0xbe42b3a79f81f8da5a2b3233081cfc32cb382033702384dcf5c2055e51ef9c0d
run 3: L2 gas=235823  receipt latency=1.92s
bench key funded on L2
```

## Task 5 (2026-10-01): `bath0` preparation (Checkpoint 2 cleared) and live confirmation

Changes on `bath0`, made with the owner's "go" for exactly these steps (Q6 a, Q7 a with hardening). Nothing else on `bath0` was changed.

1. **Compose file** `/home/i_wanilin/zksync-ecosystem/thesis_local/docker-compose.yml`, 2026-10-01 10:58 Z. Backup **`docker-compose.yml.orig-localhost`** (`cp -p`, 2206 bytes, sha256 `ed656f43a447136ceb2d9283b127ff0766d6fddc4f0f7a475a8116cabe660bae`). New file 2205 bytes, sha256 `4e89b7254a1b16689596c05459a6eeaa6b368a4c99aba9b1c283db6d003f3f96`. `diff` (exactly two lines):
   ```
   16c16
   <       - 127.0.0.1:8945:8545
   ---
   >       - 0.0.0.0:8945:8545
   25c25
   <     command: node --dev --datadir /rethdata --http --http.addr 0.0.0.0 --http.port 8545 --http.corsdomain "*"  --chain /chaindata/reth_config --dev.block-time 300ms
   ---
   >     command: node --dev --datadir /rethdata --http --http.addr 0.0.0.0 --http.port 8545 --http.api eth,net,web3 --chain /chaindata/reth_config --dev.block-time 300ms
   ```
   PostgreSQL stays `127.0.0.1:5432:5432`. 3050/3051 needed no change (F6, confirmed below). **Note:** `chain.tgz` itself contains the pre-Q7 `thesis_local/docker-compose.yml` (2206 bytes, 2026-08-14), so `deploy/cluster up zksync` unpacks `chain.tgz` with that member excluded; after three `up`s the compose file's sha256 is still `4e89b725…3f96`.
2. **PostgreSQL volume (Q6 a)**: `docker compose down` (removed the two exited containers and the network), `docker volume rm` + `create thesis_local_postgres-data`, `docker compose up -d postgres`, `dropdb --if-exists` (did not exist) + `createdb zksync_server_thesis_chain` (the database in `secrets.yaml` `server_url`), then `docker compose exec -T postgres pg_restore -U postgres -d zksync_server_thesis_chain --no-owner < ~/snap/postgres.dump` (same form as 2026-09-29): **started 10:58:56 Z, 135.3 s (2 min 15 s), rc 0, empty stderr**. Result: `miniblocks` 2712 rows (max 2711), `l1_batches` max 1121, database 518 MB. `docker compose stop -t 60 postgres` (clean shutdown checkpoint in the log). Packed raw: `docker run --rm -v thesis_local_postgres-data:/v alpine tar czf - -C /v . > ~/snap/postgres-vol.tgz` (via `.tmp` + `mv`): **152.8 s**, raw volume 1.0 GB, **`postgres-vol.tgz` 746 068 393 bytes**, written 11:03 Z.
3. **`/home/i_wanilin/snap/SHA256SUMS`**: one line appended, now 4 lines (sha256 of the file `fda6b8fd9ea642c55e44bf60c0a3770876203d13dd22b447052103d1da7ed196`):
   ```
   908b408f064d5401ef42628e84e08b0b201ca81f4bb070c4be7a9562ab07d604  postgres-vol.tgz
   ```
   `sha256sum -c SHA256SUMS`: all 4 OK in 11 s. `era-artifacts.tgz` stays out of it; `deploy/cluster` checks it against F5's hash `bd2ffe0c…afc6` and compares the unpacked tree with `tar --compare` (no differences, ~10 s).

Live confirmation with the stack up (`deploy/cluster up zksync`, 2026-10-01 11:18 Z and 11:27 Z):

| # | Fact | Value found | Verdict |
|---|---|---|---|
| F2 | Server command line, cwd | `/proc/<pid>/cmdline`: `/home/i_wanilin/zksync/bin/zksync_server --genesis-path $C/genesis.yaml --config-path $C/general.yaml --wallets-path $C/wallets.yaml --secrets-path $C/secrets.yaml --contracts-config-path $C/contracts.yaml` (C as in F2), cwd `/home/i_wanilin/zksync-ecosystem/zksync-era`, no environment; `server.pid` = the pgrep'd PID (setsid/nohup exec in place). Healthy ~3 s after start. **The server exits at once if reth does not answer when it starts** (first try: "Connection reset by peer" on `eth_call` to the Bridgehub; `up` now waits for reth's `eth_blockNumber` on 127.0.0.1:8945 first) | **confirmed** |
| F6 | Listening sockets | `ss -ltn`: `0.0.0.0:3050`, `0.0.0.0:3051`, `0.0.0.0:3071`, `0.0.0.0:8945`, `127.0.0.1:5432` | **confirmed** (8945 now LAN, 5432 localhost) |
| F3b' | reth after hardening | `docker inspect`: `…--http.port 8545 --http.api eth,net,web3 --chain …` (no `--http.corsdomain`), binding `0.0.0.0:8945`. From the workstation: `eth_chainId` = `0x9`; `debug_traceTransaction` → `-32601 Method not found`. The server still commits to the L1: batch 1122 (6 transfers) sealed after the 20 s deadline, commit tx `0xbfcba30e…665b` at 11:21:04 Z. Only warnings in the server log: one `eth_call … execution reverted` at start and `blob_base_fee_per_gas is too low` on a resend — both present in the older `~/zk-*.log` too | as required |
| F19 | Health endpoint | `curl http://127.0.0.1:3071/health` → `{"status":"ready","details":{…},"components":{…}}` — the top-level `status` comes first; `up` checks `^{"status":"ready"` | new |
| F20 | `up zksync` time | 234–238 s from start to server launched (verify 3 tarballs, era-artifacts compare, chain.tgz, reth-data and the 746 MB PostgreSQL volume), healthy 3 s later: **237–241 s** in all (3 ups) | new; `RPC_WAIT_S` 300 is enough (it counts from the server start) |
| F21 | Config after `up` | `general.yaml` = snapshot's with line 51 `block_commit_deadline_ms: 20000`; sha256 `c3d1aedd…a5a5` — **byte-identical to F4's** feasibility config | as expected |
| F22 | Server stop | `pkill -TERM` ends the server immediately; the log shows no graceful-shutdown lines (it probably handles only SIGINT gracefully; not tested). Harmless here: every `up` restores the snapshot | note |
