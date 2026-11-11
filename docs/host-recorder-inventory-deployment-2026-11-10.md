# Host recorder 0.2.5 — live inventory deployment

## Result and scope

HA is running the source-bound inventory recorder; Plucky is running the matching
standalone analyzer and scheduled file archive. All 35 audited request shapes
succeeded at least twice in the fixed production verification window. The export
contains all 2,352 unique audited register words from the GridBOSS and both active
FlexBOSS inverters.

This is stock-controller-firmware monitoring, not deployed firmware
instrumentation. No controller firmware, protection settings or operating settings
were written; no generator/load test was induced and no second device poller was
started. Recorded request intents were FC03/FC04 only.

The generator was classified off during verification. These samples do **not**
establish a voltage-present/frequency-zero dropout, reconstruct the weekend, or
prove every internal decision branch has been decoded.

## Installed identity

- Host version: **0.2.5**; base commit
  `4860419c1a22736504df4fdc4cf75f9bfd81c8c5`.
- Source: an **uncommitted, content-attested working-tree snapshot**, not a newly
  published GitHub release. The package receipt records all source asset hashes.
- Native recorder build:
  `fb1d22f8052189b798608e7e382e4256bb99fb82c6851694d071f454515aeb2a`.
- New native session: `af19d926-43ae-44d5-9cdd-a6a3da1ed2c9`.
- Catalog: `candidate-gridboss195-flexboss252-2026-11-10.v1`.
- Package: `eg4-monitor-0.2.5.tar.gz`, SHA256
  `6c5bace6a69e3d698e08f383c507b3e12beffbfc8a302eedae3b5a02db09b21e`.
  All 19 packaged assets matched native staging hashes before the stopped-Core
  whole-component replacement. Native health reports the expected build/session.
- Plucky current release:
  `/home/greg/.local/libexec/eg4-recorder-archive/releases/inventory-0.2.5-20261110T204916Z`.
  All three adjacent analyzer modules were installed together. Archive scripts,
  service configuration, dependencies and retention policy were preserved.
  Its `venv` points to the preceding immutable release's unchanged runtime;
  **retain that preceding release**.
- HA Core remains 2026.10.4; no Core update was applied. All 125 preexisting EG4
  entities remain present. The EG4 entry's data, options, version, minor version,
  disabled state, entry ID and domain were compared and preserved.

## Preexisting storage failure and approved recovery

Before this deployment, HA's filesystem was 100% full with zero available space.
Entities continued updating while persistence failed. The old recorder's last
successful raw append was **2026-11-10T14:42:39.503Z**. The captured predeployment
health reported 122,702 raw write errors, 19,863 journal write errors, broken
journal continuity and 143,278 dropped incident records / 952,877,719 lost bytes.
These counters describe recording loss, not a generator fault.

A healthy archive transfer did not mean the source was still recording. Plucky
cannot recover records that HA never persisted; restarting does not reset the
historical evidence of that loss.

Supervisor's disk breakdown identified approximately 5.641 GiB of regenerable
ESPHome cache. After explicit approval and a configuration backup, the installed
ESPHome Device Builder's supported `firmware/reset_build_env` operation ran
`esphome clean-all`. Job `6ea42f7de1ad` ran 21:08:40–21:09:04 UTC and exited 0.
There were no active build jobs beforehand and no flash/upload operation was
requested. All 84 non-metadata configuration-directory files were byte-identical
afterward; only `.device-builder.json`'s `._firmware_jobs` metadata changed.
HA history, configurations, backups and EG4 evidence were not deleted.

Retained source recording resumed around **21:08 UTC**. The interval since the
last successful append remains a historical recording gap. At 21:42 UTC the
filesystem had 6,053,556 KiB available, approximately 5.77 GiB. This is recovered
headroom, **not a permanent capacity fix**: rebuilding ESPHome caches or growth
from other HA writers can consume it again. Do not disable the recorder's
free-space guard to hide that risk.

## Native startup

All times in this receipt are UTC on 2026-11-10.

| Operation | Began | Returned |
| --- | --- | --- |
| Core stop | 21:12:33 | 21:13:13 |
| Installed-package configuration check | 21:13:13 | 21:14:07 |
| Core start | 21:14:58 | 21:16:27 |

The authenticated HA API first returned 200 at **21:15:18.773269**. First successful
primary source acquisition completed at 21:15:29.924914 for the slave,
21:15:30.387948 for GridBOSS and 21:15:31.614232 for the master. Some ordinary numeric
entities remained `unknown` until 21:18:08 while the high-rate recorder was already
receiving data. Startup errors were retained, not excluded by resetting the window.
Core-command timing does not represent an electrical outage.

## Fixed-window coverage and accuracy

The window selects acquisitions starting in
**[21:15:18.773269, 21:25:18.773269)**, exactly 600 seconds. All transactions from
those acquisitions are included, including four tail queries beginning after the
cutoff; the last completed at **21:25:20.721218**. This is not a second, cleaner
window. Thirty-two controller-journal copies were deduplicated against their
original acquisitions.

