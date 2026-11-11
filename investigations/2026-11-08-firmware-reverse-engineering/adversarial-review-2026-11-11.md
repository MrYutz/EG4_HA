# Adversarial review: hourly trigger and decision-tree gaps

2026-11-11. Offline review of the firmware map, the decision trees and the
November 7 telemetry. No equipment access, settings writes or firmware
operations. Firmware statements refer to the downloaded candidate images
(GridBOSS record 195, FlexBOSS ARM, converter record 253), not verified
readbacks of the installed controllers.

## Verdict

**The decision tree has no time axis.** It enumerates which predicates can open
the generator relay but never asks what fires on a schedule. The telemetry
shows the worst events recur hourly. Both controllers contain hourly routines
that the maps never tied to the incident. None of the routines traced so far
has a demonstrated path to the generator rejection, so the hourly trigger is
**still unidentified**.

## 1. The severe events are on an hourly train

Recomputed from
`../2026-11-07-generator-cycling/support-source-2026-11-07-1645-v1/complete-event-table.csv`
and `../2026-11-07-generator-cycling/telemetry-sanitized.csv`. Times are local
(UTC−04:00), host observation times.

| Slot | What happened |
|---|---|
| 22:35, 23:35, 00:35, 01:35 | Catalogued dropouts (events 2–5) |
| 02:35 | Data gap plus the uncatalogued EPS 159.3 V reading |
| 10:36:16–18 | No dropout; phase-lock frequency 62.3 then 65.01 Hz, generator voltage 238.2 → 233.5 V |
| 11:36, 12:36, 13:36, 14:36, 15:36 | Catalogued dropouts (events 17, 23, 28, 30, 34) |
| 16:36 | Major disturbance (EPS 95.9–282.9 V) |
| 17:37 | Major disturbance (EPS 48.9 V) |

- Every hourly slot with the generator connected and charging shows a
  disturbance.
- Overnight onsets marked by a GridBOSS read failure: 22:35:08.8, 23:35:13.0,
  00:35:16.6, 01:35:21.1. Period 3604.1 ± 0.5 s. Over the whole day the phase
  drifts from hh:35:07 to about hh:36:30.
- All three "qualified-zero" handoffs in `incident-recheck/README.md`
  (13:36:24, 14:36:26, 16:36:32) are on this train.
- 10:36:16 is the only phase-lock excursion in the capture while the generator
  was loaded and no dropout followed. It is on the train.
- At 16:36 the first anomaly is the master inverter's corrupt status word
  33280 at 16:36:29.641; GridBOSS read failures begin at 16:36:37.
- The day-one README dismissed an hourly cause because the morning cadence was
  irregular. That inference does not hold: two processes overlap (section 2).

## 2. Two mechanisms are conflated

- **Hourly trigger:** about 13 slots, including every severe event.
- **Reclose-then-drop chains:** events 8→9, 10→11, 13→14, 15→16, 23→24, 26→27
  begin 123–130 s after the previous one (the 120 s warm-up plus a few
  seconds). After the 30 s warm-up change, 14:36→14:40→14:42→14:45 looks
  similar at shorter spacing.

The "20 of 22 windows kept selector 2 throughout" figure in
`adversarial-review-2026-11-09.md` was computed across both populations and
should be redone per mechanism.

## 3. Hourly routines in the recovered firmware

### GridBOSS: traced, no path to the generator logic found

- `08032100` (called at `0803a646`, task 4) counts seconds in `u16[b604]`. At
  ≥3600, and only when the soft-clock minute is 21–39 (`080321c6..ca`), it
  resyncs the soft clock and zeroes the counter.
  - Strap `b77b==0`: reads the internal RTC (`080248da`) and converts
    (`08024a7a`).
  - Strap `b77b==1`: sets `b773=0`, `b776=2`; a clock-chip transaction is then
    run by `08032bc4` and stepped by the TIM5 interrupt `0802a970` (vector
    slot 66). No interrupt masking.
