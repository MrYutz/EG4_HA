# EG4 host recorder MVP specification

## Decision

**One host-only release of the existing Home Assistant integration, tested and deployed immediately.** Capture useful evidence before the next power problem, including tomorrow. Do not build a new logging platform or wait for a maintenance window.

This is an implementation specification, **not a claim that the changes are built, pushed or deployed**. Greg authorizes the described host rollout, including a short HA restart, once focused checks pass. No equipment operating/protection settings or controller firmware changes are included.

Established premise: the generator remained healthy at approximately240 V/60 Hz during the investigated dropouts, independently checked by Greg. Diagnose why GridBOSS/FlexBOSS withdrew acceptance or conversion. Internal qualified-frequency zero is not evidence that the engine stopped.

Evidence: [adversarial review and reproduced recorder defects](../investigations/2026-11-08-firmware-reverse-engineering/adversarial-review-2026-11-09.md).

## Scope: six fixes, one release

| Requirement | Smallest complete change | Main owner |
|---|---|---|
| R1 Preserve evidence | Save raw responses before decoding; retain successful partial reads and rejected data | `local_dongle.py` |
| R2 Reliable transport | Handle unsolicited first-frame traffic and remove the eight-frame matching ceiling without weakening validation | `local_dongle.py` |
| R3 Honest timing/state | Independent device schedules, per-block times, correct freshness/completeness and generator labels | `__init__.py`, `eg4_logger.py` |
| R4 Complete/recoverable journal | Add input6 protection bits; recover baseline after queue/disk loss | `local_dongle_trace.py` |
| R5 Preserve incidents | Bounded pre/post-event capture and longer retention | `local_dongle_trace.py`, or one small adjacent incident module |
| R6 Prove recorder health | Existing diagnostic attributes and a usable offline analyzer | `sensor.py` as needed, existing analyzer |

Paths are under `custom_components/eg4_monitor/`, except `scripts/analyze_local_dongle_trace.py`.

Reuse current discovery/manual routes, transport framing, JSONL storage, logger, entities and tests. Keep entity IDs. No new dependencies, dashboard, database, message broker, configuration wizard, parallel decoder implementation or logging daemon. Keep short-lived sockets; a persistent-socket redesign is not a prerequisite.

No Q1 activation, Q3/QD/QS, flash-history upload/erase, arbitrary-register probing, generator/load/breaker experiment, or second device collector. No automated off-host replication service: incident directories must be copyable through the existing SSH route, and deployment must preserve an off-host evidence copy.

“All data” means the defined stock-register profiles and bytes received by this integration, including failures. It cannot mean unpublished controller RAM, all CAN traffic, waveforms or measurements while HA itself is unpowered.

## R1 — Save first; decode second

Every transaction attempt produces a forensic result. Queue its evidence before semantic decoding or the next request. Derived role samples reference the contributing transactions.

Minimum fields:

- Schema/version, record kind, recorder session UUID, role/acquisition ID and transaction ID.
- Role, route, configured/observed device identity and requested function/start/count; actual request bytes if transmitted.
- UTC acquisition start/end and monotonic duration. Processing/persistence time is separate. Monotonic timestamps are comparable only within the same recorder session.
- Received frame bytes; register words when structurally obtainable; separate framing/CRC/identity/range/semantic outcomes.
- Error type/stage, partial bytes already received, and explicit resource/truncation indicators.

Required behavior:

1. CRC-valid unknown status `33280` survives as a complete raw block plus decoding error. Do not assign it an invented mode.
2. Live-voltage/frequency inconsistencies survive even when decoded telemetry is rejected.
3. Successful GridBOSS power/voltage data survives a later frequency timeout. A summary with only some successful blocks is explicitly partial.
4. A primary semantic rejection does not automatically skip all scheduled diagnostics when the transport remains usable. Remaining work stays bounded and its failures remain explicit.

**Do not relax telemetry validation.** Invalid bytes may be evidence, never trustworthy sensor values merely because they were saved. Preserve missing/error separately from zero and cached previous values.

Remove unrelated light/HA-state dumps from new records. No credentials or tokens. Version new record formats; do not rewrite historical evidence or emit duplicate legacy/new live records.

## R2 — Match responses within a bounded deadline

- Replace “first frame must be identity” with bounded demultiplexing: retain unrelated/history frames and continue to an identity-bearing frame.
- Match the requested response using strict device identity, CRC, function, register range and length checks.
- A matching response after nine unrelated valid frames must succeed if within the transaction deadline. Remove the arbitrary eight-frame ceiling.
- Bound total transaction time, frame size and received traffic; retain the current frame-size ceiling and initially cap transaction traffic at256 KiB. Report resource exhaustion/truncation explicitly.
- Distinguish identity rejection, malformed framing, timeout and traffic-limit exhaustion. Preserve bytes already received; do not activate or clear flash history.
- Retain one polling owner and at most one request in flight per device. No extra client to “get around” failures.

