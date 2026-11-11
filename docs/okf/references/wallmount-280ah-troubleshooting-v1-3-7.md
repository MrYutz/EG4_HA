---
type: "External Reference"
title: "EG4 WallMount Indoor 280Ah Troubleshooting & Maintenance Guide v1.3.7"
description: "Current EG4 WallMount 280Ah troubleshooting guide for BMS states, cell protection, communications, and maintenance diagnostics."
resource: "https://eg4electronics.com/wp-content/uploads/2024/04/EG4%C2%AE-Indoor-280Ah-Battery-Troubleshooting-Maintenance-Guide.pdf"
tags: ["eg4", "wallmount", "280ah", "battery", "bms", "troubleshooting"]
timestamp: "2026-08-13T11:04:00Z"
source_path: "docs/references/eg4/EG4-WallMount-Indoor-280Ah-Battery-Troubleshooting-Maintenance-Guide-v1.3.7.pdf"
status: active
sensitivity: public
approval: none
---

# Summary

This current maintenance guide is retained with the specification sheet because
it gives the operational interpretation for BMS alarms and communication
troubleshooting when a voltage or SOC observation is unexpected.

# Relevant Claims

- BMS Tools exposes individual cell, module, current, and temperature state for
  troubleshooting, but it cannot communicate with a closed-loop inverter at the
  same time.
- The guide distinguishes pack over-voltage, cell over-voltage, charge
  over-current, and communication failures; an inverter voltage setting should
  not be raised merely to force a reported SOC to 100%.
- For an unexpected battery state, the guide directs the operator to check
  individual cell voltage and battery/inverter communication before changing
  operating parameters.

# Applies To

- [Voltage-mode AC-charge settings](/decisions/voltage-mode-ac-charge-settings.md)
- [EG4 WallMount Indoor 280Ah Battery Specifications v1.1.8](/references/wallmount-280ah-battery-spec-v1-1-8.md)

# Local Copy

The pinned PDF is at `docs/references/eg4/EG4-WallMount-Indoor-280Ah-Battery-Troubleshooting-Maintenance-Guide-v1.3.7.pdf`.
Its SHA-256 is recorded in `docs/references/eg4/README.md`.

# Citations

[1] [EG4 WallMount Indoor 280Ah Troubleshooting & Maintenance Guide v1.3.7](https://eg4electronics.com/wp-content/uploads/2024/04/EG4%C2%AE-Indoor-280Ah-Battery-Troubleshooting-Maintenance-Guide.pdf), BMS Tools, protection, and troubleshooting sections.
