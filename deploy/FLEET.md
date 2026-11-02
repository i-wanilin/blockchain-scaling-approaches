# Fleet register

The physical boards of the Raspberry Pi cluster. The **card** carries the hostname,
the SSH identity and the configuration; the **board** carries the hardware. They are
recorded separately because cards can move between boards: on 2026-09-29 they did,
and only the boards' Wi-Fi hardware addresses showed what ran where. Every run's
manifest records each board's Wi-Fi address and serial, so a card swap cannot go
unnoticed.

Boards are stacked; **position 0 is at the top**, and each position holds the card
of the same number.

| Pos. | Card | Board | Wi-Fi hardware address | Board serial | SD card | Kernel (2026-09-29) |
|---|---|---|---|---|---|---|
| 0 | `bath0` | Pi 4 Model B Rev 1.5, 8 GB | `d8:3a:dd:b4:7e:66` | `100000007cf44eea` | 128 GB | 6.8.0-1065-raspi |
| 1 | `bath1` | Pi 3 Model B+ Rev 1.4, 1 GB | `b8:27:eb:8e:aa:01` | `eadbff54` | 128 GB | 6.8.0-1065-raspi |
| 2 | `bath2` | Pi 3 Model B+ Rev 1.4, 1 GB | `b8:27:eb:b8:2a:56` | `99ed7f03` | 32 GB | 6.8.0-1065-raspi |
| 3 | `bath3` | Pi 3 Model B Rev 1.2, 1 GB (**dead since 2026-10-05**) | `b8:27:eb:fd:2d:56` | `84a87803` | 32 GB | 6.8.0-1065-raspi |
| 4 | `bath4` | Pi 3 Model B Rev 1.2, 1 GB | `b8:27:eb:ad:32:57` | `9bf86702` | 32 GB | 6.8.0-1065-raspi |
| 5 | `bath5` | Pi 3 Model B Rev 1.2, 1 GB | `b8:27:eb:5f:5e:70` | `fd0a0b25` | 32 GB | 6.8.0-1065-raspi |
| 6 | `bath6` | Pi 3 Model B Rev 1.2, 1 GB | `b8:27:eb:2d:44:f6` | `5b7811a3` | 32 GB | 6.8.0-1065-raspi |
| 7 | `bath7` | Pi 3 Model B Rev 1.2, 1 GB | `b8:27:eb:72:55:d7` | `3d270082` | 32 GB | 6.8.0-1065-raspi |

All eight: Ubuntu Server 24.04.5 LTS, user `i_wanilin`, SSH by the WSL key.
`bath3` is dead since 2026-10-05 (*Out of service* below); the other seven ran
every final measurement.

## Roles in the final runs

| Board | UC1 (final series, 2026-10-11/12) | UC2 (series, 2026-10-06) |
|---|---|---|
| `bath0` | the stack under test (Anvil / Arbitrum / zkSync) | the stack under test |
| `bath1` | sensor; chrony time server | player 0 (host) and Perun hub; time server |
| `bath2` | customer | player 1 |
| `bath3` | idle (dead) | idle (dead) |
| `bath4` | customer | player 3 |
| `bath5` | customer | player 4 |
| `bath6` | customer | player 5 |
| `bath7` | customer (replaces `bath3`) | player 2 |

## Wi-Fi: mixed setup (final, since 2026-10-05/06)

The router (FRITZ!Box 6660) has one SSID on both bands. The final setup is mixed:

| Radio | BSSID | Boards | How pinned |
|---|---|---|---|
| 5 GHz | `02:00:00:00:00:02` | `bath1`, `bath2` (Pi 3B+) | `deploy/pin-5ghz.sh` (2026-10-05) |
| 2.4 GHz, channel 11 (2462 MHz) | `02:00:00:00:00:01` | `bath0` | `deploy/pin-24ghz.sh`; its 5 GHz link measured only 13 Mbit/s |
| 2.4 GHz, channel 11 | `02:00:00:00:00:01` | `bath4`…`bath7` (Pi 3B, 2.4 GHz only) | nothing to pin |

Every experiment file declares `WIFI_5GHZ_BOARDS="bath1 bath2"`, and pre-flight
checks each board's BSSID before every run. Before 2026-10-05 all boards were on
2.4 GHz; those series are archived (`deploy/README.md` §7).

