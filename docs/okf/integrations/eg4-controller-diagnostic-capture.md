---
type: "Interface Contract"
title: "EG4 Controller Diagnostic Capture"
description: "Deployed read-only GridBOSS/FlexBOSS capture profiles, controller-observation semantics, measured cadence and unresolved diagnostic gaps."
resource: "docs/mother-of-all-data-loggers.md"
tags: ["eg4", "home-assistant", "telemetry", "generator", "diagnostics"]
timestamp: "2026-11-09T18:32:07Z"
source_path: "custom_components/eg4_monitor/{__init__.py,local_dongle.py,local_dongle_trace.py}"
status: active
sensitivity: internal
approval: none
---

# Overview

The existing HA sampler records direct GridBOSS and both FlexBOSS roles using serialized, short-lived dongle reads. Initial supplemental diagnostics were deployed with an approved November8 Core restart; the corrected/expanded profile was deployed with one approved November9 restart. This is a diagnostic upgrade, **not a repair or change to inverter settings, protections or firmware**.

The local-dongle-count sensor counts the two FlexBOSS roles; GridBOSS has a separate client. A value of2 is compatible with successful three-device capture and is not evidence that GridBOSS is missing.

[Dongle discovery](/integrations/eg4-local-dongle-discovery.md) owns route/identity behavior. The [firmware investigation](/references/generator-firmware-investigation.md) owns candidate-map interpretation and causal limits. This concept is a capture contract, not a live deployment or operating runbook.

# Read Contract

| Device | Function | Start / count | Recorded evidence |
|---|---|---|---|
| GridBOSS primary | 04 input | 0/40 and120/12 | Reported terminal/output measurements, flags and source selection |
| GridBOSS supplemental | 04 input | 500/100 | Decision pairs518/519, command counters, fast flags568/569, received control577, packed status578/579, readiness588 |
| GridBOSS supplemental | 03 holding, local dongle wrapper | 4500/10 | Initialized GPIO/test-state command readback; historical4516 values are not valid GPIO evidence |
| Each FlexBOSS primary | 04 input | 0/40;80/40 BMS refreshed separately | Mode, voltage/power and cached age/availability-qualified BMS context |
| Each FlexBOSS supplemental | 04 input | 40/40 | Faults62/63, true warnings60/61 and source/status77;60/61 now enters discrete observation signatures |
| Each FlexBOSS supplemental | 03 holding | 110/125 | Request/configuration readbacks, generator rating/Boost, battery thresholds and GridBOSS-enable |
| Each FlexBOSS supplemental | 04 input | 120/26 | Ordinary120..145: raw commands142/143, limiter144 and previous-mode/packed-state145;144/145 also trigger observations |

Only functions03/04 are added. Supplemental requests share the existing role's polling lock/client and run after successful primary telemetry has been cached. There is no independent ongoing dongle client or history-upload/erase request. Wrong-function/range/identity responses are rejected; missing diagnostics do not become synthetic zero values or discard successful primary telemetry.

The former GridBOSS120/count40 range extended into undefined serializer scratch132–159 in the recovered candidate; the deployed profile uses120/count12. Protocol success does not prove the candidate's installed-byte identity or physical sensor/contact meaning.

November9 native correction: local GridBOSS GPIO output starts at4500, not4516; the latter copies outside the initialized20-byte output. Historical4516 values remain preserved but cannot establish commands. The deployed profile now uses4500/10, selects real Flex warnings60/61 instead of analog64/65 for discrete changes, and adds ordinary120/26 on both inverters. Analog142/143 remains raw context. [Native proof](../../../investigations/2026-11-08-firmware-reverse-engineering/direct-session/README.md) and [verified deployment](../../../investigations/2026-11-08-firmware-reverse-engineering/capture-upgrade/README.md).

Do not generalize all03 reads as observation-only: the candidate FlexBOSS handlers for requests starting exactly224 or248 change command/transfer state. The deployed start110/count125 request does not take the exact-start224 handler. Nonstandard07 and engineering-mode configuration are not part of this deployed read contract.

# Output and Retention

- Raw records use`eg4_local_dongle_trace.v1` and include primary telemetry plus per-block function, start/count, acquisition start/end, duration, success and raw values or error. Reads across blocks/devices are sequential, not atomic.
- `ControllerObservationTracker` emits`eg4_controller_observation.v1` baselines, discrete changes and availability loss/resumption under`<TracePath>/controller-observations/local-dongle.jsonl`. It compares both bytes of519 and command-path counters without the AC-evidence logger's debounce. A restart starts a baseline, not a synthetic physical transition.
- Raw and controller-observation streams have independent25MiB×8-file rotation limits. This is byte-bounded retention, not guaranteed days or a lifetime journal. Queue/filesystem caching is not a power-loss-proof recorder.
- Missing remains null/error. A real raw zero remains zero. Diagnostic availability changes are data quality, not demonstrated power interruptions. Output-data readbacks and command counters do not establish contact movement or engine cycles.
- The existing inferred AC-presence/supply logger remains separate from raw firmware decisions and is not proof of mechanical engine state.

