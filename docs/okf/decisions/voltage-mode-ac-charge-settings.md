---
type: "Decision"
title: "Use Voltage Mode for AC Charging Until SOC Is Revalidated"
description: "Keep FlexBOSS AC charging in voltage mode with a 52.0 V start and 56.2 V stop because the previously observed battery SOC calibration was unreliable."
resource: "docs/references/eg4"
tags: ["eg4", "flexboss21", "gridboss", "battery", "ac-charge", "voltage-mode", "soc"]
timestamp: "2026-08-13T11:04:00Z"
source_path: "docs/references/eg4/*.pdf; live FlexBOSS register read 2026-08-13"
status: active
sensitivity: internal
approval: approved
---

# Context

The site has five EG4 WallMount Indoor 280Ah modules (approximately 1,400 Ah)
connected to two FlexBOSS21 inverters through a shared battery arrangement. The
current inverter profile is Lithium with closed-loop BMS telemetry. However, the
previous SOC behavior was not trustworthy: the system reported 100% while the
bank was near 51 V. That makes SOC thresholds unsuitable as the primary AC-charge
control until the battery SOC calculation and per-module state are revalidated.

The current EG4 battery specification recommends 56.2 V ±0.2 V for bulk/absorb
and 54.0 V ±0.2 V for float. It lists 60.0 V as module over-voltage protection,
not a normal charge target. The current FlexBOSS manual defines voltage control
but does not prescribe a universal AC-charge-start voltage.

# Decision

Remain in voltage mode for now. Use these operating settings on both parallel
FlexBOSS21 units:

| Setting | Value | Reason |
| --- | ---: | --- |
| Battery charge control | `Volt` | Do not use the unreliable SOC value as the charge trigger. |
| AC Charge | `Enable` | Permit utility-grid charging when the configured AC-charge schedule is active. |
| Start AC Charge Volt | **52.0 V** | Local operating threshold above the 51.2 V nominal bank voltage; provides recharge hysteresis without waiting for a very low voltage. |
| Stop AC Charge Volt | **56.2 V** | Matches the center of EG4's recommended 56.2 V ±0.2 V bulk/absorb range. |
| Float | **54.0 V** | Matches EG4's recommended 54.0 V ±0.2 V float range. |
| EOD / discharge cutoff | **20% SOC** | Retain EG4's recommended minimum when the inverter/BMS uses SOC for discharge protection. |
| Equalization | Disabled | Not applicable to this Lithium bank. |

The AC Charge control selector must use its voltage-capable option (the current
UI displays this as `SOC/Volt (AC Charge)`), with the voltage fields above active.
SOC fields remain diagnostic/non-authoritative while this decision is active.

# Why 59 V Is Not Used

A 59 V stop threshold is not a sensible normal target for these batteries. It is
well above EG4's recommended 56.2 V ±0.2 V charge voltage and only 1 V below the
60.0 V module over-voltage protection threshold. Cell imbalance can cause a BMS
protection event before the module reaches that nominal voltage. Raising the
threshold to make an inaccurate SOC report 100% is explicitly avoided.

# Observed Application

On 2026-08-13 at approximately 11:04 UTC, read-only register reads matched the
operator's update on both inverters:

- FlexBOSS master `54700E0353`: AC charge start register = 52.0 V; stop register = 56.2 V.
- FlexBOSS slave `52842P0677`: AC charge start register = 52.0 V; stop register = 56.2 V.

No inverter setting was changed by the repository tooling.

# Options Considered

- **SOC mode now:** rejected until the observed SOC calibration problem is
  understood and the aggregate reading is reconciled with pack-level BMS data.
- **52.0 V / 56.2 V voltage mode:** selected as a conservative operational
  hysteresis band that uses the manufacturer's recommended charge voltage.
- **56.0 V / 59.0 V voltage mode:** rejected because 59.0 V is near BMS
  over-voltage protection and is not an operating recommendation.
- **Raise the stop voltage to force SOC calibration:** rejected; calibration
  must be resolved from BMS/pack evidence, not from a higher charge ceiling.

# Validation and Revisit Conditions

- Watch charge power, battery voltage, maximum/minimum cell voltage, BMS charge
  permission, and GridBOSS/FlexBOSS AC input together during the next charge.
- If a brief voltage sag below 52.0 V causes unwanted starts, tune the start
  threshold only after reviewing a sustained voltage trace; do not raise the
  56.2 V stop threshold to solve relay or source-dropout behavior.
- Reconsider SOC mode only after a controlled full-charge observation, valid
  BMS/pack data, and a credible explanation for the prior 100%-at-51-V report.
- Individual slave-pack BMS and cell data remain unavailable, so the source pack
  of the observed top-end cell spread is not yet identified.

# Related Concepts

- [FlexBOSS21 Manual v1.3.7](/references/flexboss21-manual-v1-3-7.md)
- [GridBOSS Manual v2.0.7](/references/gridboss-manual-v2-0-7.md)
- [WallMount 280Ah Battery Specifications v1.1.8](/references/wallmount-280ah-battery-spec-v1-1-8.md)
- [WallMount 280Ah Troubleshooting Guide v1.3.7](/references/wallmount-280ah-troubleshooting-v1-3-7.md)
- [Local FlexBOSS BMS SOC Is Canonical](/decisions/local-bms-soc-authority.md)