- `0801e240` (task 3 at `0801e890`) marks blocks dirty (`b70e |= 0xe0`) on each
  soft-clock hour change and writes them to external SPI flash
  (`0801d666 → 08017316 / 080171ae`). Own task, yields while waiting, no
  interrupt masking.
- The GridBOSS clock read 2026-11-08 14:53:28 against host 14:53:31 local, so
  its hour rollover is at hh:00, not hh:35. The flash save is not the train.
- The application never touches the independent watchdog. Software resets come
  only from the update handler `0800d4b2` and the vector-slot-17 handler
  `0802732a` (taken to be the supply-voltage detector).

### FlexBOSS ARM: hourly interrupts-off window, probably harmless

- `0801540c` has the same 3600-second counter (`ea6e`) and 21–39 minute gate.
  It is called from the supervisory task `08023340` immediately after the
  priority owner `08019500`.
- Strap `ec31==1` (sampled at `08029224` on what appears to be the I2C data
  pin): it calls `08023b30`, which reads at least seven registers from an I2C
  clock chip at address `0xA2`. Each read goes through `080114a4`, which
  disables interrupts (`080584c4`), bit-bangs the transaction and restores.
- Estimated blackout 0.3–0.5 ms per register read at an assumed 120 MHz
  (from delay-loop counts; not measured).
- The converter link is 19200 baud (0.52 ms per character), interrupt-driven,
  40-byte frames with a 16-bit checksum. A byte is lost only if the blackout
  exceeds one character time, which needs a processor clock of about 70 MHz or
  less. A damaged frame fails its checksum and is dropped, not misapplied.
- The soft clock is read by the charge and discharge time-window logic
  (`080175d8`, `08017ce0`, `0801843c`), GEN Boost (`08019088`) and `08019500`.
  The effect of an hourly clock step on those checks was **not traced**.
- `08019500` also holds a 3600-evaluation hold-off (`ea82`) and a
  1200-evaluation vote that set and clear bit 2 of request word `e974`. Run
  continuously this is a 4800-evaluation cycle, not 3600.

### Converter (record 253): permission cycle is about 300 s

- `907c6` is called only at word `91f59`, inside a task body at `91f03` that
  the Ghidra export never recovered.
- Registration `a10db..a10e0` creates it as task 2. Task 0 is the source task
  `97770`, task 1 the CAN task `a0a17`, task 4 the UART task `9b9f7`.
- The task runs its protection chain only on event bit 1. That event is posted
  at `bac5..bb4a` in RAM routine `b7aa` when `11dc8` bit 0 is set, which the
  retained `decision-tree/c28x-can/phase-cadence-native.json` shows is once
  per 360 units of the converter's internal angle.
- If 360 units is one electrical cycle (asserted by the cadence note, not
  independently verified here), then at 60 Hz: 3000 evaluations = 50 s
  (permission off), 15000 = 250 s (on), 100 = 1.7 s, 250 = 4.2 s,
  2901 = 48 s, 6000 = 100 s.
- A 300 s cycle is neither the hourly train nor the 123–150 s chains.

## 4. No sync check shown before closing onto a live bus

From `decision-tree/gridboss-control/REPORT.md` §4.6 and §5, not from a fresh
reading of the native code:

- The dead-bus close route requires EPS ≤ raw 100 (10.0 V).
- The other route (control bit 2 plus both handshake latches) closes
  immediately and bypasses both the phase gate and the EPS threshold.
- Generator phase acceptance `b78a` compares the generator waveform with the
  GridBOSS's own phase table, not with the inverter-formed EPS voltage.
- No recovered check compares generator phase with live EPS phase at the moment
  of closing. Whether the inverters are slaved to the same reference is not
  established.

This is the path to scrutinise for the equipment damage. The opening direction
matters too: the tree does not explain why the 16:36 handoff produced
161 → 96 → 283 V when 13:36 and 14:36 were clean.

## 5. Other weaknesses

