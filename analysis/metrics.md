# Result metrics: what the results script extracts

`goodput` (keys, file names, specs) is what the thesis calls throughput: payments
confirmed per second.

This list, fixed on 2026-10-16, says which numbers go into
`analysis/data/results.csv`, the table the thesis quotes, and where each one comes
from. The script `analysis/results_table.py` extracts exactly these.

## How to read this list

- **Scope:** the columns of the two final tables in the cluster spec
  (`docs/superpowers/specs/2026-09-21-pi-cluster-suite-design.md`, "Final UC1 series"
  and "UC2 series", §8). Sections A and B are those tables. Section C lists the numbers
  quoted in the text next to them, and section D lists what only the figures need.
- **Runs:** the 3 runs named in the first line of each final aggregate,
  `results/pi/uc1/aggregate-{perun,arbitrum,zksync}5mx7-w{10,100,1000}.txt` (27 runs) and
  `results/pi/uc2/aggregate-uc2-{perun,arbitrum,zksync}.txt` (9 runs).
- **Source** is the key path in each run's `summary.json`.
- **Per run** is how one number per run is made when the key holds several values (for
  example one per customer). "—" means the key is already one number.
- **Across runs:** mean ± sample sd (n − 1), min, max and the 3 run values, the same as
  `measure/aggregate.py`. Rows marked *range* differ: the spec quotes them as min–max
  over every player of every run (6 players × 3 runs = 18 values). For those rows the CSV
  gives min and max of the 18 values, leaves `sd` empty and says `range` in its `stat`
  column, so a range can't be mistaken for mean ± sd.
- **Units** stay as measured: UC1 latencies in s, UC2 latencies in ms. A *fraction* is
  a part of a whole between 0 and 1 (0.857 = 85.7 %). The CSV keeps it as stored in
  `summary.json`, and the thesis prints it as %. The code names these keys `…_share`.
- **Offered load is a setting, not a metric.** UC1: each of the 5 customers pays once
  per window, so `w1000`, `w100` and `w10` offer 5, 50 and 500 payments per second.
  UC2: the same 1070 payments in every run (seed 42), over a match of nominally 60 s:
  17.8 per second. The measured rate, `offered.per_s`, is 17.816–17.827 across the 9
  runs, because each match ran slightly longer than 60 s (the "stretch"). The script
  takes the load from the window name (UC1) and as 1070 payments, 17.8 per second (UC2),
  and checks it against `offered_rate_per_s`, `offered.payments` and `offered.per_s` in
  each run.

## A. UC1 final table: 9 metrics × 9 cells (3 systems × w1000, w100, w10)

| id | spec column | unit | source in `summary.json` | per run |
|---|---|---|---|---|
| `latency_p50` | latency p50 (succeeded) | s | `payment_latency_s.p50` | — |
| `latency_lb_p50` | latency p50, all payments (lower bound) | s | `payment_latency_lower_bound_s.p50` | — |
| `latency_p99` | latency p99 (succeeded) | s | `payment_latency_s.p99` | — |
| `goodput` | goodput: payments confirmed per second (column "goodput / offered per s") | 1/s | `headline.goodput_per_s` | — |
| `succeeded_share` | succeeded | fraction | `headline.succeeded_share` | — |
| `deadline_miss_share` | deadline miss | fraction | `deadline_miss_share` | — |
| `open_s` | Open | s | `open_s.c1` … `c5` | median of the 5 customers |
| `close_confirmed_s` | Close: on-chain close confirmed (Perun only) | s | `close_s.c1` … `c5` | median of the 5 customers |
| `withdrawal_submitted_s` | Close: withdrawal submitted (Arbitrum, zkSync) | s | `close_s.c1` … `c5` | median of the 5 customers |

For both Close rows the key is the same, `close_s`, and its meaning per system is in
the run's `close_definition`. The spec splits it into two columns, and so does this list.

Also carried per cell: `saturated` (how many of the 3 runs) and the run IDs.

## B. UC2 final table: 40 metrics × 3 systems