**Pinning history.** A dual-band board (Pi 4, Pi 3B+) needs band + BSSID + the
driver option `roamoff=1`, or it drifts to the other radio (`deploy/README.md` §3).
On 2026-09-30 all three dual-band boards were found on 5 GHz after a reboot (`bath0`
for 17 hours, so the sustained runs and overnight zkSync tests of 2026-09-29/30
crossed the band bridge; no thesis data affected). That day
`deploy/wifi-pin-at-boot.sh` was installed on `bath0`, `bath1` and `bath2`. After
boot it checks the BSSID, and if it differs it reloads `brcmfmac_wcc`/`brcmfmac` and
reapplies netplan, up to three times (log: `journalctl -t wifi-pin-at-boot`).
**It was installed with its default, the 2.4 GHz BSSID.** On `bath0` that is right.
On `bath1` and `bath2`, now pinned to 5 GHz by netplan, it sees the wrong BSSID
after every boot and reloads the driver three times; netplan brings the board back
to 5 GHz each time. It then logs a `WARNING` and exits 1: the board ends on the
correct radio after a minute or two of needless reconnects. It was disabled there on
2026-10-13 (`sudo systemctl disable wifi-pin-at-boot` on `bath1` and `bath2`; checked
2026-10-15: disabled on both, still enabled on `bath0`, where the 2.4 GHz BSSID is
right).

## Software and power

**One kernel on all eight since 2026-09-30:** `6.8.0-1065-raspi`, with the kernel
packages held (`apt-mark hold`). Before, Ubuntu's automatic updates had left
`bath1` on 1064; it was upgraded and rebooted. Each run's manifest records the
kernel.

**Power (measured 2026-09-29).** Every board has its own supply, plugged directly —
no extension cables, no adapters. Under full load (four cores busy, flag sampled
every 2 s, and a fixed hashing job timed on every Pi 3 at once):

| Board | Model | Under load | Speed under load |
|---|---|---|---|
| `bath0` | Pi 4 | clean | full |
| `bath1` | Pi 3B+ | clean | full (about 1.3 GHz) |
| `bath2` | Pi 3B+ | borderline: undervolted in 3 of 29 samples, earlier 26 of 28 | full in the timed job |
| `bath3`…`bath7` | Pi 3B | undervolted in nearly every sample | **600 MHz**, about half speed (14.1 s for a job a 3B at 1.2 GHz would do in about 7 s) |

The cause is the model, not the supplies: several supplies and cables were tried
on the 3Bs, and all five behave the same, while both 3B+ boards (a newer power
circuit) run at full speed. `bath1` and `bath2` swapped supplies so that `bath1`,
which carries the time server, the UC1 sensor and the UC2 host, has the clean one;
the undervoltage moved with the supply. Accepted as it is: the boards are on loan
and no hardware is bought. How runs treat this: cluster spec §5 and §6.

**Heat.** Under full load `bath0` (bare, no heatsink) reached 81 °C within 90 s and
both 3B+ boards 66–69 °C; all three hit the soft temperature limit
(`get_throttled` bit `0x80000`). On 2026-09-29 the **two heatsinks of the defective
Pi 4 were moved onto `bath0`**: under the Arbitrum sustained run it stayed at about
56–60 °C and never reached the limit (cluster spec §8). The Pi 3s have no
heatsinks.

**Operating system settings, all eight boards (2026-09-29):**

- Wi-Fi power saving off, and switched off again whenever `wlan0` appears
  (`deploy/wifi-powersave-off.sh`); with it on, a ping between two Pi 3s took up
  to 1.1 s;
- automatic updates (`unattended-upgrades`, `apt-daily`, `apt-daily-upgrade`) off
  and snap refreshes held — an update interrupted by a power cut had left `bath1`'s
  package system half-installed;
- `sudo` without a password for `i_wanilin` (`/etc/sudoers.d/90-i_wanilin-nopasswd`),
  so that setup steps run from the workstation without a prompt per board;
- the user is in the `video` group, so `vcgencmd get_throttled` needs no `sudo`;
- **time from `bath1`** (since 2026-09-30): chrony on all eight; `bath1` follows
  the internet and serves `192.168.178.0/24` (stratum 8 when offline), the others
  follow `bath1` only. First offsets: 0.1 µs to 1.7 ms;
- one kernel, held (above).

**Undervolted even at light load (2026-10-15).** `bath4`, `bath6` and `bath7` read
`0x50005` (undervolted now) at a load of 0.2–0.5, `bath7` at 600 MHz. So the Pi 3Bs are
undervolted not only under full load, as measured on 2026-09-29, but also when nearly
idle.

