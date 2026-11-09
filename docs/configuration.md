# Configuration Guide

## Available Configuration Options

### Basic Configuration
The integration requires only your EG4 portal credentials:
- Username
- Password

### Advanced Options
You can customize the following settings:
- Update interval (default: 120 seconds)
- Sensor selection (choose which sensors to enable)
- Local EG4 dongle auto-discovery (enabled by default)
- Local FlexBOSS master dongle host (optional manual override, TCP port 8000)
- Local FlexBOSS slave dongle host (optional manual override, TCP port 8000)
- Local GridBOSS dongle host (optional direct read-only telemetry, TCP port 8000)
- Local dongle TCP port
- Local trace logging enabled (bounded JSONL under `.eg4-investigation`)
- Local core trace interval (default: 1 second)
- Local BMS trace interval (default: 5 seconds)

The A1/1A client first recursively collects IPv4 candidates reported by the
authenticated portal and probes only private/local addresses read-only. Public
portal IP addresses are never scanned. If a FlexBOSS role is still missing, it
builds bounded probe targets from Home Assistant's enabled local IPv4
interfaces; networks broader than `/24` are narrowed, and each network is
limited to 254 hosts. Explicit host fields remain role-specific overrides and
prevent auto-discovery from replacing that role. Discovery runs at integration
setup/reload, not on every telemetry update.

The client opens short-lived read-only connections, auto-detects dongle and
inverter serials from the first frame, and reads voltage/power plus the
inverter's BMS summary registers. When pack count and cell-voltage extrema
validate the BMS summary, the packed SOC is exposed as `SOCSource: local_bms`
and populates `sensor.battery_state_of_charge`. If that corroboration is absent,
the local inverter SOC remains diagnostic-only and lead-acid canonical SOC fails
closed. Discovery provenance and candidate IPs are exposed on the local dongle
count sensor attributes.

With trace logging enabled, each local core sample is written as one JSONL
record with UTC timestamps, sequence/gap/latency, status, charge/discharge
power, AC/EPS voltage and frequency, raw core registers, and the latest BMS
register block plus a small timestamped snapshot of related HA generator/grid
states and light on/off states for correlation. When a GridBOSS host is
configured, a same-sequence direct GridBOSS sample is recorded with grid,
generator, UPS, and load voltage/current/power/frequency, raw input registers,
and an explicitly inferred transfer observation. The observation is not a
relay-contact measurement; it says what the GridBOSS terminals reported. Files
rotate at 25 MiB and retain eight files. The trace path is exposed in the
local-dongle diagnostic attributes. The local dongle
count sensor also reports discovery provenance through `Source`,
`ManualHosts`, `PortalReportedIPs`, `PortalLocalIPs`, `ScannedNetworks`,
`ScannedHostCount`, and `Discovered` attributes.

## Direct transfer telemetry

Configure the GridBOSS host as `192.168.3.241` for the currently identified
GridBOSS dongle. The direct reader uses read-only A1/1A function-04 requests
and does not write registers. The most useful correlation fields are:

- `GridBOSS Generator Voltage/Frequency/Power`
- `GridBOSS UPS Voltage/Power`
- `GridBOSS Grid Voltage/Frequency`
- `FlexBOSS Local AC Input Voltage/Frequency`
- `FlexBOSS Local EPS Voltage/Frequency/Power`
- `GridBOSS Transfer Observation`

Use the JSONL trace and compare records with the same `sequence`. A generator
present at GridBOSS terminals while FlexBOSS local AC input drops separates a
GridBOSS-to-FlexBOSS path/acceptance issue from a generator-side interruption.
A GridBOSS generator-power drop at the same time points upstream toward the
generator/ATS. The state names ending in `_candidate` are observations, not
proof of relay position.

## Sensor Entities

### Battery Sensors
- **Battery State of Charge**
  - Entity ID: `sensor.battery_state_of_charge`
  - Unit: Percentage (%)
  - Description: Authoritative SOC when a validated BMS source is available; unknown when the portal only reports unverified lead-acid SOC.

- **Battery Portal SOC (Unverified)**
  - Entity ID: generated from `Battery Portal SOC (Unverified)`
  - Unit: Percentage (%)
  - Description: EG4 cloud SOC retained for diagnostics only; do not use for protection or automation.

- **Battery SOC (Local BMS)**
  - Unit: Percentage (%)
  - Description: Packed SOC from the local FlexBOSS read, exposed only when the adjacent BMS summary validates.

- **Battery Power**
  - Entity ID: `sensor.eg4_battery_power`
  - Unit: Watts (W)
  - Description: Current battery power flow (positive = charging, negative = discharging)

- **Battery Voltage (Local Dongle)**
  - Unit: Volts (V)
  - Description: Read-only voltage from the configured FlexBOSS dongle.

- **Battery Power (Local Dongle)**
  - Unit: Watts (W)
  - Description: Read-only local inverter power (`charging - discharging`).

- **Battery SOC (Local Inverter, Unverified)**
  - Unit: Percentage (%)
  - Description: Local packed SOC diagnostic. When the adjacent local BMS summary is valid, the canonical Battery State of Charge uses this same local value; otherwise it remains diagnostic-only.

- **Local BMS Diagnostics**
  - Includes pack count, capacity, current, minimum/maximum cell voltage,
    cell temperature, and cycle count when the inverter receives a populated
    BMS summary.

### Solar Sensors
- **Solar Production**
  - Entity ID: `sensor.eg4_solar_production`
  - Unit: Watts (W)
  - Description: Current solar panel power production

- **Daily Solar Energy**
  - Entity ID: `sensor.eg4_daily_solar_energy`
  - Unit: Kilowatt-hours (kWh)
  - Description: Total solar energy produced today

### Grid Sensors
- **Grid Power**
  - Entity ID: `sensor.eg4_grid_power`
  - Unit: Watts (W)
  - Description: Current grid power flow (positive = importing, negative = exporting)

- **Grid Frequency**
  - Entity ID: `sensor.eg4_grid_frequency`
  - Unit: Hertz (Hz)
  - Description: Current grid frequency

## EG4 WallMount 280Ah notes

For five WallMount 280Ah modules on two FlexBOSS21 units, verify the physical
wiring against the current EG4 battery manual. EG4 documents internal battery
busbars for up to four modules with two inverters; five modules requires an
appropriately rated external fused positive busbar and unfused negative busbar.
Have a qualified installer verify this before relying on current sharing or SOC
comparisons.

For the current Lithium configuration, keep FlexBOSS battery charge control in
voltage mode while SOC calibration is being investigated. The site decision is:

- Start AC Charge Volt: **52.0 V**
- Stop AC Charge Volt: **56.2 V**
- Float: **54.0 V**
- EOD/discharge cutoff: **20% SOC**

The 56.2 V stop matches the current EG4 WallMount 280Ah recommendation of
56.2 V ±0.2 V. Do not use 59 V as a normal stop target: the battery specification
lists 60.0 V as module over-voltage protection, not an operating charge voltage.
The prior SOC report of 100% near 51 V is why voltage mode remains selected; SOC
is retained as diagnostic telemetry until revalidated. See the pinned
[EG4 reference manuals](references/eg4/README.md) and the detailed
[voltage-mode AC-charge decision](okf/decisions/voltage-mode-ac-charge-settings.md).

## Lovelace Cards

### Example Battery Card
```yaml
type: entity
entity: sensor.battery_state_of_charge
name: Battery Level
```

### Example Power Flow Card
```yaml
type: custom:power-flow-card
entities:
  battery: sensor.eg4_battery_power
  solar: sensor.eg4_solar_production
  grid: sensor.eg4_grid_power
