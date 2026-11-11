---
type: "External Reference"
title: "EG4 GridBOSS User Manual v2.0.7"
description: "Current GridBOSS manual defining generator-port operation and GridBOSS-owned generator charging settings for FlexBOSS systems."
resource: "https://eg4electronics.com/wp-content/uploads/2025/04/EG4-GridBOSS-Model-MI-200-2P-HYB-AW-03-User-Manual-v2.0.pdf"
tags: ["eg4", "gridboss", "manual", "generator", "charging"]
timestamp: "2026-08-13T11:04:00Z"
source_path: "docs/references/eg4/GridBOSS-User-Manual-v2.0.7.pdf"
status: active
sensitivity: public
approval: none
---

# Summary

This is the current official GridBOSS manual for the MI-200-2P-HYB-AW-03 and
MI-200-2P-HYB-AW-04 model family. It is the authority for the GridBOSS
transfer path and generator controls when FlexBOSS units are attached to
GridBOSS hybrid ports.

# Relevant Claims

- When a hybrid inverter operates with GridBOSS, generator support is provided
  exclusively through the GridBOSS generator port; the inverter generator port
  must not be used for that installation.
- The generator example separates inverter AC Charge from GridBOSS generator
  settings. Generator SOC/voltage enable, battery charge current, rated power,
  start/end thresholds, warm-up, and auto-off are configured on GridBOSS.
- The generator section states that inverter generator settings are not used in
  a GridBOSS system except for `GEN Boost`.
- GridBOSS closes the generator relay only after the configured warm-up period
  and stable generator voltage/frequency are present. This matters when
  correlating a source dropout with battery charging behavior.

# Applies To

- [Voltage-mode AC-charge settings](/decisions/voltage-mode-ac-charge-settings.md)
- [EG4 Local Dongle Discovery](/integrations/eg4-local-dongle-discovery.md)

# Local Copy

The pinned PDF is at `docs/references/eg4/GridBOSS-User-Manual-v2.0.7.pdf`.
Its SHA-256 is recorded in `docs/references/eg4/README.md`.

# Citations

[1] [EG4 GridBOSS User Manual v2.0.7](https://eg4electronics.com/wp-content/uploads/2025/04/EG4-GridBOSS-Model-MI-200-2P-HYB-AW-03-User-Manual-v2.0.pdf), Sections 8.1, 8.2, and 10 Generator.