Latency is generated → confirmed. Guests are p1–p5, the host is p0 on `bath1`.

| id | spec row | unit | source in `summary.json` | per run |
|---|---|---|---|---|
| `guests_fire_p50` | latency p50, guests fire | ms | `latency_ms.guests.fire.generated_to_confirmed.p50` | — |
| `guests_hit_p50` | latency p50, guests hit | ms | `latency_ms.guests.hit.generated_to_confirmed.p50` | — |
| `guests_fire_p99` | latency p99, guests fire | ms | `latency_ms.guests.fire.generated_to_confirmed.p99` | — |
| `guests_hit_p99` | latency p99, guests hit | ms | `latency_ms.guests.hit.generated_to_confirmed.p99` | — |
| `host_fire_p50` | latency p50, host fire | ms | `latency_ms.host.fire.generated_to_confirmed.p50` | — |
| `host_hit_p50` | latency p50, host hit | ms | `latency_ms.host.hit.generated_to_confirmed.p50` | — |
| `host_all_p99` | latency p99, host all | ms | `latency_ms.host.all.generated_to_confirmed.p99` | — |
| `next_shot_miss_guests` | next-shot miss share, guests | fraction | `next_shot_miss_share.guests.share` | — |
| `next_shot_miss_host` | next-shot miss share, host | fraction | `next_shot_miss_share.host.share` | — |
| `guests_lb_p50` | latency p50, guests, all payments (lower bound) | ms | `latency_lower_bound_ms.guests.p50` | — |
| `goodput` | goodput: payments confirmed per second | 1/s | `goodput_per_s` | — |
| `succeeded` | succeeded (of 1070) | count | `outcomes.succeeded` | — |
| `unresolved` | unresolved | count | `outcomes.unresolved` | — |
| `late` | late | count | `outcomes.late` | — |
| `succeeded_share` | the same three as fractions of 1070, so a quoted % comes from the CSV too | fraction | `outcomes.shares.succeeded` | — |
| `unresolved_share` | | fraction | `outcomes.shares.unresolved` | — |
| `late_share` | | fraction | `outcomes.shares.late` | — |
| `tick_lag_p50` | tick lag pooled p50 | ms | `match.tick_lag_ms.pooled.p50` | — |
| `tick_lag_p99` | tick lag pooled p99 | ms | `match.tick_lag_ms.pooled.p99` | — |
| `tick_lag_max` | tick lag pooled max | ms | `match.tick_lag_ms.pooled.max` | — |
| `stretch` | stretch | ratio | `match.stretch` | — |
| `open_s` | Open per player, median | s | `open.p0` … `p5` `.s` | median of the 6 players |
| `open_ledger_s` | Perun Open, ledger part, 1.4–2.5 (*range*, Perun only) | s | `open.p0` … `p5` `.ledger_s` | every player's value |
| `open_virtual_s` | Perun Open, virtual part, 0.6–3.1 (*range*, Perun only) | s | `open.p0` … `p5` `.virtual_s` | every player's value |
| `open_common_start_s` | Perun Open per player when all six players begin their Open at the same moment, 8.3 (Perun only; three extra runs of 2026-11-02, `deploy/experiments/probe-uc2-perun-openat.env`, `--open-at`) | s | `open` `t_virtual_open − t_start` in each player's `lifecycle.json` | median over the six players, then mean ± sd of the three runs |
| `goodput_inflight200`, `confirmation_p50_inflight200` | zkSync at the 100 ms window with 200 payments in flight per customer instead of 20: throughput 3.1 and median confirmation 297 (one run, 2026-10-04, spec U9; `deploy/experiments/archive/probe-zksync5-w100-if200.env`) | 1/s, s | `headline.goodput_per_s`, `headline.confirmation_s.p50` of that run | the run's value, n = 1 |
| `receive_lag_p99_inflight200` | the same run: 99th percentile of the sensor stream's delay at the customers (the shared 2.4 GHz band was not overloaded) | s | `receive_lag_s.p99` of that run | the run's value, n = 1 |
| `close_s` | Close per player, median | s | `close.p0` … `p5` `.s` | median of the 6 players |
| `close_s_range` | zkSync Close "range 2.0–39.7" (*range*; all three systems, it costs nothing) | s | `close.p0` … `p5` `.s` | every player's value |
| `close_virtual_settle_s` | Perun Close, virtual settles (*range*, Perun only; added 2026-10-16 for F3) | s | `close.p0` … `p5` `.virtual_settle_s` | every player's value |
| `close_ledger_settle_s` | Perun Close, ledger settle (*range*, Perun only; added 2026-10-16 for F3) | s | `close.p0` … `p5` `.ledger_settle_s` | every player's value |
| `gas_open` | gas, Open (median per player) | gas | `gas.open_median` | — |
| `gas_close` | gas, Close (median per player) | gas | `gas.close_median` | — |
| `gas_per_shot` | gas per shot (fire = hit) | gas | `gas.per_fire` (the script checks it equals `gas.per_hit`) | — |
| `cpu_bath0_mean` / `_max` | CPU, `bath0` | % | `cpu.bath0.mean_cpu_busy_pct` / `.max_cpu_busy_pct` | — |
| `cpu_bath1_mean` / `_max` | CPU, `bath1` | % | `cpu.bath1.…` (same two keys) | — |
| `cpu_others_mean` | CPU, other players, mean | % | `cpu.{bath2,bath4,bath5,bath6,bath7}.mean_cpu_busy_pct` | **mean** of the 5 boards |
| `cpu_others_max` | CPU, other players, max | % | `cpu.{…same 5}.max_cpu_busy_pct` | **max** of the 5 boards |
| `undervolted_bath4` / `6` / `7` | undervolted share | fraction | `throttle.bathN.undervolted_share` | — |