- **Reason codes are read as causes.** At the hourly events the GridBOSS
  reported frequency 0 and source 1, which points at the fast ADC qualifier or
  capture-age path. The tree never narrows to that.
- **The healthy-generator premise is stronger than the evidence.** A multimeter
  cannot see a sub-cycle transient, and the GridBOSS recorded a 5 V sag at
  10:36:18. That could be a real bus transient or a GridBOSS measurement
  glitch; current evidence cannot separate them.
- **The converter index omits reachable code.** The whole protection task body
  at `91f03` was missing, including the calls to `90b8b`, `907c6` and about 25
  other routines. This extends finding 4 of `causal-path-audit-2026-11-11.md`.
- **Unexplained:** the GridBOSS going quiet for 20–30 s at 01:35 and 16:36.

## 6. Status of hourly candidates

| Candidate | Status |
|---|---|
| GridBOSS clock resync and hourly flash save | Traced; no path to the generator logic found |
| FlexBOSS interrupts-off clock read corrupting the converter link | Unlikely |
| FlexBOSS clock step affecting charge time-window checks | Not traced |
| Converter `907c6` permission cycle | Ruled out (about 300 s) |
| Other converter hour-scale timers | Not searched |
| Wi-Fi dongle or cloud | Not examined; no firmware here |

## 7. Recommended next steps

1. **Find which device owns the train.** The hourly routine fires only when
   that device's own clock minute is 21–39; otherwise it waits for minute 21.
   With the generator off, set one device's clock so its minute is outside
   that window at the next predicted slot. If the train moves to that device's
   minute 21, that device owns it. This is a normal time setting but a write
   to the equipment.
2. **Burst-capture around the predicted slot.** Read the device clocks, then
   poll at maximum rate from 60 s before to 120 s after each slot.
3. **Put a power-quality recorder or scope on the generator terminals and the
   backup output** across a slot. This separates a real transient from a
   sensing error and shows the phase relationship at reclose.
4. **Firmware:** trace the FlexBOSS clock-step effect on its time-window
   checks; search the converter images for hour-scale timers; rebuild the
   converter index from native control flow.

## Checks performed

- Recomputed onset phase for all 35 catalogued events against 3600 s.
- Listed every phase-lock excursion with the generator live (9 samples; one
  with the generator loaded and no dropout, at 10:36:16–18).
- Counted read failures within ±25 s of each hourly slot, including
  generator-off hours.
- Read native instruction listings for the GridBOSS and FlexBOSS routines
  named above from `static-analysis/engine/*/export/instructions.tsv`.
- Disassembled converter words `91d00..91f62` and `a1090..a1174` from
  `static-analysis/mapped/record-253/segment-0010ff00.bin` with the TI
  `dis2000` already installed, in a scratch directory. No project file other
  than this one was created or changed; `SHA256SUMS.txt` was not updated.

---

# Addendum (same day): first-principles pass

Method: telemetry first, then only the firmware paths the telemetry leaves
open. Worked from the retained telemetry and the existing text listings.

## A. What the telemetry fixes

From `telemetry-sanitized.csv`, raw samples around events 2, 3, 4, 8, 9, 10,
11 and 14:

1. **Every dropout is the GridBOSS opening its generator relay.** Generator
   current goes from about 100 A per leg to under 2 A between two consecutive
   samples, and the house load (8–14 kW) moves to the inverters. There is never
   an intermediate sample with the generator still carrying house load but not
   charging, which is what an inverter-only withdrawal would look like.
2. **The GridBOSS still rates the generator as good.** In the first sample
   after each of these onsets the selector is 2, qualified frequency is about
   60 Hz and inputs 122–125 are zero.
3. **Recovery equals the warm-up setting** (about 120 s, later about 30 s). In
   the firmware the warm-up counter resets only when inhibit asserts or the
   selector leaves 2. So every dropout passes through one of those, briefly.
4. With the utility dead, the only inputs that can do that are five flags:
   fast ADC flag `bf`, capture-age `b790`, slow qualifier `b799`, CAN veto
   `b791`, and the **generator stop request `b5a9.bit4`**. Only the first two
   also zero the frequency.
