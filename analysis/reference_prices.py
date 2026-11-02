#!/usr/bin/env python3
"""Reference prices for the euro conversion of the measured gas (thesis, Chapter 5, Cost).

The gas each transaction used is measured on the local stacks; its price there says nothing
about real fees (the local L1 charges a few wei per gas). This script collects, from the
public networks, the prices a user would have paid over one stated period, so that the
thesis can convert gas into euros at one reference price per chain:

- ETH/EUR: the daily close on Kraken (or, with --eth-source coingecko, CoinGecko's daily
  price).
- Ethereum mainnet: base fee plus the median priority fee of the transactions in the
  block, one sample per step (public RPC, eth_getBlockByNumber).
- Arbitrum One: L2 base fee of the sampled block (Arbitrum ignores priority fees), and,
  from the first plain ETH transfers at or after the sampled block, the receipt's
  gasUsedForL1: the gas Arbitrum adds to a transfer for posting its data to the L1. The
  local stack charged next to nothing for that share (its L1 gas price is a few wei), so
  the measured 21000 gas of a payment lacks it.
- zkSync Era: base fee of the sampled block, and the gas used by the first plain ETH
  transfers at or after it, as a check against the gas measured locally.

The result is written to analysis/data/reference_prices_24m.json: every sample, and the
means over the whole period. The thesis does not use these means: analysis/cost_eur.py
reads the samples and takes the medians over the reference period, 21 October 2025 to
20 October 2026. The file in the repository was written by one run of

    python3 analysis/reference_prices.py --start 2024-11-01 --end 2026-10-21 --step-hours 24 \
        --eth-source kraken
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import statistics
import time
import urllib.request

RPC = {
    "ethereum": "https://ethereum-rpc.publicnode.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "zksync": "https://mainnet.era.zksync.io",
}
KRAKEN = "https://api.kraken.com/0/public/OHLC?pair=ETHEUR&interval=1440&since={a}"   # daily, last 720 days
COINGECKO = ("https://api.coingecko.com/api/v3/coins/ethereum/market_chart/range"
             "?vs_currency=eur&from={a}&to={b}")
STEP_S = 6 * 3600          # one sample every 6 hours
TRANSFERS_PER_SAMPLE = 3   # plain transfers read per sample (Arbitrum, zkSync)
SCAN_BLOCKS = 40           # blocks searched after a sample for plain transfers
OUT = "analysis/data/reference_prices_24m.json"


def rpc(chain, method, params, tries=8):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC[chain], data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": "thesis-reference-prices"})
    for i in range(tries):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=60))
            if "error" in r:
                raise RuntimeError(r["error"])
            return r["result"]
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(min(60, 3 * 2 ** i))   # public RPCs rate-limit bursts (HTTP 403/429)


def block(chain, n, full=False):
    return rpc(chain, "eth_getBlockByNumber", [hex(n), full])


def block_at(chain, ts, lo, hi):
    """The first block with timestamp >= ts (binary search between block numbers lo and hi)."""
    while lo < hi:
        mid = (lo + hi) // 2
        if int(block(chain, mid)["timestamp"], 16) < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def is_plain_transfer(tx):
    return tx.get("input") in ("0x", "") and int(tx.get("value", "0x0"), 16) > 0 and tx.get("to")


def transfers_after(chain, n):
    """Receipts of the first plain ETH transfers in blocks n, n+1, ... (at most SCAN_BLOCKS)."""
    found = []
    for k in range(n, n + SCAN_BLOCKS):
        for tx in block(chain, k, True)["transactions"]:
            if is_plain_transfer(tx):
                rc = rpc(chain, "eth_getTransactionReceipt", [tx["hash"]])
                if rc and int(rc["status"], 16) == 1:
                    found.append({"block": k, "hash": tx["hash"], "gas_used": int(rc["gasUsed"], 16),
                                  "gas_used_for_l1": int(rc["gasUsedForL1"], 16) if rc.get("gasUsedForL1") else None})
                if len(found) == TRANSFERS_PER_SAMPLE:
                    return found
    return found


def sample(chain, ts, lo, hi):
    n = block_at(chain, ts, lo, hi)
    b = block(chain, n)
    s = {"t": ts, "block": n, "block_time": int(b["timestamp"], 16), "base_fee_wei": int(b["baseFeePerGas"], 16)}
    if chain == "ethereum":
        # median priority fee (tip) of the block's transactions; eth_feeHistory for old
        # blocks counts as an archive request on the public node
        bf = s["base_fee_wei"]
        tips = []
        for tx in block(chain, n, True)["transactions"]:
            if "maxPriorityFeePerGas" in tx:
                tips.append(min(int(tx["maxPriorityFeePerGas"], 16), int(tx["maxFeePerGas"], 16) - bf))
            else:
                tips.append(int(tx["gasPrice"], 16) - bf)
        s["median_priority_fee_wei"] = int(statistics.median(tips)) if tips else 0
        s["gas_price_wei"] = s["base_fee_wei"] + s["median_priority_fee_wei"]
    else:
        s["gas_price_wei"] = s["base_fee_wei"]
        s["transfers"] = transfers_after(chain, n)
    return s


def mean(xs):
    return statistics.fmean(xs) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="first day, UTC, YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="day after the last day, UTC, YYYY-MM-DD")
    ap.add_argument("--step-hours", type=float, default=STEP_S / 3600, help="hours between samples (default 6)")
    ap.add_argument("--eth-source", choices=("coingecko", "kraken"), default="kraken",
                    help="ETH/EUR: CoinGecko (free API: last 365 days) or Kraken daily close (last 720 days)")
    ap.add_argument("--out", default=OUT, help=f"output file (default {OUT})")
    args = ap.parse_args()
    out_path = args.out
    step = int(args.step_hours * 3600)
    t0 = int(dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.timezone.utc).timestamp())
    t1 = int(dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.timezone.utc).timestamp())
    times = list(range(t0, t1, step))

    out = {"period": {"start": args.start, "end_exclusive": args.end, "step_s": step},
           "sources": {"rpc": RPC, "eth_eur": COINGECKO.format(a=t0, b=t1)},
           "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}

    if args.eth_source == "kraken":
        out["sources"]["eth_eur"] = KRAKEN.format(a=t0 - 86400)
        res = json.load(urllib.request.urlopen(urllib.request.Request(
            out["sources"]["eth_eur"], headers={"User-Agent": "thesis-reference-prices"}), timeout=60))["result"]
        candles = res[[k for k in res if k != "last"][0]]
        prices = [[c[0] * 1000, float(c[4])] for c in candles if t0 <= c[0] < t1]   # daily close
    else:
        prices = json.load(urllib.request.urlopen(urllib.request.Request(
            COINGECKO.format(a=t0, b=t1), headers={"User-Agent": "thesis-reference-prices"}), timeout=60))["prices"]
    out["eth_eur_daily"] = prices
    eur = [p for _, p in prices]
    out["eth_eur"] = {"samples": len(eur), "mean": mean(eur), "min": min(eur), "max": max(eur)}

    import os
    part = out_path + ".partial"
    done = json.load(open(part)) if os.path.exists(part) else {}
    for chain in RPC:
        if chain in done and done[chain].get("period") == out["period"]:
            out[chain] = done[chain]["data"]
            print(chain, "(from", part + ")", flush=True)
            continue
        latest = int(rpc(chain, "eth_blockNumber", []), 16)
        lo = block_at(chain, t0 - 86400, 1, latest)   # bracket: a day before the start
        with cf.ThreadPoolExecutor(3) as ex:
            samples = list(ex.map(lambda ts: sample(chain, ts, lo, latest), times))
        c = {"samples": samples, "n": len(samples),
             "gas_price_wei_mean": mean([s["gas_price_wei"] for s in samples])}
        if chain == "ethereum":
            c["base_fee_wei_mean"] = mean([s["base_fee_wei"] for s in samples])
            c["median_priority_fee_wei_mean"] = mean([s["median_priority_fee_wei"] for s in samples])
        else:
            tr = [t for s in samples for t in s["transfers"]]
            c["transfers_n"] = len(tr)
            c["transfer_gas_used_mean"] = mean([t["gas_used"] for t in tr])
            c["transfer_gas_used_median"] = statistics.median([t["gas_used"] for t in tr]) if tr else None
            if chain == "arbitrum":
                c["transfer_gas_used_for_l1_mean"] = mean([t["gas_used_for_l1"] for t in tr if t["gas_used_for_l1"] is not None])
        out[chain] = c
        done[chain] = {"period": out["period"], "data": c}
        json.dump(done, open(part, "w"))
        print(chain, {k: v for k, v in c.items() if k != "samples"}, flush=True)

    print("eth_eur", out["eth_eur"])
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1)
    os.remove(part)
    print("wrote", out_path)


if __name__ == "__main__":
    main()
