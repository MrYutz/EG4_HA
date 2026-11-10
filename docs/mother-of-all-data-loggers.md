# EG4 Mother of All Data Loggers

## Implementation status

The integration now has a stateful logger for the local telemetry path:

- Direct GridBOSS terminal evidence is sampled by the existing local loop. The
  configured1-second target is not an achieved-rate guarantee; see measured capture below.
- Generator state is inferred from GridBOSS generator voltage/frequency and power.
  It is explicitly **AC evidence**, not proof of the mechanical engine state or
  transfer-relay position.
- Normal generator transitions use hysteresis and two distinct successful source
  acquisitions. Reprocessing a cached value under a new loop sequence cannot
  confirm a transition or make its acquisition timestamp fresh.
- A complete charging total requires fresh charge-power evidence from **every
  configured participating inverter**. Missing master plus zero-watt slave is
  partial/unknown, not a complete zero. Per-role identity, age and power remain
  available as context. Portal-only sites use explicitly identified portal
  observations at their normal polling cadence.
- Charging sessions use hysteresis, two-sample start/stop debounce plus a
  ten-second minimum stop dwell (the portal-only fallback naturally resolves
  on its next coordinator sample), an estimated energy accumulator, and a
  90-day/100-session bounded ledger.
- Unknown or stale samples do not end an active charge session. The session remains
  open and is marked `interrupted`.
  Session activity means an open session, even when instantaneous charging state
  is unknown. A newly failed primary read interrupts continuity immediately;
  a young last-good cache cannot conceal that failure or confirm recovery.
- Unknown endpoints and restart gaps break energy-integration continuity and
  reset pending debounce/stop dwell. A resumed known sample establishes a new
  endpoint; it cannot integrate the preceding unobserved interval.
- State is restored from Home Assistant `Store` across restarts. Restore itself
  does not fire a synthetic start event; the first fresh sample reconciles state.
- Accepted transitions fire HA bus events and are exposed through high-rate logger
  sensors. Raw register evidence remains in the bounded JSONL trace.

The repository intentionally does not own the live Lovelace dashboard because the
selected deployment model is HA UI/storage mode. A storage dashboard has been
created on the Trigo HA instance at `/eg4-data-logger` with title **EG4 Mother of
All Data**. The dashboard template below is a portable raw-editor backup; maintain
the live dashboard in HA.

## Independent local capture

The host-recorder release uses one local capture owner with an independent task
for each device. Portal consumers read its snapshots instead of starting another
collection round. A failed device or slow maintenance block cannot impose an
all-role barrier, including when raw persistence is disabled. Installation is
verified separately; these targets are not a claim of measured production rate.

Every role reads dynamic evidence first, then at most one oldest-due maintenance
block. Startup baselines are staggered between dynamic rounds. Read indices are
zero-based, device-specific and read-only:

| Role | Read profile | Target |
| --- | --- | --- |
| Each FlexBOSS | Input0/40,40/40,120/26 | Dynamic rounds, requested1s spacing |
| GridBOSS | Input0/40,120/12,500/100 | Dynamic rounds, requested1s spacing |
| Each FlexBOSS | Input80/40 BMS | Startup and5s |
| Each FlexBOSS | Holding110/125 | Startup and60s; reused by baseline |
| GridBOSS | Local holding4500/10 | Startup and5s |
| Each FlexBOSS | Baseline holding0/20,20/90,235/102 plus latest110/125 | Startup and5min, staggered |
| GridBOSS | Baseline holding0/20,20/90,110/125,235/88 and local2000/108 | Startup and5min, staggered |