5. **Reconnects are break transfers.** In the three reconnects examined
   (events 9, 11, 14) the master goes to status 0 (standby) with AC present one
   sample before the generator shows load. The dead-bus close route needs
   EPS ≤ 10 V, which cannot happen while the inverters hold the bus, so only
   the handshake route is available.
6. **Reclose-then-drop:** the generator carries 8–12 kW of house load for one
   sample, then the relay opens again and the master returns to status 64.

## B. Newly mapped path: inverter status can make the GridBOSS reject the generator

`b5a9.bit4` was listed as an open frontier. It is written by the start/stop
routine `080335d4` (task 4). Native instructions `08033642..08033788` set it,
in normal mode with no manual run request (`b7b4==0`), when any of these holds:

| Condition | Meaning |
|---|---|
| `b7ae==0 && b7ac==1` | Source accepted and grid selected |
| ODR bit 11 set | Grid relay commanded closed |
| `b7a3 != 0` | Update-handler state |
| `b598` bit 5/0 (SOC mode) or bit 7/1 (voltage mode) | Battery data never received since boot |
| `b598` bit 6 set, or `(b55d & 3) == 0` | Class-4 subtype-1 frame never received, or its bits 8–9 are zero |
| `b599` bit 5 set, or `(b564 & 3) == 0` | Class-4 subtype-4 frame never received, or no inverter reports bit 0 or 1 |

The whole block is skipped when `a6a6` bit 0 is set (local holding 2102 bit 0;
read as 2, so not skipped). A second set/clear of the same flag in the running
branch uses `b564` bit 1, the battery-low latch `b75b` (start/stop SOC or
voltage with hysteresis) and setting `a5e8+0x36` bit 0; that part is from the
decompiler output and was not checked instruction by instruction.

Where those inputs come from:

- CAN handler `080301bc` (class 4, channel 1 or 2):
  - subtype 1: `b55c` = payload bytes 0–1; clears `b598` bit 6.
  - subtype 2: `b2f8` = payload bytes 2–3.
  - subtype 4: per-inverter status byte = payload byte 0, ORed across three
    entries into `b564`; per-inverter voltage and SOC; clears `b599` bit 5.
  - subtype 5: aggregate voltage `b684` and SOC `b788`.
- The "never received" bits in `b598`/`b599` are set at boot (`0801ca60`) and
  only cleared afterwards. No runtime timeout that re-sets them was found.
- Converter side (record 253): `9bb8c`, inside the UART handling, queues the
  class-4 frames through `98ccb`; `98d40` dequeues and sends them.
  - subtype 1 word 0 = `12b00` (copied from UART field `12dc9`, supplied by
    the ARM), sent when `12cc8 == 1`.
  - subtype 4 word 0 = `127cd`. `907c6` copies `127cd` bit 1 into permission
    word `127d1` bit 1 and tests bit 0, so these are raw permission bits. No
    writer of `127cd` appears in the recovered listing.

So the GridBOSS drops the generator if the last subtype-1 frame has
`12b00 & 0x0300 == 0`, or if every inverter's `127cd & 3` is zero. Neither is
a generator measurement. This path does not zero the reported frequency, which
matches item A2.

## C. Live cross-check of the mapping

GridBOSS readback of 2026-11-09 (`direct-session/baseline-gridboss.json`):

| Input | Value | Mapped meaning |
|---|---|---|
| 580 | 100 | Aggregate SOC `b788` |
| 581 | 543 | Aggregate battery voltage `b684` (54.3 V) |
| 585 | 0x0001 | `b598`/`b599`: run state 0, held mode 0 |
| 586 | 0x8200 | `b55c`, the subtype-1 word |
| 587 | 3 | `b564`, ORed inverter status bits |

Both inverters' holding register 0 read `0x8200` in the same session. The same
value arriving at the GridBOSS as input 586 confirms the chain inverter
register 0 → ARM → UART → converter `12b00` → CAN → GridBOSS `b55c`.

