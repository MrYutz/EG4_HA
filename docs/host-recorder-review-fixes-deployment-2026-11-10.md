# Host recorder review-fixes deployment —2026-11-10

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

## Measured shared storage bottleneck

A standalone replay used2,670genuine rows spanning553.145source seconds and five actual retained incident manifests. It exercised real raw-writer and incident-capture storage, not mock echoes. On Plucky x86_64,0.2.3 replay took72.79s with zero drops/errors but performed243,722manifest reads totaling4,210,828,451bytes and4,649,141stat calls. The old0.2.2 control took76.53s with the same amplification. These are off-host measurements, not native ARM speed claims.

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
