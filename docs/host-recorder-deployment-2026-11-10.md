# Host recorder0.2.2 deployment receipt —2026-11-10

## Active release and scope

Deployed to the enabled EG4 entry `01KM5TPJ4PMEGW55RT8W1KY3ZS`; API entry state is `loaded`.
Preflight HA Core was2026.10.4/aarch64; no Core update was performed.

- Installed source commit: `ccd1fd76114b05fd3920268458f1d86c946ad033` ([PR7](https://github.com/MrYutz/EG4_HA/pull/7)). Later receipt-only commits do not change this package.
- Native `RecorderHealth.BuildId`: `b1fb8b8de118fac7f675e3a43a164e1eef3926a08ee7665671365b8658a7b156`.
- Recorder session: `74387339-2366-4f39-b443-0280221bc66c`.
- Committed19-file deployment archive SHA256: `1681713ee1bf27b59a6fa6f91cd332d4df7d0f1c85e8ff6ef07dbb3c05597275`. All installed plugin files and the staged standalone analyzer matched their committed hashes; configuration checks passed before each restart.
- Collection remains enabled through the existing single device-consumer path. No firmware, equipment operating/protection settings, extra live diagnostic reader, dependency, service or dashboard was added or changed. Only three host-storage ceilings changed; all other config-entry semantics were asserted unchanged while Core was stopped.

Independent multimeter evidence of healthy generator AC throughout the historical dropouts remains authoritative. Qualified0 Hz does not establish engine stopping. The historical inverter/GridBOSS acceptance/signaling failure mode is **not yet established** by this release.

## Preserved first recovery windows

All times below are UTC on2026-11-10. Each window is the fixed first600 seconds after the first authenticated API200 observed by the external verifier. Startup delay, rejected data, failures and losses were retained; no cleaner window replaced an earlier one. Health maxima are sampled every5 seconds, not claimed exact instantaneous peaks.

| Build | Pushed source | Fixed window | Sampled raw queue maximum | Sampled raw drops |
| --- | --- | --- | --- | --- |
| 0.2.0 | `4e634d0` | 05:38:43.237895–05:48:43.237895 | 86 | 0 |
| 0.2.1 | `d19e45c` | 06:09:53.035659–06:19:53.035659 | 1024 | 1048 |
| 0.2.2 | `ccd1fd7` | 06:37:25.864739–06:47:25.864739 | 169 | 0 |

The0.2.0 final review found two partial/crash evidence omissions: signed65535 must remain−1 W, and raw output frames must be analyzable without a later acquisition summary. Both were fixed red/green. The0.2.1 real host then exposed a blocking persistence bottleneck: repeated global inventory walks under the append lock.0.2.2 caches **membership only**, invalidating on directory/owner/manifest identity or metadata changes, with health-cadence rediscovery. Current bytes/free space and target/prune inode/path checks remain live. No queue-capacity increase or loss-counter suppression was used.

A read-only native profile of20inventory calls changed from0.878s to0.065s, including the cold scan. This is an inventory measurement, not a promised whole-recorder speedup.

## Corrected live window

Source attempts begun within the fixed window retain their full original end intervals, including rounds that cross its end. The three roles first attempted acquisition about50.36–50.39s after API recovery; that startup absence remains part of the window. The configured1s target is **not** observed1Hz.

| Role | Acquisition attempts | Failed acquisitions | Partial rounds | Failed/all transactions | Median/max start spacing(s) | Distinct profiles, all with success | BMS maximum/last age(s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| flexboss_master | 204 | 1 | 3 | 3/709 | 2.502 / 12.859 | 8 | 15.804 / 2.550 |
| flexboss_slave | 216 | 1 | 8 | 9/744 | 2.304 / 12.387 | 8 | 14.033 / 2.566 |
| gridboss | 125 | 0 | 0 | 0/454 | 4.158 / 13.151 | 9 | N/A |

GridBOSS continued through inverter failures. All dynamic, diagnostic, BMS and holding-setting profiles were attempted and had successful responses. Original BMS identities and intervals remain in each record. Holding-profile freshness at the fixed window end:

| Role | Function:start:count | Successful/attempted | Last known successful age(s) |
| --- | --- | --- | --- |
| flexboss_slave | `3:0:20` | 2/2 | 238.487 |
| flexboss_slave | `3:110:125` | 9/9 | 39.406 |
| flexboss_slave | `3:20:90` | 2/2 | 233.454 |
| flexboss_slave | `3:235:102` | 2/2 | 230.979 |
| flexboss_master | `3:0:20` | 2/2 | 238.061 |
| flexboss_master | `3:110:125` | 9/9 | 37.252 |
| flexboss_master | `3:20:90` | 2/2 | 233.175 |
| flexboss_master | `3:235:102` | 2/2 | 230.273 |
| gridboss | `3:0:20` | 2/2 | 236.029 |
| gridboss | `3:110:125` | 2/2 | 221.385 |
| gridboss | `3:2000:108` | 2/2 | 206.945 |
| gridboss | `3:20:90` | 2/2 | 226.884 |
| gridboss | `3:235:88` | 2/2 | 210.895 |
| gridboss | `3:4500:10` | 69/69 | 6.017 |

The corrected window had no raw, journal or incident drops and no raw/journal write errors. Its sampled incident queue maximum was237records/1,485,050bytes. Raw queue wait medians were0.82–0.91s; the cold-start maximum was53.729s and is not hidden as fresh persistence. Unchanged journal baselines need not produce a row every poll; source ages and pending/persisted health are separate.

Through the later07:02:25 health observation, the same corrected session remained healthy with raw/journal/incident loss counters still zero; raw persistence had completed8,940records. The existing availability entity reports two inverters; GridBOSS is separately present in its `RecorderHealth.Roles`.

Raw custody checks covered1,898sent requests, all wire function3/4, plus9attempts with truthfully null unsent request bytes.51rejected/unrelated frame verdicts retained their bytes. Full firmware-relevant words remained available without invented physical-contact or protection-bit meanings:

| Role | Matching input6 samples | Matching input519 samples |
| --- | --- | --- |
| flexboss_master | 203 | N/A |
| flexboss_slave | 215 | N/A |
| gridboss | N/A | 125 |

## Restart timing and observation gaps

Native command timing is not API downtime, acquisition continuity or electrical-outage duration:

| Build | Core stop command start–return | Core start command start–return |
| --- | --- | --- |
| 0.2.0 | 05:37:52.042971–05:38:22.455677 | 05:38:28.122640–05:39:52.052921 |
| 0.2.1 | 06:07:50.139710–06:09:33.924618 | 06:09:37.696079–06:11:38.786306 |
| 0.2.2 | 06:33:31.077946–06:36:55.411590 | 06:37:01.746056–06:39:18.376970 |

The two corrective restarts were required by discovered defects, not maintenance-window waiting or rollback rehearsal. Retained rolling-raw source-interval separations around those restarts are below. Interrupted/lost observations mean continuity is unknown; these are **not engine-cycle or house-outage measurements**.

| Role | Legacy→0.2.0(s) |0.2.0→0.2.1(s) |0.2.1→0.2.2(s) |
| --- | --- | --- | --- |
| flexboss_master | 92.534 | 134.684 | 291.658 |
| flexboss_slave | 93.747 | 134.681 | 307.054 |
| gridboss | 91.001 | 134.675 | 289.332 |

An actual0.2.0 Core stop persisted terminal `reason=shutdown` health before the later session. The permanent isolated-HA Core-stop regression also exercises drain and terminal health through actual HA/HTTP/TCP lifecycle behavior.

## Storage, capture and export

Producer root: `/config/.eg4-investigation/host-recorder-v2/01KM5TPJ4PMEGW55RT8W1KY3ZS/`, with `raw/`, `controller-observations/` and `incidents/`. Canonical addon paths resolve under `/homeassistant/`; Core uses `/config/`.

| Stream | Host byte ceiling | Maximum age policy |
| --- | --- | --- |
| Raw |768MiB |72h |
| Controller journal |64MiB |14d |
| Incidents, including open segments |512MiB |14d |
| Filesystem reserve |1GiB | Enforced before writes |

Observed corrected raw cohort volume was88.056MiB/h: about8.72h at the raw byte ceiling, **not72h guaranteed retention**. Free space after removing only our verified transfer-stage duplicates was2.020GiB. Other HA writers and incident/journal growth can shorten retention further. No unrelated HA database, old investigation or backup was deleted or reconfigured. These ceilings do not govern the HA Recorder database.

The genuine final live snapshot contains22files/222,467,947bytes, with every off-host SHA256 verified. Active JSONL was copied as a fixed open-descriptor complete-line prefix, not presented as a finalized file or atomic site snapshot. Live/restarted/warming incidents remain explicitly incomplete where appropriate. Earlier failed-build snapshots and their losses remain preserved. Owned transfer stages were removed **only after** off-host size/hash verification; producer evidence, backups and native synthetic evidence remain.

Durable private off-host evidence root: `/home/greg/.local/state/eg4-host-recorder/2026-11-10/`.

- Index: `6.1-green.json`; failed evidence: `6.1-red.json`.
- Final genuine capture: `production-0.2.2/received-0.2.2-first-window/`.
- Original first capture: `production-6.1/received-0.2.0-first-window/`.
- Failed build and stop snapshot: `production-0.2.1/stopped-0.2.1/`, with its original fixed health window.
- Native installed-source synthetic copy: `production-0.2.2/synthetic-0.2.2-installed-proof/`.
- Exact metrics, command timings, custody inventories, source hashes and observation gaps are in the `6.1-*.json` records. Historical paths inside those records may name the original `/tmp/eg4-host-recorder` location; this durable mirror preserves identical contents.

The actual streaming analyzer ran successfully on both off-host genuine snapshots and the copied synthetic bundle. Reports are `6.1-final-genuine-analysis.txt` and `6.1-offhost-synthetic-analysis.txt`; raw data and these reports remain outside Git.

## Installed-source synthetic proof

The unmodified installed0.2.2 recorder ran on native aarch64/Python3.14.10 with an explicitly controlled clock, **no device sockets, transport or HA state publication**, in a separately labeled synthetic directory. A temporary private interpreter did not alter addon/global package state and was removed afterward; it is not an integration dependency.

The closed, complete, final-hash-verified bundle retains1,312acquisitions per role, source seconds−700..611, with18trigger records including the EPS anomaly outside the generator-power-loss interval, qualified-frequency inconsistency and input6 changes. This proves full warmed pre/post retention and export, not a real equipment fault. All transferred files matched hashes; the off-host CLI succeeded. Its manifest remains on-host at:

`/homeassistant/.eg4-investigation/deployments/host-recorder-20261110T035331Z/synthetic-0.2.2-installed-proof/incidents/incident-20261110T065307-575a1f9c924f/manifest.json`

## Contract verification and review

[R1–R6/A1–A8](host-recorder-mvp-spec.md) are accounted for by raw/transport boundaries, truthful source/charge transitions, independent role/profile capture, journal persistence/recovery and actual shutdown, owned-only pressure/rotation/restart retention, controlled-clock complete incidents and genuine installed production/export proof.

- Final complete workspace suite:244passed,3skipped,3upstream HA/aiohttp warnings (`6.1-final-full-suite-green.txt`).
- Final storage/trace/actual-HA-stop group:68passed; final review regressions plus existing investigative consumer:10passed after their red evidence.
- Changed recorder/analyzer source typing:0errors. Critical/import Ruff passed. Earlier unrestricted21style/validation-boundary diagnostics and unchanged7options-flow SDK typing diagnostics are classified, not suppressed or called clean.
- Both independent final review axes closed all findings and reported no remaining P1/P2 in `ccd1fd7`; source review is not substituted for the real host proof above.
- The user's untracked investigative Q1 caller and fixture needed only namespace/private-frame-interface migration after full-suite collection exposed them. Their assertions and device behavior were not weakened; they remain untracked, were not published/deployed or run on hardware. The exact local-only patch is retained in the evidence root. Other user work was not committed.

## Rollback readiness

Before replacement the installed0.1.0 integration plus relevant session Store/config entries were archived on-host and copied off-host. Actual archive45members/1,256,448bytes, SHA256:

`533ee80a82cef2a07704f9c704c26025b0b0c986628394748f4a0cc599bbec76`

Native backup: `/config/.eg4-investigation/deployments/host-recorder-20261110T035331Z/integration-and-state.tar`; durable off-host copy: `deployment-backups/20261110T035331Z.tar`. Source hashes, release0.1.0 and Store schema1 were verified. Previous whole integration folders and newer Store snapshots are also retained privately. The archive contains credentials: do not publish it or raw config entries.

Rollback route on the established SSH addon, **not executed solely as a rehearsal**:

```sh
ha core stop
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mv /config/custom_components/eg4_monitor /config/.eg4-investigation/deployments/host-recorder-20261110T035331Z/failed-$stamp
tar -xf /config/.eg4-investigation/deployments/host-recorder-20261110T035331Z/integration-and-state.tar -C /config custom_components/eg4_monitor
ha core check && ha core start
```

Replace the whole integration directory so new-only modules cannot survive rollback. Preserve all new capture/export evidence. Restore only the relevant logger Store from its pre-release backup if payload compatibility actually requires it; do not restore global `core.config_entries` and discard unrelated HA changes. The three host-only ceilings can remain ignored by older code. No rollback-triggering integration load/write/profile defect remains in the corrected proof; transient device failures remain visible rather than concealed.
