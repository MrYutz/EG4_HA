---
type: "External Reference"
title: "Generator Firmware Investigation: Evidence and Limits"
description: "Recovered generator-acceptance and converter-power decision paths, with explicit limits on incident attribution and observability."
resource: "investigations/2026-11-08-firmware-reverse-engineering/decision-tree/README.md"
tags: ["eg4", "generator", "gridboss", "flexboss", "firmware", "diagnostics"]
timestamp: "2026-11-09T18:32:07Z"
source_path: "investigations/2026-11-08-firmware-reverse-engineering"
status: active
sensitivity: internal
approval: none
---

# Summary

Downloaded first-party IAAB-16xx and FAAB-27xx release-family candidates were mapped and analyzed across GridBOSS Thumb, FlexBOSS ARM, both C28x Para075/375 candidates, and copied CLA code. The exact installed component bytes and correct converter hardware variant remain unverified. These are code-backed findings about recovered candidates, not a diagnosis or repair of the installation.

Generator engine operation, power-path commands, physical contact position, converter permissions, accepted watts, and reported EPS voltage are different observables. Do not equate them.

# Established Findings

- GridBOSS input519 contains the generator-channel **last-written decision**: state in the low byte, reason in the high byte. It is volatile, untimestamped, non-clearing on read, and not a FIFO or immutable first-cause log. Normal closing leaves it stale; repeated same-reason commands can leave it identical. Some opening paths do not update it. Zero does not establish closed/normal contacts.
- In normal operation, ordered generator-opening branches distinguish inhibit (`0x0104`), grid/fallback selection (`0x0204`), enforced controlword-bit0 veto (`0x0304`), mode-bit2/edge-latch condition (`0x0404`), inverter-side aggregate inhibit (`0x0504`), and GridBOSS aggregate trip (`0x0604`). These are recovered software codes, not manufacturer error descriptions or proof of physical opening. Boot, override and test branches are separately indexed in the source report.
- A real return-status edge exists in the code: GridBOSS inhibit/warm-up status in0204/0214 feeds converter aggregation, which can return controlword bit0 through0201/startup0221 to GridBOSS. Input577 exposes the received controlword. This establishes logical feedback, **not a self-sustaining physical oscillation or proof that controllers were fighting**.
- Healthy slow generator V/F is insufficient for reconnection. Normal closing also depends on source/acceptance, inverter veto, phase acceptance, trips, mode and handshake. EPS at most10.0V is required by one timed closing route; a separate valid handshake route bypasses that EPS threshold. Battery-maintained EPS therefore does not universally forbid reconnection.
- Inverter battery/BMS permissions, peer roles, request ownership, protections, minimum-selected power limits and ramps can alter converter targets without a generator V/F fault. Equal-numbered bits/registers across controllers are not equivalent. In particular, the traced generator-inhibit aggregate selects converter low faultbit6, **not** the ARM rating-guard bit10; another internal protection bit10 is in a different word.
- Holding24 acceptance/acknowledgement delay and GridBOSS holding226 warm-up are distinct settings. Both read30 in the November8 configuration snapshot. Their routines use setting×100 evaluations; a roughly31-second pause alone does not identify the initiating branch. An opening command alone does not reset warm-up: source/inhibit eligibility does.

# What Was Exercised

Original GridBOSS qualification/warm-up instructions and the whole relay ISR with selected real SysTick/readiness routines were executed offline. Under explicit synthetic boundary states, fixed generator V/F fields coexisted with repeated software set/reset pulses. Their fast cadence is **not** a reproduced31-second interruption, proven contact movement or demonstrated defect. ARM chains were also exercised. Critical C28x/CLA conclusions were checked against official TI native decoding; a full coupled C28x/CLA/physical installation was not emulated.

# Firmware Logging Limits

Both candidate ARM applications have periodic flash history containing inputs0–120, excluding122–125 and518/519. GridBOSS timing is about300.5 seconds under the1ms SysTick assumption; the manufacturer describes five-minute offline history with maximum90-day storage, not guaranteed installed retention. One unsolicited CRC-valid record was retrieved: its binary device timestamp decodes to2025-06-12 06:04:19, without timezone/clock-quality proof. No incident-date record or complete retained-date inventory was recovered.

