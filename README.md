# Home Assistant EG4 Monitor Integration

Custom Home Assistant integration for EG4 portal-backed systems, tuned for modern multi-device installs like dual FlexBOSS + GridBoss.

## What Changed

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
- Uncleared error monitoring
  - active problem binary sensor
  - count + summary + top open event details

## Quick Start

1. Copy `custom_components/eg4_monitor` to Home Assistant `custom_components`
2. Restart Home Assistant
3. Add `EG4 Monitor` from Integrations
4. (Recommended) Configure serial role overrides in integration options

## Key Entities

- Site power: `sensor.grid_power`, `sensor.load_power`, `sensor.grid_voltage`, `sensor.grid_frequency`, `sensor.eps_voltage`
- Battery: `sensor.battery_state_of_charge`, `sensor.battery_power`, `sensor.battery_charging_power`, `sensor.battery_discharging_power`, `sensor.battery_power_direction`, `sensor.battery_energy_remaining`
- Energy: `sensor.today_usage`, `sensor.total_usage`, `sensor.today_export`, `sensor.total_export`, `sensor.ups_usage_today`, `sensor.ups_usage_total`, `sensor.today_solar_yield`, `sensor.total_solar_yield`
- Generator: `binary_sensor.generator_running`, `binary_sensor.generator_supplying_power`, `sensor.generator_status_source`
- Active faults: `binary_sensor.uncleared_error_active`, `sensor.uncleared_error_count`, `sensor.uncleared_error_summary`

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

- This integration is cloud portal based today.
- Local CAN/Modbus transport can be added later behind the same normalized site model.

## Disclaimer

This project is not officially affiliated with EG4 Electronics.