The *range* rows reproduce the spec (checked 2026-10-16): Perun ledger part
1.41–2.47 s, virtual part 0.63–3.14 s, zkSync Close 1.96–39.65 s, ledger settle
2.55–3.34 s ("≈ 3 s"). **One does not:** the spec says the virtual settles "take
0.4–1.5 s"; the 18 values give 0.25–1.50 s. The CSV carries the measured range, and
the spec sentence (§8, after the UC2 table) was corrected on 2026-10-16.

Checked against the spec while drafting this list (2026-10-16), all equal after
rounding:
- UC2: Open 13.96 / 1.67 / 2.51 s, host fire p50 17.9 / 177 / 40 240 ms, tick lag p50
  24.9 / 23.6 / 21.6 ms, CPU others 18.5 / 17.2 / 16.2 and 40 / 42 / 37 %, `bath0` max
  2.1 / 32 / 99.9 %, undervolted shares of `bath4`, `bath6` and `bath7`;
- UC1: Arbitrum `w1000` confirmation p50 0.086 s.

The script checked the rest.

## C. Quoted in the text next to the tables (decided 2026-10-16)

Taken, except the ETH fees: the local test chains' ETH prices don't carry over (the
spec says so), and gas measures the cost correctly.

UC1, per cell:

| id | quoted as | unit | source | per run |
|---|---|---|---|---|
| `confirmation_p50` | the system's own confirmation time, submission → confirmed | s | `confirmation_s.p50` | — |
| `gas_payment` | gas per payment: 0 / 21 000 / 87 742 | gas | `gas.payment_l2_median` | — |
| `late_executed` | payments given up at the drain deadline that executed anyway | count | `late_executed_total` | — |
| `cpu_bath0_mean` | `bath0` CPU, 2 % on Perun … 99–100 % on zkSync | % | `diagnostics.recorder.bath0.mean_cpu_busy_pct` | — |
| `undervolted_bathN` | undervolted share per Pi 3B customer (`bath4`–`bath7`; quoted as ranges) | fraction | `diagnostics.recorder.bathN.undervolted_share` | one row per board |
| `goodput_per_board` | `bath2` 99.7–100 per s, the Pi 3Bs 63–78 at Arbitrum `w10` | 1/s | `per_customer.cN.goodput_per_s`, board from `per_customer.cN.board` | one row per board |
| `receive_lag_p99` | stream receive lag (explains the Arbitrum `w100` rep1 stall) | s | `receive_lag_s.p99` | — |

