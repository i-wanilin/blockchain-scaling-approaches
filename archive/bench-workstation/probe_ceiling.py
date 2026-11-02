#!/usr/bin/env python3
"""Throughput-ceiling probe for a local L2 sequencer.

WHY THIS EXISTS
---------------
The spike reported Arbitrum at 4.0 tx/s. That was 1/latency of a closed-loop
harness with one in-flight transaction:

    379 tx / 94.49 s = 4.01/s   and   1 / 249.3 ms = 4.01/s

With one request outstanding, throughput is an identity, not a measurement.

WHAT IT SWEEPS
    A = distinct sender accounts          -> breaks per-account nonce ordering
    D = concurrent submitters per account -> tests nonce pipelining
    P = OS processes                      -> escapes the Python GIL

CONCURRENCY MODEL
    One thread per IN-FLIGHT SLOT (A x D threads), split across P processes.

    Two defects this fixes, in order of discovery:
    1. One thread per ACCOUNT: since eth_sendRawTransaction blocks, that thread
       serialised its own sends, so D never varied real concurrency. Led to the
       withdrawn "4 tx/s is a per-account limit" conclusion.
    2. Threads only: CPython's GIL capped the GENERATOR near 280 tx/s while
       load average sat at ~1.0 core on a 16-core host. A plateau there measured
       the probe, not the node. Hence P.

    Accounts are partitioned across processes, so no two processes ever share an
    account and nonces cannot race.

READING THE RESULT
  * tps rises with A*D, load stays low        -> node not yet saturated
  * tps plateaus AND load ~= 1 core * P       -> GENERATOR bound, raise P
  * tps plateaus AND load is high, or errors  -> node bound; that's the ceiling

Usage:
  probe_ceiling.py <rpc> [--per-account N] [--accounts 1,2,4] [--depths 1,4]
                        [--procs P] [--label TEXT] [--out FILE]
"""
import sys, os, time, threading, itertools, statistics as st
import multiprocessing as mp
from web3 import Web3
from eth_account import Account

Account.enable_unaudited_hdwallet_features()
MNEMONIC = "test test test test test test test test test test test junk"
SINK = "0x000000000000000000000000000000000000dEaD"


def parse(argv):
    o = {"rpc": "http://127.0.0.1:8547", "per": 30, "accts": [1, 2, 4, 8],
         "depths": [1, 4], "procs": 1, "out": None, "label": "", "gasmult": 1,
         "gaslimit": 21000}
    rest, i = [], 0
    while i < len(argv):
        a = argv[i]
        if   a == "--per-account": o["per"] = int(argv[i + 1]); i += 2
        elif a == "--accounts":    o["accts"] = [int(x) for x in argv[i + 1].split(",")]; i += 2
        elif a == "--depths":      o["depths"] = [int(x) for x in argv[i + 1].split(",")]; i += 2
        elif a == "--procs":       o["procs"] = int(argv[i + 1]); i += 2
        elif a == "--gas-mult":    o["gasmult"] = int(argv[i + 1]); i += 2
        elif a == "--gas-limit":   o["gaslimit"] = int(argv[i + 1]); i += 2
        elif a == "--out":         o["out"] = argv[i + 1]; i += 2
        elif a == "--label":       o["label"] = argv[i + 1]; i += 2
        else: rest.append(a); i += 1
    if rest: o["rpc"] = rest[0]
    return o


O = parse(sys.argv[1:])
_lines = []
def emit(s=""):
    print(s, flush=True); _lines.append(s)


def key(i):
    return Account.from_mnemonic(MNEMONIC, account_path=f"m/44'/60'/0'/0/{i}")


def loadavg():
    try: return float(open("/proc/loadavg").read().split()[0])
    except Exception: return -1.0


def worker(rpc, idxs, depth, per, chain, gas_price, barrier, q, gas_limit=21000):
    """Runs in its own PROCESS. Owns `idxs` accounts exclusively."""
    w = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 300}))
    signed, ctr = {}, {}
    for i in idxs:                                   # pre-sign before the barrier
        a = key(i)
        n0 = w.eth.get_transaction_count(a.address)
        signed[i] = [w.eth.account.sign_transaction(
            {"to": SINK, "value": 1, "gas": gas_limit, "gasPrice": gas_price,
             "nonce": n0 + k, "chainId": chain}, a.key).raw_transaction
            for k in range(per)]
        ctr[i] = itertools.count()

    sub, e2e, errs = [], [], []
    marks = {"first": None, "last": None}
    lock = threading.Lock()

    def slot(i):
        ww = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 300}))
        raws, c = signed[i], ctr[i]
        while True:
            k = next(c)
            if k >= len(raws): return
            t0 = time.time()
            try:
                h = ww.eth.send_raw_transaction(raws[k])
                t1 = time.time()
                with lock:
                    sub.append((t1 - t0) * 1000)
                    if marks["first"] is None: marks["first"] = t0
            except Exception as e:
                with lock: errs.append(str(e)[:100]); continue
            try:
                ww.eth.wait_for_transaction_receipt(h, timeout=600, poll_latency=0.01)
                t2 = time.time()
                with lock:
                    e2e.append((t2 - t0) * 1000); marks["last"] = t2
            except Exception as e:
                with lock: errs.append(str(e)[:100])

    ths = [threading.Thread(target=slot, args=(i,)) for i in idxs for _ in range(depth)]
    barrier.wait()                                   # all processes start together
    for t in ths: t.start()
    for t in ths: t.join()
    q.put({"ok": len(e2e), "sent": len(sub), "sub": sub, "e2e": e2e,
           "errs": errs[:3], "n_err": len(errs),
           "first": marks["first"], "last": marks["last"]})


