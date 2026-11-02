# UC2 game tuning record

Acceptance band (owner): every player fires; 0.10 <= hits/shots <= 0.40 (pooled per match);
kills >= 1; first kill strictly after tick 60 (3 s). Seeds 1-20 and 42, 6 players, 20 Hz, 1200 ticks.
Frozen by the owner and not tuned: fire_cooldown_s 0.10, max_ammo 30, reload_s 2.5,
start_tokens 10000, shot_cost 1, hit_payment 5, respawn_s 6.0.
One parameter changed per step (relative to the previous row); stopped at the first row passing 21/21.
Command: `python -m arena.tune [--damage D] [--plausible-lane L]`.

| # | projectile_damage | plausible_lane | other FireParams | seeds passing | mean hit share | mean kills | mean payments/s | failures |
|---|---|---|---|---|---|---|---|---|
| 1 | 16 | 60 | defaults | 10/21 | 0.195 | 13.76 | 15.7 | first kill <= tick 60 (10 seeds), seed 6 share 0.077 |
| 2 | 12 | 60 | defaults | 17/21 | 0.236 | 12.10 | 16.5 | first kill early: seeds 2, 4, 16, 42 |
| 3 | 10 | 60 | defaults | 17/21 | 0.233 | 10.29 | 16.9 | first kill early: seeds 2, 4, 16, 42 |
| 4 | 8 | 60 | defaults | 19/21 | 0.256 | 9.00 | 17.5 | first kill early: seeds 4, 16 |
| 5 | 7 | 60 | defaults | 19/21 | 0.263 | 8.00 | 18.1 | first kill early: seeds 4, 16 |
| 6 | 6 | 60 | defaults | 20/21 | 0.284 | 7.48 | 18.8 | seed 20 share 0.405 |
| 7 | 6 | 50 | defaults | 18/21 | 0.306 | 7.76 | 18.9 | share > 0.4: seeds 6, 9, 17 |
| **8** | **6** | **40** | defaults | **21/21** | 0.304 | 7.71 | 18.0 | none |

Other FireParams stay at lead_ticks 6, long_range 240, burst 3-8, spec_p 0.02, release_ticks 4.
Hits to kill at damage 6: ceil(160 / 6) = 27 (two pinned tests re-pinned from 10 / 0.9 s to 27 / 2.6 s;
the lead-ticks bot test geometry moved from 75 to 55 units off the line for lane 40).

## Frozen values

projectile_damage 6; FireParams plausible_lane 40.0.

config_digest (SHA-256 of sorted JSON of Config and FireParams):
`777169e581c58faa94fbb1edce41823e038d00cf9bf5b06332755700b7e6d1d1`

`python -m arena.bound --json`:

```
{"S": 336, "ammo": 336, "virtual_stake": 1680, "hub_deposit": 8400, "player_need": 8736, "start_points": 10000, "respawn_ok": true, "ok": true}
```

## Per-seed table (frozen values)

| seed | shots | hits | hit share | kills | first kill tick | min shots/player | payments/s |
|---|---|---|---|---|---|---|---|
| 1 | 677 | 221 | 0.326 | 8 | 392 | 28 | 15.0 |
| 2 | 821 | 294 | 0.358 | 10 | 239 | 38 | 18.6 |
| 3 | 929 | 153 | 0.165 | 3 | 314 | 48 | 18.0 |
| 4 | 737 | 261 | 0.354 | 8 | 88 | 49 | 16.6 |
| 5 | 787 | 270 | 0.343 | 9 | 172 | 39 | 17.6 |
| 6 | 831 | 246 | 0.296 | 8 | 597 | 66 | 17.9 |
| 7 | 946 | 236 | 0.249 | 7 | 470 | 65 | 19.7 |
| 8 | 934 | 222 | 0.238 | 6 | 117 | 81 | 19.3 |
| 9 | 875 | 283 | 0.323 | 8 | 99 | 66 | 19.3 |
| 10 | 868 | 280 | 0.323 | 9 | 290 | 55 | 19.1 |
| 11 | 744 | 296 | 0.398 | 10 | 188 | 61 | 17.3 |
| 12 | 815 | 273 | 0.335 | 10 | 310 | 60 | 18.1 |
| 13 | 900 | 258 | 0.287 | 8 | 434 | 70 | 19.3 |
| 14 | 945 | 256 | 0.271 | 8 | 110 | 51 | 20.0 |
| 15 | 867 | 198 | 0.228 | 5 | 350 | 49 | 17.8 |
| 16 | 810 | 304 | 0.375 | 10 | 143 | 62 | 18.6 |
| 17 | 685 | 244 | 0.356 | 8 | 193 | 17 | 15.5 |
| 18 | 850 | 197 | 0.232 | 5 | 347 | 38 | 17.4 |
| 19 | 803 | 194 | 0.242 | 4 | 219 | 65 | 16.6 |
| 20 | 811 | 302 | 0.372 | 9 | 121 | 75 | 18.6 |
| 42 | 813 | 257 | 0.316 | 9 | 331 | 51 | 17.8 |

Pooled over the 21 seeds: mean hit share 0.304, mean kills 7.71, mean payments/s 18.0.

## Offered load (a result, not a target)

Seed 42: 813 shots + 257 hits = 1070 payments in 60 s = **17.8 payments/s**.
