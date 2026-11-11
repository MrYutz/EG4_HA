---
type: "Decision"
title: "Local FlexBOSS BMS SOC Is Canonical"
description: "Use validated local FlexBOSS BMS-backed SOC for the canonical Home Assistant battery SOC while retaining portal SOC as diagnostic."
resource: "custom_components/eg4_monitor"
tags: ["eg4", "soc", "bms", "decision"]
timestamp: "2026-08-11T22:40:00Z"
source_path: "custom_components/eg4_monitor/__init__.py"
status: active
sensitivity: internal
approval: approved
---

# Context

The EG4 portal identifies the bank as `LEAD_ACID` and does not provide usable BMS pack SOC rows. Its cloud SOC can disagree with the physical bank and must not drive protection or automation. The local FlexBOSS A1/1A path is reachable without the cloud portal and returns both packed SOC/SOH and a BMS summary.

# Decision

When a local FlexBOSS read returns a SOC value plus a structurally valid BMS summary, publish that value as the canonical `sensor.battery_state_of_charge` source:

```text
SOCSource = local_bms
SOCQuality = bms_reported
```

This is acceptable because the source is local to the inverter/battery system and the BMS summary corroborates the reading. The portal value remains under explicit diagnostic fields (`PortalSOC`, `PortalSOCSource`, and `PortalSOCQuality`).

If the local BMS summary disappears, is malformed, or fails physical-range validation, canonical SOC must return to `unknown`; no cloud or uncorroborated local inverter SOC may silently replace it.

This establishes the best available telemetry source; it does not make the SOC
value suitable for every control loop. The site previously observed a 100% SOC
report near 51 V, so AC charging currently remains in voltage mode under
[Use Voltage Mode for AC Charging Until SOC Is Revalidated](/decisions/voltage-mode-ac-charge-settings.md).

# Options Considered

- **Portal SOC as canonical:** rejected because it is cloud-derived, conflicts with the `LEAD_ACID` payload semantics, and lacks validated BMS rows.
- **Uncorroborated local packed SOC:** rejected because a local register alone does not prove that the BMS path is populated or correctly routed.
- **Direct per-battery RS485/CAN only:** remains a future option for per-module diagnostics, but is not required for the current aggregate local BMS decision.
- **Validated local FlexBOSS SOC plus BMS summary:** selected because it is local, read-only, available now, and corroborated by pack/cell telemetry.

# Consequences

- HA automations may use the canonical SOC only while `SOCSource` is `local_bms` and `SOCQuality` is `bms_reported`.
- Portal SOC remains useful for diagnosing calibration drift but must not control protection.
- Loss of local BMS telemetry fails safe to `unknown`.
- The local reading is an aggregate bank view through FlexBOSS, not independent per-module SOC.
- Changes to the validity checks require tests and a new live comparison against BMS cell extrema.

# Related Concepts

- [EG4 Local Dongle Discovery](/integrations/eg4-local-dongle-discovery.md)
- [Configuration Guide](../../../docs/configuration.md)