All five inputs are in the 500–599 block the recorder already collects.

## D. One more observation at the 16:36 onset (withdrawn; see section O)

The rejected master read at 16:36:29.641 reported status `33280`. That is
`0x8200`, the master's holding register 0. An input-register reply carried a
holding-register word at the moment the dropout began. Two readings, not
distinguishable from this one sample: another client was reading the master's
holding registers through the dongle at that instant and the replies crossed;
or the inverter's own register buffers were inconsistent for a moment.

## E. Corrections to the first part of this review

- GridBOSS read timeouts mark each overnight hourly onset (5 of 5) and none of
  the five reclose-then-drop onsets checked. In generator-off hours there is
  no timeout at the hourly mark, so the timeout accompanies the dropout rather
  than proving an independent hourly session.
- All `OSError` failures in the overnight capture belong to the single
  02:35:51–02:39:15 gap, not to an hourly pattern.

## F. What this means for the decision tree

- The tree's reason-1 opening (`0x0104`, inhibit) has a producer that is driven
  by what the inverters report about the battery, and it was not in the tree.
- For the next generator run, the discriminating stock registers are 519, 568,
  569, 577, 579, 580, 581, 585, 586, 587 and 588. A dropout with 519 = `0x0104`
  and 569 and 579 bit 8 clear points at the stop request or the CAN veto;
  a change in 586 bits 8–9, 587 bits 0–1 or the run-state bits of 585 and 578
  around the onset points at the stop request specifically.
- Still unmapped: who writes converter word `127cd`; what the ARM sends in
  UART field `12dc9` during mode changes; why the trigger recurs hourly.

---

# Addendum 2: producers of the stop-request inputs

## G. Installed GridBOSS start/stop settings (readback of 2026-11-09)

| Holding | Value | Firmware use |
|---|---|---|
| 179 | 0x0760 | High byte `a4b3` bit 1 set: **voltage mode**, not SOC |
| 194 | 505 | Start voltage `a512`: 50.5 V |
| 195 | 555 | Stop voltage `a514`: 55.5 V |
| 196 / 197 | 80 / 100 | Start / stop SOC (unused in voltage mode) |
| 224 | 1 | `a61e` bit 0: threshold control enabled |
| 226 / 227 | 30 / 30 | Warm-up, cooldown |

Mapping of these holdings to addresses is from
`state-logging/firmware/register-map.json`.

Battery-low latch `b75b` (decompiler output of `080335d4`, voltage mode with
BMS data): set when aggregate voltage `b684` (input 581) ≤ start; cleared when
≥ stop; otherwise held. In auto-run state the stop request asserts when the
latch is clear and `b564` bit 1 is set, and releases when the latch is set.

Why this is probably **not** the trigger: the master's battery voltage on
Nov 7 stayed between 50.3 and 53.4 V, never near 55.5 V; and if the latch had
cleared, the generator would have stayed unwanted until the battery fell to
50.5 V rather than returning after the warm-up time. A one-evaluation spike in
input 581 at or above 55.5 V would still be worth looking for.

## H. Battery-type field: a value that circulates

GridBOSS condition `(b55d & 3) == 0` tests bits 8–9 of the word the master
converter sends in class-4 subtype 1. That word's path:

1. **Inverter ARM** builds its class-0 frame to the converter in `08051eb0`;
   body bytes 5–6 are the settings word at `2000d488` (holding register 0,
   `0x8200` on both units), read live at build time (`08051ef0`, `08051f00`).
2. **Converter UART parser `9a35c`**: if the role check `a092d()` returns 1
   or `11ec6` bit 8 is set, the word is staged in `12dc9`; otherwise it is
   applied directly to `12b00` (`9a3d8`).
