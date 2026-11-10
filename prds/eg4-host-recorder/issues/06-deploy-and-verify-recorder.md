---
id: EG4REC-06
title: Push, deploy and verify the recorder on Home Assistant
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
test_seam: Actual HA API and installed recorder -> fixed live capture, isolated synthetic export and rollback evidence
authority_gaps: []
blocked_by:
- 05-preserve-and-analyze-incident-bundles.md
depends_on_story_ids:
- 8
slice_order: 6
completion_evidence:
  implementation_commit: ccd1fd76114b05fd3920268458f1d86c946ad033
  red_evidence: /home/greg/.local/state/eg4-host-recorder/2026-11-10/6.1-red.json
  green_evidence: /home/greg/.local/state/eg4-host-recorder/2026-11-10/6.1-green.json
  alignment_verified: true
---

## What to build

Deliver the integrated host-only release now, using the already authorized HA Core restart rather than waiting for a deployment window. Prove the pushed code is the installed, running recorder and that genuine evidence can be copied and analyzed. This is real production delivery, not a deployment recipe or an unexecuted checklist. Preserve controller firmware and operating settings unchanged.

## Acceptance criteria

- [x] R1–R6 and A1–A8 are accounted for. Run focused changed-module tests together after integration, Ruff on changed Python, actual loopback acquisition and analyzer CLI. Record exact exercised results and classify unrelated pre-existing failures; do not claim an unrun full suite passed.
- [x] Preserve current user work and inspect installed source identity. Update integration release version, logger/user documentation and README change note, retain entity IDs, and remove superseded live capture paths. Commit only intended release files and push normally to configured origin using existing branch workflow, without force-push or firmware/raw-log/credential additions.
- [x] Use the established HAOS access route and confirm the actual enabled EG4 entry/device routes. Measure host free space and observed bytes/hour; document any necessary ceiling adjustment and resulting retention estimate while preserving the free-space reserve.
- [x] Before replacement, copy existing bounded evidence off-host and back up the installed integration plus relevant session Store. Stage the tested coherent file set, verify installed hashes and run the HA Core configuration check; restore files before restart if that check fails.
- [x] After a successful configuration check, perform one HA Core restart, then confirm API recovery, EG4 loaded/enabled, the new build/session identity and fresh records from all three roles. Record restart command timing and actual acquisition gaps separately, never as an electrical-outage measurement.
- [x] Preserve the first fixed ten-minute post-recovery window, including failures. Verify all dynamic/maintenance profiles, role acquisition times, actual cadence, configuration/BMS age, health, persistence-loss counters and any observed rejected/partial evidence. Do not choose a cleaner window or label a configured1s target as observed1Hz.
- [x] Exercise the installed recorder implementation with controlled timestamps in a separately designated synthetic verification directory. Prove pre/post retention, final manifest/export and analyzer output without fake HA state publications or contaminating genuine records. Transport/queue/disk fault injection remains offline, not against live equipment or storage.
- [x] Copy genuine capture/health/controller records and the clearly labeled synthetic bundle off-host, verify file hashes and analyze the copies. Raw logs remain outside Git and completion evidence references their locations.
- [x] Save a deployment receipt with pushed commit, installed hashes, backup location, configuration-check/restart results, observed capture gaps, all-role outcomes, measured cadence, storage/retention estimate, capture/export paths and explicit synthetic designation.
- [x] Leave collection enabled with no extra diagnostic client. External device timeouts may be honestly degraded if captured and healthy roles continue; new integration-load failure, repeated recorder-write failure, missing profiles, unexpected device writes or fabricated evidence block completion and require correction or rollback.
- [x] Verify rollback readiness against the actual backup and documented restoration/check/restart route, preserving new evidence and restoring session Store only if compatibility requires it. Execute rollback if the blocking conditions occur; do not add an unnecessary production restart solely to rehearse it.
- [x] Completion means the pushed build is active on HA, all three roles are recording with visible health, export works, focused proofs and rollback evidence exist, and no controller firmware or operating/protection settings have changed. Access failure or simulated deployment cannot be reported as completion.

## Blocked by

- [05-preserve-and-analyze-incident-bundles.md](./05-preserve-and-analyze-incident-bundles.md)

## Pull request stack

Stack root: `main` at `9a1a916516daddfb0d8b1a78ea0ce4405351940e`; all five implementation slices are merged.

