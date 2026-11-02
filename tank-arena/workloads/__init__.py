"""workloads — the "datasimulator": one trace interface, many generators.

Per the methodology notes ("Research — workload realism & test environment",
§2), all workloads are driven through ONE common interface so that a
difference in benchmark results is never blamable on the load generator.
A workload is an iterator of timestamped, normalized events:

    {"t_ms": float,            # milliseconds since trace start, monotone
     "type": "message"|"payment",
     "sender": str,            # node/entity id
     "receiver": str,          # node/entity id ("*" = broadcast)
     "amount": int,            # token amount; 0 for pure messages
     "meta": {...}}            # generator-specific payload

* ``message`` — a state update / data message (a state-channel update or
  rollup calldata message, no value transfer).
* ``payment`` — a token transfer (micropayment / ERC-20 transfer).

Generators on this branch (stdlib, deterministic):

* workloads.game       — UC2: replay a tank-arena JSONL trace.

Only ``workloads.game`` exists here; it is exercised by tests/test_workloads.py
and is not used by the cluster measurements (UC2 payments come from
arena/node.py via arena/paybridge.py; UC1 from measure/). The earlier
platooning, pmu and CSV-replay generators are not on this branch.
"""

from __future__ import annotations

from typing import Any

MESSAGE = "message"
PAYMENT = "payment"

EVENT_KEYS = ("t_ms", "type", "sender", "receiver", "amount", "meta")


def make_event(t_ms: float, etype: str, sender: str, receiver: str,
               amount: int = 0, meta: dict[str, Any] | None = None) -> dict:
    """Build one normalized workload event."""
    if etype not in (MESSAGE, PAYMENT):
        raise ValueError(f"bad workload event type {etype!r}")
    return {
        "t_ms": round(t_ms, 4),
        "type": etype,
        "sender": sender,
        "receiver": receiver,
        "amount": amount,
        "meta": meta or {},
    }


def validate(event: dict) -> None:
    """Raise ValueError if `event` is not a normalized workload event."""
    for key in EVENT_KEYS:
        if key not in event:
            raise ValueError(f"workload event missing {key!r}: {event}")
    if event["type"] not in (MESSAGE, PAYMENT):
        raise ValueError(f"bad workload event type {event['type']!r}")
    if event["type"] == PAYMENT and event["amount"] <= 0:
        raise ValueError(f"payment with non-positive amount: {event}")
