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

## Controller diagnostics: deployed

Read-only controller observations are deployed alongside the existing raw trace.
Initial deployment was verified on2026-11-08. Historical November9 correction
evidence is retained outside Git at
`investigations/2026-11-08-firmware-reverse-engineering/capture-upgrade/README.md`;
it is not evidence that the new host-recorder initiative has been deployed.
The debounced AC-evidence logger remains separate; controller decision words are
never debounced or labeled as physical contact transitions.

Supplemental requests run in the existing role's polling lock, after successful
primary telemetry has been cached, on trace-recording cycles only:

| Role | Function | Start | Count | Evidence |
|---|---|---:|---:|---|
| FlexBOSS | 04 input | 40 | 40 | Faults62/63, true warnings60/61, derived77 |
| FlexBOSS | 03 holding | 110 | 125 | Settings and controller readbacks through234 |
| FlexBOSS | 04 input | 120 | 26 | Commands142/143 as raw context; limiter/state144/145 also trigger discrete observations |
| GridBOSS | 04 input | 500 | 100 | Defined diagnostics including518/519 and command counters |
| GridBOSS | 03 holding, local dongle wrapper | 4500 | 10 | Initialized GPIO/test-state output readback, not contact feedback |

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

These are additional serialized network requests, not free background reads.
Each diagnostic stage is limited to `min(client.timeout, 2.0)` seconds; multiple
stages/blocks and roles can accumulate additional delay. Individual block
`started_at`, `completed_at`, `duration_ms`, success/error and values are retained,
along with whole-sample timing and any recorded sample gap. Actual cadence must
be measured from those timestamps after deployment; **one-second sampling is not
guaranteed**. A failed supplemental request leaves successful primary telemetry
usable and records missing diagnostic values rather than substituting zeros.

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

An independent, size-bounded JSONL writer under
`<existing TracePath>/controller-observations/` records a baseline per role,
discrete controller changes, availability loss and resumption. Repeated identical
observations and voltage/current/timestamp noise do not create history entries.
Both bytes of each decision word are compared: input519 changing from`0x0504`
to`0x0604` is retained even though its low state byte remains4. Command-path
counters, packed flags, relevant settings, command readbacks and observed transfer
state/quality are compared too; unresolved continuously changing input593 and
GPIO594 are retained in emitted raw blocks but do not trigger observations.
Restart begins a new baseline, not a synthetic physical transition.

The regular profile does not poll GridBOSS holding18/24/37/38/170/226/2102.
These were read separately as a timestamped configuration baseline; corresponding
unpolled fields in controller observations remain null, not zero or current values.

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

Run `python scripts/analyze_local_dongle_trace.py <trace-file>` (optionally
`--role <role>`) to stream historical or v2 evidence. Rejected/partial records are
reported with acquisition intervals; the analyzer does not turn them into valid
telemetry or engine/contact claims.

## Operational retention

The raw trace and independent `controller-observations/` history each use the
existing JSONL rotation convention: 25 MiB per file, at most eight files including
the active `local-dongle.jsonl`. Limits are **bytes/files, not days**; controller
change frequency and record size determine the available time span, and neither
trace promises complete retention of a particular incident. Awaiting a writer's
close drains accepted queued records and flushes them before returning; this does
not make disk failures, a process crash, or queue-full rejection durable.

The logger state store separately retains the active session, last session,
recent events, and up to90 days/100 completed sessions. Long-term trends still
depend on the target HA Recorder retention policy. Configure Recorder in the
HA-owned configuration if90-day entity history is required.

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
