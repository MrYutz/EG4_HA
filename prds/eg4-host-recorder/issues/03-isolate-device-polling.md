---
id: EG4REC-03
title: Keep healthy devices recording when another device stalls
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
test_seam: Integration polling lifecycle -> per-block records and existing diagnostic health attributes
authority_gaps: []
blocked_by:
- 02-correct-generator-and-charge-state.md
depends_on_story_ids:
- 3
- 7
slice_order: 3
completion_evidence:
  implementation_commit: null
  red_evidence: null
  green_evidence: null
  alignment_verified: false
---

## What to build

Run independently scheduled role acquisition through the established transport and the corrected state/freshness consumer. Deliver all specified dynamic, BMS, diagnostic and configuration context, plus visible measured role health, without a new collector or a higher-rate incident mode. The dependency on slice 02 ensures asynchronous partial updates cannot revive false complete-zero or cached-debounce behavior.

## Acceptance criteria

- [ ] A3: a stalled GridBOSS, inverter or maintenance request does not prevent the other healthy roles from producing new acquired records. No all-role barrier controls the next round.
- [ ] All integration reads for the same device, including coordinator/configuration reads, share one owner/lock and permit at most one request in flight. Initialized local collection continues during portal-coordinator errors.
- [ ] Dynamic reads run ahead of stable settings; at most one due maintenance block runs between dynamic rounds. There is no incident burst, aggressive reconnect loop or speculative timeout reduction.
- [ ] Every R3 fixed-profile row is implemented at its specified startup/target schedule: all inverter and GridBOSS dynamic blocks, BMS, inverter holding110/125, GridBOSS local4500/10 and all staggered baseline shapes. Requested1s spacing is a target, never a claim of achieved1Hz.
- [ ] Safe-start exclusions remain enforced: no exact FlexBOSS start224 or248, historical GridBOSS4516, undefined input132–499 or inaccessible engineering1100 substitutions. Only the approved read-only03/04 operations are used.
- [ ] Power/frequency and core/BMS blocks keep distinct acquisition intervals and success statuses. Fresh coincident maintenance reads are reused; changed configuration retains acquisition references. Failed baseline blocks stay missing, while last successful context remains clearly aged, not falsely current.
- [ ] Per-role last attempt/success, primary/diagnostic age and measured median/max spacing are exposed through the existing diagnostic entity and recorder health mechanism. Old cache data is not refreshed by processing time.
- [ ] Controlled-response integration evidence demonstrates isolation, maintenance priority, no same-device concurrency and the specified profiles, using produced records/observable state rather than source-text or call-forwarding assertions. The corrected gap/completeness contract remains intact.
- [ ] Capture/profile documentation describes actual timestamp and schedule semantics, including that a scheduling sequence is not an atomic snapshot.

## Blocked by

- [02-correct-generator-and-charge-state.md](./02-correct-generator-and-charge-state.md)
