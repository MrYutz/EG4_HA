---
type: "Project Profile"
title: "EG4 Home Assistant Integration"
description: "Project knowledge for EG4 portal, local GridBOSS/FlexBOSS telemetry, and read-only controller diagnostics in Home Assistant."
resource: "README.md"
tags: ["eg4", "home-assistant", "telemetry"]
timestamp: "2026-11-09T18:32:07Z"
source_path: "custom_components/eg4_monitor"
status: active
sensitivity: internal
approval: none
---

# Overview

This project integrates EG4 portal data with read-only local telemetry from EG4/LuxPower Wi-Fi dongles attached to FlexBOSS inverters and GridBOSS. Cloud data remains useful for site-wide energy and diagnostics; local BMS-backed FlexBOSS data is the preferred battery SOC source when its validity checks pass. Generator investigations distinguish software commands, accepted power, reported EPS voltage and physical engine/contact behavior.

# Knowledge Map

Read these concepts in order:

1. [EG4 Local Dongle Discovery](/integrations/eg4-local-dongle-discovery.md) for discovery, transport, role assignment, and failure behavior.
2. [Local FlexBOSS BMS SOC Is Canonical](/decisions/local-bms-soc-authority.md) for SOC telemetry trust and fallback semantics.
3. [Use Voltage Mode for AC Charging Until SOC Is Revalidated](/decisions/voltage-mode-ac-charge-settings.md) for the current AC-charge thresholds and the reason SOC is not controlling charge.
4. [Controller Diagnostic Capture](/integrations/eg4-controller-diagnostic-capture.md) for deployed profiles, raw decision-word semantics, retention, measured cadence and capture gaps.
5. [Generator Firmware Investigation: Evidence and Limits](/references/generator-firmware-investigation.md) for recovered decision paths, logical feedback and the boundary between code findings and incident attribution.
6. [EG4 reference manuals](/references/index.md) for version-pinned manufacturer documents and firmware-investigation evidence.

Operational user documentation remains in `/README.md`, `/docs/configuration.md`, `/docs/installation.md`, and `/docs/faq.md`.

# Confirmed Conventions

- Production local dongle communication remains read-only and uses short-lived TCP connections to proprietary A1/1A framing on the configured EG4 port. Separately authorized stock Q1 investigative sessions used bounded1113/1114 diagnostic writes under temporary EG4 pauses; they are not deployed continuous acquisition or operating-setting changes. A hard hardware single-client limit is unproved.
- Auto-discovery is enabled by default. It probes local/private addresses recursively extracted from authenticated portal payloads before falling back to bounded scans of Home Assistant's enabled local IPv4 networks.
- Public portal-reported addresses are never scanned.
- Manual master/slave host fields override discovery for their respective roles; blank fields permit discovery.
- Discovered identities are assigned to FlexBOSS roles by inverter serial from the authenticated portal role map. GridBOSS and unknown identities are not used as battery SOC sources.
- A structurally valid local BMS summary (pack count plus cell-voltage extrema) authorizes the local packed SOC for `sensor.battery_state_of_charge`. Without it, canonical SOC fails closed while portal/local inverter estimates remain diagnostic.
- A valid local BMS SOC remains useful telemetry, but AC charging is intentionally held in voltage mode because the observed system previously reported 100% SOC near 51 V.
- Current voltage-mode AC-charge settings are 52.0 V start and 56.2 V stop on both FlexBOSS units. The 56.2 V stop matches the current EG4 battery recommendation; 59 V is not a normal charge target.
- Generator charging thresholds and limits belong to GridBOSS when FlexBOSS is connected through GridBOSS; inverter generator settings are not authoritative except Generator Boost.
- Manufacturer configuration ownership does not imply that inverter battery/BMS, role, protection and power-limit logic cannot alter generator acceptance or converter targets. The recovered firmware paths are not a diagnosed incident or permission to change protections.
- Controller input519 is an untimestamped last-written decision, not current contacts, engine state or an immutable first-cause log. Periodic register capture cannot observe every internal latch or guarantee first cause.
- No credentials, tokens, cookies, or raw portal dumps belong in this knowledge bundle.

# Validation

The current implementation is validated with:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check custom_components/eg4_monitor tests
.venv/bin/python -m compileall -q custom_components/eg4_monitor
python3 -m json.tool custom_components/eg4_monitor/translations/en.json >/dev/null
git diff --check
python3 ~/.agents/skills/okf-project-knowledge/scripts/validate-okf-bundle.py docs/okf --strict-links
```

The 2026-08-11 deployment proof found two FlexBOSS dongles through the local-LAN fallback with auto-discovery enabled and blank manual host overrides. The canonical HA SOC was populated from local BMS telemetry.

The November8 diagnostic deployment and November9 follow-up supersede the earlier blank-host discovery state: existing master/slave host overrides are now configured after cold-start discovery returned no identities. Both FlexBOSS roles and separate GridBOSS capture were verified. This was a diagnostic upgrade, not a power-control repair. See the [capture contract](/integrations/eg4-controller-diagnostic-capture.md) for dated evidence; its roughly5-second observed cadence is not a guaranteed1Hz recorder.

The later November9 [stock Q1 pilot](../../investigations/2026-11-08-firmware-reverse-engineering/engineering-capture/README.md) obtained real master-converter status without custom firmware or production deployment. Both inverter streams resumed; the retained source shows materially intermittent GridBOSS capture before and after the pilot. Do not generalize earlier successful windows into current all-device health.

A subsequent [single-pause direct session](../../investigations/2026-11-08-firmware-reverse-engineering/direct-session/README.md) acquired three engineering snapshots from **each** FlexBOSS and expanded stock banks, then restored HA. It established retained-buffer behavior and partial protection/mode exposure, not a cycling cause. Native corrections invalidate historical4516 GPIO interpretations and64/65 warning labels. The later approved host upgrade now uses4500/10, warning60/61 and both inverters' ordinary120/26; raw old evidence remains unchanged.

The [incident-day projection recheck](../../investigations/2026-11-08-firmware-reverse-engineering/incident-recheck/README.md) separates recorded source handoffs from one abnormal EPS-voltage response, without proving a cause. Native closure publishes selected-source/local-EPS mismatch at ordinary Flex61.bit14 and distinguishes qualified frequency zero from a physical engine stop. Published ordinary outcomes now enter the corrected capture; missing historical guards/counters were not recovered.

The [corrected capture deployment](../../investigations/2026-11-08-firmware-reverse-engineering/capture-upgrade/README.md) passed56 focused tests, TCP smoke and`ha core check`, followed by one approved Core restart and actual three-device raw/journal verification. It remains03/04 only, with no continuous Q1 or controller power/protection changes. Measured post-restart gaps reached15s and explicit read failures remain; improved capture is not proof of a physical repair or full decision-tree observability.
