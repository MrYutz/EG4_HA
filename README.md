# Home Assistant EG4 Monitor Integration

Custom Home Assistant integration for EG4 portal-backed systems, tuned for modern multi-device installs like dual FlexBOSS + GridBoss.

## What Changed

### Unreleased — recorder review fixes

- Shared raw measurement projection keeps capture evidence separate from decoded publication guards; repeated invalid readings no longer manufacture recovery events.
- Bounded reclamation-only metadata commits allow owned evidence to be pruned below the free-space reserve without permitting ordinary payload writes there.
- The standalone analyzer recovers controller/protection and holding-word changes from matching raw transactions, deduplicates their summaries and retains unresolved timing across host-clock regressions.

These fixes are not deployed. The receipt below documents the existing0.2.2 installation.

### Plucky recorder archive

- File-only SFTP pulls preserve eight days of raw/controller logs on Plucky, without changing device sampling or making HA depend on network storage.
- Incident snapshots have no automatic expiry; moving or incomplete source evidence stays explicitly incomplete.
- The minute timer, private storage, failure status and recovery limits are documented in the [off-host archive guide](docs/mother-of-all-data-loggers.md#plucky-off-host-archive).
- Installed source, actual scheduled transfers and the approved HA disk-pressure correction: [Plucky deployment evidence](docs/plucky-recorder-archive-deployment-2026-11-10.md).

### Host recorder 0.2.2

- Independent read-only schedules keep healthy FlexBOSS/GridBOSS roles recording when another request stalls.
- Versioned raw transactions preserve rejected/partial bytes before decoding; cached data retains its original identity and age.
- Generator AC presence, qualification and accepted power remain separate. Qualified 0 Hz is not evidence of engine cycling.
- Input6 protection candidates and existing controller decisions are journaled with persistence-loss recovery, without invented physical meanings.
- Bounded pre/post incidents, owned-only retention, explicit loss/coverage and final hashes make saved evidence exportable.
- Partial/crash reconstruction includes signed−1 W power-loss reasons and raw-only UPS/EPS anomalies, deduplicated by original source identity.
- Storage guards avoid repeated directory walks for every record; current byte ceilings, free-space reserve and inode/path safety remain enforced.
- Existing `RecorderHealth` diagnostics and the standalone streaming analyzer expose capture failures, source gaps, output anomalies and bundle completeness.

Production installation and measured limits: [2026-11-10 deployment receipt](docs/host-recorder-deployment-2026-11-10.md).

Host-only release: no power firmware, equipment operating/protection settings, new
device collector or dashboard. See the [operator and evidence guide](docs/mother-of-all-data-loggers.md).

### Existing site-aware telemetry

This project was expanded from simple single-device polling to a site-aware model:

- Multi-device cloud polling with role detection (`flexboss_master`, `flexboss_slave`, `gridboss`)
- GridBoss-specific ingestion paths for real site telemetry
  - `getParallelGroupDetails`
  - `getMidboxRuntime`
  - `dayMultiLineParallel`
  - event list (`api/analyze/event/list`)
- Portal-aligned energy naming and mapping
  - `Today's Usage`, `Total Usage`, `Today Export`, `Total Export`
  - `UPS Usage Today`, `UPS Usage Total`
- Generator status improvements
  - running/supplying power binary sensors
  - status source sensor
- Battery telemetry improvements
  - charging power, discharging power, direction (`Idle/Charging/Discharging`)
  - battery energy remaining (kWh)
  - verified battery status, current, voltage, cycle count, temperatures, BMS limits, and pack rows
- Verified portal endpoint ingestion
  - inverter energy counters from `getInverterEnergyInfo` (tenth-kWh normalization)
  - battery details from `getBatteryInfo` and `getInverterBatteryInfoParallel`
- Stability safeguards
  - bounded per-device last-good cache with stale-data diagnostics
  - persisted 24-hour grid-quality/outage metrics
  - configurable runtime and battery-detail polling intervals
- Automatic local telemetry discovery
  - portal-reported local IPs are probed first after login
  - Home Assistant local IPv4 segments are scanned as a bounded fallback
  - manual FlexBOSS host fields remain optional role-specific overrides
- Direct GridBOSS transfer diagnosis
  - read-only GridBOSS input-register polling for grid/generator/UPS terminals
  - source-interval correlation with FlexBOSS local AC input/EPS telemetry, not an atomic site snapshot
  - bounded JSONL transition trace retaining raw register evidence
- Uncleared error monitoring
  - active problem binary sensor
  - count + summary + top open event details
- Read-only controller capture correction (2026-11-09)
  - corrected GridBOSS local GPIO4500/10 and FlexBOSS warning60/61 observation
  - both inverters now retain ordinary command/limiter/previous-state inputs142–145
  - [deployed profiles, measured gaps and diagnostic limits](docs/mother-of-all-data-loggers.md#controller-diagnostics-deployed); no inverter firmware or power-setting changes

## Quick Start

1. Copy `custom_components/eg4_monitor` to Home Assistant `custom_components`
2. Restart Home Assistant
3. Add `EG4 Monitor` from Integrations
4. (Recommended) Configure serial role overrides in integration options

## Key Entities

- Site power: `sensor.grid_power`, `sensor.load_power`, `sensor.grid_voltage`, `sensor.grid_frequency`, `sensor.eps_voltage`
- Transfer diagnosis: `sensor.eg4_site_gridboss_generator_power`, `sensor.eg4_site_gridboss_generator_voltage`, `sensor.eg4_site_gridboss_ups_voltage`, `sensor.eg4_site_gridboss_transfer_observation`, `sensor.eg4_site_flexboss_local_ac_input_voltage`, `sensor.eg4_site_flexboss_local_eps_voltage`
- Battery: `sensor.battery_state_of_charge`, `sensor.battery_power`, `sensor.battery_charging_power`, `sensor.battery_discharging_power`, `sensor.battery_power_direction`, `sensor.battery_energy_remaining`
- Energy: `sensor.today_usage`, `sensor.total_usage`, `sensor.today_export`, `sensor.total_export`, `sensor.ups_usage_today`, `sensor.ups_usage_total`, `sensor.today_solar_yield`, `sensor.total_solar_yield`, `sensor.eg4_site_battery_charge_today`, `sensor.eg4_site_battery_discharge_today`
- Diagnostics: `binary_sensor.eg4_site_eg4_data_stale`, `sensor.eg4_site_grid_quality_state`, `sensor.grid_outage_duration_24h`, `sensor.eg4_site_battery_estimated_runtime`
- Generator: `binary_sensor.generator_running`, `binary_sensor.generator_supplying_power`, `sensor.generator_status_source`
- Stateful logger: `sensor.eg4_site_generator_run_state`, `binary_sensor.eg4_site_generator_gridboss_supplying`, `sensor.eg4_site_charge_session_state`, `binary_sensor.eg4_site_battery_charge_session_active`
- Active faults: `binary_sensor.uncleared_error_active`, `sensor.uncleared_error_count`, `sensor.uncleared_error_summary`

### Battery SOC trust

When the portal identifies the bank as `LEAD_ACID`, its cloud `soc`/capacity
fields are not treated as authoritative: the portal payload has no BMS pack SOC
rows (`batteryArray`/`remainCapacity` are empty), and the EG4 web UI hides SOC for
this battery type. Without a validated local BMS snapshot,
`sensor.battery_state_of_charge` is unavailable while the portal value is
retained explicitly as **unverified**. When an auto-discovered or manually
configured local FlexBOSS dongle returns a structurally valid BMS summary, the
packed SOC is promoted as
`SOCSource: local_bms`; this is the automation-grade path in this integration.

### Local inverter telemetry discovery

The installation also has three reachable EG4 Wi-Fi dongles on the IOT VLAN:

- `192.168.3.14:8000` — FlexBOSS master `54700E0353`
- `192.168.3.162:8000` — FlexBOSS slave `52842P0677`
- `192.168.3.241:8000` — GridBOSS `4354850310`

They speak the proprietary `A1 1A` LuxPower/EG4 TCP framing, not standard
Modbus TCP. Read-only probes of the master returned battery voltage from input
register 4 (0.1 V units), packed SOC/SOH from register 5, and populated BMS
summary fields (parallel packs, current, cell-voltage extremes, temperatures,
and cycle count). The master and portal have reported conflicting SOC values in
successive samples. Because the master also returns the required BMS summary,
the integration promotes its packed SOC as `SOCSource: local_bms`; the portal
value remains diagnostic/unverified. If the BMS summary disappears, canonical
SOC fails closed again. The slave and GridBOSS dongles do not currently expose
populated BMS ranges.

The integration polls FlexBOSS dongles read-only and exposes local
voltage/power plus BMS summary diagnostics. Auto-discovery is enabled by default:
it first probes private IP addresses reported by the authenticated portal, then
falls back to bounded scans of Home Assistant's local IPv4 segments when no
FlexBOSS is found. Public portal IPs are never scanned. The two host fields
remain optional manual overrides (`192.168.3.14` and `192.168.3.162` for this
installation).

Capture is enabled by default. Each role targets 1s dynamic rounds, with 5s BMS
refreshes and staggered fixed maintenance; `RecorderHealth` reports achieved cadence
and original source ages rather than promising1Hz. New JSONL lives under
`/config/.eg4-investigation/host-recorder-v2/<entry_id>/`, split into `raw/`,
`controller-observations/` and copyable `incidents/` directories. Old
`local-dongle/` investigations and deployment backups are never pruned by this owner.
The stateful logger also exposes high-rate generator AC-evidence and battery
charging-session entities, restores its active session across restarts, retains
up to 90 days/100 completed sessions, and fires accepted transition events:
`eg4_monitor_generator_state_changed`,
`eg4_monitor_generator_transfer_observation_changed`,
`eg4_monitor_charging_session_started`, and
`eg4_monitor_charging_session_ended`. Generator state is inferred from fresh
GridBOSS terminal voltage/frequency/power; it is not a mechanical engine or
relay-contact measurement. See
[`docs/mother-of-all-data-loggers.md`](docs/mother-of-all-data-loggers.md) and
the raw-editor dashboard template at
[`docs/mother-of-all-data-loggers-dashboard.yaml`](docs/mother-of-all-data-loggers-dashboard.yaml).
The live Trigo HA storage dashboard is **EG4 Mother of All Data** at
`/eg4-data-logger`.
Do not run a second persistent client against a dongle without accounting for
the dongle's single-client limit. Discovery occurs during integration setup or
reload; reload the integration after a dongle address or network change.

The discovery diagnostic sensor exposes `Source`, `ManualHosts`,
`PortalReportedIPs`, `PortalLocalIPs`, `ScannedNetworks`, `ScannedHostCount`,
and `Discovered` attributes. The 2026-08-11 deployment validation used blank
manual host overrides and found both FlexBOSS dongles through the local-LAN
fallback.

### WallMount 280Ah / five-module wiring and charge settings

The current EG4 WallMount 280Ah manual documents internal battery busbars for
up to four batteries with two inverters. A five-battery/two-inverter system
requires the manual's external fused-positive and unfused-negative busbar
arrangement, rated at least 200 A per battery. Verify the installed wiring with
a qualified installer; unequal current sharing can create both voltage-drop and
per-battery SOC divergence.

For this five-module bank, the nominal capacity is 1,400 Ah / approximately
71.7 kWh. A 9.7 kW discharge is only about 0.14C when current sharing is
balanced, so 50.6 V should be interpreted with the local cell-extrema and
wiring measurements—not as a standalone SOC lookup.

Manufacturer PDFs and the detailed voltage-mode decision are retained in the
local research copy, not distributed with this integration PR. The documented
site operating decision remains voltage-mode AC charging: **52.0 V start**,
**56.2 V stop**, and **54.0 V float**. The stop/float values follow EG4's
[280Ah battery specification](https://eg4electronics.com/wp-content/uploads/2024/04/EG4%C2%AE-Indoor-280Ah-Battery-Specifications-Sheet.pdf);
59 V is not a normal charge threshold. These are historical site settings,
not changes authorized by the [host-recorder initiative](docs/host-recorder-mvp-spec.md).

## Automation Reference

`Grid Power Loss Detection` was updated to repeat every 30 minutes while all are true:

- `sensor.grid_voltage < 200`
- `sensor.battery_state_of_charge < 50`
- `binary_sensor.generator_running == off`
- `input_boolean.mute_grid_power_alarm == off`

It sends the same media alert + mobile push on each repeat interval.

## Mute Button Reference

A mute helper is used to silence repeated alerts:

- Helper: `input_boolean.mute_grid_power_alarm`
- Dashboard placement: `Main Dash` -> `Battery Backup Status`
- UI chip behavior:
  - icon changes by state (`mdi:volume-off` when muted, `mdi:volume-high` when active)
  - red when muted, green when active
  - tap toggles mute state

## Local Dev Credentials

Use structured env vars via `.env.local`:

```env
EG4_PORTAL__USERNAME="your_eg4_username"
EG4_PORTAL__PASSWORD="your_eg4_password"
HOME_ASSISTANT__BASE_URL="http://192.168.0.253:8123"
HOME_ASSISTANT__TOKEN="your_long_lived_access_token"
```

Keep secrets out of git. `.gitignore` is set to ignore `.env`, `.env.local`, and `.env.*` while keeping `.env.example` tracked.

## Notes

- Cloud portal data remains the site-wide diagnostic/energy source; optional local
  dongle polling supplies automation-grade battery BMS telemetry when available.
- Direct battery CAN/Modbus transport remains a future option behind the same
  normalized site model.
- Curated project knowledge is maintained under [`docs/okf/`](docs/okf/),
  including the discovery integration and local-BMS SOC decision. Validate it
  with `python3 ~/.agents/skills/okf-project-knowledge/scripts/validate-okf-bundle.py docs/okf --strict-links`.

## Disclaimer

This project is not officially affiliated with EG4 Electronics.