UC2, per system:

| id | quoted as | unit | source | per run |
|---|---|---|---|---|
| `guests_submitted_p50` | zkSync ≈ 7 s from submission to confirmation | ms | `latency_ms.guests.all.submitted_to_confirmed.p50` | — |

**Checks, not metrics.** The script stops unless `reconciliation.ok_with_late` is true
in every run, and the CSV records the count as `reconciliation_ok_with_late` (27 of 27 for UC1, 9 of 9 for UC2). It also
checks the UC2 payment mix, the same in every run: 813 fire and 257 hit
(`offered.fire`, `offered.hit`). These are not metrics, like the offered load above.

## D. Needed only by the figures (decided 2026-10-16: take)

| figure | id | needs | unit | source |
|---|---|---|---|---|
| F3, gas panel, UC1 | `gas_open` | gas of Open (the customer's L1 deposit) | gas | `gas.deposit_l1_median` |
| F3, gas panel, UC1 | `gas_close` | gas of Close. Perun: the customer's on-chain close. Arbitrum, zkSync: the withdrawal on the L2 | gas | Perun `gas.close_l1_median`; rollups `gas.withdraw_l2_median` |
| F4, host | `host_fire_p99`, `host_hit_p99` | host fire and hit p99. The spec has only host all p99 | ms | `latency_ms.host.{fire,hit}.generated_to_confirmed.p99` |

For Perun the Close gas is the **customer's** side (`close_l1_median`), not the sensor's
(`close_sensor_l1_*`), so that each system's Close is paid by the same party: the
customer. F1, F2 and the rest of F3 and F4 use rows from A and B.

## E. The counterpart's cost (added 2026-10-16)

F3 shows what one customer (UC1) or player (UC2) pays. The other end of the channel, the
party that receives the payments, pays too, and that cost is not in F3. It goes into the
CSV and the text where it was measured:

| id | use case, system | counterpart | unit | source | per run |
|---|---|---|---|---|---|
| `sensor_gas_close_per_channel` | UC1, Perun | the sensor: its withdrawal from each customer's channel | gas | `gas.close_sensor_l1_per_channel` | — |
| `hub_gas_open_per_channel` | UC2, Perun | the hub: its approve + deposit into each player's ledger channel | gas | `logs/bath1-uc2-hub.out`, line `hub gas: ledger_open=N gas / 12 tx …` | N ÷ 6 |
| `hub_gas_close_per_channel` | UC2, Perun | the hub: its settlement of each player's ledger channel | gas | the same line, `ledger_settle=N gas / 6 tx` | N ÷ 6 |

- The hub rows are the only ones read from a **log**, not from `summary.json`: the UC2
  analysis never put the hub's gas into the summary. The script checks the line is
  there in each run with 12 and 6 transactions (2 per channel to open, 1 to settle;
  6 players). `--verify` does not cover them, since they are read from the raw log
  directly.
- At Open the UC1 sensor deposits nothing, so it pays no Open gas.
- **Not measured:** the UC1 sensor's withdrawal on the rollups. Its 0.045 ETH stayed
  on the L2 (`<run>/results/sensor-balance.json`), so the thesis states it as a limitation.
- UC2 rollups have no counterpart: every player both pays and receives, and its own
  withdrawal is its Close (table B).

## Decisions (2026-10-16)

1. **UC1 Close: two ids**, `close_confirmed_s` (Perun) and `withdrawal_submitted_s`
   (Arbitrum, zkSync). They measure different things. Perun's is the whole close,
   confirmed on the L1. The rollups' is only the withdrawal's submission; Arbitrum's
   challenge period and zkSync's batch proof were not measured. Under one id, Arbitrum's
   0.2 s next to Perun's 2.5 s would read as "Arbitrum closes faster". F3 shows the
   rollup bars hatched, labelled "submitted only".
2. **Range rows:** only the UC2 rows marked *range* in B (Perun Open ledger and
   virtual parts, Close per player, and the two Perun Close steps added later). Min and max over all 18 per-player values, `sd`
   empty, `stat` = `range`. The UC1 undervolted ranges in C are min–max over the 3 runs
   of one board, which the normal min and max columns already give (checked: they
   reproduce the spec's ranges, for example `bath4` on Perun `w10` 0.91–0.93).
3. **Section C:** taken, except the ETH fees.
4. **Section D:** taken.
5. **UC2 outcomes:** both counts (of 1070, as the spec quotes them) and fractions.

## F. The rollup's block spacing (added 2026-10-23)

The text explains why a rollup payment's confirmation grows with the load: the sequencer
makes blocks no faster than its minimum gap. This row measures the gap the payments
actually saw:

| id | use case, system | unit | source | per run |
|---|---|---|---|---|
| `block_interval_s` | UC1, Arbitrum and zkSync | s | `results/c*/ledger.csv.gz`, columns `block`, `t_confirmed`, `outcome` | (last − first `t_confirmed` of the succeeded payments) ÷ (number of distinct blocks among them − 1) |

- Like the hub rows, it is read from the raw ledgers, not from `summary.json`, so
  `--verify` does not cover it. Only blocks that carry at least one payment count; a
  block without payments is invisible to the clients. At low load the value is therefore
  larger than the sequencer's gap; when every block carries payments, it is the gap.


## G. The payment deadline each system needs (added 2026-10-27)

In the runs the payment deadline is the window: a payment must be confirmed before the next
window begins. The deadline changes nothing in a run (the sensor streams on whether a payment
is on time or not), so any other payment deadline can be applied to the same payments
afterwards. These rows give, for each UC1 cell, the payment deadline that a given share of the
windows still misses:

| id | use case, system | unit | source | value |
|---|---|---|---|---|
| `deadline_for_miss_5pct`, `deadline_for_miss_1pct` | UC1, all three | s | `results/c*/ledger.csv.gz`, columns `outcome`, `t_intended`, `t_confirmed` | the smallest payment deadline d at which at most 5 % (1 %) of the windows are missed: not succeeded, or `t_confirmed − t_intended` > d |
| `miss_share_floor` | UC1, all three | fraction | the same | the windows whose payment did not succeed ÷ all windows: the share missed at any payment deadline |

- Stat `pooled`: `mean` is computed over the windows of the three runs together, `rep1` to
  `rep3` per run. A pooled value is not the mean of the three runs; for the stalled
  Arbitrum run at 50 per s (rep1) the two differ widely.
- Where `miss_share_floor` is above the share, no payment deadline reaches it, and the row
  `deadline_for_miss_*` is left out (and `rep` is empty for such a run).
- Read from the raw ledgers like F, so `--verify` does not cover it. `results_table.py`
  checks that the same ledgers give each run's `deadline_miss_share` at the window.

## H. When the sensor's earnings reach the L1 (Perun, UC1; added 2026-11-07)

Not in results.csv: `analysis/perun_sensor_close.py` writes `analysis/data/perun_sensor.csv`
(same columns), which the thesis's macro script reads as well.

| Metric | Meaning | Unit | Source | Per run |
|---|---|---|---|---|
| `sensor_withdraw_s` | the sensor's own withdrawal from a customer's channel, one L1 transaction after the customer's Close | s | `results/sensor/sensor-result.json`: `t_settle_end − t_settle_start` | median over the 5 channels |
| `sensor_money_back_s` | from the start of the customer's Close to the end of the sensor's withdrawal from that channel (includes the sensor's gas scan of the run's L1 blocks before the withdrawal) | s | `cN/lifecycle.json` `close.t_start` → `t_settle_end` | median over the 5 channels |
