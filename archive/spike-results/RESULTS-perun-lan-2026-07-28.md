# Perun hub three-machine Wi-Fi baseline — 2026-07-28

This is the first physical-LAN execution of the distributed Perun hub launcher.
It is retained as an engineering baseline, including the defects it exposed.
It is not presented as the final beta result.

## Reproducibility envelope

- Code: commit `8e6fbd04e5a6ad7c39c5e89830ab2e64fc1d3a14`
- Chain: native Windows Anvil v1.7.1, chain ID 31337, one-second blocks
- Network: trusted home 5 GHz Wi-Fi, subnet `192.168.178.0/24`
- Ingrid/hub: Windows, `192.168.178.22`, TCP 8545/8080/9000
- Alice: ASUS G16, native Windows binary, `192.168.178.35:9000`
- Bob: HP x360, Linux Mint 22.3, `192.168.178.27:9000`
- Bob → Ingrid pre-run ping: 5/5 received; mean 2.629 ms
- Workload: 379 sequential, closed-loop virtual-channel payments from Alice
  to Bob. Each `pay` waits for the peer-signed Perun update before returning.

The deployment used one ledger channel from each client to Ingrid and one
Alice–Bob virtual channel backed by those ledgers. Payment messages travelled
directly between Alice and Bob; Ingrid was not a per-payment relay.

## Observed result

Alice printed:

```text
distributed payments: 379 in 1.634564s => 231.9 payment/s, 4.313 ms/payment
```

All 379 requested updates completed. `4.313 ms/payment` is the arithmetic
closed-loop mean derived from total burst duration, not a percentile. This
version did not retain individual latency samples, so p50/p90/p99 cannot be
reconstructed after the fact.

Anvil continued mining one-second blocks after the payment burst; the retained
terminal capture covers blocks 214–418. This confirms that the local chain did
not crash during the run.

## Failed preparation attempt

An earlier Bob launch timed out after 90 seconds because Alice had not been
started. The old launcher required every configured peer to register inside a
hardcoded 90-second window. Bob's command was also piped through `tee` without
`pipefail`, which masked the native process's nonzero status.

That attempt is excluded from performance results.

## Shutdown defects exposed by the successful run

After the measurement line, both clients logged:

```text
Error de-registering channel from watcher: ... sub-channels present
recvLoop finished unexpectedly ... use of closed network connection
```

The measured payments had already completed. The substantive lifecycle defect
was that the distributed launcher exited without first defunding the virtual
channel and then settling its parent ledgers. The TCP message was teardown
noise caused by closing the bus.

Ingrid also logged two other classes of message:

- Authentication EOF messages coincided with raw TCP port probes. Port 9000 is
  an authenticated Perun protocol endpoint, not a generic TCP health endpoint.
  Future preflight checks use the HTTP manifest instead.
- Adjudicator subscription errors occurred when Anvil was stopped before
  Ingrid. The finalized launcher makes Ingrid stop automatically only after the
  channel lifecycle and result acknowledgements complete.

## Interpretation

The run is valid evidence that the independent Windows/Linux processes opened
a real TCP-backed virtual channel and completed a 379-payment Wi-Fi burst. Its
throughput figure is usable as a preliminary steady-state baseline.

It is not evidence of a clean end-to-end lifecycle, restart safety, percentile
latency, or repeatability across trials. Those claims are reserved for the
finalized beta campaign.
