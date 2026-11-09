---
id: EG4REC-02
title: Make generator and charging states truthful through gaps
type: AFK
status: ready
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
  implementation_commit: null
  red_evidence: null
  green_evidence: null
  alignment_verified: false
---

## What to build

Feed the existing logger truthful acquisition identity, freshness and per-role completeness from slice 01, then correct observable generator, charge-session and transfer events. Preserve normal coherent-sample hysteresis and existing entity IDs. This establishes the consumer semantics required before independent per-role scheduling feeds it more frequent partial updates; it does not redesign collection.

## Acceptance criteria

- [ ] A4:239 V / qualified0 Hz /0 W yields PresenceState=present, SupplyState=not_supplying, RunState=unknown, Quality=interrupted and Confidence=unknown, with a qualified-frequency reason and never generator_voltage_absent. Mechanical engine state remains unmeasured.
- [ ] Terminal voltage, qualified-frequency validity, accepted power and inferred run state remain separate. Existing hysteresis for coherent normal readings is preserved rather than changing equipment protections or inventing interpretations for rejected data.
- [ ] Only distinct successful source acquisitions contribute to debounce. Cached repeats retain their original identity/time/quality and age at consumption, even if processing time advances or another refresh is slow.
- [ ] A complete charge total requires fresh observations from every configured participating inverter. Missing master plus zero-watt slave remains partial/unknown with per-role context, not a complete zero.
- [ ] A5: partial/unknown samples keep an active charge session open and interrupted. No energy integration crosses an interval with an unknown endpoint; no stop dwell or pending transition carries through a gap/restart.
- [ ] A restored active session is interrupted without a synthetic start event. Last-known transfer state survives a gap; a changed resumed observation carries intervening_unknown=true rather than an invented physical transition time.
- [ ] Logger documentation distinguishes debounced state from immediate raw controller/transfer observations and does not claim either is physical contact feedback.
- [ ] Focused sample/event and restoration regressions prove A4–A5, including cached duplicates and incomplete-role totals. Normal coherent transitions remain covered by relevant existing tests.

## Blocked by

- [01-preserve-raw-dongle-evidence.md](./01-preserve-raw-dongle-evidence.md)
