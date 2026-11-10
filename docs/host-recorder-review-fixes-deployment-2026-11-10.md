# Host recorder review-fixes deployment —2026-11-10

## Active release

**0.2.4 is installed and production-verified** from source `8d1208d4a58534626bcb715a13f3322aee03b2d0`. All three roles collect, all25read profiles had successes, queues drained, and Plucky continues receiving the original bytes. The failed attempt and rollback below are retained—not erased by the later successful session.

## Failed0.2.3 verification and preserved rollback

The user requested deployment after [PR9](https://github.com/MrYutz/EG4_HA/pull/9) merged at `67467e3f0ab6acf9d19114aecebc5ea97f51ec83`. Release0.2.3 source `6a1f6bf34c390802dfd0f960ec27a98b107614bb` was pushed in [PR12](https://github.com/MrYutz/EG4_HA/pull/12), installed as a coherent package, hash-checked and configuration-checked before one Core stop/start. All18integration files matched the source. The updated analyzer and unchanged archive scripts were installed in an isolated Plucky release; no sampling, device profile, equipment setting or HA Core version changed.

Native0.2.3 build: `9c6b9a5a5615097eba1be43e93782421b5f3880d87c52238c5a878c90346b657`; session `a0e5c50c-0295-428c-abc9-0459a4d50920`. The original fixed600second window began at the first authenticated API200,13:03:24.996217UTC. Its initial12sensor404responses remain recorded; the first device attempts began about52.33s later. Startup absence is not a measured power outage. Later persistence catch-up was joined to this same window, not substituted with a cleaner window.

| Role | Acquisition attempts | Failed acquisitions | Partial acquisitions | Failed/all transactions | Median/max spacing(s) | Profiles with success |
| --- | --- | --- | --- | --- | --- | --- |
| FlexBOSS master |135 |0 |2 |2/489 |3.583/16.490 |8/8 |
| FlexBOSS slave |201 |1 |2 |2/700 |2.459/13.430 |8/8 |
| GridBOSS |97 |1 |3 |3/365 |5.531/17.406 |9/9 |

All25profiles had success, with source failures/partials retained. Configured1s polling was not achieved1Hz. Source attempts begun inside the window retain full end intervals, including those crossing its boundary.

The deployment was **not accepted as healthy** merely because this first window showed zero raw/journal drops or write errors:

- Native raw queue grew487→661→797 at13:13,13:16 and13:19. Incident queue reached995of1024at13:19.
- Between13:13:08.453483 and13:19:10.594775, raw persistence completed4.1669records/s while estimated arrivals were5.0229records/s. Failed-session queue waits reached189.865s; recent successful writes did not mean recent acquired data was on disk.
- Parent initiated rollback at13:21:02.988248. Core stop returned13:24:00.346201; restore/check/start returned13:26:22.767992. These command times are not API downtime or electrical-outage durations.
- Shutdown exposed one actual raw reserve-related write error at13:22:45.937191, followed by recovery. Terminal health at13:23:48.827461 retained `WriteErrors=1`, `LossGeneration=1`, zero queue drops and a drained raw queue. Journal drops/errors and incident loss counters were zero.
- Transaction `4c239842-03cc-424a-a58b-c0a61b6bd4b7`, master profile `4:120:26`, acquired13:19:59.811242–13:20:01.015438, is absent from raw transactions but survives in the incident copy. Raw-stream completeness failed; successful incident custody does not erase that loss.

The stopped failed session and interrupted incident remain preserved on Plucky. The rollback restored the exact prior0.2.2 package, not a partial mix. Crucially, the rollback control also accumulated raw/incident queues—165/205at13:27 and374/455at13:31—so PR9-specific causality was not established.

By13:49:47.825540 the rollback control had reached the reserve failure:1,459raw write errors,1,763incident dropped records and8,339,579lost incident bytes, with zero queue-rejection raw drops and zero journal errors/drops. These are prior-session losses, not recovered by restarting the counter. Missing history is not fabricated.

| Role |0.2.2→0.2.3 retained gap(s) |0.2.3→rollback gap(s) |Rollback→0.2.4 gap(s) |
| --- | --- | --- | --- |
| FlexBOSS master |163.076 |286.719 |500.117 |
| FlexBOSS slave |163.070 |286.703 |500.008 |
| GridBOSS |163.056 |286.690 |500.786 |

These are gaps between retained raw transaction intervals, including failed persistence and restart time—not measurements of electrical outages or engine cycles. Exact endpoints remain in the private evidence.

## Measured shared storage bottleneck

A standalone replay used2,670genuine rows spanning553.145source seconds and five actual retained incident manifests. It exercised real raw-writer and incident-capture storage, not mock echoes. The old0.2.2 control also showed the same amplification; its replay took76.53s. Corrected comparisons below are off-host measurements, not native ARM speed claims.

Every guarded append performed two quota/reserve passes. Those repeatedly decoded all retained manifests for age expiry and repeatedly reopened current manifests while building retention candidates, despite the earlier membership cache. Growing retained history amplified ordinary append work. The genuine incident corpus did not show a per-poll recovery storm or a new payload-size explosion.

## Backups and evidence

Native deployment directory:
`/homeassistant/.eg4-investigation/deployments/host-recorder-0.2.3-20261110T125552Z/`.

The private pre-install package/configuration/Store archive was copied to Plucky and SHA256-verified:
`a6000343c20141bb1774a538113de3a003efa8fc103f912a05a63b8b5d9f2638` (1,682,944bytes).
A separate stopped configuration/Store snapshot remains preserved. The failed0.2.3 package was retained under `verification-failed-0.2.3`; no unrelated HA data or backup was deleted.

Private evidence root:
`/home/greg/.local/state/eg4-host-recorder/pr9-deployment-20261110T125552Z/`.

It retains the original API window, partial and settled raw exports, configuration/installed hashes, actual source failures, rollback timing, replay profiles and sealed incident receipts. Credentials and raw records remain outside Git.

The0.2.3 installed-byte-identical sources also produced an isolated, explicitly synthetic complete incident on Plucky Python3.13:1,203acquisitions per role covering source−600..602seconds, eight trigger records, closed manifest and all final hashes verified. The updated analyzer succeeded on that bundle, the genuine first-window export and a genuine sealed incident. This proves the off-host source/export seam—not native ARM throughput, a real power fault or deployment acceptance.

The generator's independent healthy240V/60Hz evidence remains authoritative. No host deployment or observation gap here establishes engine cycling or resolves the historical inverter/GridBOSS acceptance failure.

## Corrected0.2.4 package

Source `8d1208d4a58534626bcb715a13f3322aee03b2d0` retains all merged PR9 fixes and adds the measured storage-path correction. Unchanged incident manifests are reused only after fresh path/regular-file and device/inode/size/mtime/ctime validation; cache misses check the opened descriptor and read stability. Mutable callers detach before modification. Sorted candidates and file observations are reused only inside one locked quota pass and invalidated after expiry or unlink. No rate, queue, reserve, current-byte quota, raw-first reclamation, ownership, atomic-write, fsync or loss-accounting requirement was relaxed.

| Identical retained-storage replay |0.2.3 |Corrected |
| --- | --- | --- |
| Wall seconds |72.7925 |45.3663 |
| Manifest read opens |243,722 |27 |
| Manifest bytes read |4,210,828,451 |212,772 |
| Stat calls |4,649,141 |3,672,478 |

Throughput improved1.60×. Fsync/statvfs and atomic-commit counts were unchanged. All2670raw rows and identical113,479,471incident bytes persisted without replay loss. These remain Plucky x86_64 measurements, not native ARM guarantees.

Full corrected source suite:350passed,3skipped,3upstream warnings. Focused storage/trace/recorder/actual-HA-stop group:103passed; source typing0errors and critical/import Ruff passed. Both independent Standards and Spec reviews of `8d1208d` reported no findings. The new real-file regressions cover unchanged-manifest read amplification, in-place same-size/preserved-mtime changes, replacement inode/entry identity and symlinks.

All18installed0.2.4 files matched committed hashes. Native build ID is `d87885fda49cf9740f5592228e427d6f546cfe9e4f34b3f6e08f08822bf6e323`. The isolated Plucky archive/analyzer release now selects the same source. Native rollback backup is under `/homeassistant/.eg4-investigation/deployments/host-recorder-0.2.4-20261110T134933Z/`; its off-host verified archive SHA256 is `16c94b87c7858ab08724d15deea7dc8bbe8f00622e0123ed36ec9070d7873271`.

| Corrected deployment operation | Command start(UTC) | Command return(UTC) |
| --- | --- | --- |
| Core stop |13:50:15.170037 |13:50:46.317691 |
| Core start |13:51:41.818021 |13:53:14.921921 |

The existing256MiB raw/32MiB controller/128MiB incident ceilings and1GiB reserve remain unchanged. No configuration, HA Core update, equipment settings, firmware or additional device reader changed.

## Corrected native capture and custody

Session `9619ce02-0190-4ef3-a3d3-0f59e72c3a80` was verified over its first fixed600seconds after authenticated API200:13:51:57.882525–14:01:57.882525UTC. All103successful sensor samples matched the new build;13startup sensor404responses remain recorded. First device acquisition began about40.40s after API recovery. The window was not restarted or cleaned.

| Role |Acquisition attempts |Failed acquisitions |Partial acquisitions |Failed/all transactions |Median/max spacing(s) |Profiles with success |Maximum BMS age(s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FlexBOSS master |152 |0 |3 |3/544 |3.381/19.292 |8/8 |49.167 |
| FlexBOSS slave |187 |0 |3 |3/658 |2.800/16.221 |8/8 |47.506 |
| GridBOSS |75 |8 |12 |25/281 |5.909/18.455 |9/9 |N/A |

These source failures/partials and achieved rates remain visible; this is not guaranteed1Hz or uninterrupted device availability. GridBOSS transport/holding reads were intermittently degraded, including `3:2000:108`; no callback errors were observed. Other roles continued, and all25profiles had successful reads.

| Sampled queue depth |First-window peak |Window-end observation |14:06:32 follow-up |
| --- | --- | --- | --- |
| Raw |65 |3 |0 |
| Incidents |110 |12 |2 |

Raw/journal drops and write errors were zero throughout the corrected window and follow-up. Incident dropped records/lost bytes were zero. One startup incident-health sample was degraded; the retained reserve-related `LastError` remains inspectable even after current incident state recovered. Overall health was often degraded by real device/profile failures during the window, then healthy at the14:06:32follow-up—not represented as continuous equipment health. That follow-up had4,118persisted raw records.

The settled original-window export verifies later acquisitions persisted for all three roles before declaring its tail received. Persistence wait host-timestamp deltas were0.093s median/27.691s maximum, with no negative deltas; these are not promised timing bounds. Exact bytes, fixed-prefix hashes, selection provenance and updated-analyzer output are preserved privately. The analyzer exited zero on the genuine window.

At14:06:16UTC, the archive was healthy with21raw files/517,154,032bytes, seven sealed incidents and one moving incident. Every sealed retained file and both manifest hashes matched. Existing incomplete source incidents remain incomplete. Eight-day raw/controller retention and permanent snapshots remain in force; collection is not dependent on Plucky.

The corrected installed-byte-identical sources also passed the isolated synthetic full-pre/post incident and updated analyzer:1,203acquisitions per role, eight triggers, closed complete manifest and verified final hashes. Execution was on Plucky Python3.13 without HA state publication or equipment connections; native assurance comes from the genuine window above, not an ARM claim about that synthetic run.

All EG4 options were structurally verified unchanged. Native free space was1,072,068KiB at the final check—still close to the1GiB reserve. The repair removes demonstrated overhead and enables owned reclamation; it is not a general HA disk-capacity fix or a guarantee against unrelated writers. No unrelated HA data/backups were deleted. Backups and retained failed evidence support whole-package rollback; global configuration or entity registries were not restored.

Corrected-window evidence is under the private root's `production-0.2.4/`: `first-recovery-window-summary.json`, `native-queue-stability.json`, `first-window-raw-receipt.json`, `final-ha-health.json`, `sealed-incident-custody.json`, `configuration-preserved.json` and source/backup hashes. Raw logs/configuration remain outside Git.
