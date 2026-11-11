---
type: "External Reference"
title: "EG4 FlexBOSS21 User Manual v1.3.7"
description: "Current FlexBOSS21 installation and configuration manual, including Lithium communication and AC Charge controls."
resource: "https://eg4electronics.com/wp-content/uploads/2024/08/FlexBOSS-21-Manual.pdf"
tags: ["eg4", "flexboss21", "manual", "ac-charge", "lithium"]
timestamp: "2026-08-13T11:04:00Z"
source_path: "docs/references/eg4/FlexBOSS21-User-Manual-v1.3.7.pdf"
status: active
sensitivity: public
approval: none
---

# Summary

This is the current official FlexBOSS21 manual used for inverter-side battery,
parallel, application, and charging settings. The repository stores the exact
revision reviewed during the 2026 battery-transition investigation.

# Relevant Claims

- The battery communication section instructs users of EG4 batteries to select
  `Lithium` and then `0:EG4` for closed-loop communication.
- Section 14.1 defines **AC Charge Enable** as the ability to charge batteries
  from the grid. It describes AC Charge Power and control based on SOC, voltage,
  or time, but does not prescribe a universal AC charge-start voltage.
- The inverter's maximum battery charge current is 250 A. Actual closed-loop
  BMS limits remain authoritative for the battery bank.
- The manual warns that settings may need adjustment after installation and
  should be checked against the battery manufacturer's specifications.

# Applies To

- [Voltage-mode AC-charge settings](/decisions/voltage-mode-ac-charge-settings.md)
- [Local FlexBOSS BMS SOC Is Canonical](/decisions/local-bms-soc-authority.md)
- [EG4 Local Dongle Discovery](/integrations/eg4-local-dongle-discovery.md)

# Local Copy

The pinned PDF is at `docs/references/eg4/FlexBOSS21-User-Manual-v1.3.7.pdf`.
Its SHA-256 is recorded in `docs/references/eg4/README.md`.

# Citations

[1] [EG4 FlexBOSS21 User Manual v1.3.7](https://eg4electronics.com/wp-content/uploads/2024/08/FlexBOSS-21-Manual.pdf), battery communication section and Section 14.1, AC Charge Mode.