# Capture Coverage

**Sampled:** terminal/output measurements, source selector, last decisions, exposed control/fault/packed status, command counters, warm-up-ready byte, and each inverter's exposed mode/settings. Valid BMS context is available from the master; the observed slave's lack of direct BMS availability must not be treated as zero permission.

**Partial:** combined/delayed status words, the producer of an asserted inverter aggregate, complete source eligibility, peer/role ownership, phase/handshake conditions, and the winning converter power limiter. Slow RMS observations do not determine fast ADC/phase behavior.

**Snapshot only:** GridBOSS holdings18/24/37/38/170/226 and local2102 were read separately during the stopped-Core window, not continuously polled. Captured values included mode18=0, both delay24 and warm-up226=30, limits37/38=1200/2880 and warning mask2102=2. Null unpolled configuration fields in observations are not current zeros.

**No complete raw exposure through the audited03/04 map:** computed inhibit`b7ae`, sticky acceptance`b5a0`, acknowledgement`b59c`, full generator phase state`b78a`, edge latch`b7b2`, complete handshake/qualification/heartbeat ages, raw ADC rings and complete converter internal RAM. Transient decisions can be overwritten between samples. Faster Modbus polling cannot reveal unpublished bytes.

**Useful projections, not full raw states:** input578.bit4 conditionally reflects the low bit of generator phase`b78a` when source2 was selected at packing time; source1 uses another flag. This is delayed packed evidence subject to normal shadow-bank invariants, not physical phase alignment. Sticky acceptance`b5a0` bits0/2 project through`b551` into CAN0204/0214 byte3, which is not included in the two polled578/579 words. CAN0224 adds sampled GPIO/raw warning evidence, not verified contact feedback. A complete wire trace can constrain upstream changes but does not prove receiver acceptance/execution.

Complete node-by-node attribution needs additional packet/internal tracing and installed-variant identification; physical voltage/contact proof additionally needs independent observations. Those are not provided by this capture, and full first-cause attribution is not guaranteed.

# Stock Engineering Pilot, Not Continuous Capture

The downloaded converter candidates implement Q1 status / Q3 selected sampling / QD finite export. The explicitly approved November9 one-off Q1 pilot successfully returned58 bytes/15 internal fields from the master after a temporary integration pause; ordinary125-word settings readbacks matched after monitoring resumed. This separate tool used only corrected engineering selectors1113/1114, not276/277. Production polling remains03/04 only; no HA engineering action or continuous Q1 capture was deployed.

Q1 field5 is packed, not a standalone raw107e2 read. Native producer bounds permit conditional lossless recovery of current numerical mode,107e5 and127dc; all six new replies decode to0/3/0 under those bounds. Retain raw data and compatibility limits, not guessed decimal digits or physical mode names. A direct getter for raw107e2 still requires excluded QS setup. Q3/QD remain unactivated. RX reset is not a stop/cancel, engineering status originals are externally inaccessible, and consistency is not a generation nonce. No firmware change occurred.

The existing second-floor Emporia/ESPHome meter separately publishes both voltage legs. Builder configuration declares direct phase inputs and3s averaging, consistent with observed approximately3s value changes; actual feed/calibration and compiled-image/configuration identity remain unverified. It is a candidate corroborating observer, not generator-terminal or fast waveform proof.

Prioritized host acquisition, slower settings refresh and anomaly-triggered raw pre/post preservation are recommendations only, not implemented features. Current discrete-signature history can omit analog-only changes; byte-bounded raw rotation does not guarantee preservation until investigation. Portal Local Data is a different, coarse history mechanism whose Read/Clear controls are remote writes, not added passive polling.

Detailed source/side-effect evidence and stock-first recommendation: [acquisition research](../../../investigations/2026-11-08-firmware-reverse-engineering/acquisition-options/README.md).

# Single-Pause Follow-up

The separately authorized November9 direct session paused only EG4 once for1561.287s, used one persistent connection per known device, and obtained three Q1 replies from **each** FlexBOSS. Each was58 bytes/15 fields. GridBOSS answered all39 persistent-session requests. Thirty-six function07 exception03 replies were preserved while no readable engineering result was available; they are not an idle certificate or a protection fault. No transport error occurred in these persistent sessions. This is not a controlled test of a hardware single-client limit or a continuous-health guarantee.

Repeated reads retrieved the last Q1 result, including the earlier master pilot's bytes before new activation and the round3 bytes minutes later while ordinary measurements changed. It is one retained buffer, not a timestamped FIFO or automatically refreshed log. Host reads alone do not refresh it. Current/previous numerical modes and retained handler nibbles constrain context, not incident ordering.

Ordinary input63 preserves converter107f1 bits9..15 in the audited chain; those direct-session bits were clear. Bit8 is redirected to input6 low-bit0; low bits have masks/overlays. Q1's107ed is a **different** protection bank's high half, not proof full107ec is clear. Input145 low-byte exposes previous numerical mode; its high byte is masked, packed and partly retained. Extended120..239 was acquired once; the ordinary120..145 subset is now continuously polled.