def cell(n_acct, depth, per, procs, chain, gas_price):
    procs = max(1, min(procs, n_acct))
    chunks = [list(range(n_acct))[p::procs] for p in range(procs)]
    chunks = [c for c in chunks if c]
    barrier, q = mp.Barrier(len(chunks)), mp.Queue()
    ps = [mp.Process(target=worker,
                     args=(O["rpc"], c, depth, per, chain, gas_price, barrier, q,
                           O["gaslimit"]))
          for c in chunks]
    for p in ps: p.start()
    res = [q.get() for _ in ps]
    for p in ps: p.join()

    ok = sum(r["ok"] for r in res); sent = sum(r["sent"] for r in res)
    firsts = [r["first"] for r in res if r["first"]]
    lasts = [r["last"] for r in res if r["last"]]
    span = (max(lasts) - min(firsts)) if firsts and lasts else 0
    tps = ok / span if span > 0 else 0
    sub = [x for r in res for x in r["sub"]]
    e2e = [x for r in res for x in r["e2e"]]
    n_err = sum(r["n_err"] for r in res)
    def q90(v): return sorted(v)[min(int(len(v) * .9), len(v) - 1)] if v else 0
    emit(f"{n_acct:>5} {depth:>6} {len(chunks):>6} {n_acct*depth:>7} {ok:>6}/{sent:<6} "
         f"{span:>7.2f} {tps:>9.2f} {tps/n_acct:>8.2f} "
         f"{st.mean(sub) if sub else 0:>8.1f} {st.mean(e2e) if e2e else 0:>9.1f} "
         f"{q90(e2e):>9.1f} {loadavg():>6.2f} {n_err:>5}")
    for r in res:
        if r["errs"]:
            emit(f"        ! {r['errs'][0]}"); break
    return tps


if __name__ == "__main__":
    mp.set_start_method("fork")
    w3 = Web3(Web3.HTTPProvider(O["rpc"], request_kwargs={"timeout": 180}))
    if not w3.is_connected(): sys.exit(f"cannot reach {O['rpc']}")
    CHAIN = w3.eth.chain_id
    # Headroom over the node's CURRENT gas price. Transactions are pre-signed,
    # so a base fee that rises under load would reject them mid-run with
    # "max fee per gas less than block base fee". Free ETH on a dev chain, and
    # gas PRICE is not what this probe measures - only throughput and latency.
    GP = w3.eth.gas_price * O["gasmult"]

    emit(f"# probe_ceiling  rpc={O['rpc']}  chain={CHAIN}  gasPrice={GP}")
    emit(f"# {O['label']}" if O["label"] else "#")
    emit(f"# per-account tx={O['per']}  accounts={O['accts']}  depths={O['depths']}  procs={O['procs']}")
    emit(f"# gasPrice x{O['gasmult']} headroom = {GP} (base fee rises under load)")
    emit(f"# gas limit per tx = {O['gaslimit']}")
    emit(f"# host: {os.cpu_count()} cores, idle load {loadavg():.2f}, started {time.strftime('%H:%M:%S')}")
    emit()
    emit("# tps   = confirmed-in-a-block per second  (the throughput figure)")
    emit("# /act  = tps per account")
    emit("# inflt = A x D, total transactions outstanding at once")
    emit("# sub   = mean ms eth_sendRawTransaction blocks for (~1 block interval)")
    emit("# load  = 1-min load average at end of cell, on a 16-core host.")
    emit("#         PLATEAU + LOW LOAD  -> node saturated (a real ceiling)")
    emit("#         PLATEAU + ~P cores  -> generator saturated (raise --procs)")
    emit(f"{'acct':>5} {'depth':>6} {'proc':>6} {'inflt':>7} {'ok/sent':>13} {'span_s':>7} "
         f"{'tps':>9} {'/act':>8} {'sub_ms':>8} {'e2e_mean':>9} {'e2e_p90':>9} {'load':>6} {'err':>5}")

    for d in O["depths"]:
        for a in O["accts"]:
            try: cell(a, d, O["per"], O["procs"], CHAIN, GP)
            except Exception as e: emit(f"{a:>5} {d:>6}  FAILED: {str(e)[:70]}")

    if O["out"]:
        with open(O["out"], "w") as f: f.write("\n".join(_lines) + "\n")
        print(f"\nwritten: {O['out']}")