## R3 — Accurate timing, independent collection, honest state

### Scheduling and freshness

Run the three device schedules independently instead of waiting for every role before starting another round. All integration requests for a given device share its owner/lock, including coordinator/configuration reads. A GridBOSS failure must not stall otherwise healthy inverter capture.

Keep dynamic reads ahead of stable settings. Interleave at most one due maintenance block between dynamic rounds. Do not add a higher-rate incident burst, aggressive reconnect loop or speculative timeout reduction. Report the rate actually achieved.

Keep initialized local collection running through portal-coordinator errors; no authentication/discovery redesign is required. Each block, including power/frequency and core/BMS, has its own acquisition interval and success status. A common scheduling sequence is not an atomic snapshot.

Cached values retain their original acquisition ID/time and quality. Compute age when consumed, not before a potentially slow refresh. New processing time does not make old data fresh.

### Logger corrections

- Separate terminal-voltage presence, qualified-frequency validity, accepted power and inferred run state. Mechanical engine state remains unmeasured.
- For239 V / qualified0 Hz /0 W, emit `PresenceState=present`, `SupplyState=not_supplying`, `RunState=unknown`, `Quality=interrupted`, `Confidence=unknown`, and a reason identifying invalid/unavailable qualified frequency. Never `generator_voltage_absent`.
- Preserve existing hysteresis thresholds for coherent normal readings; these are logger thresholds, not equipment protections.
- Debounce only distinct successful source acquisitions. Repeated cached data cannot confirm a transition.
- A complete charging total requires fresh data from every configured participating inverter. If master is missing and slave reports0 W, retain a partial/unknown total and per-role context—not a complete zero.
- Unknown/partial samples keep an active charge session open and interrupted. Do not integrate energy across an interval with an unknown endpoint.
- Reset pending debounce/stop-dwell evidence across gaps/restarts. Restore active sessions as interrupted without synthetic start events.
- Retain last-known transfer state across gaps. A changed observation at resumption carries `intervening_unknown=true`; it does not locate a physical transition inside the gap.
- Document the distinction between debounced generator state and immediate raw controller/transfer observations. Neither is physical contact feedback.

### Fixed read profiles

All indices are zero-based and device-specific. Keep current read-only03/04 operations and source-backed baseline shapes:

| Role | Read | Initial schedule |
|---|---|---|
| Each FlexBOSS | Input0/40,40/40,120/26 | Every dynamic round; requested1s spacing, achieved rate measured |
| Each FlexBOSS | Input80/40 BMS | Existing5s target; actual age/availability retained |
| Each FlexBOSS | Holding110/125 | Startup and60s |
| GridBOSS | Input0/40,120/12,500/100 | Every dynamic round; requested1s spacing, achieved rate measured |
| GridBOSS | Local holding4500/10 | Startup and5s; command/test-state readback, not contacts |
| Each FlexBOSS | Baseline holding0/20,20/90,110/125,235/102 | Startup and5min, staggered between dynamic rounds |
| GridBOSS | Baseline holding0/20,20/90,110/125,235/88; local2000/108 | Startup and5min, staggered; includes qualification/wait/warm-up context and warning mask2102 |

Reuse fresh maintenance reads when schedules coincide. Store changed configuration plus its acquisition references; incidents include the latest successful baseline and its age. Failed baseline blocks remain missing, not inherited as current values.

Do not start FlexBOSS reads at224 or248. The listed safe-start ranges may include those positions without invoking the exact-start behavior. Never use historical GridBOSS4516, undefined input132–499 or inaccessible engineering1100 as replacements. Proof: [verified read surfaces](../investigations/2026-11-08-firmware-reverse-engineering/direct-session/history-and-read-surfaces.md).

## R4 — Meaningful journal changes and persistence recovery

Add **FlexBOSS input6 low bits0/1** to discrete signatures: candidate-mapped protection outcomes are redirected there out of the ordinary warning/fault locations. Keep the full raw word as context, without inventing physical meanings. Retain current warnings60/61, faults62/63, limiter/state144/145, both bytes of519 and command counters. Small analog142/143 fluctuations need not all trigger the journal; raw capture retains them.

Distinguish observed, pending and persisted comparison state:

- Queue rejection or disk failure must not permanently suppress the next identical observation.
- Count loss, mark continuity broken, and persist a recovery baseline when writing resumes—even if device state is unchanged.
- Keep cumulative dropped-record/write-error counts and last successful persistence. A later successful write must not erase evidence of the earlier failure.
- Keep filesystem work off the HA event loop and queues bounded. Queue acceptance is not disk success.
- Flush ordinary appends; synchronize incident finalization and graceful shutdown to disk off the event loop. Do not promise power-loss-proof retention of every queued record.

