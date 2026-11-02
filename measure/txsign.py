"""EIP-1559 transaction signing that is cheap enough for a Raspberry Pi.

Why not eth_account's sign_transaction: measured on a Pi 3B+ it takes 39 ms per
transaction, which caps one account below 30 tx/s and blocks the event loop while
it runs. Signing with coincurve (libsecp256k1) takes 5.4 ms, and RLP-encoding the
type-2 transaction directly and signing its hash with coincurve takes 0.82 ms.
The bytes are identical to eth_account's (see tests/test_txsign.py).
"""
from __future__ import annotations

import coincurve
import rlp
from eth_hash.auto import keccak


def _bytes(x) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    if isinstance(x, str):
        return bytes.fromhex(x[2:] if x.startswith("0x") else x)
    raise TypeError(f"expected bytes or hex string, got {type(x).__name__}")


class Eip1559Signer:
    def __init__(self, private_key: bytes):
        self._key = coincurve.PrivateKey(bytes(private_key))

    def sign(self, tx: dict) -> tuple[str, str]:
        """tx needs chainId, nonce, to, gas, maxFeePerGas, maxPriorityFeePerGas;
        value defaults to 0 and data to empty. Returns (raw tx hex, tx hash hex)."""
        to = _bytes(tx["to"])
        if len(to) != 20:
            raise ValueError("`to` must be a 20-byte address (contract creation is not supported)")
        fields = [tx["chainId"], tx["nonce"], tx["maxPriorityFeePerGas"], tx["maxFeePerGas"], tx["gas"],
                  to, tx.get("value", 0), _bytes(tx.get("data", b"")), []]
        for f in fields[:5] + [fields[6]]:
            if not isinstance(f, int) or isinstance(f, bool) or f < 0:
                raise ValueError(f"numeric transaction field must be a non-negative int, got {f!r}")
        digest = keccak(b"\x02" + rlp.encode(fields))
        sig = self._key.sign_recoverable(digest, hasher=None)
        r, s, v = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:64], "big"), sig[64]
        raw = b"\x02" + rlp.encode(fields + [v, r, s])
        return "0x" + raw.hex(), "0x" + keccak(raw).hex()