3. **Converter broadcast `9bb8c`** (runs when all UART classes have arrived,
   `12dc6 == 0x7f`): copies `12dc9` to `12b00` on its first pass, whenever the
   two differ, or when resend flag `12cc8` is set, then queues class-4
   subtype 1 with word 0 = `12b00`. `12cc8` is set by the CAN handler `98276`
   on an incoming class-4 frame and by `984b1` from `9a198`.
4. **Converter permission routine `907c6`** (`9084a..9084d`): if
   `12b00 & 0x0300 == 0` it takes the disable branch at `90a8f`, which clears
   the charge and discharge permissions.
5. **GridBOSS** stores the word in `b55c` and applies the stop condition.

Writers of the ARM's battery-type byte (`2000d489`):

| Writer | What it is |
|---|---|
| `08044440` | Modbus holding-register write handler |
| `08038118` | Front-panel / UI routine |
| `0802585c` | Model-defaults routine; clears the battery-type bits to 0. Called only from `08027f04`, which has no direct caller (start-up validation) |
| `08056bb0` (`08057470`) | Parameter mirror received from the converter |
| `080501cc` (`080517ba`) | Converter-to-ARM frame with flag bit 0 set overwrites holding 0 and 1 |

So the field circulates ARM → converter → CAN → other converter → other ARM,
and each hop can overwrite the next. A single frame carrying zero in bits 8–9
would both clear the converter's own permissions and make the GridBOSS drop
the generator. Nothing here shows that such a frame was ever sent.

## I. Permission word `127cd` (feeds `b564`)

- Class-4 subtype 4 word 0 is `127cd` in both the BMS-mode and local-mode
  builds (`9bd23`, `9bd44`).
- `907c6` uses `127cd` bits 0 and 1 as the source for permission bits in
  `127d1` when it is not copying them from the aggregate word `11ec6`.
- No instruction in the recovered listing writes `127cd`. I also disassembled
  the three task bodies missing from the listing (`8ea39`, `94da5`, and
  `a07b7`–`a0c40` which holds the CAN task) and found no reference to it.
- The live GridBOSS value of `b564` is 3, so the installed unit does set both
  bits. Whether they ever change is **not established**.

## J. Coverage gap confirmed

Six of the eight converter task bodies are absent from the record-253 export
(`a0a17`, `91f03`, `8ea39`, `a07b7`, `a0b18`, `94da5`). All direct call targets
are present; the gaps are code reached only through the task table.

## K. Where this leaves the generator-rejection tree

For a dropout with the observed signature (generator still rated good,
recovery equal to warm-up), the candidates that survive are:

1. One class-4 frame from the master converter with zero in `127cd` bits 0–1
   or in `12b00` bits 8–9 (stop request).
2. Controlword bit 0 from the converters with control established (`b791`).
3. A fast-flag or capture-age assertion that cleared within about two seconds.

All three produce reason `0x0104` in input 519, so 519 alone will not separate
them. Inputs 577, 579, 586 and 587 will, but the first two flags last about
half a second, shorter than the current polling interval.

---

# Addendum 3: fast-poll feasibility

## L. Live probe, 2026-11-11 (read-only)

One TCP connection to the GridBOSS dongle (port 8000), function-04 request for
inputs 568–588, built with the project's own packet builder. No writes.

- Attempt 1: connection accepted, then closed by the dongle before any reply.
- Attempt 2: same; the close came 0.115 s after the first request.
- No data was returned. I stopped after two attempts.

The Home Assistant recorder was running and holds a connection to the same
dongle. The most likely reading is that the dongle will not serve a second
local client; this probe does not prove that is the reason. Whether the
recorder noticed the two attempts was not checked.

## M. Retained evidence that back-to-back reads work on an owned connection

`direct-session/transport-gridboss.json` (2026-11-09, HA paused, one
persistent connection):

- 30 requests were sent with less than 50 ms idle after the previous reply, up
  to 10 in a row. All were answered.
- Round trip 330–614 ms. Small reads (3–12 words) took 330–450 ms; 100–125
  word reads took 500–610 ms.
- The inverters answered in about 280 ms (median) in the same session.