## R5 — Automatic incident preservation

Use already collected records, not another device reader. Maintain a **10-minute pretrigger buffer, bounded to16 MiB**, then preserve **10 minutes after the latest trigger**. Report actual pretrigger coverage and truncation; do not claim a full window if the recorder just started or hit its memory ceiling.

Trigger on edges, without waiting for HA debounce:

- Accepted generator power falls from supplying to low while voltage remains present, independently of qualified frequency.
- Voltage/frequency evidence becomes inconsistent or changes availability.
- Abnormal output voltage or loss of a previously live output.
- Relevant source/mode/decision/control/protection/limiter changes, including input6.
- Primary/diagnostic availability loss/resumption, semantic rejection or recorder persistence loss.

Use existing power hysteresis. For this site's approximately240 V UPS/EPS channels, initial output capture thresholds are **below180 V or above264 V**. Low-output detection requires a previously live channel; overvoltage does not. These are capture triggers—not safety settings—and must not be applied to120 V leg channels.

Merge overlapping triggers, preserving all reasons/times. Repeated identical failures do not create an incident every poll. Keep an incident open while a monitored outage/anomaly is active, then close after the post-trigger quiet period. Long incidents use bounded file segments under one incident ID, not unbounded RAM or a silently truncated duration.

An incident is a plain directory containing JSONL and a small manifest:

- Incident/session IDs, triggers, actual coverage, missing roles, errors/gaps and truncation.
- All roles' pre/post raw data plus current configuration context with acquisition times.
- Hashes for finalized files, never a growing file represented as final.

Write the manifest atomically. After a crash/restart, mark any unfinished prior incident interrupted, preserve it, and start a new recorder session with an explicit gap. The new pretrigger buffer warms up honestly; no cross-restart continuity is invented. Existing raw files remain available for manual joining by the analyzer.

### Storage defaults

| Stream | Age target | Byte ceiling |
|---|---:|---:|
| Raw |72h|2 GiB|
| Controller journal |14 days|200 MiB|
| Incidents, including open segments |14 days|1 GiB|

Use25 MiB segments. Age/byte ceilings are limits, not promised retention days or preallocation. Protect a **1 GiB free-space reserve** for HA. Before rollout, measure host space and observed bytes/hour; adjust documented ceilings if necessary and show the resulting retention estimate.

Prune only the new recorder's owned files. Prefer expiring ordinary raw over incident evidence. If quota/disk pressure forces incident eviction, mark its manifest incomplete with lost coverage. If safe writes are impossible, expose degraded capture instead of filling HA's disk. Never delete sealed investigations, deployment backups or unrelated HA files to make room.

No compression/upload service or custom bundle viewer is required. Deployment verifies that a bundle can be copied off-host and analyzed.

## R6 — Visible health and usable reconstruction

Expose compact `RecorderHealth` attributes through the existing local-dongle diagnostic entity. Resolve its current entity ID during deployment; the recorded installation uses `sensor.eg4_site_local_eg4_dongles_available`. No new dashboard.

Refresh at most every30s and on significant failure/recovery:

- Session/build identity; `starting`, `healthy` or `degraded`.
- Per-role last attempt/success, primary/diagnostic freshness and measured median/max spacing.
- Last successful persistence, queue depth, cumulative drops/errors and last error.
- Actual oldest/newest raw timestamps, bytes/free space and estimated retention.
- Active/latest incident ID/path and incomplete/truncated state.

Persist a compact health record every30s and at startup/shutdown/recovery so a quiet journal is distinguishable from a dead recorder. Rate-limit repeated warnings.

Extend `scripts/analyze_local_dongle_trace.py` to stream a trace file or recorder/incident directory, including historical/new schemas. Report coverage, per-role errors/partials, session changes, acquisition gaps, controller/protection changes, output anomalies, configuration age and incident completeness. Enumerate anomalies outside low-generator-power intervals too.

Use host-time onset/recovery brackets. Overlapping acquisition intervals mean unresolved ordering. No zero-fill, interpolation, inferred continuous house-outage duration, mechanical-stop claim, or current-contact interpretation of519/GPIO echoes.

## Focused acceptance tests

Use existing production seams: transport against loopback TCP; recorder append/observe/close against temporary storage; logger sample/events; integration scheduling/aggregation with controlled device responses; analyzer CLI. Use retained real payloads where available. Add missing behavioral regressions, not mocks that merely echo arguments or assertions about source text.