Authenticated portal/source inspection separates existing-server list/export from **Read**, which posts a remote write for `HOLD_READ_LOCAL_DATA_DAY` with1–90, and **Clear**, which posts the same parameter with0. Both native applications implement numeric holding111 upload/erase control; the server enum→111 binding remains unproved. Upload can pause recording and encounter initialization erases. No Read/Clear or remote write was issued. November7 and Show All list requests returned no uploaded rows for any of the three devices; that does not prove empty onboard flash.

The incident capture lacked519 and decisive internal states. The later live value`0x0204` cannot date an event or identify the November7 incident's cause. No generator-instability cause, complete incident replay, firmware fix or output-safety certification is established. Do not convert these findings into protection widening, firmware patching or timer changes.

# Capture and Remaining Evidence

See [Controller Diagnostic Capture](/integrations/eg4-controller-diagnostic-capture.md) for the deployed read-only profiles, measured cadence, missing-value semantics and blind spots. Current register capture can narrow future events but cannot populate every internal decision node or prove first cause. The absence of a recorded recurrence, especially with generator AC absent, does not prove a repair.

Manufacturer configuration ownership in the [GridBOSS manual](/references/gridboss-manual-v2-0-7.md) remains distinct from the firmware's inverter/battery/role/protection participation; the investigation is not an approved settings change.

# Additional Stock Acquisition

Native ARM/C28x analysis establishes a separate stock converter engineering facility: Q1 status, Q3 four-channel selected acquisition with up to500 samples/channel, and QD finite sample export. A direct raw107e2 getter requires excluded QS setup, but Q1's packed field5 can be inverted under recovered producer bounds; all six follow-up replies yield numerical current0,107e5=3,127dc=0. The Q task and segmented ARM/function07 response route exist in both candidates. This is not arbitrary RAM or a complete GridBOSS decision log. Q3/QD timing/headroom, installed-byte identity and full-tree coverage remain unverified; no sampler or QS was activated.

Prefer stock acquisition, the existing host logger and independent electrical observations before custom controller firmware. Raw missing states sometimes have partial projections: source-qualified delayed phase information is already in578.bit4; sticky acceptance bits also reach a CAN status byte not covered by578/579. The existing Emporia/ESPHome voltage observer has a configured3s average and cannot exclude short disturbances. See the [acquisition research](../../../investigations/2026-11-08-firmware-reverse-engineering/acquisition-options/README.md) for exact limits and recommended next steps, not an approved operating procedure. The current download is not a reproducible vendor source/build or verified flash package; custom instrumented firmware remains a last resort, not an established requirement.

# Installed Status Pilot

On November9, after explicit authorization for a temporary EG4 integration pause and one Q1 request, the master returned58 bytes containing all15 native status fields. Repeated payload/metadata reads matched. Only engineering1113=49 and1114=1 were written once each; the earlier263–278 mapping was corrected before hardware access because276/277 are ordinary settings. The configured write address came from holding15, not the unsolicited cached wrapper's zero. See the [implemented pilot and raw evidence](../../../investigations/2026-11-08-firmware-reverse-engineering/engineering-capture/README.md).

No Core restart, production-code deployment, power/protection setting change or firmware operation occurred. The compared125-word range110–234 matched a later resumed HA sample. Original diagnostic mode/Q-character values remain unknown and were not restored; no reset was misrepresented as cancellation. No firmware nonce or atomic snapshot exists, so request association remains best-effort rather than guaranteed first-cause ordering.

Both inverter streams resumed successfully. GridBOSS was heavily intermittent before and after the pilot; a CRC-valid unsolicited256-byteA5A5 history record was also observed on its local stream without a history control from this investigation. That record is not a519 log and does not prove the power incident's cause. Do not claim complete monitoring health or that every timeout is explained by history traffic.

# Both-Inverter Direct Session

The later, separately authorized single1561.287s pause returned three58-byte Q1 buffers from each FlexBOSS, plus full initialized ordinary input0..239 and expanded readback banks. All39 persistent GridBOSS requests succeeded. HA was restored enabled/loaded and three-device availability was observed afterward; this does not erase the earlier intermittent failures or prove a hard socket limit.

Q1 is a retained snapshot with **retained per-handler breadcrumbs**, not event history. Under candidate bounds, all six vectors show current numerical0/previous32. Diagnostic107f4=`3060` combines a mode32 nibble6 with an independently retained shared high3; it is not a four-digit fault code or an ordered incident story. The slave's changing11a56.bit6 is a sample-ready acknowledgement, not a generator-cycle flag. Local battery measurements were54.4V/54.2V; ordinary selected input4 was54.3V in the paired brackets. These differences do not establish a bad sensor. Current generator519 stayed0204; no failure was reproduced.

