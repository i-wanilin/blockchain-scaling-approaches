"""net — minimal TCP transport for the distributed (lockstep) mode.

One player = one OS process = one node ("one node = one entity", no
aggregation). Nodes exchange per-tick inputs over TCP, either fully
meshed or via a relay node. This package holds the wire protocol
(net/protocol.py) and the relay server (net/relay.py); the node itself
lives in arena/node.py so it can be started as `python -m arena.node`.

This is the layer that later runs across Raspberry Pis with real wire
latency (and `tc netem` in between) — the environment-realism axis.
"""