| Check | Required proof |
|---|---|
| A1 Raw/partial preservation | Unknown33280, inconsistent V/F and interrupted responses are retained but not published as valid; GridBOSS power survives frequency timeout |
| A2 Transport matching | History-before-identity and nine unrelated frames succeed within deadline; bad CRC/identity still rejected; endless traffic bounded |
| A3 Device isolation | One role/maintenance request stalls while other roles keep collecting; no competing same-device requests |
| A4 State/freshness |239V/0Hz/0W does not become voltage-absent/off; cached repeats cannot debounce; missing master plus zero slave stays partial/interrupted |
| A5 Gap continuity | No charge-stop dwell or energy integration across unknown/restart gaps; resumed transfer carries uncertainty |
| A6 Journal | Input6-bit-only and519-reason-only changes persist; queue/disk failure followed by unchanged state yields a recovery baseline |
| A7 Incidents/retention | Overlapping triggers retain all-role pre/post context; rotation/pressure/restart stays bounded, explicit and leaves unrelated files untouched |
| A8 Reconstruction | Analyzer reports correct coverage/gaps/anomalies on genuine retained data and clearly synthetic failure fixtures |

Run focused changed-module tests together after integration, plus Ruff on changed Python. Exercise actual loopback TCP and analyzer CLI. Classify unrelated existing failures rather than hiding them or expanding scope. Record exact checks and results; do not claim an unrun full suite passed.

A physical dropout is not a prerequisite. Do not induce electrical faults to validate logging.

## Immediate delivery checklist

### Implement, test, push

1. Inspect current worktree and installed source hashes; preserve user changes. Implement R1–R6 and migrate affected callers. Remove superseded live capture paths rather than layering a second recorder.
2. Update logger documentation, README change note and integration release version. Retain entity IDs; make documented labels/events match implementation.
3. Pass A1–A8 through the focused checks. Stage only intended release files, excluding credentials, firmware downloads and unrelated investigation work.
4. Commit and push normally to configured origin, currently `git@github.com:MrYutz/EG4_HA.git`, using the existing branch workflow. No force-push. Record release commit and file SHA256s; no unrequested review-window wait before authorized host deployment.

### Deploy now, with a rollback—not a deployment-window ceremony

5. Use the established HAOS SSH route to `192.168.0.253`; install into `/config/custom_components/eg4_monitor/`. Confirm the actual enabled EG4 entry/routes; historical entry is `01KM5TPJ4PMEGW55RT8W1KY3ZS`.
6. Check space, copy existing bounded evidence off-host, and back up the installed integration and relevant session Store under `/config/.eg4-investigation/deployments/host-recorder-<UTC>/`. Do not edit registries or dashboards.
7. Stage the tested coherent file set and verify hashes. Run `ha core check`; on failure restore files before restart. On success perform **one HA Core restart** to load changed Python reliably. An integration reload alone is not proof of new modules running.
8. Confirm HTTP/API recovery, EG4 enabled/loaded, new build/session identity and fresh records from all three roles. Record restart command timing and actual sample gaps separately; neither is a power-outage measurement.

### Verify production capture, then leave it running

9. Preserve the first fixed **10-minute** post-recovery window, failures included. Verify dynamic and maintenance profiles, raw rejected/partial-data representation where observed, health, timestamps, write-loss counters and measured cadence. Do not choose a cleaner later window or claim1Hz from configuration.
10. Run a clearly synthetic incident through the installed recorder implementation into a **separate verification directory**, using controlled timestamps. Verify pre/post retention, final manifest/export and analyzer output without publishing fake HA states or contaminating genuine records. Disk/queue/transport fault injection stays offline—not against live equipment/storage.
11. Copy genuine capture/health/controller records and the synthetic verification bundle off-host; verify hashes and analyze the copies.
12. Save one deployment receipt: pushed commit, installed hashes, backup, check/restart results, observed gaps, three-role results, measured cadence, storage/retention estimate, capture paths and explicit synthetic designation. Finish with collection enabled and no extra diagnostic client running.

External device timeouts need not block delivery if captured honestly and healthy roles continue. New integration-load failures, repeated recorder-write failures, missing profiles, unexpected writes or fabricated data block success and require correction/rollback.

**Rollback:** restore the backed-up integration, run `ha core check`, restart and verify prior collection resumes. Preserve all new evidence. Restore saved session Store only if compatibility requires it, recording the maintenance discontinuity.

## Done

Implementation is complete only when R1–R6 and A1–A8 have evidence, the release is pushed and active on HA, all three roles are recording with visible health, incident export works, and backup/rollback are usable. No controller firmware or operating settings have changed.

This release is not a promise to expose unpublished latches or prevent the next dropout. It must preserve a trustworthy account of what each controller reported, when it was acquired, and exactly where observation failed.