**Power banks run dry.** On 2026-09-30 `bath5` dropped off the network because its
power bank was empty. A long run on a power-bank board can end the same way; the
pre-flight check catches a missing board, not one that dies mid-run.

**Installed by hand on `bath0` only (2026-09-29/30):** Docker with Compose, `liburing2`,
Anvil 1.7.1 at `/home/i_wanilin/bin/anvil` (Foundry's official arm64 release;
`deploy/cluster` calls it by this path, `ANVIL_BIN`, and every Perun run records
its SHA-256), and `perf`
(`linux-tools-$(uname -r)`) for profiling.

## Out of service

| Board | Fault | Evidence |
|---|---|---|
| `bath3`: Pi 3 Model B Rev 1.2, `b8:27:eb:fd:2d:56`, serial `84a87803` | **dead since 2026-10-05**, board power fault | Went offline during the interrupted UC1 mixed-Wi-Fi series of 2026-10-05. The red power LED stays dark even on a known-good supply. A reflashed card did not help. `bath7` (also a Pi 3B) took its place in UC2 (2026-10-06) and in the final UC1 series (2026-10-11/12); `bath3` is `IDLE_BOARDS` in every final experiment file. The 7 runs of the 2026-10-05 series that used it are not thesis data and are not in the repository. |
| Pi 4 Model B, 8 GB (the second one; never booted, so its Wi-Fi address is unknown; its two heatsinks now sit on `bath0`) | reads no SD card | (2026-09-29) Red LED steady, green only a flash at power-on. No card boots in it — not the Ubuntu card, not the bootloader recovery card — while the same cards boot in the other Pi 4. Candidate for a warranty claim. |

## Addresses

Fixed, set on each board by `deploy/set-static-ip.sh`, outside the router's DHCP
range (`.20`–`.200`): **`bath<N>` = `192.168.178.21<N>`**, so `bath0` = `.210` …
`bath7` = `.217`. Before that, the router handed out addresses that moved with every
card swap (e.g. the Pi 4 went from `.36` to `.44`), and its name lookup
(`bath<N>.fritz.box`) lagged behind.

## Reboots of `bath1` (power cable)

`bath1` (sensor, UC2 host, time server; clean power) restarted on its own five
times, with no shutdown in its journal (the log simply stops; no undervoltage flag,
no kernel error):

- on 2026-10-09/10, outside any run;
- on 2026-10-11 at about 23:33, 12 minutes into the stream of the zkSync UC1
  `w1000` run `20261011-230326`. The run failed (`RUN_FAILED`) and was replaced by
  `20261012-033531` (cluster spec §8 step 3);
- on 2026-10-12 at about 06:15, idle, after the series had ended;
- on 2026-10-12 at about 17:00 and again at about 17:19, both times during a Wi-Fi
  baseline run, right after the `bath2` ↔ `bath1` step (the baseline was repeated and
  the affected `bath4` → `bath1` pair measured again).

After each boot, `wifi-pin-at-boot` (installed 2026-09-30 with the 2.4 GHz BSSID)
spends about two minutes reloading the Wi-Fi driver on `bath1` before giving up with a
WARNING, since `bath1` is now pinned to 5 GHz; the board is barely reachable meanwhile.
Disabled on `bath1` and `bath2` on 2026-10-13 (`sudo systemctl disable wifi-pin-at-boot`).

**Cause found (2026-10-13): the power cable.** The owner swapped `bath1`'s cable at about
01:56. `bath1` then ran 12 h without a restart, while `bath2` restarted on its own at
06:51 with the same signature (the journal stops mid-task, no shutdown, no undervoltage
flag, no kernel error). The fault followed the cable/supply to `bath2`; that cable was
taken out of use.

**Result: no reboot since the swap.** Checked 2026-10-15 at 00:22: `bath1` up since
2026-10-13 01:56 (46 h), `bath2` up since 2026-10-13 14:08 (34 h), when it got
`bath3`'s cable in place of the faulty one. None of the reboots set an undervoltage
flag: the board lost power at once, which a broken cable explains and a weak supply
does not. No final measurement is
affected beyond the one replaced run. Nothing links the reboots to `wifi-pin-at-boot`,
which runs only after a boot.

**`bath1` undervoltage flag (seen 2026-10-26).** During the Arbitrum blob test, `bath1`'s
throttled flags read `0x50000`: undervoltage at some point since its last boot. They were
clean before. For any future run: reboot `bath1` first and check the replacement cable
of 2026-10-13. No existing data is affected (cluster spec §11).
