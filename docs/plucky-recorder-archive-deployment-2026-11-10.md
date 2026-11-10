# Plucky recorder archive —2026-11-10 deployment

## Installed source and runtime

- Source: `dec0820cef61b1032c13ba21ad4e667c4582b0f3`, [PR10](https://github.com/MrYutz/EG4_HA/pull/10), stack entry7.1 / EG4REC-07 / R7–A9 / story9.
- The pushed PR head, `main` base and complete incremental diff were verified before installation. All six installed script/requirements/unit files matched the committed hashes; the analyzer is the unchanged published version, not the user's modified working copy.
- Immutable release: `/home/greg/.local/libexec/eg4-recorder-archive/releases/dec0820cef61b1032c13ba21ad4e667c4582b0f3/`; `current` selects it. Its isolated Python3.13 environment contains Paramiko5.1.0. No HA dependency or component source changed.
- Enabled, active user timer: `eg4-recorder-archive.timer`. The oneshot service has a15min deadline, runs60s after the previous activation finishes and cannot overlap itself. Plucky's user manager has lingering enabled.
- Destination: `/home/greg/.local/state/eg4-host-recorder/archive/01KM5TPJ4PMEGW55RT8W1KY3ZS/`. Archive paths were verified private; the configuration directory/file are0700/0600.
- Source: existing recorder files under `/homeassistant/.eg4-investigation/host-recorder-v2/01KM5TPJ4PMEGW55RT8W1KY3ZS/` on HA. Verified host keys and explicit key-only SFTP authentication; no device connections. The existing root credential is broader than this read-only client's operations, not an OS sandbox.

## Exercised transfer and preservation

All times are UTC. Initial local backfill consumed the early preserved source first, then the later stopped source, before connecting to the newer live source. This retained evidence subsequently evicted by HA.

| Activation | Start | Finish | Newly copied bytes |
| --- | --- | --- | --- |
| Installed bootstrap |12:34:02.012 |12:34:04.759 |753,891,233 |
| Later stopped-source backfill |12:34:04.798 |12:34:05.111 |83,068,222 |
| First live SFTP service |12:34:05.949 |12:34:38.691 |43,877,956 |
| Immediate timer activation |12:34:39.324 |12:34:39.958 |810,560 |
| Subsequent minute activation |12:35:40.562 |12:35:41.657 |2,913,006 |

The last listed run was healthy:17raw files/428,117,478bytes, four controller files/8,806,168bytes, four sealed incidents and one moving incident. Every sealed incident's retained file sizes/hashes and both source/archive manifest hashes were independently checked. All four sealed incidents remain **incomplete** where the source was incomplete; sealing proves received custody, not complete historical acquisition. The installed analyzer successfully reconstructed the large sealed incident, including formerly uncheckpointed records.

Raw/controller segments expire eight days after their last newly received bytes. Incident snapshots have no automatic expiry. There is no recent-data byte-ceiling eviction on Plucky; insufficient free space fails visibly. Eight days accumulates prospectively, not as fabricated backfill.

## Actual HA storage failure and approved correction

During this work the existing HA recorder crossed its1GiB free-space reserve. Native health reported its last successful raw persistence at11:07:07.461, followed by reserve-related write failures and lost records. Prior loss counters and the failed-period source were preserved, not reset in reports or treated as evidence of generator cycling.

Greg separately approved smaller recorder-only ceilings and a Core restart because the existing options API cannot edit these advanced values. Before changes:

- A moving copy and a stopped copy were retained on Plucky. All46stopped-source files/810,816,391bytes matched remote SHA256s.
- A private complete configuration backup was saved outside Git. A structural comparison established that only the three recorder ceilings changed.
- Five oldest preserved, recognized raw rotations totaling124,561,677bytes were removed from the stopped recorder namespace; associated obsolete ownership claims were removed. No unrelated investigation, HA database or backup was deleted. The producer subsequently applied its normal owned retention.
- `ha core check` passed. No power firmware, equipment settings, device profiles, sampling interval or integration source changed.

| Operation | Command start | Command return |
| --- | --- | --- |
| Core stop |12:12:16.094 |12:12:42.558 |
| Core start |12:15:55.864 |12:18:38.625 |

These command durations are not API downtime or electrical-outage durations. Retained transaction evidence has the following discontinuity, including the earlier failed persistence and approved restart:

| Role | Last old-session transaction end | First new-session transaction start | Retained-source gap(s) |
| --- | --- | --- | --- |
| FlexBOSS master |11:03:38.892 |12:17:00.312 |4401.420 |
| FlexBOSS slave |11:03:39.393 |12:17:00.315 |4400.922 |
| GridBOSS |11:03:39.282 |12:17:00.316 |4401.035 |

The gap is unknown observation coverage—not a measured power loss or engine cycle. Missing records cannot be recovered by successful replication.

### Recovered capture and current limits

At12:36:25UTC, native recorder state was healthy in session `e14185a9-84fe-4605-a35a-760d133fcf80`, with the same0.2.2 build ID `b1fb8b8de118fac7f675e3a43a164e1eef3926a08ee7665671365b8658a7b156`. The new session had5,187persisted raw records, zero raw/journal drops or write errors, and all three roles collecting. Incident evidence still exposes the historical reserve error and incomplete/truncated coverage. Quiet controller-journal age is not a promise of one row per sample.

| HA spool | Current ceiling |
| --- | --- |
| Raw |256MiB |
| Controller journal |32MiB |
| Incidents |128MiB |
| Unchanged free-space reserve |1GiB |

Native free space at the final check was1,263,052KiB. At the earlier measured88.056MiB/h,256MiB gives approximately2.9h of local raw catch-up, not a guarantee. Other HA writers, gaps and recorder loss can shorten coverage. Plucky retention does not make HA disk pressure impossible; check both native `RecorderHealth` and archive `last_success_at`. Normal replication needs no HA restart.

## Verification and review

- Final complete workspace suite:298passed,3skipped,3upstream warnings.
- Archive/local-files/real-loopback-SFTP group:54passed, including actual child-process termination, retention boundary, path/inode protection, partial rotation, source disappearance and interrupted-transfer receipt/resume.
- Changed archive source typing:0errors; critical/import Ruff passed.
- Independent Standards and Spec reviews of `dec0820` closed all findings, with no remaining findings on either axis.
- Review defects were reproduced and corrected: crash windows around namespace/index updates; partial incident rotation; omitted uncheckpointed incident files; CLI replacement of an already-written failure receipt. A real-corpus run also exposed slow unbuffered line scanning; default buffered local reads reduced the installed753,891,233byte backfill to about2.75s. The unchanged published analyzer additionally caught invalid final hashes on moving files; moving manifest entries now carry no final hash.
- The user's six modified source/analyzer/test files and other investigation artifacts were not committed or deployed.

## Private evidence and operation

Evidence root: `/home/greg/.local/state/eg4-host-recorder/plucky-archive-delivery-2026-11-10/`.

It contains approvals, source/installed hash inventories, stopped-source/configuration backups, actual systemd journal/status, native before/after health, measured source gaps, red/green test outputs and analyzer reports. Raw evidence and credentials remain outside Git. Earlier retained investigations remain intact.

Use the [operator guide](mother-of-all-data-loggers.md#plucky-off-host-archive) for status/configuration and stopping the timer without deleting evidence. The historical inverter/GridBOSS acceptance/signaling failure mode remains undetermined; this release improves retention, not power behavior.
