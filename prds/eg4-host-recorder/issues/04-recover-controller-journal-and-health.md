---
id: EG4REC-04
title: Preserve protection changes and recover the journal after write failures
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
test_seam: Recorder append/observe/close against temporary storage -> persisted journal and health
authority_gaps:
- firmware-identity
- protection-bit-semantics
blocked_by:
- 01-preserve-raw-dongle-evidence.md
depends_on_story_ids:
- 5
- 7
slice_order: 4
completion_evidence:
  implementation_commit: 7802774faa493e6cd4397fea10f5d27941b14fc4
  red_evidence: /tmp/eg4-host-recorder/4.1-protection-red.txt
  green_evidence: /tmp/eg4-host-recorder/4.1-green.txt
  alignment_verified: true
---

## What to build

Make received discrete controller changes and recorder loss survive the entire observe/queue/write/recovery path. Include omitted input6 protection bits without claiming their physical meaning. Deliver durable-loss health and a recovery baseline even when the controller has not changed since a failed write. This can proceed once slice 01 establishes raw record identity; it does not wait for the independent-scheduling branch.

## Acceptance criteria

- [X] A6: input6 low-bit0/1-only changes and519 reason-byte-only changes produce persisted journal entries. Full raw words remain available and candidate firmware semantics are explicitly uncertain.
- [X] Warnings60/61, faults62/63, limiter/state144/145, both bytes of519 and command counters remain in discrete capture. Analog142/143 fluctuations remain raw evidence without becoming mandatory per-fluctuation journal triggers.
- [X] Observed, pending and successfully persisted comparison state are distinct. Queue rejection or disk failure does not permanently suppress the next identical observation.
- [X] A6: after queue/disk loss, continuity is marked broken and an unchanged observation yields a persisted recovery baseline when writing resumes. Cumulative dropped-record/write-error counts and last-successful-persistence time retain the earlier failure after subsequent success.
- [X] Filesystem work stays off the HA event loop and queues remain bounded. Queue acceptance is not reported as disk success; failures are visible without an unbounded retry or warning flood.
- [X] Ordinary appends flush. Graceful close synchronizes required output off the event loop; the incident-finalization path can use the same persistence boundary in slice 05. No claim is made that queued records survive arbitrary host power loss.
- [X] Existing diagnostic health exposes session/build, starting/healthy/degraded, persistence age, queue depth, cumulative loss and last error. Compact health records persist every30s and on startup/shutdown/recovery; attribute updates are at most every30s except significant failure/recovery and repeated warnings are rate-limited.
- [X] Focused temporary-storage regressions exercise actual persisted change/recovery behavior under queue rejection and disk failure, not merely mocked acknowledgements. Journal/health documentation explains recovery and remaining physical-semantics gaps.

## Blocked by

- [01-preserve-raw-dongle-evidence.md](./01-preserve-raw-dongle-evidence.md)

## Pull request stack

Stack root: `main` at `580aacd07a9d830e16d3ad92781ff547b0bd2068`; independent polling is merged.

| Key | Branch | Purpose | Predecessor / PR base | Acceptance advanced |
| --- | --- | --- | --- | --- |
| 4.1 | `codex/eg4-host-recorder-4.1-recover-journal` | Persist discrete protection changes, acknowledge actual writes, recover unchanged baselines after loss, and expose bounded persistence health through the existing recorder | `main` at the stack root | All EG4REC-04 acceptance criteria and A6, preserving A1–A5 |

Keep the existing threaded JSONL writer and independent acquisition owner. A queued record is not a successful write. Controller comparison state advances only through explicit write outcomes; filesystem failures must not stall role polling or suppress a later recovery baseline. One complete green entry keeps the persistence producer, journal and diagnostic consumer aligned. Incident retention/finalization policy remains EG4REC-05.

## Completion evidence

- Entry4.1: [PR5](https://github.com/MrYutz/EG4_HA/pull/5), head `7802774faa493e6cd4397fea10f5d27941b14fc4`, merged into `main` as `1a1bec20a41e237dc61dbd0575d7cbdeb00067f2`.
- Verified remote head/base SHA, all11 scoped files and full incremental diff; only index metadata and optional hunk-section labels were normalized. Evidence: `/tmp/eg4-host-recorder/4.1-remote.json` and `4.1-merged.json`.
- Initial99 focused checks passed. After review corrections, all23 runtime/protection checks passed; all3 changed production modules typechecked with0errors. Critical Ruff selectors passed; full lint is not claimed clean. Detailed red/green evidence: `4.1-green.txt`.
- Actual temporary-file/loopback regressions cover input6 under semantic rejection,519 reason changes, queued versus persisted state, real queue/disk loss and unchanged recovery, ordered pending changes, cumulative counters, bounds, cancellation, sync and both-stream shutdown saturation.
- Both independent review axes closed repeated semantic-health churn and omitted shutdown health. Settled verdicts remain through pending decode; shutdown drains journal receipts and raw output before its final health record. No remaining actionable review findings.
- Existing diagnostic identity and enabled/loaded HA entry were confirmed through read-only API calls. No production plugin replacement/Core restart or power/firmware/operating-setting change occurred in this slice. Incident retention/export and final production verification remain the next two issues.
