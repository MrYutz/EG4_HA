---
id: EG4REC-06
title: Push, deploy and verify the recorder on Home Assistant
type: AFK
status: in-progress
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
test_seam: Actual HA API and installed recorder -> fixed live capture, isolated synthetic export and rollback evidence
authority_gaps: []
blocked_by:
- 05-preserve-and-analyze-incident-bundles.md
depends_on_story_ids:
- 8
slice_order: 6
completion_evidence:
  implementation_commit: null
  red_evidence: null
  green_evidence: null
  alignment_verified: false
---

## What to build

Deliver the integrated host-only release now, using the already authorized HA Core restart rather than waiting for a deployment window. Prove the pushed code is the installed, running recorder and that genuine evidence can be copied and analyzed. This is real production delivery, not a deployment recipe or an unexecuted checklist. Preserve controller firmware and operating settings unchanged.

## Acceptance criteria

- [ ] R1–R6 and A1–A8 are accounted for. Run focused changed-module tests together after integration, Ruff on changed Python, actual loopback acquisition and analyzer CLI. Record exact exercised results and classify unrelated pre-existing failures; do not claim an unrun full suite passed.
- [ ] Preserve current user work and inspect installed source identity. Update integration release version, logger/user documentation and README change note, retain entity IDs, and remove superseded live capture paths. Commit only intended release files and push normally to configured origin using existing branch workflow, without force-push or firmware/raw-log/credential additions.
- [ ] Use the established HAOS access route and confirm the actual enabled EG4 entry/device routes. Measure host free space and observed bytes/hour; document any necessary ceiling adjustment and resulting retention estimate while preserving the free-space reserve.
- [ ] Before replacement, copy existing bounded evidence off-host and back up the installed integration plus relevant session Store. Stage the tested coherent file set, verify installed hashes and run the HA Core configuration check; restore files before restart if that check fails.
- [ ] After a successful configuration check, perform one HA Core restart, then confirm API recovery, EG4 loaded/enabled, the new build/session identity and fresh records from all three roles. Record restart command timing and actual acquisition gaps separately, never as an electrical-outage measurement.
- [ ] Preserve the first fixed ten-minute post-recovery window, including failures. Verify all dynamic/maintenance profiles, role acquisition times, actual cadence, configuration/BMS age, health, persistence-loss counters and any observed rejected/partial evidence. Do not choose a cleaner window or label a configured1s target as observed1Hz.
- [ ] Exercise the installed recorder implementation with controlled timestamps in a separately designated synthetic verification directory. Prove pre/post retention, final manifest/export and analyzer output without fake HA state publications or contaminating genuine records. Transport/queue/disk fault injection remains offline, not against live equipment or storage.
- [ ] Copy genuine capture/health/controller records and the clearly labeled synthetic bundle off-host, verify file hashes and analyze the copies. Raw logs remain outside Git and completion evidence references their locations.
- [ ] Save a deployment receipt with pushed commit, installed hashes, backup location, configuration-check/restart results, observed capture gaps, all-role outcomes, measured cadence, storage/retention estimate, capture/export paths and explicit synthetic designation.
- [ ] Leave collection enabled with no extra diagnostic client. External device timeouts may be honestly degraded if captured and healthy roles continue; new integration-load failure, repeated recorder-write failure, missing profiles, unexpected device writes or fabricated evidence block completion and require correction or rollback.
- [ ] Verify rollback readiness against the actual backup and documented restoration/check/restart route, preserving new evidence and restoring session Store only if compatibility requires it. Execute rollback if the blocking conditions occur; do not add an unnecessary production restart solely to rehearse it.
- [ ] Completion means the pushed build is active on HA, all three roles are recording with visible health, export works, focused proofs and rollback evidence exist, and no controller firmware or operating/protection settings have changed. Access failure or simulated deployment cannot be reported as completion.

## Blocked by

- [05-preserve-and-analyze-incident-bundles.md](./05-preserve-and-analyze-incident-bundles.md)

## Pull request stack

Stack root: `main` at `9a1a916516daddfb0d8b1a78ea0ce4405351940e`; all five implementation slices are merged.

| Key | Branch | Purpose | Predecessor / PR base | Acceptance advanced |
| --- | --- | --- | --- | --- |
| 6.1 | `codex/eg4-host-recorder-6.1-production-release` | Publish version0.2.0 and operator guidance, install the pushed coherent package, perform the authorized Core stop/start cycle, preserve the first fixed live window, prove installed-source synthetic export, then record reviewed deployment and rollback evidence | `main` at the stack root | All EG4REC-06 criteria and end-to-end R1–R6/A1–A8 |

Push and open this green source entry before replacement. Keep its source identity fixed through the production proof; add the receipt/evidence to the same entry after observation. Final full-suite verification and two-axis review precede merge. Mark delivery done only after the implementation stack and recorded merge evidence are complete. No firmware or operating/protection settings change; only documented host-storage ceilings may be adjusted.
