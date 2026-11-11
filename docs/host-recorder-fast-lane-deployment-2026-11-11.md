# Host recorder 0.2.6 — fast lane and idle cadence deployment

## Result and scope

Home Assistant is running recorder 0.2.6. With no generator present, all three
devices are read once every 30 seconds; the GridBOSS fast lane is armed and
inactive. Both behaviours are described in the
[operator guide](mother-of-all-data-loggers.md#gridboss-fast-lane).

This is stock-controller-firmware monitoring. No controller firmware,
protection setting or operating setting was written; all requests are FC03 or
FC04 reads.

**Not verified by this deployment:** the fast lane with a generator present,
the held-open connection against the real GridBOSS dongle from inside Home
Assistant, and the switch from the idle cadence to the full rate when a
generator starts. The generator was off throughout.

All times are UTC on 2026-11-11.

## Installed identity

- Host version **0.2.6**; base commit
  `4860419c1a22736504df4fdc4cf75f9bfd81c8c5`.
- Source: an uncommitted working-tree snapshot. The working tree differed from
  the installed 0.2.5 package in seven files only (`__init__.py`,
  `config_flow.py`, `const.py`, `local_dongle.py`, `local_recorder.py`,
  `register_catalog.py`, `translations/en.json`), plus the version field in
  `manifest.json`.
- Package `eg4-monitor-0.2.6.tar.gz`, SHA256
  `67b73bbbfc9fb0c39ce8dd3bce50a48dfed3e30c3e6eb372f75a3b31338995dc`. All 19
  assets matched on the host both in staging and after installation.
- Native recorder build
  `b40aa952e4bdefd474f202b2e60bc6f18893e520ef7a9895991920434929e8cf`, which is
  the value computed from the source before deployment.
- New native session `b5b44d3e-c9f5-4581-8e4f-a808013c319e`.
- Catalog revision unchanged: `candidate-gridboss195-flexboss252-2026-11-10.v1`.
- Home Assistant Core remains 2026.10.4. The EG4 entry's stored record is
  byte-identical before and after (SHA256 `bbdcf3c4…4be688`); the two new
  options are absent from it, so the defaults apply (`generator`, 30 s).
- The standalone analyzer release on Plucky was **not** updated; it is still
  `inventory-0.2.5-20261110T204916Z` and lacks the idle-round gap rule.

## Cutover

Stopped-Core whole-component replacement, run detached on the host so a lost
connection could not leave Core stopped.

| Operation | Began | Returned |
| --- | --- | --- |
| Core stop | 16:11:35 | 16:14:01 |
| Component swap | 16:14:01 | 16:14:01 |
| Installed-package configuration check (passed) | 16:14:01 | 16:14:54 |
| Core start | 16:14:54 | 16:17:00 |

Pre-flight at 16:10:39: utility 244.7 V, backup 243.6 V, generator 2.0 V.

Recorder gap, last successful old-session read to first successful new-session
read: **301 s** on each device (16:11:35 to 16:16:36).

## Verification window

Acquisitions starting 16:16:34 through 16:25:34, read from the Plucky archive.

| Role | Rounds | Idle rounds | Idle spacing, median / max | Failed / attempted reads | Distinct shapes read |
| --- | ---: | ---: | ---: | ---: | ---: |
| GridBOSS | 19 | 18 | 30.0 / 30.05 s | 0 / 90 | 11 |
| FlexBOSS master | 19 | 18 | 30.0 / 30.03 s | 0 / 97 | 12 |
| FlexBOSS slave | 19 | 18 | 30.0 / 30.03 s | 0 / 97 | 12 |

- Every round reported `ok`, none `partial`. All 35 profile shapes were read
  at least once.
- The first round of each role ran at the full rate (generator state not yet
  known); every later round was idle.
- Idle rounds read the short-period block each time (GridBOSS holding 4500,
  inverter input 80) and the longer-period blocks as they fell due.
- Health at 16:25:53: overall `healthy`; cadence `idle`, generator not
  present, freshness limit 75 s for each role; raw, journal and incident
  persistence healthy with zero write errors and zero dropped records;
  `FastLane` mode `generator`, inactive, no samples, no failures.
- All 125 EG4 entities are present. Fourteen were `unknown` before and
  fourteen after.

## Other integrations after the restart

The Core restart was a restart of all of Home Assistant. Afterwards:

- **UniFi** is in `setup_error`: a `KeyError` on the site lookup during setup
  at 16:16:17. It was loaded before the restart. A reload at 16:55:59, at
  Greg's request, was accepted and failed the same way:
  a `KeyError` on the stored site ID at `hub.py` line 97. The controller
  at 192.168.0.1 answers ping, but the site list it returns to Home Assistant
  does not contain the site ID stored in the entry. The lookup only runs at
  setup, so the restart exposed this rather than caused it; the cause is on
  the UniFi side and is not diagnosed here.
  The three unavailable entities whose names contain "eg4"
  (`device_tracker.eg4_2`, `sensor.eg4_rx_2`, `sensor.eg4_tx_2`) are UniFi's
  view of the GridBOSS dongle, not recorder entities.
- **TP-Link** (8 entries) and **OTBR** (2 entries) are in `setup_retry`.
- The **Hubitat** hub at 192.168.0.75 does not answer from Home Assistant or
  from Plucky ("no route to host").
- 1,115 of 2,422 entities are `unavailable`.

No whole-system baseline was captured before the restart, so apart from UniFi
it is not known which of these were already in that state.

## Host storage

`/homeassistant` had 1,862,400 KiB free (93 % used) after the deployment, down
from about 5.77 GiB at 21:42 the previous day. The idle cadence reduces the
recorder's own write rate, but the trend should be watched.

## Rollback

Previous whole component:
`/homeassistant/.eg4-investigation/deployments/fastlane-0.2.6-20261111T160934Z/previous-eg4_monitor/`.
With Core stopped, move the current component aside, move the previous one
back, run `ha core check`, then start Core. To turn the new behaviour off
without rolling back, set the idle interval to 0 and the fast-poll mode to
`off` in the integration's options.

## Checks and evidence

Local suite before packaging: **384 passed, 3 skipped**. Lint clean.

Private evidence root on Plucky:

```text
/home/greg/.local/state/eg4-host-recorder/fastlane-0.2.6-20261111T160934Z/
```

It holds the package and its hash manifest, host staging and installed
hashes, the cutover transcript, before and after EG4 entity states, the
recorder-gap measurement, `verification.json` and `SHA256SUMS`.
