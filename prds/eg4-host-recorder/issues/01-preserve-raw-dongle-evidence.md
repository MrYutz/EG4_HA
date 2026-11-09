---
id: EG4REC-01
title: Preserve raw dongle evidence through invalid and interleaved responses
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
test_seam: Dongle acquisition against loopback TCP -> persisted JSONL -> analyzer CLI
authority_gaps: []
blocked_by: []
depends_on_story_ids:
- 1
- 2
- 7
slice_order: 1
completion_evidence:
  implementation_commit: null
  red_evidence: null
  green_evidence: null
  alignment_verified: false
---

## What to build

Make an actual device acquisition produce inspectable forensic evidence even when decoding rejects a payload, a later block fails, or unsolicited frames precede the matching response. Preserve strict telemetry validation and bounded transport behavior. Extend the existing analyzer enough to consume these new single-file records alongside historical records; directory-wide incident reconstruction belongs to slice 05. This slice delivers R1 and R2 and the raw-record part of R6, not a transport-only refactor.

## Acceptance criteria

- [ ] Every transaction attempt queues a versioned forensic result before semantic decoding or the next request. R1 metadata includes session/role/acquisition/transaction identity, requested and observed route/device, request bytes when sent, received bytes/obtainable words, separate validation outcomes, acquisition UTC interval/monotonic duration, error stage and truncation. Derived role samples reference contributing transactions; processing/persistence times do not replace acquisition time.
- [ ] A1: unknown status33280, inconsistent voltage/frequency and interrupted responses retain received evidence while remaining rejected as trustworthy telemetry. Successful GridBOSS power/voltage survives a subsequent frequency timeout and the aggregate is explicitly partial.
- [ ] A primary semantic rejection does not skip otherwise scheduled diagnostics when the transport is usable; remaining work and failures stay bounded and explicit. Missing/error, zero and cached previous values remain distinguishable.
- [ ] A2: unsolicited history before identity and a matching reply after nine unrelated valid frames succeed within the transaction deadline. These frames are preserved without activating or clearing history.
- [ ] CRC, device identity, function, range and length checks remain strict. Bad CRC/identity, malformed framing, timeout and traffic-limit exhaustion remain distinct failures with already-received bytes retained.
- [ ] Endless traffic is bounded by the total transaction deadline, existing frame-size ceiling and initial256 KiB transaction-traffic cap, with explicit exhaustion/truncation. No second polling owner or concurrent same-device request is introduced.
- [ ] The actual analyzer CLI streams a saved new-format acquisition and a historical trace without rewriting evidence; its output exposes partial/rejected transactions and their acquisition intervals rather than assigning invented values or modes.
- [ ] The live capture path emits one versioned representation, not duplicate legacy/new records, and excludes unrelated HA/light dumps, credentials and tokens. Affected record-format documentation and callers are updated.
- [ ] Focused missing behavioral regressions and actual loopback/CLI evidence prove A1–A2 and the new raw-record consumer path. No source-text, forwarding-only or mock-echo assertions substitute for these outcomes.

## Blocked by

None - can start immediately

## Pull request stack

Stack root: `main` at `ddd2fd69e3a8570a05995bc261617d30c6364da0` (the published origin tip at setup).

| Key | Branch | Purpose | Predecessor / PR base | Acceptance advanced |
| --- | --- | --- | --- | --- |
| 1.1 | `codex/eg4-host-recorder-1.1-baseline` | Adopt the existing unpublished integration, tests and user documentation as a coherent prerequisite | `main` at the stack root | Existing integration can be reviewed and tested; no R1/R2 defect is claimed fixed |
| 1.2 | `codex/eg4-host-recorder-1.2-raw-evidence` | Preserve transaction evidence, bounded matching, integration wiring and single-file analyzer | Verified 1.1 branch/PR, retargeted to `main` after its merge | All acceptance criteria in this issue, A1–A2 and raw-record consumption |

Greg explicitly approved adopting the pre-existing local integration and unpublished telemetry changes. The original local `main` commit `4fee9cbeec4e277468a074e72feec18d986137eb` remains preserved; its content is included in 1.1 above the published root. Firmware downloads, raw investigation dumps, spreadsheet and agent state remain excluded. The prerequisite does not deploy or claim the known recorder defects are corrected.

Public seams and the full focused acceptance contract were approved during ticket generation. Red/green evidence is stored outside Git and referenced here when each entry completes. The only deployment occurs after the complete initiative passes its release checks.

### Entry 1.1 evidence

- PR: <https://github.com/MrYutz/EG4_HA/pull/1>.
- Adopted baseline commit: `4d0cf5d437eac838d7b7f34c2d4a4296484f32f0`; annotation correction: `d19bc55`.
- Green: 73 focused existing tests; after annotation changes, transport/trace34 tests, Pyright0 errors and Ruff passed. Evidence: `/tmp/eg4-host-recorder/baseline-verification.txt` (outside Git).
- Two-axis review: Standards found two non-blocking existing catalogue/constants debts and a large-setup-closure smell; no additional annotation defect. Spec found two documentation hazards: prohibited equipment-manipulation instructions and links to unpublished supporting evidence. Both are corrected in this entry by passive/offline verification instructions and explicit external-retention labels.
- These baseline maintainability debts do not widen the recorder initiative. No R1/R2 acceptance or production rollout is claimed; entry1.2 owns the correction. Merge evidence will be recorded before slice completion.

### Stack progress

- 1.1 remote head/base verified through GitHub REST: head `a1a3648f6f35dfafbda4b3172cb4ed69e3b95154`, base `main` at the recorded root; scoped41-file incremental diff inspected.
- 1.1 merged as `d77222f5481683c897015db0a9bd1699b3017bcb`; GitHub reports the PR closed and merged. No production deployment.
- 1.2 branch was created from the verified1.1 branch before implementation. Its PR initially targets that predecessor, then retargets `main` with the provider diff checked unchanged after the predecessor merge.
- 1.2 red: TCP-to-filesystem regression `test_rejected_status_keeps_received_words_on_disk` fails before implementation because the evidence sink is unavailable. Evidence: `/tmp/eg4-host-recorder/1.2-red.txt`; earlier reproduced transport/lost-payload failures remain in the governing adversarial review.
- Analyzer-only focused checks:3 subprocess CLI tests passed, Ruff passed, Pyright0 errors. A genuine, unmodified legacy trace copied off HA contains5118 records covering2026-11-09 19:48:12.670–21:49:40.023 UTC; the streaming CLI reported all three roles and8 acquisition failures without inventing telemetry or session identities. Copy and measured storage throughput remain outside Git under `/tmp/eg4-host-recorder/`.
