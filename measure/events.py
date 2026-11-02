"""A payment event: what the use-case code asks the payment client to pay.

JSON lines over a Unix socket, one object per line. The ID is derived from the
event (run, customer, window), so a retry or a resent line never pays twice.

UC1: `to` is an address and amount_wei is wei. UC2 (R2): `to` is a LOGICAL
recipient ("arena" or "p<k>"), which the payment client maps to an address,
and amount_wei carries points (token units), not wei; the field name is kept
so the ledger and the wire format stay the same for both use cases.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PaymentEvent:
    id: str
    to: str
    amount_wei: int
    t_intended: float   # when the window was received (the deadline clock starts)
    t_generated: float  # when the customer emitted the event

    def to_line(self) -> bytes:
        return (json.dumps(asdict(self), separators=(",", ":")) + "\n").encode()

    @classmethod
    def from_line(cls, line: bytes) -> "PaymentEvent":
        return cls(**json.loads(line))


def event_id(run: str, customer: int, window: int) -> str:
    return f"uc1/{run}/c{customer}/w{window}"