| Key | Branch | Purpose | Predecessor / PR base | Acceptance advanced |
| --- | --- | --- | --- | --- |
| 6.1 | `codex/eg4-host-recorder-6.1-production-release` | Publish version0.2.0 and operator guidance, install the pushed coherent package, perform the authorized Core stop/start cycle, preserve the first fixed live window, prove installed-source synthetic export, then record reviewed deployment and rollback evidence | `main` at the stack root | All EG4REC-06 criteria and end-to-end R1–R6/A1–A8 |

Push and open this green source entry before replacement. Keep its source identity fixed through the production proof; add the receipt/evidence to the same entry after observation. Final full-suite verification and two-axis review precede merge. Mark delivery done only after the implementation stack and recorded merge evidence are complete. No firmware or operating/protection settings change; only documented host-storage ceilings may be adjusted.

## Administrative completion workflow

Implementation entry6.1 is merged: PR7 head `ba14771346707e40afb89469f62cf62043955308`, merge `26ef80cdc84e2b62ff45740cc25791d3a1ddda15`. The installed, pushed source is `ccd1fd76114b05fd3920268458f1d86c946ad033`; the later receipt commit changes no package files.

Before changing completion metadata, administrative branch `codex/eg4-host-recorder-6.2-completion-evidence` is based on `main` at that verified merge. Its sole scope is to record this completed implementation/review/production/merge evidence, derive the issue board and advance the manifest. It adds no implementation slice, source change, deployment or restart. Publish and verify its remote head/base/incremental metadata diff, validate the packet, then merge the completion record.

## Completed delivery evidence

- [PR7](https://github.com/MrYutz/EG4_HA/pull/7) merged as `26ef80cdc84e2b62ff45740cc25791d3a1ddda15`. Final remote head `ba14771346707e40afb89469f62cf62043955308`, base and all12 scoped files/full incremental diff were verified before merge; merged flag/SHA were verified afterward.
- Installed0.2.2 source `ccd1fd76114b05fd3920268458f1d86c946ad033`, native build `b1fb8b8de118fac7f675e3a43a164e1eef3926a08ee7665671365b8658a7b156`, session `74387339-2366-4f39-b443-0280221bc66c`. Actual enabled entry is loaded and all three roles have fresh records and visible health.
- Final workspace suite244passed/3skipped/3upstream warnings;68storage/trace/actual-HA-stop checks passed. Changed recorder/analyzer source types and critical/import Ruff pass. Earlier SDK typing/style diagnostics are explicitly classified, not suppressed.
- Adversarial review closed two partial/crash omissions; genuine deployment exposed and corrected a persistence-throughput defect rather than hiding it with a larger queue. All three original first600s windows remain preserved, including1,048sampled raw drops on0.2.1. The corrected06:37:25.864739–06:47:25.864739UTC window has all25profiles successful and no raw/journal/incident losses or write errors; later07:02:25health remains healthy/loss-free.
- Installed-source native aarch64 synthetic proof retains1,312acquisitions per role across the full warmed pre/post interval, closes complete and passes final hashes and both native/off-host actual analyzer CLI. It uses no device sockets or fake HA states. Genuine prefixes, controller/health data, rejected bytes and all synthetic transfer hashes were independently checked off-host.
- [Deployment receipt](../../../docs/host-recorder-deployment-2026-11-10.md) records all actual check/restart timings, source gaps, startup/queue delay, cadence and profile/BMS age, byte-limited retention, backup and tested export custody. Source gaps are not power-outage or engine-cycle measurements. Independent healthy-generator evidence remains authoritative; historical inverter/GridBOSS failure cause is not fabricated.
- Durable private evidence: `/home/greg/.local/state/eg4-host-recorder/2026-11-10/` (mode0700), indexed by `6.1-green.json`, with red evidence and verified `6.1-merged.json`. Raw data and credential-bearing backups stay outside Git. Only our verified transfer-stage duplicates and temporary scripts/private interpreter were removed; old investigations, producer data, native synthetic evidence and rollback artifacts remain.
- Both final source/operational review axes report no remaining P1/P2. No firmware or equipment operating/protection changes occurred. The user's untracked investigative caller/fixture received only a necessary local interface migration, remained unpublished/not deployed and passed its existing loopback/safety checks.