Cached blocks retain their original acquisition intervals and IDs. A failed
baseline block is missing in current context; last-successful values remain
separate and explicitly aged. A common round is not an atomic snapshot.
Do not use exact FlexBOSS starts224/248, GridBOSS4516, undefined input132–499 or
engineering1100. Safe ranges and caveats are in the
[governing read profile](host-recorder-mvp-spec.md#fixed-read-profiles).

The existing local-dongle diagnostic exposes compact `RecorderHealth` with
session/build, per-role attempts/success/freshness, measured median/max spacing
and missing/failed context. Its local updates continue during portal failures.
The existing count retains its inverter-only meaning; health lists GridBOSS
separately. No dashboard, entity ID or protection setting changes.

Role notifications coalesce logger-state checkpoints without postponing an
already-pending one-second save request. Transfer-change evidence retains the
previous/current inferred states and explicitly marks intervening unknown
coverage; the initial observation establishes a baseline, not a physical edge.

### Historical controller capture

Initial deployment was verified on2026-11-08. Historical November9 correction
evidence remains outside Git under
`investigations/2026-11-08-firmware-reverse-engineering/capture-upgrade/README.md`;
it is not proof that the new release is installed. Controller observations are
not debounced and are never physical contact feedback.

The existing GridBOSS frequency/context request is narrowed to input120/count12:
only120–131 are defined by the recovered firmware. Old40-word traces remain
evidence, but words132–159 have no established firmware-defined meaning. No
history-upload/erase action or holding111 history-selector polling is added.

**November9 deployed correction:** local GPIO starts at4500, not4516. Historical
4516/10 reads copied outside the initialized output and cannot establish commands.
The current profile uses4500/10. Real Flex warnings60/61 now trigger controller
observations;64/65 remains raw analog context, not a discrete trigger. Both roles
also collect ordinary input120..145. Continuous Q1 or another polling client
was not added. Historical native proof and live verification remain in the local
research copy under `direct-session/README.md` and `capture-upgrade/README.md`;
these reports and their raw evidence are not bundled with the integration.

The earlier collector serialized these additional requests inside an all-role
round, with supplemental stages capped at `min(client.timeout, 2.0)` seconds.
The resulting timings below describe that collector, not the independent owner.
Current records retain each block's `started_at`, `completed_at`, `duration_ms`,
success/error and values plus whole-round timing/gaps. Measure actual cadence:
**one-second sampling is not guaranteed**. Successful primary or partial raw
evidence survives later supplemental failures; missing values are not zeros.

The initial November8 post-deployment98-sample-per-role window measured median gaps about4.92s,
maximum7.887s. All294 primary samples succeeded; eight supplemental blocks failed
without synthetic zero values or discarded primary samples. Those figures describe
that observed window, not guaranteed future cadence or physical power quality.
Cold-start discovery returned no inverter identities, so the existing master/slave
host options now use the verified routes`192.168.3.14`/`192.168.3.162`.

The later November9 fixed16-sample-per-role window had48/48 successful primary
reads, all expanded inverter blocks successful, and one corrected GPIO timeout.
Median starts were about4.234s with a14.999s maximum gap. Additional explicit
availability events were preserved separately. This is not a guaranteed1Hz
recorder, continuous-health claim or proof of a cycling repair. The restart caused
about63s of observed sampling gap, not a measured power outage.

### Controller journal and persistence health

Next to the raw trace, `controller-observations/local-dongle.jsonl` records
per-role baselines, discrete changes and availability loss/resumption. Input6
bits0/1 participate in the FlexBOSS discrete signature; the complete word remains
raw context. Its candidate firmware meaning is explicitly unverified. Structurally
valid raw primary blocks remain journal evidence even when semantic rejection
prevents publication as valid telemetry. Higher input6 bits and analog142/143
noise are not mandatory per-fluctuation triggers.

Both bytes of decision words are retained: input519 changing from`0x0504` to
`0x0604` is captured even though the low state byte stays4. Warnings60/61,
faults62/63, limiter/state144/145, command counters, relevant settings, readbacks
and observed transfer quality remain discrete context. Undefined593/594 data is
retained raw but does not trigger observations.

Observed, queued/pending and successfully persisted comparison states are
separate. Queue rejection or disk failure breaks journal continuity. After writes
resume, an unchanged controller observation can produce a `recovery_baseline`;
an older successful receipt cannot clear a newer loss. Restart starts a baseline,
not a synthetic physical transition.

Each writer defaults to1024 outstanding records and an8MiB serialized-byte budget,
including in-flight work. Disk latency does not put role polling behind a shared
write barrier. Ordinary appends flush; synchronization and graceful close fsync
required output off the HA event loop. Queue acceptance is never disk success or
a guarantee against arbitrary host power loss.

The existing diagnostic's `RecorderHealth` includes `RawPersistence` and
`JournalPersistence`: current state, queue depth/bytes, cumulative drops/write
errors, last successful persistence and historical last error. Recovered state
does not erase earlier loss. Disabled persistence is explicit, not healthy storage.
Compact `recorder_health` rows (`role=recorder`) live in raw JSONL at startup,
shutdown,30-second intervals and significant failure/recovery. Cached attributes
refresh at most every30s except those transitions; warnings are rate-limited.

Historical regular profiles did not poll GridBOSS holding18/24/37/38/170/226/2102;
those fields were null unless separately baselined. The new approved settings
banks retain their actual acquisition references and age, not invented historical
values or effective change times.

Every emitted observation keeps full raw diagnostic blocks, primary telemetry
context and their individual acquisition timestamps, without the unrelated HA
state dump. These sequential reads are **not an atomic controller snapshot**.
Input518/519 hold the last-written state/reason bytes, not an event queue:
state0 is not a declaration of closed contacts, ordinary close commands can leave
the previous diagnostic untouched, and a stored reason need not remain active.
Output-data readback and close-command-path counters do not prove physical
contact position or actual completed transfers. Availability loss/resumption is
data quality, not an inferred opening/closing event.

This capture cannot establish complete causality. The recovered firmware leaves
important inputs unexposed, including sticky acceptance bits at`b5a0`, computed
inhibit`b7ae`, edge latch`b7b2`, qualification latches/counters, heartbeat ages and
raw ADC/ring data. Packed words may combine delayed local and other-bank flags.
Several decisions can occur between polls; no first-cause or complete-transition
guarantee follows. The recovered firmware maps are not proof of the installed
image's identity.

## New HA event contract

Generator state and charge-session events use their documented state-machine
acceptance rules. Raw controller/transfer observations are separate and immediate:

- `eg4_monitor_generator_state_changed`
- `eg4_monitor_generator_transfer_observation_changed`
- `eg4_monitor_charging_session_started`
- `eg4_monitor_charging_session_ended`

Event data includes `event_id`, `entry_id`, `source_serial`, `source`,
`provenance`, `confidence`, `observed_at`, `received_at`, `sequence`, `from`,
`to`, `reason`, and the state/session evidence relevant to that event. The
transfer event remains an observation; it must not be treated as relay-contact
truth.
Missing observations retain the last-known transfer state. A changed resumed
observation carries `intervening_unknown=true`; it does not locate a physical
switching event inside the gap.

For239 V terminal voltage, qualified0 Hz and0 W accepted power, the generator
evidence is `PresenceState=present`, `SupplyState=not_supplying`,
`RunState=unknown`, `Quality=interrupted`, `Confidence=unknown`. The reason names
unavailable/invalid qualified frequency—not absent voltage or a stopped engine.

Example automation trigger:

```yaml
triggers:
  - trigger: event
    event_type: eg4_monitor_generator_state_changed
conditions:
  - condition: template
    value_template: >-
      {{ trigger.event.data.to == 'running_supplying' }}
actions:
  - action: notify.notify
    data:
      title: EG4 generator supplying power
      message: >-
        GridBOSS measured {{ trigger.event.data.generator_power_w | default('unknown') }} W.
```

The exact payload may gain fields while preserving these top-level names. Prefer
`event_id` for deduplication in automations.

## Logger entities

The logger entities have stable implementation keys. Home Assistant's current
site device naming produces these live IDs with the `eg4_site_` prefix (for
example, `sensor.eg4_site_generator_run_state`); confirm the entity registry if
the site/device name changes. The logger entities are:

### Generator

- `sensor.eg4_site_generator_run_state`
- `sensor.eg4_site_generator_presence_state`
- `sensor.eg4_site_generator_supply_state`
- `sensor.eg4_site_generator_state_quality`
- `sensor.eg4_site_generator_state_confidence`
- `sensor.eg4_site_generator_run_duration`
- `sensor.eg4_site_generator_last_transition`
- `sensor.eg4_site_generator_last_on`
- `sensor.eg4_site_generator_last_off`
- `sensor.eg4_site_generator_last_transition_reason`
- `binary_sensor.eg4_site_generator_gridboss_ac_present`
- `binary_sensor.eg4_site_generator_gridboss_supplying`
- `binary_sensor.eg4_site_generator_gridboss_state_unknown`

### Charging sessions

- `sensor.eg4_site_charge_session_state`
- `sensor.eg4_site_charge_session_quality`
- `sensor.eg4_site_charge_session_source`
- `sensor.eg4_site_charge_session_duration`
- `sensor.eg4_site_charge_session_start`
- `sensor.eg4_site_last_charge_session_start`
- `sensor.eg4_site_last_charge_session_end`
- `sensor.eg4_site_last_charge_session_duration`
- `sensor.eg4_site_charge_session_count`
- `sensor.eg4_site_charge_session_energy_estimated` (per-session estimate; not cumulative)
- `binary_sensor.eg4_site_battery_charge_session_active`
- `binary_sensor.eg4_site_battery_charge_session_interrupted`

Each logger sensor exposes its relevant `Generator` or `Charging` evidence as
state attributes, including thresholds, source, quality, sample age, active/last
session, and retained-session count. The full bounded completed-session ledger is
kept in the versioned Home Assistant Store record rather than copied into every
recorder state attribute.

## Existing evidence to retain

Keep the existing raw and normalized entities on the dashboard:

- `sensor.eg4_site_gridboss_generator_voltage`, `sensor.eg4_site_gridboss_generator_frequency`,
  `sensor.eg4_site_gridboss_generator_power`, `sensor.eg4_site_gridboss_generator_current`
- `sensor.eg4_site_gridboss_grid_voltage`, `sensor.eg4_site_gridboss_grid_frequency`,
  `sensor.eg4_site_gridboss_ups_voltage`, `sensor.eg4_site_gridboss_ups_power`,
  `sensor.eg4_site_gridboss_transfer_observation`
- `sensor.eg4_site_flexboss_local_ac_input_voltage`,
  `sensor.eg4_site_flexboss_local_ac_input_frequency`,
  `sensor.eg4_site_flexboss_local_eps_voltage`,
  `sensor.eg4_site_flexboss_local_eps_frequency`,
  `sensor.eg4_site_flexboss_local_eps_power`
- `sensor.eg4_site_battery_charging_power_local_dongle`,
  `sensor.eg4_site_battery_discharging_power_local_dongle`,
  `sensor.eg4_site_battery_soc_local_bms`,
  `sensor.eg4_site_battery_local_bms_current`,
  `sensor.eg4_site_battery_local_maximum_cell_voltage`,
  `sensor.eg4_site_battery_local_minimum_cell_voltage`,
  `sensor.eg4_site_battery_maximum_cell_temperature_local_bms`,
  `sensor.eg4_site_battery_minimum_cell_temperature_local_bms`

The old `binary_sensor.generator_running` and `sensor.generator_status_source`
remain portal/midbox-derived values. The new `generator_*_gridboss` entities are
the explicit GridBOSS AC-evidence path. Local BMS, fuel, and FlexBOSS cards are
optional: they show `unavailable` when those sources are not configured or fresh.
The canonical SOC gauge likewise becomes unavailable rather than silently falling
back to an unverified SOC source.

## Versioned raw acquisition evidence

The host-recorder release writes `eg4_local_dongle_trace.v2` records. Deployment
is a separate, explicitly verified step; this format description is not evidence
that the release is already active on HA. Historical v1 traces stay unchanged.

- `transaction` records preserve request bytes, received frames/partials,
  structurally obtainable words, separate validation outcomes and bounded
  errors before semantic decoding or the next request. Unknown status33280 or
  invalid voltage/frequency evidence remains inspectable but invalid for sensors.
- `interpretation` records reference the raw transaction and its original
  acquisition interval, labeling semantic acceptance/rejection separately.
- `acquisition` summaries reference contributing transaction IDs and explicitly
  mark partial results. A later GridBOSS frequency failure cannot erase the
  earlier power/voltage transaction. Scheduled diagnostics still run after a
  primary semantic rejection, within their existing bounded requests.
  This includes a due BMS refresh: core rejection does not suppress Input80/40.
  `bms_source` retains the cached block's original acquisition/transaction IDs
  and time, so reuse is not confused with the failed or current transaction.
- Session/acquisition/transaction IDs identify observations; UTC start/end and
  monotonic duration describe capture, not an atomic snapshot. `queued_at` and
  `persistence_started_at` separately describe host processing/filesystem work;
  enqueue is not a durability acknowledgment. Monotonic time is session-local.

Unsolicited/history frames are retained while awaiting identity or a strict
matching response. The whole transaction has a deadline, existing4096-byte frame
limit and256 KiB received-traffic cap, rather than an eight-frame matching limit.
No history activation/erase or second device client is used. New raw summaries
omit unrelated HA/light state dumps, credentials and tokens.

Run `python scripts/analyze_local_dongle_trace.py <file-or-directory>` (optionally
`--role <role>` and `--gap-seconds <seconds>`) to stream historical or v2 evidence.
A recorder root reports rolling capture, controller journal and each incident
separately; copied incident rows are not counted as additional rolling acquisitions.
An incident directory verifies manifest references, retained file sizes and final
SHA256 hashes. Growing, missing, evicted or modified files remain explicitly incomplete.

The analyzer reports source coverage, failures/partials, session changes, acquisition
gaps, raw controller/protection changes and configuration age. Output anomalies are
examined throughout the input, not only during low generator power. A structurally
received UPS/EPS word can still be reported when a later block or semantic decode
fails: it is labeled `structural_raw_register`, never promoted into valid telemetry.
Cached blocks from another acquisition cannot manufacture a new voltage observation.
Onset/recovery brackets retain original read intervals and unresolved overlapping
ordering. No zero-fill, interpolation, engine/contact claim or continuous house-outage
duration is inferred.

## Operational retention

The release owns only
`/config/.eg4-investigation/host-recorder-v2/<entry-id>/`, identified by `recorder.json`.
Its `raw/` and `controller-observations/` directories contain rolling JSONL;
`incidents/incident-<id>/` contains plain JSONL and an atomic `manifest.json`.
Historical `local-dongle/` captures, sealed investigations, deployment backups and
unrelated HA files remain outside this retention owner.

| Stream | Default age ceiling | Default byte ceiling | Advanced config-entry option |
| --- | --- | --- | --- |
| Raw |72h|2 GiB|`local_trace_raw_max_bytes`|
| Controller journal |14 days|200 MiB|`local_trace_journal_max_bytes`|
| Incidents, including open segments |14 days|1 GiB|`local_trace_incident_max_bytes`|

Segments target25 MiB. Positive integer byte overrides affect host storage only
and survive ordinary integration-options saves; they have no additional UI controls.
Limits are ceilings, not preallocation or promised days. The recorder rejects writes
that would cross its1 GiB free-space reserve; unrelated HA writes can still change
free space. Ordinary raw expires before incident evidence under pressure. Forced
incident eviction leaves incomplete/lost-coverage metadata rather than a plausible
complete bundle. Existing diagnostic `RecorderHealth.Incidents` reports actual raw
coverage/bytes, free space, estimated retention, active/latest paths and truncation.

Immediate source edges preserve the preceding ten minutes, bounded to16 MiB, plus
ten quiet minutes after the latest trigger/recovery. Startup warmup and memory
truncation are explicit. Overlapping reasons share one incident; identical failures
do not create an incident each poll, and an active anomaly keeps the window open.
Triggers include accepted-power loss with voltage present regardless of qualified
frequency, inconsistent V/F, output anomalies, discrete controller/protection
changes, primary/diagnostic loss or resumption, semantic rejection and recorder loss.
The240 V capture limits are below180 V/above264 V for GridBOSS total UPS voltage
and this site's FlexBOSS EPS-r report. Low output requires a previously live channel;
overvoltage does not. Never apply these limits to120 V legs or equipment settings.

The manifest retains session/build identity, source coverage, all reason/time events,
missing roles, errors/gaps and aged all-role configuration context. Only synchronized,
immutable files receive final hashes. An unfinished window becomes `interrupted`
on close/restart; the next session has a gap and a new warming buffer, not invented
continuity. Copy a closed/interrupted incident directory off-host and pass that
directory to the same analyzer. Active rolling files are moving evidence, not atomic
snapshots. Graceful close drains accepted output and synchronizes it off the HA loop;
it cannot make queue rejection, disk failure or abrupt power loss durable.
Core shutdown, entry unload and failed setup share the capture-close path. HA does
not call integration unload when Core stops; an explicit stop listener joins the
role tasks, settles incident custody, closes both rolling writers and saves logger
state before returning. Stopping collection is not evidence of an electrical outage.

The logger state store separately retains the active session, last session,
recent events, and up to90 days/100 completed sessions. Long-term trends still
depend on the target HA Recorder retention policy. Configure Recorder in the
HA-owned configuration if90-day entity history is required.

## Plucky off-host archive

The Plucky-side puller copies existing recorder files; it does not poll devices,
change sampling, mount storage into HA or require a Core restart. HA keeps its local
outage buffer and continues collecting when Plucky is unavailable.

| Plucky evidence | Retention |
| --- | --- |
| Raw and controller segments | Eight days after the last newly received bytes |
| Open/incomplete incident copies | No automatic expiry |
| Sealed incident snapshots | No automatic expiry |

Age cleanup applies only to owned raw/controller segments, never unrelated files
or incident snapshots. There is no byte-ceiling eviction of recent evidence.
Below the default10GiB free-space guard, new copying fails visibly instead.
Indefinite incident retention requires monitoring Plucky disk usage.

The source is the canonical SSH-addon path
`/homeassistant/.eg4-investigation/host-recorder-v2/<entry-id>/`.
The destination is
`/home/greg/.local/state/eg4-host-recorder/archive/<entry-id>/`, with native
`raw/`, `controller-observations/`, `incidents/` and `recorder.json` plus the
archiver's `archive.json` index and `status.json`. Directories are private.
Raw bytes are opaque and unchanged; active files are received prefixes, not
atomic site snapshots. Resumption trusts the recorder's append/rotate ownership
and checks first-record identity and a bounded overlap, not arbitrary historical
middle-file rewrites.

`incidents/incident-*/archive-receipt.json` distinguishes moving from sealed copies.
Sealing brackets received bytes with a stable final/interrupted source manifest,
checks sizes and available final hashes, and records local hashes. Sealed copies
are not overwritten by subsequent source pruning or changes. A sealed copy can
still have `received_complete: false`: custody does not repair missing acquisition,
startup warmup or interrupted coverage. Source-pruned open copies remain incomplete
and report missing source evidence.

### Plucky runtime and operation

Install a committed release of the two archive scripts, the existing analyzer,
`scripts/requirements-eg4-archive.txt` and the systemd templates into
`~/.local/libexec/eg4-recorder-archive/releases/<commit>/`. Create its isolated
`venv` and install that requirements file there; no HA dependency changes.
Point `~/.local/libexec/eg4-recorder-archive/current` at that release.
Install the service/timer templates from `scripts/systemd/` under
`~/.config/systemd/user/`. The per-user manager must remain running after logout
(`Linger=yes` on Plucky).

Create mode0600 `~/.config/eg4-recorder-archive/environment` in a mode0700 directory:

```sh
EG4_ARCHIVE_SOURCE=/homeassistant/.eg4-investigation/host-recorder-v2/<entry-id>
EG4_ARCHIVE_DESTINATION=/home/greg/.local/state/eg4-host-recorder/archive/<entry-id>
EG4_ARCHIVE_ENTRY_ID=<entry-id>
EG4_ARCHIVE_HOST=<ha-address>
EG4_ARCHIVE_USER=<ssh-user>
EG4_ARCHIVE_IDENTITY=/home/greg/.ssh/<existing-key>
EG4_ARCHIVE_KNOWN_HOSTS=/home/greg/.ssh/known_hosts
```

Host keys must already be verified in that file; unknown or changed keys fail
closed. Authentication is explicit-key-only, without agent/password fallback.
The existing production root key has broader authority than this client's
read-only SFTP operations; it is **not** an OS-enforced read-only account.

```sh
systemctl --user daemon-reload
systemctl --user start eg4-recorder-archive.service
systemctl --user enable --now eg4-recorder-archive.timer
systemctl --user status eg4-recorder-archive.timer eg4-recorder-archive.service
journalctl --user -u eg4-recorder-archive.service -n 20
cat /home/greg/.local/state/eg4-host-recorder/archive/<entry-id>/status.json
```

The timer runs60s after the previous activation finishes, never concurrently.
The service has a15min whole-run bound; failed/interrupted transfers resume from
the actual retained prefix. `status.json` and the journal expose degraded runs and
the last successful time. To stop replication without deleting evidence:
`systemctl --user disable --now eg4-recorder-archive.timer`.

The standalone `scripts/pull_eg4_archive.py --help` also exposes local backfill and
SFTP options. Preserve older saved copies before pulling a newer moving source.
Analyze an incident directory or selected segment with the unchanged
`scripts/analyze_local_dongle_trace.py`; a whole multi-day archive produces a large
detailed report. Keep reports/raw logs outside Git.

Eight days fills prospectively; already-pruned history cannot be recovered.
Plucky downtime can only catch up within HA's remaining local spool (about8.7h
at the measured production rate/ceiling, not a guarantee). Check both archive
`last_success_at` and HA `RecorderHealth` loss/coverage; successful replication
does not prove successful acquisition or explain the historical standby fault.

## Validation checklist

1. After a successful `ha core check`, restart HA Core once to load changed Python.
2. Confirm `sensor.eg4_site_generator_run_state` and
   `binary_sensor.eg4_site_generator_gridboss_state_unknown` in Developer Tools -> States.
3. Validate state events with offline controlled inputs or passive observations.
   Do not start/stop, load/unload or fault equipment to test this recorder.
4. Confirm a charging session exposes start, end, duration, quality, and estimated
   session energy.
5. Replay saved active-session state offline and verify restoration is
   `interrupted` without a synthetic start; do not add a production restart.
6. Confirm the `TracePath` attribute on `sensor.eg4_site_local_eg4_dongles_available` and
   inspect the JSONL record sequence when diagnosing a transfer.
