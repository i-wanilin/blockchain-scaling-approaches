# docs/

## Which document to read

The main document is the **cluster spec**,
[`superpowers/specs/2026-09-21-pi-cluster-suite-design.md`](superpowers/specs/2026-09-21-pi-cluster-suite-design.md).
It describes the cluster, both use cases and how they were measured (§2–§7), the order
of work with every series and its results (§8), and the future work and known
limitations (§11).

Section 8 holds both superseded and final results. Only these tables are final:

- **UC1:** the table under "Final UC1 series (2026-10-11/12, `5mx7`)" in §8 step 3,
  with the text after it;
- **UC2:** the tables under "UC2 series (2026-10-06 …)" in §8, with the text after them.

Earlier tables in §8 are superseded. The thesis does not quote the spec at all: it takes
every number from [`../analysis/data/results.csv`](../analysis/data/results.csv),
which reads only the final runs (metric list:
[`../analysis/metrics.md`](../analysis/metrics.md)).

`goodput` (keys, file names, specs) is what the thesis calls throughput: payments
confirmed per second.

## The other files

| Path | What it is |
|---|---|
| [`superpowers/specs/2026-09-03-l2-benchmark-design.md`](superpowers/specs/2026-09-03-l2-benchmark-design.md) | The parent spec: research question, metrics, use cases, the claims the thesis may make. The cluster spec builds on it. |
| [`../archive/plans/`](../archive/plans/) | The implementation plans of the cluster work, executed and archived. |
| [`figures/uc2/`](figures/uc2/) | Stills of the UC2 tank game. The result figures are in [`../analysis/figures/`](../analysis/figures/). |

The status report of 2026-10-13 (the setup, both use cases step by step, the results at
that date) is kept as a historical record in
[`../archive/history/report-2026-10-13.md`](../archive/history/report-2026-10-13.md).

The folder name `superpowers/` comes from the tool that generated the first specs. Many
files link into it, so it keeps its name.

## Thesis proposal

`proposal.pdf`: the bachelor's thesis proposal, accepted on 4 June 2026. The measurements
depart from it where the thesis says so (Chapter 5, "Departures from the proposal").
