"""Facts the zkSync payment client relies on, checked on the workstation chain
before anything runs on the Pi (UC1 zkSync plan, Task 1). Prints one line per fact.

F8  balances: deployer on the L1, accounts 100..106 on L1 and L2 (must be 0 on L2)
F9  chain ids (L1 9, L2 271)
F10 does eth_sendRawTransaction answer before sequencing? (reply time vs receipt time)
F11 is the tx hash keccak(raw) for a type-2 transaction? (the submitter assumes it)
F12 exact error texts: nonce too high, nonce too low, duplicate, fee below base fee, insufficient funds
F13 does eth_getTransactionCount(pending) count mempool transactions?
F14 eth_subscribe newHeads on the WS port; eth_getBlockReceipts supported?
F15 eth_gasPrice, a transfer's gas estimate (10 samples), effectiveGasPrice vs maxPriorityFeePerGas
Usage: zksync_uc1_probe.py <l1 http> <l2 http> <l2 ws>
Local dev chains only: the keys are public.
"""
import asyncio, json, sys, time
import websockets
from eth_hash.auto import keccak
from web3 import Web3
from measure import keys
from measure.txsign import Eip1559Signer

DEPLOYER_KEY = "0x7726827caac94a7f9e1b160f7ea819f172f7b6f9d2a97f992c38edeab82d4110"
L1, L2, WS = sys.argv[1:4]
l1, l2 = Web3(Web3.HTTPProvider(L1)), Web3(Web3.HTTPProvider(L2))
dep = l1.eth.account.from_key(DEPLOYER_KEY)
print("F9 ", "L1", l1.eth.chain_id, "L2", l2.eth.chain_id)
print("F8 ", "deployer L1", l1.eth.get_balance(dep.address), "deployer L2", l2.eth.get_balance(dep.address))
for i in range(100, 107):
    a = keys.account(i).address
    print("F8 ", i, a, "L1", l1.eth.get_balance(a), "L2", l2.eth.get_balance(a))

probe = keys.account(195)                    # outside the run range; funded on L2 by the deployer
signer, s_dep = Eip1559Signer(probe.key), Eip1559Signer(dep.key)
gp = l2.eth.gas_price
base = dict(chainId=271, gas=500_000, maxFeePerGas=2 * gp, maxPriorityFeePerGas=0)

def send(sgn, tx):
    raw, h = sgn.sign(tx)
    t0 = time.time()
    try:
        got = l2.eth.send_raw_transaction(raw).hex()
        return raw, h, got, time.time() - t0, None
    except Exception as e:
        return raw, h, None, time.time() - t0, str(e)

n = l2.eth.get_transaction_count(dep.address, "pending")
raw, h, got, dt, err = send(s_dep, dict(base, nonce=n, to=probe.address, value=10**17))
t_reply = time.time()
r = l2.eth.wait_for_transaction_receipt(h, timeout=120, poll_latency=0.05)
print("F10", f"funding: reply {dt:.3f}s, receipt {time.time() - t_reply + dt:.3f}s after the send", err or "")
print("F11", "hash==keccak(raw):", ("0x" + got.removeprefix("0x")) == h, got, h)
print("F15", "effectiveGasPrice", r["effectiveGasPrice"], "eth_gasPrice", gp, "gasUsed", r["gasUsed"])
# F10: reply vs receipt for 5 transfers at once
n = l2.eth.get_transaction_count(probe.address, "pending")
sent = [send(signer, dict(base, nonce=n + k, to=keys.account(100).address, value=1)) for k in range(5)]
t_sent = time.time()
for _, h, _, dt, err in sent:
    rr = l2.eth.wait_for_transaction_receipt(h, timeout=120, poll_latency=0.05)
    print("F10", f"reply {dt:.3f}s  receipt {time.time() - t_sent:.3f}s after the last send  block {rr['blockNumber']}", err or "")
# F13: pending nonce with 3 transactions in the mempool
n = l2.eth.get_transaction_count(probe.address, "pending")
batch = [send(signer, dict(base, nonce=n + k, to=keys.account(100).address, value=1)) for k in range(3)]
print("F13", "latest", l2.eth.get_transaction_count(probe.address, "latest"),
      "pending", l2.eth.get_transaction_count(probe.address, "pending"), "sent up to", n + 2)
# F12: error texts
print("F12 duplicate    :", send(signer, dict(base, nonce=n + 2, to=keys.account(100).address, value=1))[4])
for _, h, _, _, _ in batch:
    l2.eth.wait_for_transaction_receipt(h, timeout=120)
print("F12 nonce too low:", send(signer, dict(base, nonce=n, to=keys.account(100).address, value=2))[4])
print("F12 nonce too high:", send(signer, dict(base, nonce=n + 5000, to=keys.account(100).address, value=1))[4])
print("F12 fee < base   :", send(signer, dict(base, maxFeePerGas=1, nonce=n + 3, to=keys.account(100).address, value=1))[4])
print("F12 no funds     :", send(signer, dict(base, nonce=n + 3, to=keys.account(100).address, value=10**24))[4])
# F15: gas estimate spread for a transfer to the sensor
est = [l2.eth.estimate_gas({"from": probe.address, "to": keys.account(100).address, "value": 1}) for _ in range(10)]
print("F15", "estimate min/max", min(est), max(est))

async def ws_checks():
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]}))
        print("F14 subscribe:", (await ws.recv())[:120])
        # An idle zkSync chain seals no L2 block (checked 2026-10-01: the first run
        # waited 30 s for a head in vain), so send one transfer to make a block.
        n = l2.eth.get_transaction_count(probe.address, "pending")
        print("F14 trigger tx:", send(signer, dict(base, nonce=n, to=keys.account(100).address, value=1))[4] or "sent")
        head = json.loads(await asyncio.wait_for(ws.recv(), 30))
        num = head["params"]["result"]["number"]
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "eth_getBlockReceipts", "params": [num]}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 30))
            if m.get("id") == 2:
                print("F14 eth_getBlockReceipts:", "ok" if "result" in m else m.get("error"))
                break
asyncio.run(ws_checks())