Native mapping corrected two earlier interpretations: GridBOSS GPIO is local03 **4500/10**, not4516/10; Flex warnings are **input60/61**, not64/65. The later approved host upgrade now uses these corrected profiles/signatures and adds both inverters' ordinary120/26. Historical4516 GPIO interpretations remain invalid; no old missing warning is invented. Normal input63 preserves important107f1 bits9..15; full RAM/phase/counter state remains a separate gap.

No ordinary operating/protection write, history upload/clear, Q3/QD/QS, firmware operation or generator/load change was issued. The stock host-capture corrections are deployed; Q1 remains separate bounded investigative context, not continuous production acquisition. These results do not make every decision predicate observable. [Direct source/results](../../../investigations/2026-11-08-firmware-reverse-engineering/direct-session/README.md) and [approved capture upgrade](../../../investigations/2026-11-08-firmware-reverse-engineering/capture-upgrade/README.md).

# Incident-Day Projection Recheck

An offline recheck of27984 valid master cores still covers only22/32 existing interruption windows, with no slave stream or historical519/61/63/Q1. Three mixed-block samples report normal-looking generator voltage but qualified130=0/source126=1; each brackets ordinary master AC-charge→battery/EPS status. Two handoffs retained near-normal reported EPS; the16:36-local handoff showed95.9–282.9V. Sequential acquisition and the subsequent GridBOSS gap prevent causal ordering. [Source-hashed comparison and coverage](../../../investigations/2026-11-08-firmware-reverse-engineering/incident-recheck/README.md).

Native closure: fast ADC `bf` or capture-age `b790` can both force qualified130 to0 and exclude source2. Source fallback itself does not write130;128 is control-derived, not an independent generator-frequency observation. The capture-age counters areu8, correcting earlier prose. No physical generator fluctuation is established by the zero report.

The converter's121fe is now bound to GridBOSS0514 word2, **selected grid input1 or generator input3**, not UPS/power. Its one-sided comparison with local EPS publishes107ed.bit14 in ordinary **Flex61.bit14**. This suppresses the local member's positive control-bit0 contribution, not directly asserts a veto. Selector/cached-frame timing, role/peer gates and counters remain essential; the old incident did not record the outcome or incoming frame. This strengthens the stock-capture path without establishing a cause or need for custom firmware.



# Deployed Host Capture Correction

The approved November9 upgrade corrected4500 GPIO and warning60/61 journaling, and added ordinary142..145 context on both inverters through120/26.56 focused tests, TCP smoke and HA configuration checks passed; one Core restart and actual raw/journal records verified the change. The fixed post-restart window had48/48 successful primary records and one GPIO timeout; measured gaps reached15s. Additional availability events were retained. This is improved diagnostic capture, **not a diagnosed cause, guaranteed transient recorder or generator repair**. [Deployment evidence](../../../investigations/2026-11-08-firmware-reverse-engineering/capture-upgrade/README.md).

# Citations

- [Integrated decision tree, exact branch packages and proof limits](../../../investigations/2026-11-08-firmware-reverse-engineering/decision-tree/README.md)
- [GridBOSS source and command branches](../../../investigations/2026-11-08-firmware-reverse-engineering/decision-tree/gridboss-control/REPORT.md)
- [Upstream producers, aliases and register exposure](../../../investigations/2026-11-08-firmware-reverse-engineering/decision-tree/gridboss-inputs/REPORT.md)
- [Native CAN interface, corrected bit lanes and feedback](../../../investigations/2026-11-08-firmware-reverse-engineering/decision-tree/c28x-can/REPORT.md)
- [Converter source, battery, limit and CLA evidence](../../../investigations/2026-11-08-firmware-reverse-engineering/decision-tree/c28x-power/REPORT.md)
- [Firmware history and decision-word semantics](../../../investigations/2026-11-08-firmware-reverse-engineering/state-logging/firmware/REPORT.md)
- [Stock capture, portal request evidence, existing meter and acquisition recommendation](../../../investigations/2026-11-08-firmware-reverse-engineering/acquisition-options/README.md)
- [Approved test plan, real Q1 capture, selector correction and monitoring verification](../../../investigations/2026-11-08-firmware-reverse-engineering/engineering-capture/README.md)