| Role | Exact shapes | Unique input / holding words | Unique acquisitions | Failed / attempted transactions | Median / maximum acquisition-start gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| GridBOSS | 11 | 232 / 482 | 133 | 0 / 484 | 4.057 / 16.876 s |
| FlexBOSS master | 12 | 240 / 579 | 188 | 1 / 672 | 2.936 / 16.473 s |
| FlexBOSS slave | 12 | 240 / 579 | 222 | 4 / 773 | 2.344 / 15.311 s |

The ten newly added per-device slow shapes each completed at least twice, with
repeat intervals of 301.31–306.51 seconds. No transaction intervals overlapped
within a role. All 543 acquisition records report `ok=true`, but five also report
`partial=true`; this is not a claim that every query succeeded. Five failed
transaction attempts remain in the evidence: one slave input80/40 response
timeout, plus four identity EOFs before request construction (master input120/26
once; slave input120/26 twice and input80/40 once). The latter have no constructed
request packet and zero received bytes; no outbound bytes are invented for them.
Later transport failures, including a primary warning at 21:34:05, were observed;
the fixed-window result is not a claim of perfect lifetime communication.

Independent validation checked request/response identity, checksums, requested
ranges and every received word for **1,924 successful transactions**. It also
compared **8,932 decoded voltage/frequency/power fields** across the **543 unique
acquisitions** against those verified wire words, and compared retained raw arrays
with their source responses. This proves wire-to-recorder fidelity for those
fields, not physical meter calibration.

The audited ranges, using zero-based addresses, are:

- GridBOSS input0–131 and500–599; holding0–322,2000–2107,4097–4137,4500–4509.
- Each FlexBOSS input0–239; holding0–336,500–723,5000–5017.

These are vendor-wrapper read surfaces, not evidence that arbitrary standard RTU
requests or every CPU/internal register are accessible. Prohibited/undefined
shapes were not substituted: no GridBOSS4516, undefined GridBOSS input132–499,
exact FlexBOSS224/248 start, or uninitialized5000/40 request.

### Actual received samples

Timestamps below are response completions, not physical measurement instants.

| Role / quantity | Input register | Raw word(s) | Decoded value | Response completed UTC |
| --- | --- | --- | --- | --- |
| GridBOSS grid voltage | 1 | 2416 | 241.6 V | 21:25:15.896640 |
| GridBOSS UPS voltage | 2 | 2404 | 240.4 V | 21:25:15.896640 |
| GridBOSS generator voltage | 3 | 20 | 2.0 V | 21:25:15.896640 |
| GridBOSS grid frequency | 129 | 6009 | 60.09 Hz | 21:25:16.957442 |
| GridBOSS generator frequency | 130 | 0 | 0.00 Hz | 21:25:16.957442 |
| GridBOSS generator power | 30,31 | 0,0 | 0 W | 21:25:15.896640 |
| FlexBOSS master battery voltage | 4 | 544 | 54.4 V | 21:25:19.288078 |
| FlexBOSS slave battery voltage | 4 | 544 | 54.4 V | 21:25:19.013970 |

Source transaction IDs:

- Grid voltage/power: `086274ec-7787-4b60-9697-61f24fed2546`.
- Grid frequency: `07d06010-c0c5-4b51-b046-0539f33390f1`.
- Master: `eb393d6a-716f-4c7d-b445-d96ad66c2f91`.
- Slave: `d1ed982e-377e-42b1-8e8c-d45d8b6c97d5`.

GridBOSS input519 was also received as `516` (`0x0204`), candidate state4/reason2,
at 21:25:17.858058 in transaction `ff75b734-fd47-40a8-be2a-0fd53e9b2050`.
This is a candidate **last-written state/reason**, not a first-cause event,
firmware decision timestamp or relay-contact feedback.

The published HA high-rate generator attributes were independently matched to
recorded acquisition `e39e2086-22cb-41be-a568-f9db39dc7e61` at 21:28:06.441:
voltage2.0, frequency0.0, power0.0, RunState`off`, source`gridboss_direct`.
This compared the actual acquisition identity, not arbitrary nearby samples.

For 133 GridBOSS voltage/frequency pairs, request-start separation was median
1.021 s, p95 1.823 s, maximum 4.101 s. The paired samples are **not simultaneous**.
The generator's approximately 2 V/off observation is not the user's reported
healthy generator AC during a dropout, and does not contradict that report.

## Persistence, custody and limits

The API watcher retained 125 observations, including eight startup API errors and
eight startup entity-read errors. Within the new session, raw/journal write-error
and dropped-record counters stayed zero; incident dropped-record/lost-byte
counters stayed zero. Queue maxima were raw106, journal1, incident167; window-end
queues were 1,0,9. Raw enqueue-to-append-start delay was median0.148 s and
maximum50.813 s, including startup backlog. Neither a successful append nor a file
copy proves instant fsync or survival of an arbitrary power loss.

