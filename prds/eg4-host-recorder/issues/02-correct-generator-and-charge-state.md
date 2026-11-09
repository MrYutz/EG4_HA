---
id: EG4REC-02
title: Make generator and charging states truthful through gaps
type: AFK
status: done
phase: delivery
source_github: null
source_business_need: conversation-provisional
source_prd: prds/eg4-host-recorder/2026-11-09-host-recorder-mvp.md
governing_refs:
- docs/host-recorder-mvp-spec.md
- prds/eg4-host-recorder/2026-11-09-host-recorder-mvp.md#implementation-decisions
test_contract_refs:
- docs/host-recorder-mvp-spec.md#focused-acceptance-tests
- prds/eg4-host-recorder/2026-11-09-host-recorder-mvp.md#testing-decisions
test_seam: Logger samples/events and restored session state using acquisition identity and freshness
authority_gaps: []
blocked_by:
- 01-preserve-raw-dongle-evidence.md
depends_on_story_ids:
- 4
slice_order: 2
completion_evidence:
  implementation_commit: ed7f5931fce83db5d2d4fce541be86aff7aaedb5
  red_evidence: /tmp/eg4-host-recorder/2.1-red.txt
  green_evidence: /tmp/eg4-host-recorder/2.1-green.txt
  alignment_verified: true
---

## What to build

Feed the existing logger truthful acquisition identity, freshness and per-role completeness from slice 01, then correct observable generator, charge-session and transfer events. Preserve normal coherent-sample hysteresis and existing entity IDs. This establishes the consumer semantics required before independent per-role scheduling feeds it more frequent partial updates; it does not redesign collection.

## Acceptance criteria

- [x] A4:239 V / qualified0 Hz /0 W yields PresenceState=present, SupplyState=not_supplying, RunState=unknown, Quality=interrupted and Confidence=unknown, with a qualified-frequency reason and never generator_voltage_absent. Mechanical engine state remains unmeasured.
- [x] Terminal voltage, qualified-frequency validity, accepted power and inferred run state remain separate. Existing hysteresis for coherent normal readings is preserved rather than changing equipment protections or inventing interpretations for rejected data.
- [x] Only distinct successful source acquisitions contribute to debounce. Cached repeats retain their original identity/time/quality and age at consumption, even if processing time advances or another refresh is slow.
- [x] A complete charge total requires fresh observations from every configured participating inverter. Missing master plus zero-watt slave remains partial/unknown with per-role context, not a complete zero.
- [x] A5: partial/unknown samples keep an active charge session open and interrupted. No energy integration crosses an interval with an unknown endpoint; no stop dwell or pending transition carries through a gap/restart.
- [x] A restored active session is interrupted without a synthetic start event. Last-known transfer state survives a gap; a changed resumed observation carries intervening_unknown=true rather than an invented physical transition time.
- [x] Logger documentation distinguishes debounced state from immediate raw controller/transfer observations and does not claim either is physical contact feedback.
- [x] Focused sample/event and restoration regressions prove A4–A5, including cached duplicates and incomplete-role totals. Normal coherent transitions remain covered by relevant existing tests.

## Blocked by

- [01-preserve-raw-dongle-evidence.md](./01-preserve-raw-dongle-evidence.md)

## Pull request stack

Stack root: `main` at `4d56dfc127001a1f23c1de100976e20f90f8abb0`; EG4REC-01's complete stack is merged and its acceptance evidence recorded.

| Key | Branch | Purpose | Predecessor / PR base | Acceptance advanced |
| --- | --- | --- | --- | --- |
| 2.1 | `codex/eg4-host-recorder-2.1-truthful-state` | Correct logger classification/gap continuity and feed original source identities, freshness and complete-role totals from the integration | `main` at the stack root | All EG4REC-02 acceptance criteria, A4–A5 |

One green entry is sufficient; no layer-only PR. Existing approved seams: logger samples/events/restoration and integration aggregation with controlled source observations. Preserve coherent-sample hysteresis, entity IDs and source uncertainty. No scheduling redesign, power settings, firmware changes or production deployment in this slice.

### Verification progress

- Red:239 V / qualified0 Hz /0 W produced `PresenceState=off` instead of `present` before correction. Evidence: `/tmp/eg4-host-recorder/2.1-red.txt`.
- Logger-only green:24 public sample/event/restore regressions passed; Ruff passed; logger Pyright0 errors. Integration producer verification and two-axis review remain required.
- HA input type compatibility: read-only adapter/options mappings are accepted without copying or changing configured values; IPv4 construction and optional fuel values now express their existing narrowed contracts. Existing discovery/normalization11 tests and helper Pyright passed; a real read-only mapping smoke produced `192.168.3.0/24` and50% fuel from50/100 gallons. Evidence: `/tmp/eg4-host-recorder/2.1-readonly-inputs.txt`. No equipment settings or runtime profile change is included.

- Integrated green:63 focused logger/aggregation/discovery/normalization tests passed, Ruff passed, and Pyright on all four changed production modules reported0 errors.
- Offline production-closure smoke used real loopback TCP, HomeAssistant/Store/coordinator, logger and optional trace writer. Both persistence-enabled and disabled runs preserved original source times/IDs, rejected cached repeats, retained partial missing-master/zero-slave totals and rejected an aged GridBOSS source window. Evidence: `/tmp/eg4-host-recorder/2.1-source-green.txt`. Time separation was controlled by an offline UTC clock, not a live wall-clock device stall. This is not full HA setup/coordinator proof; production loading remains EG4REC-06.
- An additional public assertion confirms an interrupted partial charge session remains active/open. The temporary smoke script was removed after proof; results remain outside Git.

### Review correction evidence

Standards review of `50667ac` found no actionable new findings. Spec found that young caches concealed observed failures and that consecutive partial samples could report an open session inactive.

- A strengthened public regression reproduced `Active=false` after two partial samples; activity now reflects the open session independently of instantaneous charging certainty, including restoration.
- Primary success/failure is now tracked separately from retained last-good values/provenance. Failure is fed to the logger immediately, before BMS/diagnostic work or a subsequent success can hide the interruption.
- Offline real-TCP/HA/logger/storage replay compared the `50667ac` producer with its correction in both persistence modes: old code ended a session at recovery, integrated0.001→0.003kWh across the failed observation, and omitted resumed-transfer uncertainty. Corrected code kept the session open, held energy at0.001kWh until a new endpoint, and emitted `intervening_unknown=true`. Evidence: `/tmp/eg4-host-recorder/2.1-continuity-red-green.txt`.
- After corrections:63 focused tests, Ruff and Pyright0 errors passed. The temporary replay script was removed. No live outage, equipment write, or deployment was performed.

### Completion

Both review axes found no remaining correction findings at `ed7f593`. GitHub verified PR3 head/base and the final incremental diff, then merged the complete entry as `c6a9cbdc16dd8766e1ac1de5ab3ab736c1c529b1`. Evidence: `/tmp/eg4-host-recorder/2.1-merged.json`. Acceptance, focused verification, review and merge are complete; no HA deployment yet.