That supports roughly 2–2.5 samples per second for one small GridBOSS block.
Runs longer than 10 consecutive requests have not been tested.

## N. Why the production recorder is slower

`capture-upgrade/post-restart-capture.jsonl` (2026-11-09, 16 samples per role):

| Block | Median | Max |
|---|---|---|
| GridBOSS input 500 × 100 | 1118 ms | 3509 ms |
| GridBOSS holding 4500 × 10 | 947 ms | 1252 ms |
| Inverter blocks | 600–750 ms | 1.7–2.7 s |
| Whole GridBOSS sample | 4142 ms | 14756 ms |

Each block costs about twice the round trip seen on a persistent connection,
and the roles are read one after another.

## O. Live fast-poll test with the recorder paused (2026-11-11)

Evidence: `fast-poll-test/` (`fast-poll-result.json`, `pause-session.json`,
scripts). Read-only function-04 requests on one held-open connection per
dongle. No controller writes.

**Pause and restore.** The EG4 config entry was disabled through the Home
Assistant WebSocket API using the access stored for the Hermes agents on
Plucky, with the Nov 9 restore watchdog armed (300 s). Pre-flight: utility
244.4 V, generator 2.0 V.

| Step | Time (UTC) |
|---|---|
| Disable requested | 15:14:25.4 |
| Entry confirmed not loaded | 15:16:38.4 |
| Test | 15:16:41.4 – 15:18:12.0 |
| Entry loaded again | 15:18:42.1 |
| Recorder's last / first transaction | 15:14:25.7 / 15:18:39.2 |

The recorder gap was **4 min 13 s**, not the two minutes planned: the
integration took 2 min 13 s to unload. The watchdog was disarmed after the
restore was verified and exited 0. The local archive shows all three devices
recording again from 15:18:39.

**Results.**

| Poller | Duration | Reads OK | Rate | Median round trip | 90th pct | Worst gap |
|---|---|---|---|---|---|---|
| GridBOSS 568 × 21, alone | 60 s | 97 | 1.6 /s | 497 ms | 503 ms | 10.5 s |
| GridBOSS 568 × 21, with inverters in parallel | 30 s | 59 | 2.0 /s | 497 ms | 509 ms | 0.96 s |
| Master inverter 0 × 40, parallel | 30 s | 129 | 4.3 /s | 197 ms | 283 ms | 0.50 s |
| Slave inverter 0 × 40, parallel | 30 s | 103 | 3.4 /s | 231 ms | 300 ms | 2.8 s |

- GridBOSS replies cluster tightly at 450–510 ms (82 of 97 reads), so about
  two samples per second is the practical ceiling through this dongle.
- Polling the three dongles in parallel did not slow the GridBOSS.
- No connection was dropped in either phase.
- Only input 573 changed on the GridBOSS during the run (2431–2432).

**The dongle has its own read cycle, and it stalls local replies.** In phase A
four consecutive requests timed out between t = 7.9 s and 15.4 s. In that
window the connection carried replies nobody on this side had asked for:
input 0 × 127, 127 × 127 and 254 × 127. So the dongle periodically reads the
device for its own purposes, forwards those replies to the local client, and
does not answer local requests while it does. How often this cycle runs was
not measured. A fast poll will have blind spots of up to about ten seconds
whenever it runs.

**Correction (same day): the holding 0 × 80 frame is the dongle's greeting.**
Each poller also received one holding 0 × 80 frame. The production recorder's
own log shows the same frame first on every new connection, on all three
dongles (it is the frame the transport classifies as `identity`), and its
first word is 33280 (`0x8200`). It is sent on connect, not as part of a
periodic read.

That changes section D. The corrupt master status logged at 16:36:29 on Nov 7
(33280) is what results when a greeting frame is taken for the input reply.
It shows the old client paired a frame wrongly at that instant; it is **not**
evidence that another client was reading the master's settings, and it says
nothing about the inverter's buffers. Section D's two readings are withdrawn.