The selected completed cohort contains 23,594,750 raw bytes and 793,070 journal
bytes. The observed raw rate is **135.01 MiB/hour**. At the configured 256 MiB raw
ceiling, that implies approximately **1.90 hours** of local catch-up capacity at
this rate, not a guaranteed 72-hour local history. Plucky keeps raw/journal data
for eight days; sealed incidents have no automatic expiry. The archive's 10 GiB
free-space guard and existing retention policy were unchanged.

Five actual native complete-line file prefixes totaling **42,171,908 bytes** were
captured file-only over SFTP, read twice through their stable open handles and
hashed. Every prefix then matched the corresponding archived bytes on Plucky,
using first-record identity and SHA256. These are verified prefixes, not finalized
or atomic site-wide snapshots. No additional controller connection was used.

At **21:42:15.001160**, native health was `healthy`, with expected build/session,
`WriterError=null`, no new-session persistence error/drop counters, empty
missing/failed read-profile lists, and primary source ages below 3.16 seconds.
Incident status nevertheless remained **Incomplete=true, Truncated=true**;
startup/prebuffer limits are not erased by healthy current collection.
At **21:43:34.484943**, scheduled Plucky transfer status was healthy with no error,
5,435,559 newly copied bytes across four changed files, and no unavailable
incidents. The archive timer was active/waiting.

Remaining interpretation and diagnostic limits:

- All audited words are captured, but the canonical catalog still labels
  **2,038 of 2,352 as `unknown_raw`**. This does not mean every such word is unknown
  everywhere; it means it has not been promoted into source-traced catalog semantics.
- GridBOSS input10–25 raw current words are retained. Legacy signed16/10 A
  numeric entities are unchanged, but the ampere scale remains unverified against
  competing public interpretations; HA exposes `CurrentInterpretation` accordingly.
- Aggregate energy counters are retained; their kWh scale is unverified.
- Candidate firmware interpretation does not attest the installed controller image.
- Polling can miss transitions between reads. There is no deployed internal
  decision-event trace, first-cause latch proof or complete register/branch model.
- The analyzer correctly reports every role `observed_not_continuous` and
  `sample_sufficiency=insufficient`, despite no missing expected blocks. Coverage,
  source fidelity and causal sufficiency are different claims.
- Generator-active observations are still needed to evaluate the real dropout.
  This deployment cannot reconstruct missing historical records or promise that
  stock firmware exposes every decision needed to prove the cause.

## Checks and rollback assets

Exercised checks: full local suite **370 passed, 3 skipped**, with three upstream
warnings; old-package and installed-package `ha core check` both passed; deployed
standalone analyzer `--help`, `--registers` on retained source data and complete
new-window analysis; authenticated live HA states; native build and persistence
health; scheduled archive transfer and independent native/archive custody.

Private evidence root on Plucky:

```text
/home/greg/.local/state/eg4-host-recorder/inventory-0.2.5-20261110T204916Z/
```

Key files:

- `verification.json`: window definition, all 35 block results, failures, source
  timing, independent checks, sample identities, native health and explicit limits.
  A null counter summary means that counter is not exposed, not zero.
- `latest-register-samples.csv`: all 2,352 word samples with raw value, address,
  source identity, timestamp and interpretation/uncertainty.
- `latest-register-blocks.json`: all 35 latest block projections.
- `first-window-complete-acquisitions.jsonl` and
  `first-window-complete-analysis.txt`: authoritative acquisition-cohort input and
  deployed CLI output. Earlier `first-window-received-rows.jsonl` /
  `first-window-analysis.txt` were intermediate transaction-start selections and
  are superseded; do not use them for final coverage counts.
- `native-received-prefixes/receipt.json`, `native-archive-custody.json`,
  `configuration-preserved.json`, `archive-status.json`, `local-full-suite.txt`,
  package and Plucky deployment receipts, and final `SHA256SUMS`.

Native rollback assets are under
`/homeassistant/.eg4-investigation/deployments/inventory-0.2.5-20261110T204916Z/`.
The previous whole component is `previous-eg4_monitor/`. With Core stopped,
quarantine the failed current component, move the previous component back, run
`ha core check`, then start Core. Preserve new captures; do not overwrite unrelated
HA configuration or restore global `core.config_entries` indiscriminately.
This rollback was prepared, not rehearsed by undoing the successful deployment.

Offhost backups include:

- `before-integration-state-evidence.tar`, SHA256
  `213fecb5fbdd4db56f291511ed0865e30ba57f5a8bd6eb8986e511410a62eaf3`.
- `stopped-state.tar`, SHA256
  `12d9d7f256301104c65a4b114d837f2db4e92a38d644b7e8723463fa201e003b`,
  also checked against the native copy.
- `before-esphome-config.tar`.

These backups contain credentials and stay private; they are not repository
artifacts. Plucky rollback is an atomic `current` retarget to
`releases/8d1208d4a58534626bcb715a13f3322aee03b2d0`, which is retained and also supplies
the current pinned Python runtime.
