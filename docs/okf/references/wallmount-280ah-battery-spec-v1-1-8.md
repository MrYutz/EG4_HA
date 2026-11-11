---
type: "External Reference"
title: "EG4 WallMount Indoor 280Ah Battery Specifications v1.1.8"
description: "Current EG4 WallMount Indoor 280Ah electrical, BMS-protection, balancing, and recommended-setting specification."
resource: "https://eg4electronics.com/wp-content/uploads/2024/04/EG4%C2%AE-Indoor-280Ah-Battery-Specifications-Sheet.pdf"
tags: ["eg4", "wallmount", "280ah", "battery", "bms", "voltage"]
timestamp: "2026-08-13T11:04:00Z"
source_path: "docs/references/eg4/EG4-WallMount-Indoor-280Ah-Battery-Specifications-v1.1.8.pdf"
status: active
sensitivity: public
approval: none
---

# Summary

This is the current official specification sheet for the EG4 WallMount Indoor
280Ah battery family used in this installation. It supplies the battery-side
limits against which FlexBOSS settings are evaluated.

# Relevant Claims

- Nominal module voltage is 51.2 V and capacity is 280 Ah.
- The BMS charge-voltage range is listed as 56.0 V ±0.8 V; the recommended
  bulk/absorb setting is **56.2 V ±0.2 V**.
- The recommended float setting is **54.0 V ±0.2 V**.
- EG4 recommends an SOC cutoff no lower than 20% to maintain an 80% depth of
  discharge.
- Maximum continuous charge current is 140 A per module, with a recommended
  range of 60–140 A per module. A five-module bank therefore has substantial
  aggregate current headroom, while the inverter and BMS still enforce their
  own limits.
- Module over-voltage protection is 60.0 V. This is a protection threshold,
  not a normal charge-stop target.
- Passive cell balancing is 120 mA when cell voltage difference exceeds 40 mV.
- The specification describes SOC accuracy as 5% using integral calculation;
  this does not validate a previously observed SOC value that reported 100%
  while the bank was near 51 V.

# Applies To

- [Voltage-mode AC-charge settings](/decisions/voltage-mode-ac-charge-settings.md)
- [Local FlexBOSS BMS SOC Is Canonical](/decisions/local-bms-soc-authority.md)

# Local Copy

The pinned PDF is at `docs/references/eg4/EG4-WallMount-Indoor-280Ah-Battery-Specifications-v1.1.8.pdf`.
Its SHA-256 is recorded in `docs/references/eg4/README.md`.

# Citations

[1] [EG4 WallMount Indoor 280Ah Battery Specifications v1.1.8](https://eg4electronics.com/wp-content/uploads/2024/04/EG4%C2%AE-Indoor-280Ah-Battery-Specifications-Sheet.pdf), Technical Specifications, Module Operating Parameters and BMS Parameters.