All owned sockets were closed, HA returned enabled/loaded, both inverter roles and fresh GridBOSS telemetry were observed, and the restoration watchdog exited successfully. No generator/load test or failure reproduction occurred. Raw evidence, settings/dynamic-readback comparison, exact coverage and remaining decision predicates: [direct-session results](../../../investigations/2026-11-08-firmware-reverse-engineering/direct-session/README.md).


# Selected-Source/EPS Feedback

Native sender/receiver closure binds converter121fe to CAN0514 LE16 word2: GridBOSS selected grid voltage/input1 or generator voltage/input3, not UPS/input2 or power. The positive gap against converter-local EPS/input20 drives a gated delayed mismatch outcome, exposed unchanged at **Flex input61.bit14**. Input63.bit14 is a separate status/timeout outcome. The transport's warning-word location does not make a condition benign; converter handlers also consume the full internal protection bank.

Raw40/40 contains61 and the corrected signature now observes it. A selector2→1 transition changes the candidate incoming voltage; normal registers cannot establish which0514 value the converter retained at execution. Timestamped0514 plus0204/0214/control/peer traffic constrains the wire boundary, not receiver acceptance or internal counts. Mismatchbit11 suppresses that member's positive aggregate-control contribution, not directly sets a generator veto. [Native chain and historical application](../../../investigations/2026-11-08-firmware-reverse-engineering/incident-recheck/eps-mismatch-provenance.md).

# November9 Corrected Capture Deployment

After explicit approval, only the two collector/observation modules were backed up and replaced.56 focused tests, changed-module Ruff, actual TCP smoke, source-drift/hash checks and`ha core check` passed. One Core restart loaded the corrected4500 profile and both Flex120/26 profiles; raw records and actual journal keys confirmed the running cutover. Ordinary03/04 only; no continuous Q1, power/configuration or firmware change.

A fixed16-sample-per-role window retained48/48 successful primary samples. Every inverter block succeeded; GridBOSS500/100 succeeded16/16 and4500/10 succeeded15/16, with one explicit timeout. Median start gaps were about4.234s; maximum14.999s. Further availability loss/resumption and one legacy shutdown record remain identified, not hidden. Observed maintenance sampling gaps were about63s, not physical outages. No continuous-health, guaranteed1Hz or cycling-fix claim follows. [Deployment, coverage and source provenance](../../../investigations/2026-11-08-firmware-reverse-engineering/capture-upgrade/README.md).

# Verification Evidence

- November8 implementation verification:75 tests passed,3 skipped; changed-module Ruff passed. Actual TCP loopback exercised cached wrong-function rejection, changed decision bytes, partial failure, separate rotation and accepted-record flush.
- Deployment: source-drift checks, affected-file backup, staged hashes and`ha core check` passed before the approved Core stop/start. Cold-start discovery found no FlexBOSS identities; the existing master/slave host options were set to previously verified routes, with all other options preserved. This is HA configuration, not an inverter-setting write.
- Preserved post-deployment window:98 samples per role, all294 primary samples successful, median start gaps about4.92s, maximum7.887s. Eight supplemental blocks failed explicitly without fabricated values. These are measured observations, not a guaranteed future rate or physical power-quality certificate.
- A follow-up live trace check on November9 at01:17UTC observed fresh successful primary and both supplemental blocks for GridBOSS and both inverter roles, with the integration loaded. The master BMS was available; slave BMS was not. Generator AC was absent in that check, so it was not a reproduced failure or proof of a fix.
- November9 approved status-only pilot: eight focused regressions, Ruff, actual loopback CLI and actual real-artifact decode passed. HA was disabled/re-enabled without Core restart; the two access windows lasted21.983s and44.292s. Both inverter streams resumed, but retained GridBOSS samples were already heavily intermittent before Q1 and remained so afterward. Successful post-restore GridBOSS reads do not establish continuous health. Raw evidence, settings comparison and measured gaps: [pilot verification](../../../investigations/2026-11-08-firmware-reverse-engineering/engineering-capture/verification.json).

# Citations

- [Implementation and operator-facing capture semantics](../../../docs/mother-of-all-data-loggers.md)
- [Deployment, hashes, measured cadence and restored monitoring](../../../investigations/2026-11-08-firmware-reverse-engineering/state-logging/ha-capture-deployment.json)
- [Actual collector/history smoke proof](../../../investigations/2026-11-08-firmware-reverse-engineering/state-logging/diagnostic-capture-smoke.json)
- [Logging investigation and preserved capture coverage](../../../investigations/2026-11-08-firmware-reverse-engineering/state-logging/README.md)
- [Stock-interface audit and conditional register/CAN projections](../../../investigations/2026-11-08-firmware-reverse-engineering/acquisition-options/stock-interface-audit.md)
- [Portal, sampler timing and independent-observer acquisition evidence](../../../investigations/2026-11-08-firmware-reverse-engineering/acquisition-options/README.md)
- [Implemented stock status pilot, limitations and unsolicited GridBOSS history traffic](../../../investigations/2026-11-08-firmware-reverse-engineering/engineering-capture/README.md)
