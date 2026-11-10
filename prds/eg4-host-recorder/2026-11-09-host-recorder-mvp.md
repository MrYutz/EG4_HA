# PRD: EG4 host recorder MVP

| Field | Value |
| --- | --- |
| Initiative | [eg4-host-recorder](./README.md) |
| Date | 2026-11-09 |
| Status | accepted scope |
| Source business need | [Conversation-captured problem](#problem-statement) (`conversation-provisional`) |
| Authority index | [Initiative manifest](./README.md#governing-authority) |

## Problem Statement

Source: **conversation-provisional**, explicitly selected by Greg when approving this initiative and its six delivery slices. No GitHub parent issue was supplied; this packet does not create one.

The generator remained powered and healthy at approximately 240 V / 60 Hz throughout the investigated dropout windows, independently verified by multimeter. That is a fixed premise, not a hypothesis to retest. The investigation concerns inverter/GridBOSS acceptance, permissions, synchronization and signaling that withdrew power conversion despite healthy generator output. Controller-qualified frequency becoming zero does not prove an engine stop or generator failure.

The existing Home Assistant custom integration is the **host recorder**: it acquires device responses, derives states and writes evidence. Rejected/partial transactions, scheduling stalls, cached timestamps, misleading state labels, missed protection changes and persistence failures currently undermine reconstruction. The [adversarial review](../../investigations/2026-11-08-firmware-reverse-engineering/adversarial-review-2026-11-09.md) grounds these failures and distinguishes observation gaps from electrical events.

Greg wants useful capture operating before another power problem, potentially tomorrow—not a new observability platform or a controller-firmware experiment. This initiative must deliver, test, push and immediately deploy the minimum complete host-only correction.

On2026-11-10 Greg additionally requested Plucky log storage with at least one week of raw data and preserved incident snapshots. He approved one further AFK slice, eight-day raw/controller retention, indefinite incident snapshots, a minute-by-minute file-only pull job and actual CLI/local/SFTP verification seams. This later requirement extends the completed original six slices; it does not weaken their capture or equipment constraints.

During delivery, actual HA persistence exhausted its1GiB free-space reserve. Greg approved preserving current files on Plucky, reducing only recorder ceilings to256MiB raw/32MiB controller/128MiB incidents, and separately approved a safe Core stop/update/start because these advanced limits have no options API. Keep the reserve, preserve all unrelated data and record the capture gap. This operational correction does not change sampling, integration code or equipment settings.

## Solution

Ship one release of the existing integration that preserves received evidence before decoding, isolates device collection, reports truthful freshness/state, recovers its journal after loss, preserves bounded incident context and exposes enough health to know whether capture works. Reconstruct what each controller reported and when it was acquired without inventing continuity, physical contacts or precise causality.

The accepted [host recorder design](../../docs/host-recorder-mvp-spec.md) remains the detailed governing specification: **all R1–R6 requirements, A1–A8 checks, fixed read profiles, storage bounds and immediate delivery checklist apply**. This PRD adds stable user-story IDs and delivery traceability; it does not replace that contract with a narrower summary or a second schema.

Use existing routes, JSONL, entities, logger, analyzer and tests. Preserve entity IDs. One authorized HA Core restart follows focused verification, with backup, rollback and a fixed first ten-minute production observation window. No maintenance-window wait or physical fault injection is required.

## User Stories

1. As an investigator, I want exact received bytes, partial responses, semantic rejections and acquisition metadata preserved before decoding, so that missing or invalid observations cannot silently disappear or masquerade as zero. Covers R1 and A1.
2. As an operator, I want valid responses recovered despite unsolicited history/interleaved traffic within strict resource bounds, so that transport assumptions do not manufacture capture outages or relax device validation. Covers R2 and A2.
3. As an investigator, I want independently scheduled devices, per-block acquisition times and fresh configuration/BMS context, so that a stalled role or maintenance read does not hide what healthy devices reported. Covers R3 scheduling/profiles, R6 role health and A3.
4. As an operator, I want generator and charging states to distinguish voltage presence, qualified frequency, accepted power, missing roles and gaps, so that cached data or restarts cannot invent stops, transitions or energy. Covers R3 logger corrections and A4–A5.
5. As an investigator, I want discrete protection/reason changes and persistence failures recorded with recovery baselines, so that an unchanged post-failure observation is not permanently suppressed. Covers R4, R6 persistence health and A6.
6. As an investigator, I want bounded all-role pre/post-event bundles with configuration context, explicit truncation and restart/disk-pressure handling, so that important evidence survives without endangering HA storage. Covers R5 and A7.
7. As an operator and investigator, I want visible capture health and streaming analysis of old/new files or incident directories, so that I can distinguish a quiet system from a dead recorder and reconstruct supported event brackets rather than invented continuity. Covers R6 and A8, integrated with stories 1, 3, 5 and 6.
8. As the site operator, I want the tested release pushed, installed immediately and verified using genuine and separately designated synthetic evidence, so that tomorrow's event is captured and a rollback is available. Covers the complete immediate delivery checklist and Done criteria.
9. As the site operator, I want at least one week of raw/controller evidence archived on Plucky and incident snapshots preserved beyond HA's local limits, so that a later investigation does not lose records to the small HA disk while network outages cannot stall device polling. Covers approved R7 and A9.

Story numbers are stable identifiers. A story may span multiple vertical slices; partial coverage in one issue does not waive its remaining requirements.

## Implementation Decisions

- **One host-only release:** retain the existing integration and public entities; migrate affected callers and retire superseded live paths instead of running two recorders. No new dependencies, services or UI. Governing authority: [accepted design, Decision and Scope](../../docs/host-recorder-mvp-spec.md).
- **Evidence before interpretation:** every transaction attempt has versioned session/acquisition/transaction identity, request/response context, acquisition intervals and separate validation outcomes; successful partial blocks survive later failures. Invalid evidence remains invalid for sensors. Governing authority: [R1](../../docs/host-recorder-mvp-spec.md).
- **Bounded strict transport:** demultiplex unsolicited frames before identity matching; accept a matching response after nine unrelated frames within the deadline; retain strict identity/CRC/function/range/length checks, current frame-size limit and initial 256 KiB transaction-traffic limit. Keep one owner and at most one request in flight per device. Governing authority: [R2](../../docs/host-recorder-mvp-spec.md).
- **Independent observation, not atomic snapshots:** use original per-block times and actual consumption age; independent roles continue through another role's timeout and initialized local polling survives portal errors. Dynamic reads precede at most one due maintenance block per round. Use every source-backed read profile and exclusion in R3, reusing fresh overlapping reads. Governing authority: [R3](../../docs/host-recorder-mvp-spec.md) and [verified read surfaces](../../investigations/2026-11-08-firmware-reverse-engineering/direct-session/history-and-read-surfaces.md).
- **Truthful states:** the 239 V / qualified 0 Hz / 0 W case means present voltage, not supplying, unknown run state, interrupted quality and unknown confidence. Only distinct successful acquisitions debounce. Incomplete totals and unknown endpoints cannot stop a charge session, accumulate dwell or integrate energy across a gap. Transfer resumption preserves uncertainty. Governing authority: [R3 logger corrections](../../docs/host-recorder-mvp-spec.md).
- **Recoverable change journal:** include input6 low bits 0/1, both bytes of519 and existing discrete fields; distinguish observed/pending/persisted state, expose cumulative loss and retry an unchanged recovery baseline. Bounded queues and off-event-loop filesystem operations must not equate enqueue with persistence. Governing authority: [R4](../../docs/host-recorder-mvp-spec.md).
- **Bounded incident preservation:** use existing collected records; ten-minute pretrigger buffer capped at16 MiB and ten-minute post-trigger quiet period, merged edge triggers, continued active anomalies, bounded segments, atomic manifests and finalized hashes. Honor all R5 storage ceilings, the1 GiB free-space reserve, owned-file-only pruning and explicit lost coverage. Governing authority: [R5](../../docs/host-recorder-mvp-spec.md).
- **Observable loss and cautious reconstruction:** expose existing-entity health and persisted health records, stream historical/new evidence, enumerate anomalies outside known dropout windows and bracket event times using acquisition intervals. No interpolation, zero-fill, physical-contact claims or continuous house-outage durations inferred across gaps. Governing authority: [R6](../../docs/host-recorder-mvp-spec.md).
- **Immediate reversible rollout:** focused checks, documentation/version, normal push, preserved existing work/evidence, installed-file hashes, backup, HA configuration check, one restart, fixed ten-minute observation and separate synthetic verification. Genuine external timeouts may remain honestly degraded; new integration/write/profile defects block success. Governing authority: [immediate delivery checklist and Done](../../docs/host-recorder-mvp-spec.md).
- **Approved Plucky storage extension:** one file-only SFTP pull timer on Plucky, optional dependency isolated from HA, eight-day raw/controller retention and indefinite sealed incidents. Preserve honest open/interrupted coverage, incremental byte identity and available final hashes; the archive client performs no source deletion, reduced sampling or new device reads. The separately approved HA storage-pressure correction changes only three recorder ceilings after preservation, with one Core restart and explicit gaps. Governing authority: [R7/A9](../../docs/host-recorder-mvp-spec.md).

**Authority gaps and limits:** installed controller firmware identity and exact physical meanings of candidate-mapped protection bits remain unverified (`firmware-identity`, `protection-bit-semantics`). Reverse-engineered candidate behavior is evidence, not authority to claim that an internal latch or contact physically changed on this installation. Preserve raw values, identity and uncertainty; do not turn these gaps into guessed labels, firmware changes or additional probing. They do not block host-only recording. No separate ADR or independent test contract is invented: the explicitly accepted design and its acceptance checks govern this initiative.

## Testing Decisions

Greg explicitly approved these public seams alongside the six-slice proposal. Expected behavior comes from the accepted design, not current implementation output. The [focused acceptance contract](../../docs/host-recorder-mvp-spec.md#focused-acceptance-tests) owns A1–A8. Existing tests are prior art, not authority to preserve erroneous behavior.

| Public seam | Externally observable proof | Contract / prior art |
| --- | --- | --- |
| Dongle acquisition against loopback TCP, persisted raw JSONL and analyzer CLI | Rejected/partial/interleaved responses remain inspectable; invalid sensor data stays rejected; time/traffic bounds hold | R1–R2, A1–A2; [transport tests](../../tests/test_local_dongle.py), [trace tests](../../tests/test_local_dongle_trace.py) |
| Logger sample/events and restored session state | Presence, missing-role totals, debounce, dwell, energy and transfer uncertainty remain truthful across gaps/restarts | R3, A4–A5; [logger tests](../../tests/test_eg4_logger.py) |
| Integration polling lifecycle with controlled device responses, recorded acquisition times and existing diagnostic attributes | Healthy roles progress during other-role/maintenance stalls; same-device requests never compete; profiles/freshness are observable | R3, R6, A3; existing integration and transport behavior are implementation context, not a substitute for this missing isolation proof |
| Recorder append/observe/close against temporary storage and persisted health | Input6/reason-only changes and unchanged recovery baselines survive; enqueue/disk failures and cumulative loss remain visible | R4, A6; [trace tests](../../tests/test_local_dongle_trace.py) |
| Saved incident directories and streaming analyzer CLI | All-role pre/post context, pressure/restart gaps, owned-file retention and completeness match manifests without false causal claims | R5–R6, A7–A8; [existing analyzer](../../scripts/analyze_local_dongle_trace.py) |
| Actual HA API, installed recorder files and independently copied evidence | Correct build loaded, three roles collecting, observed cadence/storage/health, isolated synthetic incident and usable rollback | Immediate delivery checklist; no electrical fault injection |
| Plucky archive public function/CLI with real local/SFTP files, saved archive directories and existing analyzer | Prefix/rotation identity, eight-day minimum-age retention, source-loss resume, stable hash-verified incident sealing and genuine scheduled Plucky backfill without device polling | R7/A9; explicitly approved with EG4REC-07 on2026-11-10 |

Each slice demonstrates an end-to-end behavior, not merely a layer or mock wiring. Use a failing behavioral regression for a missing defect path, implement only the relevant correction, then run the narrow covering check. Reuse tests that already cover the behavior; do not add duplicate, source-text or argument-echo tests. Run focused changed-module checks together after integration, plus Ruff on changed Python, actual loopback TCP and analyzer CLI. Record only checks actually exercised; do not claim an unrun full suite passed.

All A1–A8 proofs remain mandatory before deployment completion. Raw evidence stays outside Git and is referenced from completion evidence. The synthetic installed-recorder scenario uses controlled timestamps and separate storage without fake HA sensor publications. Disk/queue/transport fault injection remains offline.

## Out of Scope

- Controller/custom power firmware, operating/protection settings or attempts to prevent a dropout before establishing trustworthy evidence.
- Re-litigating independently verified generator health, electrical experiments, generator/load/breaker cycling or induced physical dropouts.
- Flash-history activation/upload/erase, Q1/Q3/QD/QS, arbitrary register probes, inaccessible engineering surfaces or another device collector.
- For the original HA release: new dashboards, databases, brokers, dependencies, persistent-socket redesign, configuration wizard, compression/upload service or automatic off-host replication. The explicitly approved R7 Plucky file-only archive and isolated dependency are the sole later exception.
- Claims to capture unpublished RAM, all CAN traffic, waveforms or observations while HA is unpowered; guaranteed72-hour retention regardless of byte limits; power-loss-proof persistence of queued records.
- GitHub ticket publication, unrelated investigation cleanup or changes to equipment registries/dashboards.

## Further Notes

The approved six slices are: raw evidence/transport; truthful state; independent polling; recoverable journal; incident retention/reconstruction; immediate deployment. State handling precedes independent scheduling so new per-role updates feed already-correct gap/freshness semantics. Journal recovery can proceed after raw evidence without waiting for state/scheduling. Incident assembly depends on both complete capture/context and recoverable journal/persistence behavior. Deployment follows the integrated incident slice and verifies all earlier acceptance groups.

All six slices are AFK. Host deployment/restart is already authorized; no maintenance-window or further human equipment operation is assumed. A newly encountered access prerequisite must be reported honestly, not replaced by a simulated deployment.

Deployment targets and operational details remain in the accepted design. Confirm actual installation identity/routes, space and existing source hashes at rollout rather than treating recorded addresses or entry IDs as fresh observations. Do not overwrite user changes or include firmware downloads, credentials or raw incident logs in the release commit.

The delivery is complete only when R1–R6 and A1–A8 have evidence, the pushed build is active on HA, all three roles record with visible health, incident export is usable and backup/rollback are verified. A ready issue board is not evidence that this implementation or deployment has happened.

## Implementation Issues

Approved original six-slice breakdown plus the later approved Plucky storage extension: [Implementation issue board](./issues/README.md). All slices are AFK; dependencies and delivery status are owned by issue frontmatter and the derived board.

1. [EG4REC-01 — Preserve raw dongle evidence through invalid and interleaved responses](./issues/01-preserve-raw-dongle-evidence.md).
2. [EG4REC-02 — Make generator and charging states truthful through gaps](./issues/02-correct-generator-and-charge-state.md).
3. [EG4REC-03 — Keep healthy devices recording when another device stalls](./issues/03-isolate-device-polling.md).
4. [EG4REC-04 — Preserve protection changes and recover the journal after write failures](./issues/04-recover-controller-journal-and-health.md).
5. [EG4REC-05 — Freeze, retain and reconstruct complete incident bundles](./issues/05-preserve-and-analyze-incident-bundles.md).
6. [EG4REC-06 — Push, deploy and verify the recorder on Home Assistant](./issues/06-deploy-and-verify-recorder.md).
7. [EG4REC-07 — Archive one week of raw evidence and preserve incident snapshots on Plucky](./issues/07-archive-evidence-on-plucky.md).
