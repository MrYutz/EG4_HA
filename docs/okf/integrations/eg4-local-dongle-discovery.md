---
type: "Integration"
title: "EG4 Local Dongle Discovery"
description: "Read-only discovery and telemetry path from the authenticated EG4 portal or Home Assistant local networks to FlexBOSS Wi-Fi dongles."
resource: "custom_components/eg4_monitor"
tags: ["eg4", "home-assistant", "local-telemetry", "discovery"]
timestamp: "2026-11-09T11:19:57Z"
source_path: "custom_components/eg4_monitor/{__init__.py,local_dongle.py,local_discovery.py}"
status: active
sensitivity: internal
approval: none
---

# Overview

The integration supplements portal polling with local, read-only FlexBOSS dongle telemetry. This avoids treating a cloud `LEAD_ACID` estimate as authoritative when the authenticated portal does not expose a usable BMS SOC row.

# Capabilities Used

- Portal login and inverter-list role discovery.
- Recursive extraction of IPv4 candidates from portal plant/inverter payloads.
- Private/local address filtering; public addresses are discarded.
- A1/1A identity probing on the configured EG4 TCP port.
- Bounded Home Assistant local-network fallback scanning when FlexBOSS roles remain undiscovered.
- Short-lived core and BMS register reads after identity discovery.
- Role assignment by the inverter serial reported by the portal.
- Diagnostic discovery provenance on the local dongle-count sensor.
- Supplemental read-only controller blocks and independent controller-observation history, as specified by the [diagnostic capture contract](/integrations/eg4-controller-diagnostic-capture.md).

# Authentication / Secret Contract

Portal credentials are supplied through the existing EG4 config entry and are not copied into this bundle, logs, or local telemetry records. Local dongle identity and register reads require no separate credential.

# Data Flow

1. The integration authenticates to the EG4 portal and builds the inverter role map.
2. Explicit local master/slave host options are installed as role overrides.
3. If auto-discovery is enabled, the integration recursively extracts IPv4 strings from portal plant and inverter payloads, keeps only RFC1918/link-local/loopback candidates, and probes those candidates first.
4. If a FlexBOSS role is still missing, enabled Home Assistant network adapters are converted to bounded IPv4 networks. Networks broader than `/24` are narrowed to `/24`; each network contributes at most 254 host probes.
5. Each candidate receives one short-lived connection. The dongle's unsolicited A1/1A frame identifies its dongle and inverter serial; discovery sends no register write or control command.
6. Known FlexBOSS identities are assigned to `flexboss_master` or `flexboss_slave` using the authenticated inverter serial map. GridBOSS and unknown identities are ignored for battery telemetry.
7. Normal polling reads core registers plus the BMS summary. A valid BMS summary includes a plausible pack count and ordered cell-voltage extrema.
8. Valid local BMS-backed SOC is published through `sensor.battery_state_of_charge`; portal SOC remains separately diagnostic.

Discovery runs during integration setup/reload, not every coordinator update. Reload the integration after a dongle address or network-segment change.

# Outputs

The `sensor.eg4_site_local_eg4_dongles_available` attributes include discovery provenance such as:

- `Enabled`
- `Source` (`manual`, `portal_reported_ip`, or `local_lan_scan`)
- `ManualHosts`
- `PortalReportedIPs`
- `PortalLocalIPs`
- `ScannedNetworks`
- `ScannedHostCount`
- `Discovered`

The local telemetry entities expose voltage, signed local power, BMS current, capacity, pack count, cell extrema, temperatures, cycle count, and the local packed SOC/SOH diagnostics.

# Failure Modes

- No portal IP fields: local adapter fallback is attempted.
- HA cannot enumerate network adapters: explicit hosts still work; discovery reports the error and no scan is performed.
- No dongle responds: local telemetry is unavailable and canonical SOC remains unknown unless another validated source exists.
- Dongle returns malformed, mismatched, stale, or physically implausible frames: that candidate is rejected.
- A FlexBOSS responds but its BMS summary is absent: local packed SOC remains diagnostic-only.
- A manual host is configured: discovery does not replace that role, but it may discover any missing role.
- Concurrent clients can interfere with diagnostic ownership. HA serializes requests only within its own integration and normally opens short-lived connections; its lock is not a network-wide reservation. A hard one-TCP-client firmware limit has not been demonstrated. The approved direct test paused EG4 to remove that known competing poller, not to prove such a limit or exclude vendor/cloud traffic.

# Current Deployment Evidence

The August11 blank-host discovery result below is historical. During the November8 diagnostic deployment, cold-start scanning returned no FlexBOSS identities. Existing HA master/slave host overrides were then set to verified routes, with all other options preserved. Discovery remains enabled but no longer supplies those two configured roles.

The integration's local-dongle-count sensor counts FlexBOSS roles only; configured GridBOSS sampling is separate. November9 follow-up found both FlexBOSS roles and GridBOSS producing successful primary and supplemental reads. The observed slave lacked direct valid BMS availability; that is not a zero-permission report or proof of an electrical failure.

Capture profiles, measured timing and blind spots are in [Controller Diagnostic Capture](/integrations/eg4-controller-diagnostic-capture.md). Register logging does not make the [firmware investigation](/references/generator-firmware-investigation.md) a diagnosed cause or completed repair.


# Validation

- Unit tests cover nested IP extraction, local filtering, bounded network expansion, identity-only probing, frame validation, and role assignment.
- Live deployment validation on 2026-08-11 found both FlexBOSS roles via the local-LAN fallback with auto-discovery enabled and blank manual host overrides.
- Live HA state confirmed two local dongles and a local BMS-backed canonical SOC.
- November8 diagnostic deployment and November9 follow-up verified three-device capture after explicit FlexBOSS host options were configured; the earlier auto-discovery success is not a current discovery-availability guarantee.

# Citations

- [Repository configuration guide](../../../docs/configuration.md)
- [Repository FAQ](../../../docs/faq.md)
- [FlexBOSS21 Manual v1.3.7](/references/flexboss21-manual-v1-3-7.md)
- [WallMount 280Ah Battery Specifications v1.1.8](/references/wallmount-280ah-battery-spec-v1-1-8.md)
- [WallMount 280Ah Troubleshooting Guide v1.3.7](/references/wallmount-280ah-troubleshooting-v1-3-7.md)
