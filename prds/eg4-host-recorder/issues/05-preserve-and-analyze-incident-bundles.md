---
id: EG4REC-05
title: Freeze, retain and reconstruct complete incident bundles
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
test_seam: Recorder saved incident directories -> manifest, existing diagnostic health and streaming analyzer CLI
authority_gaps:
- firmware-identity
- protection-bit-semantics
blocked_by:
- 03-isolate-device-polling.md
- 04-recover-controller-journal-and-health.md
depends_on_story_ids:
- 6
- 7
slice_order: 5
completion_evidence:
  implementation_commit: null
  red_evidence: null
  green_evidence: null
  alignment_verified: false
---

## What to build

Use the existing all-role acquisition and recoverable journal to preserve bounded incident directories that can actually be exported and reconstructed. Complete retention, crash/pressure behavior, health and analyzer support together so the result is useful evidence rather than a buffer without a consumer. This requires slice 03 for timed/configuration context and slice 04 for trustworthy persistence/loss accounting.

## Acceptance criteria

- [ ] A7: incidents preserve all-role received records from a ten-minute pretrigger buffer bounded to16 MiB and ten minutes after the latest trigger. Actual coverage, warmup and memory-limit truncation are explicit; missing history is never claimed to exist.
- [ ] All R5 immediate edge triggers are covered before HA debounce: accepted-power loss with voltage present independently of qualified frequency; V/F inconsistency or availability changes; abnormal/lost previously live output; relevant controller/protection changes including input6; primary/diagnostic loss/resumption, semantic rejection and recorder persistence loss.
- [ ] Power capture uses existing hysteresis. The approximately240 V UPS/EPS channels trigger below180 V or above264 V; low output requires a previously live channel, overvoltage does not, and these thresholds never apply to120 V leg channels or change protection settings.
- [ ] Overlapping triggers merge with every reason/time retained. Repeated identical failure does not create a new incident every poll. An active anomaly keeps its incident open, then the quiet post-trigger period closes it; long incidents use bounded segments under one ID, not unbounded RAM.
- [ ] A plain incident directory contains JSONL and an atomic manifest with incident/session IDs, coverage, trigger times/reasons, missing roles, errors/gaps, truncation and current configuration context with acquisition age. Only finalized files receive final hashes, with off-event-loop synchronization at finalization.
- [ ] A restart preserves and marks unfinished incidents interrupted, starts a new recorder session with an explicit gap and warms a new pretrigger buffer honestly. Historical raw files remain available to the analyzer without invented cross-restart continuity.
- [ ] Use25 MiB segments and R5 defaults: raw72h/2 GiB, journal14d/200 MiB, incidents14d/1 GiB including open segments, plus a1 GiB filesystem free-space reserve. Age or byte limits can shorten coverage and never imply preallocation or guaranteed days.
- [ ] Pressure pruning touches only the new recorder-owned files and prefers ordinary raw over incident evidence. Forced incident eviction leaves explicit incomplete/lost coverage; unsafe writes yield degraded health. Sealed investigations, deployment backups and unrelated HA files remain untouched.
- [ ] Existing diagnostic and persisted health add actual raw coverage/bytes/free space, retention estimate, active/latest incident path and incomplete/truncated state, integrated with role/persistence health rather than a parallel dashboard.
- [ ] A8: the actual analyzer CLI streams a file or recorder/incident directory in historical/new schemas and reports per-role coverage, failures/partials, sessions/gaps, controller/protection changes, output anomalies outside known dropout intervals, configuration age and manifest completeness.
- [ ] Analysis brackets onset/recovery using acquisition intervals and leaves overlapping ordering unresolved. It does not zero-fill, interpolate, infer continuous house-outage duration, claim an engine stopped or treat519/GPIO echoes as current physical contacts.
- [ ] A7–A8 proof covers overlapping triggers, partial roles, rotation, storage pressure, interrupted restart and unrelated-file preservation using temporary storage, plus genuine retained data and clearly synthetic fixtures through the CLI. Export/copy usability and recorder/analyzer documentation are demonstrated without adding a collector, upload service or viewer.

## Blocked by

- [03-isolate-device-polling.md](./03-isolate-device-polling.md)
- [04-recover-controller-journal-and-health.md](./04-recover-controller-journal-and-health.md)

## Pull request stack

Stack root: `main` at `1a1bec20a41e237dc61dbd0575d7cbdeb00067f2`; independent acquisition and recoverable persistence are merged.

| Key | Branch | Purpose | Predecessor / PR base | Acceptance advanced |
| --- | --- | --- | --- | --- |
| 5.1 | `codex/eg4-host-recorder-5.1-incident-bundles` | Preserve immediate all-role pre/post incidents, bounded owned storage and crash/pressure metadata, then reconstruct the saved evidence through the existing streaming CLI | `main` at the stack root | All EG4REC-05 acceptance criteria and A7–A8, preserving A1–A6 |

One complete vertical entry joins capture, plain-directory export, atomic manifests, safe retention, existing health and its offline consumer. Reuse the existing writer/journal and do not add another device reader, upload service, compression service or dashboard. Move the new release into a distinct recorder-owned root; old investigations/backups remain outside pruning. Capacity overrides are host-recorder options, not controller operating/protection settings. Production installation and the fixed live window remain EG4REC-06.
