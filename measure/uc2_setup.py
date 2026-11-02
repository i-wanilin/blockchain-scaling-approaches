"""UC2 rollup setup: the points token, deployed fresh for every run (untimed).

    python -m measure.uc2_setup --stack arbitrum|zksync --l1 URL --l2-http URL \
        --players 6 --points 10000 --fund-wei W --out DIR

In order, every step untimed setup:
  1. fund the arena (110), players 0..n-1 (111..) and the token deployer (119)
     on the L1 from the stack's funder (zksync.funder(), Arbitrum account 0);
  2. bridge 0.5 ETH to the deployer on the L2 (the stack's open_deposit);
  3. deploy PointsToken -- Arbitrum: a type-2 contract creation of the EVM
     artifact perun/hub-demo/pointstoken/PointsToken.bin; zkSync: the committed
     EraVM bytecode contracts/points/zksync/PointsToken.zbin (hash checked)
     through deploy/zk-token/deploy.js, the key handed over in a 0600 temp file;
  4. mintBatch([p0..p<n-1>], points) from the deployer;
  5. check every player's balanceOf == points and the arena's == 0.
Writes DIR/token.json and DIR/recipients.json (the file payclient --recipients reads).

--sample-transfers N (workstation measurement only, never part of a run): after
the setup, bridge ETH to p0 and send N transfer(…, 1) from p0, round-robin to the
arena and the other players, printing each eth_estimateGas and receipt gasUsed.
That is where zksync.TOKEN_PAYMENT_GAS comes from.
Keys are from the public dev mnemonic: local dev chains only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import tempfile

from eth_abi import encode
from web3 import Web3

from measure import arbitrum, keys, points as pts, zksync

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZBIN = os.path.join(REPO, "contracts", "points", "zksync", "PointsToken.zbin")
EVM_BIN = os.path.join(REPO, "perun", "hub-demo", "pointstoken", "PointsToken.bin")
ABI = os.path.join(REPO, "perun", "hub-demo", "pointstoken", "PointsToken.abi")
DEPLOY_JS = os.path.join(REPO, "deploy", "zk-token", "deploy.js")
MINT_BATCH = "0xb6afc4dc"        # mintBatch(address[],uint256)
DEPLOYER_L2_WEI = 5 * 10**17     # step 2: 0.5 ETH
STACKS = {"arbitrum": arbitrum, "zksync": zksync}


def recipients(n_players: int) -> dict:
    """Logical recipient -> address: arena = account 110, p<k> = account 111+k."""
    rec = {"arena": keys.account(keys.ARENA).address}
    rec.update({f"p{k}": keys.account(keys.player_index(k)).address for k in range(n_players)})
    return rec


def write_recipients(out: str, n_players: int) -> str:
    path = os.path.join(out, "recipients.json")
    with open(path, "w") as f:
        json.dump(recipients(n_players), f, indent=1)
    return path


def check_zbin(path: str = ZBIN) -> str:
    """SHA-256 of the zbin; refuses one that differs from the committed <path>.sha256."""
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(path + ".sha256") as f:
        want = f.read().strip()
    if digest != want:
        raise RuntimeError(f"{path}: sha256 {digest} != committed {want}")
    return digest


def mint_batch_data(addrs, amount: int) -> str:
    return MINT_BATCH + encode(["address[]", "uint256"], [list(addrs), amount]).hex()


def balance(l2, token: str, addr: str) -> int:
    return int(l2.eth.call({"to": token, "data": pts.balance_of_data(addr)}).hex(), 16)


def _send(m, w3, acct, tx):
    r = m._send(w3, acct, tx)
    return r[0] if isinstance(r, tuple) else r   # zksync._send returns (receipt, tx)


def _funder(m):
    return m.funder() if hasattr(m, "funder") else keys.account(keys.FUNDER)


def deploy_arbitrum(l2, deployer) -> dict:
    with open(EVM_BIN) as f:
        code = "0x" + f.read().strip().removeprefix("0x")
    r = _send(arbitrum, l2, deployer, {"from": deployer.address, "value": 0, "data": code})
    if r["status"] != 1 or not r["contractAddress"]:
        raise RuntimeError(f"token deployment reverted ({r['transactionHash'].hex()})")
    return {"token": r["contractAddress"], "tx": "0x" + r["transactionHash"].hex().removeprefix("0x"),
            "gas_used": r["gasUsed"]}


def deploy_zksync(l2_http: str, deployer) -> dict:
    check_zbin()
    fd, key_file = tempfile.mkstemp(prefix="zk-token-key-")   # mkstemp: mode 0600
    try:
        with os.fdopen(fd, "w") as f:
            f.write(deployer.key.hex())
        p = subprocess.run(["node", DEPLOY_JS, "--l2", l2_http, "--key-file", key_file,
                            "--zbin", ZBIN, "--abi", ABI], capture_output=True, text=True, timeout=600)
    finally:
        os.unlink(key_file)
    if p.returncode != 0:
        raise RuntimeError(f"deploy.js failed ({p.returncode}): {p.stderr.strip()}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def setup(stack, l1_url, l2_http, players, points, fund_wei, out) -> dict:
    m = STACKS[stack]
    l1, l2 = Web3(Web3.HTTPProvider(l1_url)), Web3(Web3.HTTPProvider(l2_http))
    os.makedirs(out, exist_ok=True)
    deployer = keys.account(keys.TOKEN_DEPLOYER)
    rec = recipients(players)
    funder = _funder(m)
    for addr in list(rec.values()) + [deployer.address]:                    # 1
        m.fund_on_l1(l1, funder, addr, fund_wei)
    m.open_deposit(l1, l2, deployer, DEPLOYER_L2_WEI)                        # 2
    d = deploy_arbitrum(l2, deployer) if stack == "arbitrum" else deploy_zksync(l2_http, deployer)  # 3
    token = Web3.to_checksum_address(d["token"])
    players_addr = [rec[f"p{k}"] for k in range(players)]
    r = _send(m, l2, deployer, {"from": deployer.address, "to": token, "value": 0,       # 4
                                "data": mint_batch_data(players_addr, points)})
    if r["status"] != 1:
        raise RuntimeError("mintBatch reverted")
    bad = {n: b for n, a in rec.items()                                       # 5
           if (b := balance(l2, token, a)) != (0 if n == "arena" else points)}
    if bad:
        raise RuntimeError(f"balances after mintBatch are wrong: {bad}")
    info = {"stack": stack, "token": token, "deployer": deployer.address, "deploy_tx": d["tx"],
            "mint_tx": "0x" + r["transactionHash"].hex().removeprefix("0x"),
            "deploy_gas": d["gas_used"], "mint_gas": r["gasUsed"], "points": points}
    with open(os.path.join(out, "token.json"), "w") as f:
        json.dump(info, f, indent=1)
    write_recipients(out, players)
    return info


def sample_transfers(stack, l1_url, l2_http, out, n) -> list:
    """Workstation measurement only: n transfers of 1 point from p0 (see module doc)."""
    m = STACKS[stack]
    l1, l2 = Web3(Web3.HTTPProvider(l1_url)), Web3(Web3.HTTPProvider(l2_http))
    with open(os.path.join(out, "token.json")) as f:
        token = json.load(f)["token"]
    rec = pts.load_recipients(os.path.join(out, "recipients.json"), 1)
    targets = [a for name, a in rec.items() if name != "p0"]
    p0 = keys.account(keys.player_index(0))
    m.open_deposit(l1, l2, p0, DEPLOYER_L2_WEI)
    rows = []
    for i in range(n):
        tx = {"from": p0.address, "to": token, "value": 0, "data": pts.transfer_data(targets[i % len(targets)], 1)}
        est = l2.eth.estimate_gas(tx)
        r = _send(m, l2, p0, tx)
        if r["status"] != 1:
            raise RuntimeError(f"transfer {i} reverted")
        rows.append({"i": i, "to": tx["data"][34:74], "estimate": est, "gas_used": r["gasUsed"],
                     "effective_gas_price": r["effectiveGasPrice"]})
        print(json.dumps(rows[-1]), flush=True)
    for k in ("estimate", "gas_used"):
        v = [x[k] for x in rows]
        print(f"{stack} {k}: min {min(v)} median {statistics.median(v)} max {max(v)}", flush=True)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stack", choices=sorted(STACKS), required=True)
    ap.add_argument("--l1", required=True)
    ap.add_argument("--l2-http", required=True)
    ap.add_argument("--players", type=int, default=6)
    ap.add_argument("--points", type=int, default=10000)
    ap.add_argument("--fund-wei", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sample-transfers", type=int, default=0, help="workstation gas measurement only")
    a = ap.parse_args(argv)
    print(json.dumps(setup(a.stack, a.l1, a.l2_http, a.players, a.points, a.fund_wei, a.out)), flush=True)
    if a.sample_transfers:
        sample_transfers(a.stack, a.l1, a.l2_http, a.out, a.sample_transfers)


if __name__ == "__main__":
    main()
