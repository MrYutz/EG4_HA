---
id: EG4REC-03
title: Keep healthy devices recording when another device stalls
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
test_seam: Integration polling lifecycle -> per-block records and existing diagnostic health attributes
authority_gaps: []
blocked_by:
- 02-correct-generator-and-charge-state.md
depends_on_story_ids:
- 3
- 7
slice_order: 3
completion_evidence:
  implementation_commit: 32d934c40afacca9d75c291478dd75700922caef
  red_evidence: /tmp/eg4-host-recorder/3.1-transfer-review-red.txt
  green_evidence: /tmp/eg4-host-recorder/3.1-green.txt
  alignment_verified: true
---

## What to build

Run independently scheduled role acquisition through the established transport and the corrected state/freshness consumer. Deliver all specified dynamic, BMS, diagnostic and configuration context, plus visible measured role health, without a new collector or a higher-rate incident mode. The dependency on slice 02 ensures asynchronous partial updates cannot revive false complete-zero or cached-debounce behavior.

## Acceptance criteria

- [X] A3: a stalled GridBOSS, inverter or maintenance request does not prevent the other healthy roles from producing new acquired records. No all-role barrier controls the next round.
- [X] All integration reads for the same device, including coordinator/configuration reads, share one owner/lock and permit at most one request in flight. Initialized local collection continues during portal-coordinator errors.
- [X] Dynamic reads run ahead of stable settings; at most one due maintenance block runs between dynamic rounds. There is no incident burst, aggressive reconnect loop or speculative timeout reduction.
- [X] Every R3 fixed-profile row is implemented at its specified startup/target schedule: all inverter and GridBOSS dynamic blocks, BMS, inverter holding110/125, GridBOSS local4500/10 and all staggered baseline shapes. Requested1s spacing is a target, never a claim of achieved1Hz.
- [X] Safe-start exclusions remain enforced: no exact FlexBOSS start224 or248, historical GridBOSS4516, undefined input132–499 or inaccessible engineering1100 substitutions. Only the approved read-only03/04 operations are used.
- [X] Power/frequency and core/BMS blocks keep distinct acquisition intervals and success statuses. Fresh coincident maintenance reads are reused; changed configuration retains acquisition references. Failed baseline blocks stay missing, while last successful context remains clearly aged, not falsely current.
- [X] Per-role last attempt/success, primary/diagnostic age and measured median/max spacing are exposed through the existing diagnostic entity and recorder health mechanism. Old cache data is not refreshed by processing time.
- [X] Controlled-response integration evidence demonstrates isolation, maintenance priority, no same-device concurrency and the specified profiles, using produced records/observable state rather than source-text or call-forwarding assertions. The corrected gap/completeness contract remains intact.
- [X] Capture/profile documentation describes actual timestamp and schedule semantics, including that a scheduling sequence is not an atomic snapshot.

## Blocked by

- [02-correct-generator-and-charge-state.md](./02-correct-generator-and-charge-state.md)

## Pull request stack

Stack root: `main` at `c6a9cbdc16dd8766e1ac1de5ab3ab736c1c529b1`; the complete truthful-state slice is merged.

| Key | Branch | Purpose | Predecessor / PR base | Acceptance advanced |
| --- | --- | --- | --- | --- |
| 3.1 | `codex/eg4-host-recorder-3.1-independent-polling` | One local capture owner with independent role loops, fixed dynamic/maintenance profiles, original per-block context and live diagnostic health | `main` at the stack root | All EG4REC-03 acceptance criteria and A3, preserving A1–A5 |

Move the existing local acquisition/cache/scheduling policy out of the setup closure into one cohesive module, rather than adding another collector. Reuse the transport, decoder, writer and logger. The same start/close/snapshot interface is exercised by production and loopback tests; portal consumers use snapshots, not additional reads. One green entry avoids a layer-only refactor PR. Journal persistence/incident policy and deployment remain later tickets.

## Completion evidence

- Entry3.1: [PR4](https://github.com/MrYutz/EG4_HA/pull/4), head `32d934c40afacca9d75c291478dd75700922caef`, merged into `main` as `580aacd07a9d830e16d3ad92781ff547b0bd2068`.
- Remote head/base SHA, all12 scoped files and the complete incremental diff were verified. Only object-ID index metadata and optional hunk-section labels were normalized; hunk positions and payload matched. Evidence: `/tmp/eg4-host-recorder/3.1-remote.json` and `3.1-merged.json`.
- 133 focused checks passed; after review correction, all15 recorder tests passed including persisted transfer changes through the actual analyzer CLI. Red/green and actual HA entity/Store/TCP lifecycle evidence are indexed in `3.1-green.txt`.
- Actual isolated HA verification covered first-refresh portal503, portal-independent count2/three-role health, bounded logger checkpoints under staggered callbacks, continued healthy polling during a held GridBOSS socket, unload and external platform-forwardfailure cleanup. The venv lacked the frontend package; no rendered-UI or production-installation claim.
- Both independent review axes closed checkpoint starvation and lost transfer output with no remaining actionable findings. Source typing passed; sensor-inclusive SDK comparison retained22 pre-existing diagnostics. Critical Ruff selectors passed; unrestricted Ruff0.17.4 still reported16 style/exception-boundary diagnostics and is not claimed clean.
- No firmware/settings changes, production restart or physical fault injection. Installation and fixed-window measurement remain EG4REC-06.
