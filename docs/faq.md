# Frequently Asked Questions (FAQ)

## General Questions

### What is the EG4 Monitor integration?
The EG4 Monitor integration combines EG4 portal data with optional read-only
local FlexBOSS dongle telemetry in Home Assistant. Cloud data remains useful for
site-wide energy and diagnostics; validated local BMS telemetry supplies the
canonical battery SOC when available.

### Which EG4 models are supported?
The integration works with EG4 inverter systems that can be monitored through
the EG4 web portal. Local A1/1A dongle telemetry additionally requires a
reachable compatible EG4/LuxPower Wi-Fi dongle on the configured TCP port.

### How often is the data updated?
The portal coordinator updates every 120 seconds by default. Local telemetry
can also be sampled by the bounded trace loop; its default core trace interval
is one second and BMS refresh interval is five seconds.

## Installation Questions

### Do I need to enter the dongle IP addresses?
No. Local EG4 dongle auto-discovery is enabled by default. Leave the master and
slave host options blank. After portal login, the integration probes private or
local IP addresses found in the authenticated portal payload. If those do not
identify the FlexBOSS dongles, it scans Home Assistant's enabled local IPv4
segments with bounded concurrency.

Manual host fields are still available as role-specific overrides. A manual
host prevents discovery from replacing that role, while missing roles may still
be discovered automatically.

### What addresses are scanned?
Only addresses that are local to the HA host are eligible: RFC1918 private,
link-local, or loopback addresses. Public portal-reported addresses are
explicitly discarded and never probed. Local network fallback is limited to HA
enabled interfaces, narrowed to at most `/24` per network, and capped at 254
hosts per network.

### When does discovery run?
Discovery runs during integration setup or reload. Reload the EG4 Monitor entry
after changing a dongle address, VLAN route, or local network interface.
Normal coordinator updates reuse the discovered role clients and do not repeat a
LAN scan.

## Battery SOC Questions

### Why is `sensor.battery_state_of_charge` sometimes unknown?
If the portal reports `LEAD_ACID` without usable BMS pack SOC rows, the cloud SOC
is not trusted. The canonical sensor remains unknown until a local FlexBOSS
read returns both a plausible packed SOC and a structurally valid BMS summary.

### Is the local FlexBOSS SOC authoritative?
For this integration, yes—when it is local and the BMS summary validates. The
canonical sensor then reports `SOCSource: local_bms` and
`SOCQuality: bms_reported`. The value is an aggregate bank reading through the
FlexBOSS, not independent SOC from each battery module.

The portal value remains available separately as diagnostic data and must not be
used for protection or automation. If the local BMS summary disappears or fails
validation, canonical SOC fails safe back to unknown.

### How can I verify discovery and SOC source?
Inspect `sensor.eg4_site_local_eg4_dongles_available` attributes. Useful fields
include `Source`, `ManualHosts`, `PortalReportedIPs`, `PortalLocalIPs`,
`ScannedNetworks`, `ScannedHostCount`, and `Discovered`. For SOC, inspect
`sensor.battery_state_of_charge` attributes and confirm:

```text
SOCSource = local_bms
SOCQuality = bms_reported
```

## Troubleshooting

### Why can't I find the integration in HACS?
The integration needs to be submitted and approved for HACS. For manual use,
copy `custom_components/eg4_monitor` into the Home Assistant configuration
directory and restart Home Assistant.

### Why are some sensors showing "Unknown"?
This can happen when:
1. The specific data point is unavailable from the inverter or dongle.
2. A local dongle is unreachable, replies do not match the request, or competing traffic interferes. A hard single-client firmware limit has not been established.
3. The local BMS summary is absent, malformed, stale, or outside physical range.
4. The sensor is not supported by the installed model.

### How can I debug connection issues?
1. Enable debug logging for the integration.
2. Check the Home Assistant logs.
3. Inspect local discovery attributes and the bounded trace path.
4. Verify Home Assistant has a route to the battery VLAN.
5. Test that the EG4 portal login works directly.
6. Use a manual role host only when the local network segment is not visible to HA.

## Feature Requests and Support

### How can I request a new feature?
Please create a new issue on our [GitHub repository](https://github.com/ZoarTech/EG4_HA_Inverter/issues) with the "feature request" label.

### Where can I get support?
1. Check this FAQ.
2. Search existing [GitHub Issues](https://github.com/ZoarTech/EG4_HA_Inverter/issues).
3. Create a new issue with logs and discovery attributes, excluding credentials.
4. Join our [Discord community](#) (coming soon).
