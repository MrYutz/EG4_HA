"""The EG4 Monitor integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.network import async_get_adapters
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backup_metrics import build_backup_metrics
from .const import (
    CONF_BATTERY_DETAIL_INTERVAL,
    CONF_GRIDBOSS_SERIAL,
    CONF_LOCAL_AUTO_DISCOVERY,
    CONF_LOCAL_DONGLE_PORT,
    CONF_LOCAL_GRIDBOSS_HOST,
    CONF_LOCAL_MASTER_HOST,
    CONF_LOCAL_SLAVE_HOST,
    CONF_LOCAL_TRACE_BMS_INTERVAL,
    CONF_LOCAL_TRACE_ENABLED,
    CONF_LOCAL_TRACE_INTERVAL,
    CONF_MASTER_SERIAL,
    CONF_SLAVE_SERIAL,
    DEFAULT_BATTERY_DETAIL_INTERVAL,
    DEFAULT_LOCAL_AUTO_DISCOVERY,
    DEFAULT_LOCAL_DISCOVERY_CONCURRENCY,
    DEFAULT_LOCAL_DISCOVERY_MAX_HOSTS,
    DEFAULT_LOCAL_DISCOVERY_TIMEOUT,
    DEFAULT_LOCAL_DONGLE_PORT,
    DEFAULT_LOCAL_DONGLE_TIMEOUT,
    DEFAULT_LOCAL_GRIDBOSS_HOST,
    DEFAULT_LOCAL_TRACE_BMS_INTERVAL,
    DEFAULT_LOCAL_TRACE_ENABLED,
    DEFAULT_LOCAL_TRACE_INTERVAL,
    DOMAIN,
)
from .data_cache import BoundedPayloadCache
from .eg4_api import (
    EG4API,
    _extract_battery_pack_voltages,
    _normalize_battery_info,
)
from .local_dongle import (
    EG4GridBossDongle,
    EG4GridBossTelemetry,
    EG4LocalBMSData,
    EG4LocalDongle,
    EG4LocalDongleIdentity,
    EG4LocalTelemetry,
    async_discover_dongles,
    merge_local_telemetry,
)
from .local_discovery import (
    extract_ipv4_addresses,
    hosts_for_networks,
    local_ipv4_addresses,
    local_networks_from_adapters,
)
from .local_dongle_trace import (
    ControllerObservationTracker,
    LocalDongleTraceWriter,
    gridboss_to_record,
    telemetry_to_record,
)
from .eg4_logger import EG4TelemetryLogger, LoggerSample
from .grid_quality import (
    DEFAULT_MAX_SAMPLE_GAP_SECONDS,
    GridQualitySample,
    append_grid_quality_sample,
    build_grid_quality_snapshot,
    deserialize_grid_quality_samples,
    serialize_grid_quality_samples,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
DEFAULT_SCAN_INTERVAL = 120


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload the coordinator when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _pick_role_map(
    inverters: list[dict[str, Any]],
    master_override: str | None,
    slave_override: str | None,
    gridboss_override: str | None,
) -> dict[str, str]:
    """Assign serial roles using overrides first, then metadata heuristics."""
    role_map: dict[str, str] = {}

    serials = [str(item["serialNum"]) for item in inverters if item.get("serialNum")]
    serial_set = set(serials)

    def set_override(serial: str | None, role: str) -> None:
        if serial and serial in serial_set:
            role_map[serial] = role

    set_override(master_override, "flexboss_master")
    set_override(slave_override, "flexboss_slave")
    set_override(gridboss_override, "gridboss")

    unassigned = [item for item in inverters if item.get("serialNum") not in role_map]

    for item in unassigned:
        serial = item.get("serialNum")
        if not serial:
            continue
        text = " ".join(
            str(item.get(key, ""))
            for key in ("deviceTypeText", "modelText", "deviceName", "name", "remark")
        ).lower()
        if "gridboss" in text and "gridboss" not in role_map.values():
            role_map[serial] = "gridboss"
        elif "master" in text and "flexboss_master" not in role_map.values():
            role_map[serial] = "flexboss_master"
        elif "slave" in text and "flexboss_slave" not in role_map.values():
            role_map[serial] = "flexboss_slave"

    remaining = [
        str(item["serialNum"])
        for item in inverters
        if item.get("serialNum") and item.get("serialNum") not in role_map
    ]
    flex_like = []
    for item in inverters:
        serial = item.get("serialNum")
        if not serial or serial in role_map:
            continue
        text = " ".join(
            str(item.get(key, ""))
            for key in ("deviceTypeText", "modelText", "deviceName", "name")
        ).lower()
        if "flexboss" in text:
            flex_like.append(serial)

    if "flexboss_master" not in role_map.values():
        candidate = flex_like[0] if flex_like else (remaining[0] if remaining else None)
        if candidate:
            role_map[candidate] = "flexboss_master"

    remaining = [
        str(item["serialNum"])
        for item in inverters
        if item.get("serialNum") and item.get("serialNum") not in role_map
    ]
    if "flexboss_slave" not in role_map.values() and remaining:
        role_map[remaining[0]] = "flexboss_slave"

    remaining = [
        str(item["serialNum"])
        for item in inverters
        if item.get("serialNum") and item.get("serialNum") not in role_map
    ]
    if "gridboss" not in role_map.values() and remaining:
        role_map[remaining[0]] = "gridboss"

    for serial in serials:
        role_map.setdefault(serial, "unknown")

    return role_map


def _role_map_from_parallel_details(details: dict[str, Any]) -> dict[str, str]:
    """Map EG4 parallel-group role details into integration roles."""
    mapped: dict[str, str] = {}
    devices = details.get("devices", [])
    if not isinstance(devices, list):
        return mapped

    for device in devices:
        if not isinstance(device, dict):
            continue
        serial = device.get("serialNum")
        if not serial:
            continue
        serial = str(serial)

        role_text = str(device.get("roleText", "")).lower()
        device_type = device.get("deviceType")

        if device_type == 9 or "midbox" in role_text or "grid" in role_text:
            mapped[serial] = "gridboss"
        elif "primary" in role_text or "master" in role_text:
            mapped[serial] = "flexboss_master"
        elif (
            "subordinate" in role_text
            or "subordinates" in role_text
            or "slave" in role_text
        ):
            mapped[serial] = "flexboss_slave"

    return mapped


def _merge_role_map(
    base: dict[str, str],
    discovered: dict[str, str],
    locked_serials: set[str],
) -> dict[str, str]:
    """Merge discovered roles, preserving user-locked serial assignments."""
    merged = dict(base)
    for serial, role in discovered.items():
        if serial in locked_serials:
            continue
        merged[serial] = role
    return merged


def _safe_get(
    data: dict[str, Any] | None, path: tuple[str, ...]
) -> float | int | str | bool | None:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _norm_zero(value: float) -> float:
    """Normalize tiny signed-zero float artifacts to 0.0."""
    return 0.0 if abs(value) < 0.005 else value


def _float_or_none(value: Any) -> float | None:
    """Convert a possibly malformed portal value to a float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply_local_telemetry_snapshot(
    aggregate: dict[str, Any],
    snapshots: dict[str, EG4LocalTelemetry],
) -> None:
    """Attach local dongle telemetry and promote only BMS-backed SOC.

    The packed SOC/SOH register is accepted as automation-grade only when the
    same read also returns a structurally valid BMS summary (pack count and
    cell-voltage range). Without that corroboration, retain the inverter value
    under explicit ``Local*`` diagnostic keys and leave canonical SOC guarded.
    """
    local = aggregate.setdefault("LocalTelemetry", {})
    by_role: dict[str, dict[str, Any]] = {}
    for role, snapshot in snapshots.items():
        by_role[role] = {
            "Available": True,
            "Host": snapshot.host,
            "Port": snapshot.port,
            "DongleSerial": snapshot.dongle_serial,
            "InverterSerial": snapshot.inverter_serial,
            "BatteryVoltage": snapshot.battery_voltage,
            "BatterySOC": snapshot.battery_soc,
            "BatterySOH": snapshot.battery_soh,
            "SOCSource": (
                "local_bms"
                if snapshot.bms_available and snapshot.battery_soc is not None
                else "local_inverter_unverified"
            ),
            "SOCQuality": (
                "bms_reported"
                if snapshot.bms_available and snapshot.battery_soc is not None
                else "diagnostic_only"
            ),
            "ChargePower": snapshot.charge_power,
            "DischargePower": snapshot.discharge_power,
            "Power": (
                None
                if snapshot.charge_power is None
                or snapshot.discharge_power is None
                else snapshot.charge_power - snapshot.discharge_power
            ),
            "BMSAvailable": snapshot.bms_available,
            "BMSParallelCount": snapshot.bms_parallel_count,
            "BMSCapacityAh": snapshot.bms_capacity_ah,
            "BMSCurrent": snapshot.bms_current,
            "BMSMaxCellVoltage": snapshot.bms_max_cell_voltage,
            "BMSMinCellVoltage": snapshot.bms_min_cell_voltage,
            "BMSMaxCellTemperature": snapshot.bms_max_cell_temperature,
            "BMSMinCellTemperature": snapshot.bms_min_cell_temperature,
            "BMSCycleCount": snapshot.bms_cycle_count,
            "GridVoltage": snapshot.grid_voltage,
            "GridFrequency": snapshot.grid_frequency,
            "InverterPower": snapshot.inverter_power,
            "RectifierPower": snapshot.rectifier_power,
            "EPSVoltage": snapshot.eps_voltage_r,
            "EPSFrequency": snapshot.eps_frequency,
            "EPSPower": snapshot.eps_power,
        }

    local["ByRole"] = by_role
    local["Count"] = len(by_role)
    local["Available"] = bool(by_role)
    local["BMSAvailable"] = any(
        entry["BMSAvailable"] for entry in by_role.values()
    )
    local["BMSRole"] = None

    # Prefer a BMS-backed source, with the master preferred when both are
    # populated. Fall back to the master/slave inverter estimate only when no
    # local BMS range is available.
    selected: tuple[str, dict[str, Any]] | None = None
    for require_bms in (True, False):
        for role in ("flexboss_master", "flexboss_slave"):
            entry = by_role.get(role)
            if entry and (
                not require_bms
                or (entry["BMSAvailable"] and entry["BatterySOC"] is not None)
            ):
                selected = role, entry
                break
        if selected is not None:
            break
    if selected is None and by_role:
        role, entry = next(iter(by_role.items()))
        selected = role, entry

    battery = aggregate.setdefault("Battery", {})
    if selected is None:
        battery["LocalBMSAvailable"] = False
        return

    role, entry = selected
    local["ReferenceRole"] = role
    local["BMSRole"] = role if entry["BMSAvailable"] else None
    battery["LocalVoltage"] = entry["BatteryVoltage"]
    battery["LocalSOC"] = entry["BatterySOC"]
    battery["LocalSOH"] = entry["BatterySOH"]
    battery["LocalSOCSource"] = "local_inverter_unverified"
    battery["LocalSOCQuality"] = "diagnostic_only"
    battery["LocalBMSSOC"] = (
        entry["BatterySOC"] if entry["BMSAvailable"] else None
    )
    battery["LocalBMSSource"] = (
        "local_bms" if entry["BMSAvailable"] else None
    )
    battery["LocalBMSQuality"] = (
        "bms_reported" if entry["BMSAvailable"] else None
    )
    battery["LocalDongleSerial"] = entry["DongleSerial"]
    battery["LocalInverterSerial"] = entry["InverterSerial"]
    charge_values = [
        float(item["ChargePower"])
        for item in by_role.values()
        if item["ChargePower"] is not None
    ]
    discharge_values = [
        float(item["DischargePower"])
        for item in by_role.values()
        if item["DischargePower"] is not None
    ]
    total_charge = sum(charge_values) if charge_values else None
    total_discharge = sum(discharge_values) if discharge_values else None
    battery["LocalPower"] = (
        None
        if total_charge is None or total_discharge is None
        else total_charge - total_discharge
    )
    battery["LocalChargePower"] = total_charge
    battery["LocalDischargePower"] = total_discharge
    battery["LocalBMSAvailable"] = entry["BMSAvailable"]
    battery["LocalBMSRole"] = role if entry["BMSAvailable"] else None
    battery["LocalBMSParallelCount"] = entry["BMSParallelCount"]
    battery["LocalBMSCapacityAh"] = entry["BMSCapacityAh"]
    battery["LocalBMSCurrent"] = entry["BMSCurrent"]
    battery["LocalBMSMaxCellVoltage"] = entry["BMSMaxCellVoltage"]
    battery["LocalBMSMinCellVoltage"] = entry["BMSMinCellVoltage"]
    battery["LocalBMSMaxCellTemperature"] = entry["BMSMaxCellTemperature"]
    battery["LocalBMSMinCellTemperature"] = entry["BMSMinCellTemperature"]
    battery["LocalBMSCycleCount"] = entry["BMSCycleCount"]

    if entry["BMSAvailable"] and entry["BatterySOC"] is not None:
        battery["SOC"] = entry["BatterySOC"]
        battery["SOCSource"] = "local_bms"
        battery["SOCQuality"] = "bms_reported"
        battery["SOCUnavailableReason"] = None
        try:
            capacity_kwh = float(battery.get("TotalCapacityKWh"))
            battery["RemainingKWh"] = round(
                capacity_kwh * float(entry["BatterySOC"]) / 100.0, 3
            )
        except (TypeError, ValueError):
            battery["RemainingKWh"] = None


def _apply_battery_data_quality_guards(aggregate: dict[str, Any]) -> None:
    """Prevent unverified portal battery fields from driving HA decisions."""
    battery = aggregate.get("Battery")
    if not isinstance(battery, dict):
        return

    battery_type = str(battery.get("Type") or "").strip().upper()
    if battery_type in {"LEAD_ACID", "LEAD-ACID", "LEAD ACID"}:
        portal_soc = battery.get("PortalSOC")
        if portal_soc is None and battery.get("SOCSource") != "local_bms":
            portal_soc = battery.get("SOC")
        if portal_soc is not None:
            battery["PortalSOC"] = portal_soc
            battery["PortalSOCSource"] = "cloud_unverified"
            battery["PortalSOCQuality"] = "diagnostic_only"
        if battery.get("SOCSource") != "local_bms":
            battery["SOC"] = None
            battery["RemainingKWh"] = None
            battery["SOCSource"] = "cloud_unverified"
            battery["SOCQuality"] = "unavailable"
            battery["SOCUnavailableReason"] = (
                "portal_reports_lead_acid_without_bms_soc"
            )

    power = _float_or_none(battery.get("Power"))
    voltage = _float_or_none(battery.get("Voltage"))
    current = _float_or_none(battery.get("Current"))
    if power is not None and voltage is not None and voltage > 0 and abs(power) > 100:
        expected_current = power / voltage
        if current is None or abs(current - expected_current) > max(
            5.0, abs(expected_current) * 0.5
        ):
            if current is not None:
                battery["PortalCurrent"] = current
            battery["Current"] = round(expected_current, 2)
            battery["CurrentSource"] = "power_voltage"
    elif battery_type in {"LEAD_ACID", "LEAD-ACID", "LEAD ACID"}:
        if current is not None:
            battery["PortalCurrent"] = current
        battery["Current"] = None
        battery["CurrentSource"] = "unavailable"


def _get_device_by_role(
    device_data: dict[str, dict[str, Any]],
    role_map: dict[str, str],
    preferred_roles: tuple[str, ...],
) -> tuple[str | None, dict[str, Any] | None]:
    for role in preferred_roles:
        for serial, assigned_role in role_map.items():
            if assigned_role == role and serial in device_data:
                return serial, device_data[serial]

    for serial, data in device_data.items():
        return serial, data

    return None, None


def _build_aggregate(
    device_data: dict[str, dict[str, Any]],
    role_map: dict[str, str],
) -> dict[str, Any]:
    battery_serial, battery_source = _get_device_by_role(
        device_data,
        role_map,
        ("flexboss_master", "gridboss", "flexboss_slave", "unknown"),
    )

    site_serial, site_source = _get_device_by_role(
        device_data,
        role_map,
        ("gridboss", "flexboss_master", "flexboss_slave", "unknown"),
    )

    def _is_placeholder_site(data: dict[str, Any] | None) -> bool:
        if not data:
            return True
        voltage = _safe_get(data, ("Grid", "VoltageR"))
        freq = _safe_get(data, ("Grid", "Frequency"))
        load_power = _safe_get(data, ("Load", "Power"))
        device_time = data.get("DeviceTime")
        if isinstance(device_time, str) and device_time not in ("", "Unknown"):
            return False
        numeric = []
        for value in (voltage, freq, load_power):
            if value is None:
                continue
            try:
                numeric.append(float(value))
            except (TypeError, ValueError):
                continue
        return not any(v > 0 for v in numeric)

    if (
        site_serial
        and role_map.get(site_serial) == "gridboss"
        and _is_placeholder_site(site_source)
    ):
        fallback_serial, fallback_source = _get_device_by_role(
            device_data,
            role_map,
            ("flexboss_master", "flexboss_slave", "unknown", "gridboss"),
        )
        if fallback_serial and fallback_source:
            site_serial, site_source = fallback_serial, fallback_source

    def _energy_get(section: str, metric: str) -> Any:
        """Prefer site energy counters, filling gaps from the battery source."""
        for source in (site_source, battery_source):
            value = _safe_get(source, ("Energy", section, metric))
            if value is not None:
                return value
        return None

    aggregate: dict[str, Any] = {
        "source_serials": {
            "battery": battery_serial,
            "site": site_serial,
        },
        "device_count": len(device_data),
        "Battery": {
            "SOC": _safe_get(battery_source, ("Battery", "SOC")),
            "Power": _safe_get(battery_source, ("Battery", "Power")),
            "Voltage": _safe_get(battery_source, ("Battery", "Voltage")),
            "Current": _safe_get(battery_source, ("Battery", "Current")),
            "Temperature": _safe_get(battery_source, ("Battery", "Temperature")),
            "Type": _safe_get(battery_source, ("Battery", "Type")),
            "ParallelNum": _safe_get(battery_source, ("Battery", "ParallelNum")),
            "CapacityAh": _safe_get(battery_source, ("Battery", "CapacityAh")),
            "TotalCapacityKWh": _safe_get(
                battery_source, ("Battery", "TotalCapacityKWh")
            ),
            "RemainingKWh": _safe_get(battery_source, ("Battery", "RemainingKWh")),
            "BMS": _safe_get(battery_source, ("Battery", "BMS")),
        },
        "Solar": {
            "TotalPower": _safe_get(site_source, ("Solar", "TotalPower")),
            "PV1": _safe_get(site_source, ("Solar", "PV1", "Power")),
            "PV1Voltage": _safe_get(site_source, ("Solar", "PV1", "Voltage")),
            "PV1Current": _safe_get(site_source, ("Solar", "PV1", "Current")),
            "PV2": _safe_get(site_source, ("Solar", "PV2", "Power")),
            "PV2Voltage": _safe_get(site_source, ("Solar", "PV2", "Voltage")),
            "PV2Current": _safe_get(site_source, ("Solar", "PV2", "Current")),
            "PV3": _safe_get(site_source, ("Solar", "PV3", "Power")),
            "PV3Voltage": _safe_get(site_source, ("Solar", "PV3", "Voltage")),
            "PV3Current": _safe_get(site_source, ("Solar", "PV3", "Current")),
        },
        "Grid": {
            "PowerNet": _safe_get(site_source, ("Grid", "PowerNet")),
            "Voltage": _safe_get(site_source, ("Grid", "VoltageR")),
            "Frequency": _safe_get(site_source, ("Grid", "Frequency")),
            "PowerImport": _safe_get(site_source, ("Grid", "PowerImport")),
            "PowerExport": _safe_get(site_source, ("Grid", "PowerExport")),
        },
        "Load": {
            "Power": _safe_get(site_source, ("Load", "Power")),
        },
        "EPS": {
            "Power": _safe_get(site_source, ("EPS", "Power")),
            "Voltage": _safe_get(site_source, ("EPS", "Voltage")),
            "Frequency": _safe_get(site_source, ("EPS", "Frequency")),
        },
        "Generator": {
            "Voltage": _safe_get(site_source, ("Generator", "Voltage")),
            "Frequency": _safe_get(site_source, ("Generator", "Frequency")),
            "Power": _safe_get(site_source, ("Generator", "Power")),
            "RunStatus": _safe_get(site_source, ("Generator", "RunStatus")),
        },
        "Device": {
            "SerialNumber": _safe_get(site_source, ("SerialNumber",)),
            "Status": _safe_get(site_source, ("StatusText",)),
            "StatusCode": _safe_get(site_source, ("StatusCode",)),
            "Lost": _safe_get(site_source, ("Lost",)),
            "Firmware": _safe_get(site_source, ("FirmwareCode",)),
            "Model": _safe_get(site_source, ("Model",)),
            "ModelText": _safe_get(site_source, ("ModelText",)),
            "PowerRating": _safe_get(site_source, ("PowerRatingText",)),
            "DeviceTime": _safe_get(site_source, ("DeviceTime",)),
            "ServerTime": _safe_get(site_source, ("ServerTime",)),
        },
        "Temperatures": {
            "InverterInner": _safe_get(
                site_source, ("Temperatures", "InverterInner")
            ),
            "Radiator1": _safe_get(site_source, ("Temperatures", "Radiator1")),
            "Radiator2": _safe_get(site_source, ("Temperatures", "Radiator2")),
            "Environment": _safe_get(
                site_source, ("Temperatures", "Environment")
            ),
        },
        "InverterDetails": {
            "ACPower": _safe_get(site_source, ("InverterDetails", "ACPower")),
            "RectifierPower": _safe_get(
                site_source, ("InverterDetails", "RectifierPower")
            ),
            "VBus1": _safe_get(site_source, ("InverterDetails", "VBus1")),
            "VBus2": _safe_get(site_source, ("InverterDetails", "VBus2")),
            "IsParallelEnabled": _safe_get(
                site_source, ("InverterDetails", "IsParallelEnabled")
            ),
        },
        "ACCouple": {
            "Power": _safe_get(site_source, ("ACCouple", "Power")),
            "InverterFlow": _safe_get(
                site_source, ("ACCouple", "InverterFlow")
            ),
            "InverterData": _safe_get(
                site_source, ("ACCouple", "InverterData")
            ),
        },
        "SmartLoad": {
            "Power": _safe_get(site_source, ("SmartLoad", "Power")),
            "Enabled": _safe_get(site_source, ("SmartLoad", "Enabled")),
            "InverterFlow": _safe_get(
                site_source, ("SmartLoad", "InverterFlow")
            ),
        },
        "Energy": {
            "Today": {
                "Solar Generation": _energy_get("Today", "Solar Generation"),
                "Usage": _energy_get("Today", "Usage"),
                "Grid Import": _energy_get("Today", "Grid Import"),
                "Grid Export": _energy_get("Today", "Grid Export"),
                "Battery Charge": _energy_get("Today", "Battery Charge"),
                "Battery Discharge": _energy_get("Today", "Battery Discharge"),
            },
            "Total": {
                "Solar Generation": _energy_get("Total", "Solar Generation"),
                "Usage": _energy_get("Total", "Usage"),
                "Grid Import": _energy_get("Total", "Grid Import"),
                "Grid Export": _energy_get("Total", "Grid Export"),
                "Battery Charge": _energy_get("Total", "Battery Charge"),
                "Battery Discharge": _energy_get("Total", "Battery Discharge"),
            },
            "Environmental": {
                "CO2 Reduction": _energy_get("Environmental", "CO2 Reduction"),
                "Coal Reduction": _energy_get("Environmental", "Coal Reduction"),
            },
        },
    }

    # Solar total strategy:
    # - If GridBOSS is the active site source and reports a total, trust it.
    # - Otherwise, derive by summing all available device solar totals.
    has_gridboss = site_serial is not None and role_map.get(site_serial) == "gridboss"
    reported_total = aggregate["Solar"]["TotalPower"]

    if not has_gridboss or reported_total is None:
        total = 0.0
        found = False
        for data in device_data.values():
            value = _safe_get(data, ("Solar", "TotalPower"))
            if value is None:
                continue
            try:
                total += float(value)
                found = True
            except (TypeError, ValueError):
                continue
        if found:
            aggregate["Solar"]["TotalPower"] = round(total, 2)

    if not has_gridboss:

        def _sum_path(path: tuple[str, ...]) -> float | None:
            total = 0.0
            found = False
            for data in device_data.values():
                value = _safe_get(data, path)
                if value is None:
                    continue
                try:
                    total += float(value)
                    found = True
                except (TypeError, ValueError):
                    continue
            return round(total, 2) if found else None

        aggregate["Grid"]["PowerImport"] = _sum_path(("Grid", "PowerImport"))
        aggregate["Grid"]["PowerExport"] = _sum_path(("Grid", "PowerExport"))
        aggregate["Grid"]["PowerNet"] = _sum_path(("Grid", "PowerNet"))
        aggregate["Load"]["Power"] = _sum_path(("Load", "Power"))

    # Derive explicit battery charge/discharge power from net battery power.
    battery_power = aggregate.get("Battery", {}).get("Power")

    try:
        bp = float(battery_power) if battery_power is not None else 0.0
        aggregate["Battery"]["ChargePower"] = _norm_zero(round(max(bp, 0.0), 2))
        aggregate["Battery"]["DischargePower"] = _norm_zero(round(max(-bp, 0.0), 2))
        if bp > 10:
            aggregate["Battery"]["Direction"] = "Charging"
        elif bp < -10:
            aggregate["Battery"]["Direction"] = "Discharging"
        else:
            aggregate["Battery"]["Direction"] = "Idle"
    except (TypeError, ValueError):
        aggregate["Battery"]["ChargePower"] = None
        aggregate["Battery"]["DischargePower"] = None
        aggregate["Battery"]["Direction"] = None

    # Derive remaining battery energy if total capacity and SOC are known.
    try:
        cap = aggregate["Battery"].get("TotalCapacityKWh")
        soc = aggregate["Battery"].get("SOC")
        if cap is not None and soc is not None:
            cap_f = float(cap)
            soc_f = float(soc)
            aggregate["Battery"]["RemainingKWh"] = round(cap_f * soc_f / 100.0, 3)
    except (TypeError, ValueError):
        pass

    return aggregate


def _apply_battery_info_snapshot(
    aggregate: dict[str, Any],
    battery_info: dict[str, Any] | None,
    parallel_info: dict[str, Any] | None = None,
) -> None:
    """Overlay verified battery endpoint fields without replacing runtime power."""
    normalized = _normalize_battery_info(battery_info or {})
    battery = aggregate.setdefault("Battery", {})

    if normalized is not None:
        packs = _extract_battery_pack_voltages(battery_info or {})
        if packs:
            battery["PackVoltages"] = packs

        for key in ("Status", "TotalVoltage", "TotalNumber", "CycleCount"):
            value = normalized.get(key)
            if value is not None:
                battery[key] = value

        # Runtime and GridBoss overlays are the authoritative live power values.
        # Keep portal battery values separately so their different sampling
        # semantics are visible without silently changing existing power sensors.
        for key in (
            "Power",
            "ChargePower",
            "DischargePower",
            "SOC",
            "Voltage",
            "Current",
        ):
            value = normalized.get(key)
            if value is not None:
                battery[f"Portal{key}"] = value
                if battery.get(key) is None:
                    battery[key] = value

        for key in ("MaxCellTemperature", "MinCellTemperature"):
            value = normalized.get(key)
            if value is not None:
                battery[key] = value

    if parallel_info and parallel_info.get("success") is True:
        device_array = next(
            (
                parallel_info.get(key)
                for key in ("deviceArray", "batteryArray", "battery_units")
                if isinstance(parallel_info.get(key), list)
            ),
            None,
        )
        if isinstance(device_array, list):
            battery["ParallelDeviceCount"] = len(device_array)
            battery["ParallelDevices"] = [
                {
                    key: value
                    for key, value in device.items()
                    if key not in {"serialNum", "serial", "sn"}
                }
                for device in device_array
                if isinstance(device, dict)
            ]


def _apply_parallel_day_snapshot(
    aggregate: dict[str, Any],
    chart_data: dict[str, Any] | None,
) -> None:
    """Overlay aggregate with latest parallel day-chart values when available."""
    if not chart_data:
        return
    points = chart_data.get("data")
    if not isinstance(points, list) or not points:
        return

    latest = None
    for point in reversed(points):
        if isinstance(point, dict):
            latest = point
            break
    if latest is None:
        return

    grid_power = latest.get("gridPower")
    consumption = latest.get("consumption")
    battery_discharging = latest.get("batteryDischarging")
    solar_pv = latest.get("solarPv")
    soc = latest.get("soc")

    try:
        if grid_power is not None:
            # EG4 chart uses negative for import, positive for export.
            gp = float(grid_power)
            if gp < 0:
                aggregate["Grid"]["PowerImport"] = abs(gp)
                aggregate["Grid"]["PowerExport"] = 0.0
                aggregate["Grid"]["PowerNet"] = abs(gp)
            else:
                aggregate["Grid"]["PowerImport"] = 0.0
                aggregate["Grid"]["PowerExport"] = gp
                aggregate["Grid"]["PowerNet"] = -gp
    except (TypeError, ValueError):
        pass

    try:
        if consumption is not None:
            aggregate["Load"]["Power"] = float(consumption)
    except (TypeError, ValueError):
        pass

    try:
        if solar_pv is not None:
            aggregate["Solar"]["TotalPower"] = float(solar_pv)
    except (TypeError, ValueError):
        pass

    try:
        if battery_discharging is not None:
            discharge_power = abs(float(battery_discharging))
            aggregate["Battery"]["Power"] = -discharge_power
            aggregate["Battery"]["ChargePower"] = 0.0
            aggregate["Battery"]["DischargePower"] = discharge_power
            aggregate["Battery"]["Direction"] = (
                "Discharging" if discharge_power > 10 else "Idle"
            )
    except (TypeError, ValueError):
        pass

    try:
        if soc is not None:
            aggregate["Battery"]["SOC"] = float(soc)
    except (TypeError, ValueError):
        pass

    aggregate["source_serials"]["site_chart"] = "dayMultiLineParallel"
    aggregate["chart_latest_time"] = latest.get("time")


def _apply_midbox_runtime_snapshot(
    aggregate: dict[str, Any],
    midbox_runtime: dict[str, Any] | None,
) -> None:
    """Overlay aggregate with GridBoss runtime values when available."""
    if not midbox_runtime or not midbox_runtime.get("success"):
        return

    midbox = midbox_runtime.get("midboxData")
    device = midbox_runtime.get("deviceData")
    if not isinstance(midbox, dict):
        midbox = {}
    if not isinstance(device, dict):
        device = {}

    energy = aggregate.setdefault("Energy", {})
    energy_today = energy.setdefault("Today", {})
    energy_total = energy.setdefault("Total", {})

    # Prefer deviceData sign-convention fields for power flows.
    grid_power = device.get("gridPower")
    load_power = device.get("pLoad") or device.get("pLoad170")
    soc = device.get("soc")
    bat_power = device.get("batPower")
    bat_capacity = device.get("batCapacity")
    using_generator = device.get("_12KUsingGenerator")
    gen_dry_contact = str(device.get("genDryContact", "")).upper()

    try:
        if grid_power is not None:
            gp = float(grid_power)
            if gp < 0:
                aggregate["Grid"]["PowerImport"] = abs(gp)
                aggregate["Grid"]["PowerExport"] = 0.0
                aggregate["Grid"]["PowerNet"] = abs(gp)
            else:
                aggregate["Grid"]["PowerImport"] = 0.0
                aggregate["Grid"]["PowerExport"] = gp
                aggregate["Grid"]["PowerNet"] = -gp
    except (TypeError, ValueError):
        pass

    try:
        if load_power is not None:
            aggregate["Load"]["Power"] = float(load_power)
    except (TypeError, ValueError):
        pass

    # Voltage/frequency live under midboxData with scale factors.
    try:
        grid_rms = midbox.get("gridRmsVolt")
        if grid_rms is not None:
            aggregate["Grid"]["Voltage"] = float(grid_rms) / 10.0
    except (TypeError, ValueError):
        pass

    try:
        grid_freq = midbox.get("gridFreq")
        if grid_freq is not None:
            aggregate["Grid"]["Frequency"] = float(grid_freq) / 100.0
    except (TypeError, ValueError):
        pass

    try:
        ups_rms = midbox.get("upsRmsVolt")
        if ups_rms is not None:
            aggregate["EPS"]["Voltage"] = float(ups_rms) / 10.0
    except (TypeError, ValueError):
        pass

    try:
        if soc is not None:
            aggregate["Battery"]["SOC"] = float(soc)
    except (TypeError, ValueError):
        pass

    try:
        if bat_power is not None:
            bp = float(bat_power)
            aggregate["Battery"]["Power"] = bp
            aggregate["Battery"]["ChargePower"] = _norm_zero(round(max(bp, 0.0), 2))
            aggregate["Battery"]["DischargePower"] = _norm_zero(round(max(-bp, 0.0), 2))
            if bp > 10:
                aggregate["Battery"]["Direction"] = "Charging"
            elif bp < -10:
                aggregate["Battery"]["Direction"] = "Discharging"
            else:
                aggregate["Battery"]["Direction"] = "Idle"
    except (TypeError, ValueError):
        pass

    if aggregate.get("Battery", {}).get("Direction") is None:
        try:
            bp = float(aggregate.get("Battery", {}).get("Power") or 0.0)
            if bp > 10:
                aggregate["Battery"]["Direction"] = "Charging"
            elif bp < -10:
                aggregate["Battery"]["Direction"] = "Discharging"
            else:
                aggregate["Battery"]["Direction"] = "Idle"
        except (TypeError, ValueError):
            pass

    # Battery capacity from deviceData appears to be in 0.1kWh units.
    try:
        if bat_capacity is not None:
            cap = float(bat_capacity) / 10.0
            aggregate["Battery"]["TotalCapacityKWh"] = round(cap, 3)
            soc_value = aggregate["Battery"].get("SOC")
            if soc_value is not None:
                aggregate["Battery"]["RemainingKWh"] = round(
                    cap * float(soc_value) / 100.0, 3
                )
    except (TypeError, ValueError):
        pass

    # Midbox energy counters are in 0.1 kWh units.
    def _energy_01(*keys: str) -> float | None:
        total = 0.0
        found = False
        for key in keys:
            value = midbox.get(key)
            if value is None:
                continue
            try:
                total += float(value)
                found = True
            except (TypeError, ValueError):
                continue
        if not found:
            return None
        return round(total / 10.0, 3)

    today_export = _energy_01("eToGridTodayL1", "eToGridTodayL2")
    total_export = _energy_01("eToGridTotalL1", "eToGridTotalL2")
    today_usage = _energy_01("eToUserTodayL1", "eToUserTodayL2")
    total_usage = _energy_01("eToUserTotalL1", "eToUserTotalL2")
    today_ups = _energy_01("eUpsTodayL1", "eUpsTodayL2")
    total_ups = _energy_01("eUpsTotalL1", "eUpsTotalL2")
    # Best available proxy for solar yield on GridBoss runtime payload.
    today_solar = _energy_01("eLoadTodayL1", "eLoadTodayL2")
    total_solar = _energy_01("eLoadTotalL1", "eLoadTotalL2")

    if today_export is not None and energy_today.get("Grid Export") is None:
        energy_today["Grid Export"] = today_export
    if total_export is not None and energy_total.get("Grid Export") is None:
        energy_total["Grid Export"] = total_export
    if today_usage is not None and energy_today.get("Usage") is None:
        energy_today["Usage"] = today_usage
        # legacy compatibility alias
        energy_today["Grid Import"] = today_usage
    if total_usage is not None and energy_total.get("Usage") is None:
        energy_total["Usage"] = total_usage
        # legacy compatibility alias
        energy_total["Grid Import"] = total_usage
    if today_ups is not None and energy_today.get("UPS Usage") is None:
        energy_today["UPS Usage"] = today_ups
    if total_ups is not None and energy_total.get("UPS Usage") is None:
        energy_total["UPS Usage"] = total_ups
    if today_solar is not None and energy_today.get("Solar Generation") is None:
        energy_today["Solar Generation"] = today_solar
    if total_solar is not None and energy_total.get("Solar Generation") is None:
        energy_total["Solar Generation"] = total_solar

    # Generator values can be sourced from midboxData and deviceData.
    gen_power = None
    try:
        gen_l1 = float(midbox.get("genL1ActivePower") or 0.0)
        gen_l2 = float(midbox.get("genL2ActivePower") or 0.0)
        gen_power = gen_l1 + gen_l2
        aggregate["Generator"]["Power"] = gen_power
    except (TypeError, ValueError):
        pass

    try:
        gen_rms = midbox.get("genRmsVolt")
        if gen_rms is not None:
            aggregate["Generator"]["Voltage"] = float(gen_rms) / 10.0
    except (TypeError, ValueError):
        pass

    try:
        gen_freq = midbox.get("genFreq")
        if gen_freq is not None:
            aggregate["Generator"]["Frequency"] = float(gen_freq) / 100.0
    except (TypeError, ValueError):
        pass

    gen_running = False
    gen_status_source = "none"
    if using_generator is True or gen_dry_contact == "ON":
        gen_running = True
        if using_generator is True:
            gen_status_source = "using_generator_flag"
        else:
            gen_status_source = "dry_contact"
    else:
        try:
            gv = float(aggregate["Generator"].get("Voltage") or 0.0)
            gf = float(aggregate["Generator"].get("Frequency") or 0.0)
            gp = float(aggregate["Generator"].get("Power") or 0.0)
            if gp > 100 or (gv > 80 and gf > 45):
                gen_running = True
                gen_status_source = "measured_power_or_signal"
        except (TypeError, ValueError):
            gen_running = False

    aggregate["Generator"]["RunStatus"] = gen_running
    aggregate["Generator"]["SupplyingPower"] = bool(
        (aggregate["Generator"].get("Power") or 0.0)
        and float(aggregate["Generator"].get("Power") or 0.0) > 100
    )
    aggregate["Generator"]["UsingGeneratorFlag"] = bool(using_generator)
    aggregate["Generator"]["DryContact"] = gen_dry_contact or None
    aggregate["Generator"]["StatusSource"] = gen_status_source

    aggregate["source_serials"]["site_midbox"] = "getMidboxRuntime"
    aggregate["midbox_device_time"] = midbox.get("deviceTime")
    aggregate["midbox_server_time"] = midbox.get("serverTime")


def _apply_event_snapshot(
    aggregate: dict[str, Any],
    event_payload: dict[str, Any] | None,
    tracked_serials: set[str] | None = None,
) -> None:
    """Extract open/uncleared event information into aggregate data."""
    events = {
        "OpenCount": 0,
        "HasOpen": False,
        "OpenSummary": "none",
        "OpenTop": [],
    }

    if not event_payload or not event_payload.get("success"):
        aggregate["Events"] = events
        return

    rows = event_payload.get("rows")
    if not isinstance(rows, list):
        aggregate["Events"] = events
        return

    open_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        serial = row.get("serialNum")
        if tracked_serials and serial and str(serial) not in tracked_serials:
            continue
        status = str(row.get("status", "")).upper()
        if status == "CLOSE":
            continue
        open_rows.append(row)

    if not open_rows:
        aggregate["Events"] = events
        return

    top = []
    for row in open_rows[:5]:
        top.append(
            {
                "record_id": row.get("recordId"),
                "serial": row.get("serialNum"),
                "event_code": row.get("event"),
                "event_text": row.get("eventText"),
                "event_type": row.get("eventType"),
                "start_time": row.get("startTime"),
                "status": row.get("status"),
            }
        )

    first = open_rows[0]
    summary = f"{first.get('eventText', 'Unknown')} ({first.get('event', 'N/A')})"

    events = {
        "OpenCount": len(open_rows),
        "HasOpen": True,
        "OpenSummary": summary,
        "OpenTop": top,
    }
    aggregate["Events"] = events


def _apply_grid_quality_snapshot(
    aggregate: dict[str, Any],
    samples: list[GridQualitySample],
    sample_ts: datetime,
    max_sample_gap_seconds: int = DEFAULT_MAX_SAMPLE_GAP_SECONDS,
    record_sample: bool = True,
) -> None:
    """Add rolling grid-quality metrics without sampling stale runtime data."""
    if record_sample:
        snapshot = append_grid_quality_sample(
            samples=samples,
            now=sample_ts,
            voltage=_safe_get(aggregate, ("Grid", "Voltage")),
            max_sample_gap_seconds=max_sample_gap_seconds,
        )
    else:
        snapshot = build_grid_quality_snapshot(
            samples=samples,
            now=sample_ts,
            max_sample_gap_seconds=max_sample_gap_seconds,
        )
    snapshot["SampleRecorded"] = record_sample
    aggregate["GridQuality"] = snapshot


def _add_discovered_local_dongles(
    local_dongles: dict[str, EG4LocalDongle],
    identities: tuple[EG4LocalDongleIdentity, ...],
    role_map: dict[str, str],
    *,
    port: int,
    timeout: float,
    source: str,
    discovered: list[dict[str, Any]],
) -> None:
    """Assign discovered local identities to unconfigured FlexBOSS roles."""
    for identity in identities:
        role = role_map.get(identity.inverter_serial)
        if role not in {"flexboss_master", "flexboss_slave"}:
            # GridBOSS and unknown dongles are intentionally not used for
            # battery local telemetry.
            continue
        if role in local_dongles:
            continue
        local_dongles[role] = EG4LocalDongle(
            host=identity.host,
            inverter_serial=identity.inverter_serial,
            port=port,
            timeout=timeout,
        )
        discovered.append(
            {
                "Source": source,
                "Role": role,
                "Host": identity.host,
                "Port": identity.port,
                "DongleSerial": identity.dongle_serial,
                "InverterSerial": identity.inverter_serial,
            }
        )


async def _async_build_local_dongles(
    hass: HomeAssistant,
    plant_info: dict[str, Any],
    inverters: list[dict[str, Any]],
    role_map: dict[str, str],
    options: dict[str, Any],
    *,
    port: int,
) -> tuple[dict[str, EG4LocalDongle], dict[str, Any]]:
    """Build local dongle clients from overrides, portal IPs, or LAN scan."""
    local_dongles: dict[str, EG4LocalDongle] = {}
    manual_hosts: dict[str, str] = {}
    for role, option_key in (
        ("flexboss_master", CONF_LOCAL_MASTER_HOST),
        ("flexboss_slave", CONF_LOCAL_SLAVE_HOST),
    ):
        host = str(options.get(option_key, "") or "").strip()
        if not host:
            continue
        manual_hosts[role] = host
        local_dongles[role] = EG4LocalDongle(
            host=host,
            port=port,
            timeout=DEFAULT_LOCAL_DONGLE_TIMEOUT,
        )

    reported_ips = extract_ipv4_addresses((plant_info, inverters))
    portal_local_ips = local_ipv4_addresses(reported_ips)
    discovery: dict[str, Any] = {
        "Enabled": bool(
            options.get(CONF_LOCAL_AUTO_DISCOVERY, DEFAULT_LOCAL_AUTO_DISCOVERY)
        ),
        "Source": "manual" if manual_hosts else "none",
        "ManualHosts": dict(manual_hosts),
        "PortalReportedIPs": list(reported_ips),
        "PortalLocalIPs": list(portal_local_ips),
        "IgnoredNonLocalIPs": [
            address for address in reported_ips if address not in portal_local_ips
        ],
        "ScannedNetworks": [],
        "ScannedHostCount": 0,
        "Discovered": [],
        "ProbeResults": [],
    }
    if not discovery["Enabled"]:
        return local_dongles, discovery

    missing_roles = {
        role for role in ("flexboss_master", "flexboss_slave") if role not in local_dongles
    }
    probed_hosts: set[str] = set(manual_hosts.values())
    discovered_sources: set[str] = set()

    async def probe_hosts(hosts: tuple[str, ...], source: str) -> None:
        """Probe one candidate source and assign only known FlexBOSS roles."""
        nonlocal missing_roles
        candidates = tuple(host for host in hosts if host not in probed_hosts)
        if not candidates or not missing_roles:
            return
        probed_hosts.update(candidates)
        discovery["ScannedHostCount"] += len(candidates)
        identities = await async_discover_dongles(
            candidates,
            port=port,
            timeout=DEFAULT_LOCAL_DISCOVERY_TIMEOUT,
            concurrency=DEFAULT_LOCAL_DISCOVERY_CONCURRENCY,
        )
        discovery.setdefault("ProbeResults", []).extend(
            {
                "Host": identity.host,
                "Port": identity.port,
                "DongleSerial": identity.dongle_serial,
                "InverterSerial": identity.inverter_serial,
                "Role": role_map.get(identity.inverter_serial, "unknown"),
                "Source": source,
            }
            for identity in identities
        )
        before = len(local_dongles)
        _add_discovered_local_dongles(
            local_dongles,
            identities,
            role_map,
            port=port,
            timeout=DEFAULT_LOCAL_DONGLE_TIMEOUT,
            source=source,
            discovered=discovery["Discovered"],
        )
        if len(local_dongles) > before:
            discovered_sources.add(source)
        missing_roles = {
            role
            for role in ("flexboss_master", "flexboss_slave")
            if role not in local_dongles
        }

    if portal_local_ips:
        await probe_hosts(portal_local_ips, "portal_reported_ip")

    if missing_roles:
        try:
            adapters = await async_get_adapters(hass)
            networks = local_networks_from_adapters(
                adapters,
                max_hosts=DEFAULT_LOCAL_DISCOVERY_MAX_HOSTS,
            )
            discovery["ScannedNetworks"] = [str(network) for network in networks]
            lan_hosts = hosts_for_networks(
                networks,
                max_hosts=DEFAULT_LOCAL_DISCOVERY_MAX_HOSTS,
            )
            await probe_hosts(lan_hosts, "local_lan_scan")
        except Exception as err:
            discovery["LANScanError"] = f"{type(err).__name__}: {err}"
            _LOGGER.warning("Unable to auto-discover EG4 dongles on local LAN: %s", err)

    if discovered_sources:
        discovery["Source"] = "+".join(sorted(discovered_sources))
    elif manual_hosts:
        discovery["Source"] = "manual"
    return local_dongles, discovery


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the EG4 Monitor component."""
    hass.data[DOMAIN] = {}
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EG4 Monitor from a config entry."""
    _LOGGER.debug("Setting up EG4 Monitor integration for entry ID: %s", entry.entry_id)

    session = async_get_clientsession(hass)
    api = EG4API(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        session=session,
    )

    try:
        if not await api.login():
            _LOGGER.error("Failed to login to EG4")
            return False

        plant_info = await api.get_plant_info()
        if not plant_info or not api.plant_id:
            _LOGGER.error("Failed to fetch plant info or plant_id is missing")
            return False

        inverters = await api.get_all_inverters(plant_id_to_fetch=api.plant_id)
        if not inverters:
            _LOGGER.error("No inverter rows found for plant ID: %s", api.plant_id)
            return False

        locked_serials = {
            str(v)
            for v in (
                entry.options.get(CONF_MASTER_SERIAL),
                entry.options.get(CONF_SLAVE_SERIAL),
                entry.options.get(CONF_GRIDBOSS_SERIAL),
            )
            if v
        }

        role_map = _pick_role_map(
            inverters,
            entry.options.get(CONF_MASTER_SERIAL),
            entry.options.get(CONF_SLAVE_SERIAL),
            entry.options.get(CONF_GRIDBOSS_SERIAL),
        )

        gridboss_serial = next(
            (
                str(item.get("serialNum"))
                for item in inverters
                if item.get("serialNum")
                and "grid boss"
                in str(item.get("deviceTypeText", ""))
                .lower()
                .replace("gridboss", "grid boss")
            ),
            None,
        )
        if not gridboss_serial:
            gridboss_serial = next(
                (serial for serial, role in role_map.items() if role == "gridboss"),
                None,
            )

        if gridboss_serial:
            parallel_details = await api.get_parallel_group_details(gridboss_serial)
            if parallel_details:
                discovered_roles = _role_map_from_parallel_details(parallel_details)
                role_map = _merge_role_map(role_map, discovered_roles, locked_serials)
                _LOGGER.debug(
                    "Applied parallel group roles from %s: %s",
                    gridboss_serial,
                    discovered_roles,
                )

    except Exception as err:
        _LOGGER.error("Error during setup (login, plant, inverter list): %s", err)
        return False

    serials = [str(item["serialNum"]) for item in inverters if item.get("serialNum")]
    inverters_by_serial = {
        str(item["serialNum"]): item for item in inverters if item.get("serialNum")
    }

    try:
        local_dongle_port = int(
            entry.options.get(CONF_LOCAL_DONGLE_PORT, DEFAULT_LOCAL_DONGLE_PORT)
        )
    except (TypeError, ValueError):
        local_dongle_port = DEFAULT_LOCAL_DONGLE_PORT
    local_dongle_port = max(1, min(local_dongle_port, 65535))
    local_dongles, local_discovery = await _async_build_local_dongles(
        hass,
        plant_info,
        inverters,
        role_map,
        dict(entry.options),
        port=local_dongle_port,
    )
    if local_dongles:
        _LOGGER.info(
            "Local EG4 dongles ready for roles %s via %s",
            sorted(local_dongles),
            local_discovery.get("Source"),
        )
    else:
        _LOGGER.info(
            "No local EG4 dongles discovered; source=%s portal_ips=%s",
            local_discovery.get("Source"),
            local_discovery.get("PortalLocalIPs"),
        )

    local_trace_enabled = bool(
        entry.options.get(CONF_LOCAL_TRACE_ENABLED, DEFAULT_LOCAL_TRACE_ENABLED)
    )
    try:
        local_trace_interval = float(
            entry.options.get(CONF_LOCAL_TRACE_INTERVAL, DEFAULT_LOCAL_TRACE_INTERVAL)
        )
    except (TypeError, ValueError):
        local_trace_interval = DEFAULT_LOCAL_TRACE_INTERVAL
    local_trace_interval = max(1.0, min(local_trace_interval, 60.0))
    try:
        local_trace_bms_interval = float(
            entry.options.get(
                CONF_LOCAL_TRACE_BMS_INTERVAL, DEFAULT_LOCAL_TRACE_BMS_INTERVAL
            )
        )
    except (TypeError, ValueError):
        local_trace_bms_interval = DEFAULT_LOCAL_TRACE_BMS_INTERVAL
    local_trace_bms_interval = max(2.0, min(local_trace_bms_interval, 300.0))
    gridboss_host = str(
        entry.options.get(CONF_LOCAL_GRIDBOSS_HOST, DEFAULT_LOCAL_GRIDBOSS_HOST) or ""
    ).strip()
    gridboss_serial = gridboss_serial or entry.options.get(CONF_GRIDBOSS_SERIAL)
    gridboss_dongle = (
        EG4GridBossDongle(
            host=gridboss_host,
            inverter_serial=gridboss_serial,
            port=local_dongle_port,
            timeout=DEFAULT_LOCAL_DONGLE_TIMEOUT,
        )
        if gridboss_host and gridboss_serial
        else None
    )
    local_trace_writer = (
        LocalDongleTraceWriter(
            hass.config.path(".eg4-investigation", "local-dongle", entry.entry_id)
        )
        if local_trace_enabled and (local_dongles or gridboss_dongle is not None)
        else None
    )
    controller_trace_writer = (
        LocalDongleTraceWriter(local_trace_writer.root / "controller-observations")
        if local_trace_writer is not None
        else None
    )
    controller_observations = ControllerObservationTracker()

    async def _record_acquisition_evidence(evidence: dict[str, Any]) -> object:
        """Queue received bytes before decoding, without filesystem IO on HA."""
        if local_trace_writer is None:
            return None
        return await local_trace_writer.async_write(evidence, wait=False)

    if local_trace_writer is not None:
        for role, dongle in (
            *local_dongles.items(),
            *((("gridboss", gridboss_dongle),) if gridboss_dongle is not None else ()),
        ):
            dongle.evidence_sink = _record_acquisition_evidence
            dongle.role = role
            dongle.session_id = local_trace_writer.session_id
        if controller_trace_writer is not None:
            controller_trace_writer.session_id = local_trace_writer.session_id
    logger_store: Store[dict[str, Any]] = Store(
        hass,
        1,
        f"{DOMAIN}.{entry.entry_id}.logger_state",
    )
    telemetry_logger = EG4TelemetryLogger(
        entry.entry_id,
        source_serial=gridboss_serial,
    )
    restored_logger_state = await logger_store.async_load()
    if telemetry_logger.restore(restored_logger_state):
        _LOGGER.info("Restored EG4 logger state for entry %s", entry.entry_id)
    logger_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}_logger",
        update_method=None,
    )
    logger_coordinator.async_set_updated_data(telemetry_logger.snapshot())
    gridboss_snapshot_cache: EG4GridBossTelemetry | None = None
    gridboss_snapshot_time: datetime | None = None
    gridboss_snapshot_sequence: int | None = None
    gridboss_snapshot_error: str | None = None
    gridboss_poll_lock = asyncio.Lock()
    local_snapshot_cache: dict[str, EG4LocalTelemetry] = {}
    local_snapshot_times: dict[str, datetime] = {}
    local_bms_cache: dict[str, EG4LocalBMSData] = {}
    local_bms_times: dict[str, datetime] = {}
    local_poll_lock = asyncio.Lock()
    local_trace_task: asyncio.Task[None] | None = None
    local_trace_sequence = 0
    local_trace_last_sample_monotonic: dict[str, float] = {}
    local_trace_max_age = max(15.0, local_trace_interval * 5.0)
    gridboss_last_transfer_state: str | None = None

    try:
        configured_scan_interval = int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
    except (TypeError, ValueError):
        configured_scan_interval = DEFAULT_SCAN_INTERVAL
    scan_interval = max(configured_scan_interval, 30)
    cache_max_age = timedelta(seconds=max(scan_interval * 3, 600))
    device_cache = BoundedPayloadCache(cache_max_age)
    grid_quality_store: Store[list[dict[str, Any]]] = Store(
        hass,
        1,
        f"{DOMAIN}.{entry.entry_id}.grid_quality",
    )
    grid_quality_samples = deserialize_grid_quality_samples(
        await grid_quality_store.async_load(), datetime.now(UTC)
    )
    max_sample_gap_seconds = max(
        DEFAULT_MAX_SAMPLE_GAP_SECONDS,
        scan_interval * 3,
    )
    try:
        configured_battery_detail_interval = int(
            entry.options.get(
                CONF_BATTERY_DETAIL_INTERVAL, DEFAULT_BATTERY_DETAIL_INTERVAL
            )
        )
    except (TypeError, ValueError):
        configured_battery_detail_interval = DEFAULT_BATTERY_DETAIL_INTERVAL
    battery_detail_interval = max(configured_battery_detail_interval, 60)
    detail_cache_max_age = timedelta(
        seconds=max(scan_interval * 3, battery_detail_interval * 2, 600)
    )
    battery_info_cache = BoundedPayloadCache(detail_cache_max_age)
    battery_parallel_cache = BoundedPayloadCache(detail_cache_max_age)
    last_battery_detail_fetch: datetime | None = None

    def _cached_local_snapshots(
        now: datetime,
    ) -> tuple[dict[str, EG4LocalTelemetry], dict[str, Any]]:
        """Return only recent local samples and their freshness diagnostics."""
        snapshots: dict[str, EG4LocalTelemetry] = {}
        ages: dict[str, float] = {}
        stale_roles: list[str] = []
        for role, snapshot in local_snapshot_cache.items():
            sampled_at = local_snapshot_times.get(role)
            age = (
                max(0.0, (now - sampled_at).total_seconds())
                if sampled_at is not None
                else None
            )
            if age is not None:
                ages[role] = round(age, 3)
            if age is not None and age <= local_trace_max_age:
                snapshots[role] = snapshot
            else:
                stale_roles.append(role)
        return snapshots, {
            "Enabled": local_trace_writer is not None,
            "IntervalSeconds": local_trace_interval,
            "BMSIntervalSeconds": local_trace_bms_interval,
            "MaxAgeSeconds": local_trace_max_age,
            "SampleAgesSeconds": ages,
            "StaleRoles": stale_roles,
            "AvailableRoles": sorted(snapshots),
            "IsStale": bool(local_dongles) and not snapshots,
        }

    async def _process_logger_sample(
        gridboss: EG4GridBossTelemetry | None,
        local_snapshots: dict[str, EG4LocalTelemetry],
        *,
        sequence: int | None,
        fallback_charge_power: Any = None,
        fallback_charge_source: str | None = None,
        process_generator: bool = True,
        process_charge: bool = True,
    ) -> None:
        """Feed one correlated fresh sample into the stateful HA logger."""
        charge_values = [
            float(snapshot.charge_power)
            for snapshot in local_snapshots.values()
            if snapshot.charge_power is not None
        ]
        if not process_charge:
            charge_available = False
            charge_power = None
            charge_source = "not_sampled"
        elif charge_values:
            charge_available = True
            charge_power = sum(charge_values)
            charge_source = "local_flexboss"
        elif local_dongles:
            # A configured local source with no usable sample is unknown, not
            # an observed zero.  This prevents a dongle timeout from ending a
            # charging session.
            charge_available = False
            charge_power = None
            charge_source = "local_flexboss"
        else:
            charge_power = _float_or_none(fallback_charge_power)
            charge_available = charge_power is not None
            charge_source = fallback_charge_source or "portal_battery_power"

        observed_at = datetime.now(UTC)
        sample = LoggerSample(
            observed_at=observed_at,
            sequence=sequence,
            source_serial=(
                gridboss.inverter_serial
                if gridboss is not None
                else telemetry_logger.source_serial
            ),
            charge_source_serial=next(
                (
                    snapshot.inverter_serial
                    for snapshot in local_snapshots.values()
                    if snapshot.inverter_serial
                ),
                None,
            ),
            generator_source_available=gridboss is not None,
            generator_input_registers_valid=(
                bool(gridboss and gridboss.input_registers_valid)
            ),
            generator_voltage=(gridboss.generator_voltage if gridboss else None),
            generator_frequency=(
                gridboss.generator_frequency if gridboss else None
            ),
            generator_power=(gridboss.generator_power if gridboss else None),
            transfer_state=(gridboss.transfer_state if gridboss else None),
            charge_source_available=charge_available,
            charge_power_w=charge_power,
            charge_source=charge_source,
            battery_voltage=(
                next(
                    (
                        snapshot.battery_voltage
                        for snapshot in local_snapshots.values()
                        if snapshot.battery_voltage is not None
                    ),
                    None,
                )
            ),
            battery_soc=(
                next(
                    (
                        snapshot.battery_soc
                        for snapshot in local_snapshots.values()
                        if snapshot.battery_soc is not None
                    ),
                    None,
                )
            ),
            process_generator=process_generator,
            process_charge=process_charge,
        )
        events = telemetry_logger.process(sample)
        logger_store.async_delay_save(telemetry_logger.to_dict, 1)
        for event in events:
            hass.bus.async_fire(event["event_type"], event)
        logger_coordinator.async_set_updated_data(
            telemetry_logger.snapshot(observed_at)
        )


    async def _record_controller_observation(record: dict[str, Any]) -> None:
        """Retain discrete controller evidence independently of full raw rotation."""
        if controller_trace_writer is None:
            return
        observation = controller_observations.observe(record)
        if observation is None:
            return
        try:
            if await controller_trace_writer.async_write(observation, wait=False) is None:
                _LOGGER.warning(
                    "Unable to write controller observation for role %s: %s",
                    record["role"],
                    controller_trace_writer.last_error or "unknown writer error",
                )
        except Exception as err_trace:
            _LOGGER.warning("Controller observation writer failed: %s", err_trace)

    async def _poll_local_role(
        role: str,
        dongle: EG4LocalDongle,
        sequence: int,
    ) -> dict[str, Any]:
        """Group all received blocks under one acquisition identity."""
        with dongle.capture() as acquisition:
            return await _poll_local_acquisition(role, dongle, sequence, acquisition)

    async def _poll_local_acquisition(
        role: str,
        dongle: EG4LocalDongle,
        sequence: int,
        acquisition: dict[str, Any],
    ) -> dict[str, Any]:
        """Poll one dongle core range and periodically refresh its BMS range."""
        started_at = datetime.now(UTC)
        started_monotonic = time.monotonic()
        previous_monotonic = local_trace_last_sample_monotonic.get(role)
        local_trace_last_sample_monotonic[role] = started_monotonic
        record: dict[str, Any] = {
            **acquisition,
            "sequence": sequence,
            "role": role,
            "sample_started_at": started_at.isoformat(timespec="milliseconds"),
            "host": dongle.host,
            "port": dongle.port,
            "ok": False,
            "diagnostics": [],
        }
        if previous_monotonic is not None:
            record["sample_gap_seconds"] = round(
                started_monotonic - previous_monotonic, 3
            )

        bms_error: str | None = None
        bms_sampled = False
        try:
            core = await asyncio.wait_for(
                dongle.async_read_core_snapshot(),
                timeout=max(3.0, dongle.timeout * 2.0),
            )
            now = datetime.now(UTC)
            bms = local_bms_cache.get(role)
            bms_at = local_bms_times.get(role)
            bms_age = (
                (now - bms_at).total_seconds() if bms_at is not None else None
            )
            if bms is None or bms_age is None or bms_age >= local_trace_bms_interval:
                try:
                    bms = await asyncio.wait_for(
                        dongle.async_read_bms_snapshot(),
                        timeout=max(3.0, dongle.timeout * 2.0),
                    )
                    local_bms_cache[role] = bms
                    local_bms_times[role] = datetime.now(UTC)
                    bms_sampled = True
                    bms_age = 0.0
                except Exception as err_bms:
                    bms_error = f"{type(err_bms).__name__}: {err_bms}"

            if bms is not None and bms_age is not None and bms_age <= local_trace_max_age:
                snapshot = merge_local_telemetry(core, bms)
            else:
                snapshot = core
            local_snapshot_cache[role] = snapshot
            local_snapshot_times[role] = now
            record.update(
                {
                    "ok": True,
                    "bms_sampled": bms_sampled,
                    "bms_age_seconds": (
                        round(max(0.0, bms_age), 3)
                        if bms_age is not None
                        else None
                    ),
                    "telemetry": telemetry_to_record(snapshot),
                    "primary_sample_completed_at": now.isoformat(
                        timespec="milliseconds"
                    ),
                }
            )
            if bms_error is not None:
                record["bms_error"] = bms_error
        except Exception as err_local:
            record["error"] = f"{type(err_local).__name__}: {err_local}"
            _LOGGER.warning(
                "Local EG4 trace read failed for role %s (%s): %s",
                role,
                dongle.host,
                err_local,
            )
        if local_trace_writer is not None:
            record["diagnostics"] = await dongle.async_read_diagnostics()
        record["partial"] = bool(acquisition["successful_transaction_ids"]) and (
            not record["ok"] or bool(acquisition["failed_transaction_ids"])
        )
        record["duration_ms"] = round(
            (time.monotonic() - started_monotonic) * 1000.0, 1
        )
        record["sample_completed_at"] = datetime.now(UTC).isoformat(
            timespec="milliseconds"
        )
        if local_trace_writer is not None:
            try:
                if (
                    await local_trace_writer.async_write(record, wait=False)
                    is None
                ):
                    _LOGGER.warning(
                        "Unable to write local EG4 trace record for role %s: %s",
                        role,
                        local_trace_writer.last_error or "unknown writer error",
                    )
            except Exception as err_trace:
                _LOGGER.warning(
                    "Local EG4 trace writer failed for role %s: %s",
                    role,
                    err_trace,
                )
        await _record_controller_observation(record)
        return record

    async def _poll_gridboss_once(
        *, record: bool, sequence: int | None = None
    ) -> EG4GridBossTelemetry | None:
        """Group power/frequency and diagnostic evidence, including failed reads."""
        if gridboss_dongle is None:
            return None
        with gridboss_dongle.capture() as acquisition:
            return await _poll_gridboss_acquisition(
                record=record, sequence=sequence, acquisition=acquisition
            )

    async def _poll_gridboss_acquisition(
        *,
        record: bool,
        sequence: int | None,
        acquisition: dict[str, Any],
    ) -> EG4GridBossTelemetry | None:
        """Poll GridBOSS directly without sharing a socket with HA/cloud code."""
        nonlocal gridboss_snapshot_cache, gridboss_snapshot_time
        nonlocal gridboss_snapshot_sequence, gridboss_snapshot_error
        nonlocal gridboss_last_transfer_state
        if gridboss_dongle is None:
            return None
        async with gridboss_poll_lock:
            started_at = datetime.now(UTC)
            started_monotonic = time.monotonic()
            record_data: dict[str, Any] = {
                **acquisition,
                "sequence": sequence,
                "role": "gridboss",
                "sample_started_at": started_at.isoformat(timespec="milliseconds"),
                "host": gridboss_dongle.host,
                "port": gridboss_dongle.port,
                "ok": False,
                "diagnostics": [],
            }
            try:
                snapshot = await asyncio.wait_for(
                    gridboss_dongle.async_read_snapshot(),
                    timeout=max(5.0, gridboss_dongle.timeout * 3.0),
                )
                gridboss_snapshot_cache = snapshot
                gridboss_snapshot_time = datetime.now(UTC)
                gridboss_snapshot_sequence = sequence
                gridboss_snapshot_error = None
                previous_state = gridboss_last_transfer_state
                gridboss_last_transfer_state = snapshot.transfer_state
                record_data.update(
                    {
                        "ok": True,
                        "telemetry": gridboss_to_record(snapshot),
                        "primary_sample_completed_at": (
                            gridboss_snapshot_time.isoformat(timespec="milliseconds")
                        ),
                        "transfer_transition": (
                            {
                                "from": previous_state,
                                "to": snapshot.transfer_state,
                            }
                            if previous_state != snapshot.transfer_state
                            else None
                        ),
                    }
                )
                return snapshot
            except Exception as err_gridboss:
                gridboss_snapshot_error = (
                    f"{type(err_gridboss).__name__}: {err_gridboss}"
                )
                record_data["error"] = gridboss_snapshot_error
                _LOGGER.warning(
                    "Direct GridBOSS telemetry read failed (%s): %s",
                    gridboss_dongle.host,
                    err_gridboss,
                )
                return None
            finally:
                if record and local_trace_writer is not None:
                    record_data["diagnostics"] = (
                        await gridboss_dongle.async_read_diagnostics()
                    )
                record_data["partial"] = bool(acquisition["successful_transaction_ids"]) and (
                    not record_data["ok"] or bool(acquisition["failed_transaction_ids"])
                )
                record_data["duration_ms"] = round(
                    (time.monotonic() - started_monotonic) * 1000.0, 1
                )
                record_data["sample_completed_at"] = datetime.now(UTC).isoformat(
                    timespec="milliseconds"
                )
                if local_trace_writer is not None:
                    try:
                        await local_trace_writer.async_write(record_data, wait=False)
                    except Exception as err_trace:
                        _LOGGER.warning(
                            "GridBOSS trace writer failed: %s", err_trace
                        )
                if record:
                    await _record_controller_observation(record_data)

    async def _poll_local_once(
        *, record: bool, sequence: int | None = None
    ) -> dict[str, EG4LocalTelemetry]:
        """Poll local dongles without allowing concurrent socket clients."""
        nonlocal local_trace_sequence
        if not local_dongles:
            return {}
        async with local_poll_lock:
            if record:
                if sequence is None:
                    local_trace_sequence += 1
                    sequence = local_trace_sequence
                else:
                    local_trace_sequence = max(local_trace_sequence, sequence)
                await asyncio.gather(
                    *(
                        _poll_local_role(role, dongle, sequence)
                        for role, dongle in local_dongles.items()
                    )
                )
                snapshots, _ = _cached_local_snapshots(datetime.now(UTC))
                return snapshots

            results = await asyncio.gather(
                *(
                    dongle.async_read_snapshot()
                    for dongle in local_dongles.values()
                ),
                return_exceptions=True,
            )
            snapshots = {}
            for (role, dongle), result in zip(local_dongles.items(), results):
                if isinstance(result, EG4LocalTelemetry):
                    snapshots[role] = result
                    local_snapshot_cache[role] = result
                    local_snapshot_times[role] = datetime.now(UTC)
                else:
                    _LOGGER.warning(
                        "Local EG4 read failed for role %s (%s): %s",
                        role,
                        dongle.host,
                        result,
                    )
            return snapshots

    async def _local_trace_loop() -> None:
        """Continuously capture local telemetry and emit accepted events."""
        nonlocal local_trace_sequence
        while True:
            cycle_started = time.monotonic()
            local_trace_sequence += 1
            gridboss_snapshot, local_snapshots = await asyncio.gather(
                _poll_gridboss_once(record=True, sequence=local_trace_sequence),
                _poll_local_once(record=True, sequence=local_trace_sequence),
            )
            await _process_logger_sample(
                gridboss_snapshot,
                local_snapshots,
                sequence=local_trace_sequence,
                process_charge=bool(local_dongles),
            )
            elapsed = time.monotonic() - cycle_started
            await asyncio.sleep(max(0.0, local_trace_interval - elapsed))

    async def async_update_data() -> dict[str, Any]:
        """Fetch data for all discovered serials and build aggregate values."""
        nonlocal last_battery_detail_fetch
        try:
            now = datetime.now(UTC)
            device_data: dict[str, dict[str, Any]] = {}
            fresh_serials: list[str] = []
            stale_serials: list[str] = []
            cache_ages: dict[str, float] = {}

            for serial in serials:
                payload = await api.get_system_data(serial_to_fetch=serial)
                if payload:
                    device_cache.put(serial, payload, now)
                    fresh_serials.append(serial)
                else:
                    cached = device_cache.get(serial, now)
                    if cached is None:
                        continue
                    payload = cached.value
                    stale_serials.append(serial)
                    cache_ages[serial] = round(cached.age_seconds, 1)
                    payload["_cache"] = {
                        "stale": True,
                        "fetched_at": cached.fetched_at.isoformat(),
                        "age_seconds": round(cached.age_seconds, 1),
                    }

                payload["_meta"] = inverters_by_serial.get(serial, {})
                payload["_role"] = role_map.get(serial, "unknown")
                device_data[serial] = payload

            if not device_data:
                raise UpdateFailed("No data returned for any EG4 serial")

            aggregate = _build_aggregate(device_data, role_map)
            missing_serials = [serial for serial in serials if serial not in device_data]
            aggregate["DataFreshness"] = {
                "IsStale": bool(stale_serials or missing_serials),
                "FreshSerials": fresh_serials,
                "StaleSerials": stale_serials,
                "MissingSerials": missing_serials,
                "CacheAgeSeconds": cache_ages,
                "CacheMaxAgeSeconds": cache_max_age.total_seconds(),
            }

            gridboss_serial = next(
                (serial for serial, role in role_map.items() if role == "gridboss"),
                None,
            )
            if gridboss_serial:
                midbox_runtime = await api.get_midbox_runtime(gridboss_serial)
                _apply_midbox_runtime_snapshot(aggregate, midbox_runtime)

                date_text = now.strftime("%Y-%m-%d")
                chart = await api.get_day_multiline_parallel(
                    serial_num=gridboss_serial,
                    date_text=date_text,
                )
                _apply_parallel_day_snapshot(aggregate, chart)

            event_payload = await api.get_event_list(
                page=1, rows=100, event_text="_all"
            )
            _apply_event_snapshot(aggregate, event_payload, set(serials))

            battery_serial = _safe_get(aggregate, ("source_serials", "battery"))
            battery_detail_fetch_attempted = False
            battery_info_fetched = False
            parallel_info_fetched = False
            battery_detail_freshness: dict[str, Any] = {
                "Serial": battery_serial,
                "FetchAttempted": False,
                "IsStale": False,
                "IsAvailable": False,
                "CacheMaxAgeSeconds": detail_cache_max_age.total_seconds(),
                "Aggregate": {"Status": "unavailable", "AgeSeconds": None},
                "Parallel": {"Status": "unavailable", "AgeSeconds": None},
            }
            if isinstance(battery_serial, str):
                battery_detail_fetch_attempted = (
                    last_battery_detail_fetch is None
                    or (now - last_battery_detail_fetch).total_seconds()
                    >= battery_detail_interval
                )
                if battery_detail_fetch_attempted:
                    battery_info = await api.get_inverter_battery_info(battery_serial)
                    if battery_info:
                        battery_info_cache.put(battery_serial, battery_info, now)
                        battery_info_fetched = True
                    parallel_info = await api.get_inverter_battery_info_parallel(
                        battery_serial
                    )
                    if parallel_info:
                        battery_parallel_cache.put(
                            battery_serial, parallel_info, now
                        )
                        parallel_info_fetched = True
                    last_battery_detail_fetch = now

                cached_battery_info = battery_info_cache.get(battery_serial, now)
                cached_parallel_info = battery_parallel_cache.get(battery_serial, now)
                battery_info_payload = (
                    cached_battery_info.value if cached_battery_info else None
                )
                parallel_info_payload = (
                    cached_parallel_info.value if cached_parallel_info else None
                )
                if battery_info_payload:
                    device_data[battery_serial]["BatteryInfo"] = battery_info_payload
                if parallel_info_payload:
                    device_data[battery_serial]["BatteryParallelInfo"] = (
                        parallel_info_payload
                    )
                _apply_battery_info_snapshot(
                    aggregate,
                    battery_info_payload,
                    parallel_info_payload,
                )

                def _detail_status(
                    cached: Any,
                    fetched: bool,
                ) -> dict[str, Any]:
                    if cached is None:
                        return {"Status": "missing", "AgeSeconds": None}
                    status = "fresh" if fetched else "cached"
                    if battery_detail_fetch_attempted and not fetched:
                        status = "stale"
                    return {
                        "Status": status,
                        "AgeSeconds": round(cached.age_seconds, 1),
                        "FetchedAt": cached.fetched_at.isoformat(),
                    }

                battery_detail_freshness["FetchAttempted"] = (
                    battery_detail_fetch_attempted
                )
                battery_detail_freshness["Aggregate"] = _detail_status(
                    cached_battery_info,
                    battery_info_fetched,
                )
                battery_detail_freshness["Parallel"] = _detail_status(
                    cached_parallel_info,
                    parallel_info_fetched,
                )
                statuses = {
                    battery_detail_freshness["Aggregate"]["Status"],
                    battery_detail_freshness["Parallel"]["Status"],
                }
                battery_detail_freshness["IsStale"] = "stale" in statuses
                battery_detail_freshness["IsAvailable"] = bool(
                    statuses.intersection({"fresh", "cached", "stale"})
                )

            aggregate["DataFreshness"]["BatteryDetails"] = (
                battery_detail_freshness
            )
            aggregate["DataFreshness"]["BatteryDetailsStale"] = (
                battery_detail_freshness["IsStale"]
            )

            if local_trace_writer is not None:
                local_snapshots, local_freshness = _cached_local_snapshots(now)
            else:
                local_snapshots = await _poll_local_once(record=False)
                _, local_freshness = _cached_local_snapshots(now)
            aggregate["DataFreshness"]["LocalTelemetry"] = local_freshness
            if gridboss_snapshot_cache is not None and gridboss_snapshot_time is not None:
                gridboss_age = max(0.0, (now - gridboss_snapshot_time).total_seconds())
                if gridboss_age <= local_trace_max_age:
                    gridboss = aggregate.setdefault("GridBOSS", {})
                    gridboss.update(
                        {
                            "Available": True,
                            "Host": gridboss_snapshot_cache.host,
                            "Port": gridboss_snapshot_cache.port,
                            "DongleSerial": gridboss_snapshot_cache.dongle_serial,
                            "InverterSerial": gridboss_snapshot_cache.inverter_serial,
                            "GridVoltage": gridboss_snapshot_cache.grid_voltage,
                            "GridFrequency": gridboss_snapshot_cache.grid_frequency,
                            "GeneratorVoltage": gridboss_snapshot_cache.generator_voltage,
                            "GeneratorFrequency": gridboss_snapshot_cache.generator_frequency,
                            "GeneratorPower": gridboss_snapshot_cache.generator_power,
                            "GeneratorCurrent": (
                                None
                                if gridboss_snapshot_cache.generator_l1_current is None
                                or gridboss_snapshot_cache.generator_l2_current is None
                                else gridboss_snapshot_cache.generator_l1_current
                                + gridboss_snapshot_cache.generator_l2_current
                            ),
                            "UPSVoltage": gridboss_snapshot_cache.ups_voltage,
                            "UPSPower": gridboss_snapshot_cache.ups_power,
                            "LoadPower": gridboss_snapshot_cache.load_power,
                            "TransferState": gridboss_snapshot_cache.transfer_state,
                            "GridAvailable": gridboss_snapshot_cache.grid_available,
                            "GeneratorAvailable": gridboss_snapshot_cache.generator_available,
                            "GeneratorSupplying": gridboss_snapshot_cache.generator_supplying,
                            "UPSLive": gridboss_snapshot_cache.ups_live,
                            "InputRegistersValid": gridboss_snapshot_cache.input_registers_valid,
                            "SampleAgeSeconds": round(gridboss_age, 3),
                        }
                    )
                    aggregate["DataFreshness"]["GridBOSS"] = {
                        "Available": True,
                        "SampleAgeSeconds": round(gridboss_age, 3),
                        "Host": gridboss_snapshot_cache.host,
                        "Sequence": gridboss_snapshot_sequence,
                        "LastError": gridboss_snapshot_error,
                    }
            elif gridboss_dongle is not None:
                aggregate["DataFreshness"]["GridBOSS"] = {
                    "Available": False,
                    "SampleAgeSeconds": None,
                    "Host": gridboss_dongle.host,
                    "Sequence": gridboss_snapshot_sequence,
                    "LastError": gridboss_snapshot_error,
                }
            aggregate.setdefault("LocalTelemetry", {}).update(
                {
                    **local_discovery,
                    "TraceEnabled": local_trace_writer is not None,
                    "TracePath": (
                        str(local_trace_writer.active_path)
                        if local_trace_writer is not None
                        else None
                    ),
                }
            )
            _apply_local_telemetry_snapshot(aggregate, local_snapshots)
            _apply_battery_data_quality_guards(aggregate)

            site_source_serial = _safe_get(
                aggregate, ("source_serials", "site")
            )
            grid_sample_is_fresh = (
                isinstance(site_source_serial, str)
                and site_source_serial in fresh_serials
            )
            _apply_grid_quality_snapshot(
                aggregate,
                grid_quality_samples,
                now,
                max_sample_gap_seconds,
                record_sample=grid_sample_is_fresh,
            )
            if not local_dongles:
                await _process_logger_sample(
                    None,
                    {},
                    sequence=None,
                    fallback_charge_power=_safe_get(
                        aggregate, ("Battery", "ChargePower")
                    ),
                    fallback_charge_source="portal_battery_power",
                    process_generator=False,
                    process_charge=True,
                )
            aggregate["Logger"] = telemetry_logger.snapshot(now)
            aggregate["Backup"] = build_backup_metrics(aggregate, entry.options)
            grid_quality_store.async_delay_save(
                lambda: serialize_grid_quality_samples(grid_quality_samples),
                10,
            )

            return {
                "plant": plant_info,
                "roles": role_map,
                "device_data": device_data,
                "aggregate": aggregate,
            }
        except Exception as err_update:
            raise UpdateFailed(
                f"Error communicating with EG4 API: {err_update}"
            ) from err_update

    if local_trace_writer is not None or local_dongles or gridboss_dongle is not None:
        local_trace_sequence += 1
        gridboss_snapshot, local_snapshots = await asyncio.gather(
            _poll_gridboss_once(record=local_trace_writer is not None, sequence=local_trace_sequence),
            _poll_local_once(record=local_trace_writer is not None, sequence=local_trace_sequence),
        )
        await _process_logger_sample(
            gridboss_snapshot,
            local_snapshots,
            sequence=local_trace_sequence,
            process_charge=bool(local_dongles),
        )

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )
    await coordinator.async_config_entry_first_refresh()

    if local_trace_writer is not None or local_dongles or gridboss_dongle is not None:
        local_trace_task = hass.async_create_background_task(
            _local_trace_loop(),
            name=f"{DOMAIN}_{entry.entry_id}_local_trace",
        )

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "logger_coordinator": logger_coordinator,
        "serials": serials,
        "inverters": inverters_by_serial,
        "roles": role_map,
        "local_trace_task": local_trace_task,
        "local_trace_writer": local_trace_writer,
        "controller_trace_writer": controller_trace_writer,
        "controller_observation_path": (
            str(controller_trace_writer.active_path)
            if controller_trace_writer is not None
            else None
        ),
        "telemetry_logger": telemetry_logger,
        "logger_store": logger_store,
        "gridboss_host": gridboss_host,
        "gridboss_serial": gridboss_serial,
        "local_trace_path": (
            str(local_trace_writer.active_path)
            if local_trace_writer is not None
            else None
        ),
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data[DOMAIN].get(entry.entry_id, {})
    trace_task = entry_data.get("local_trace_task")
    if trace_task is not None:
        trace_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await trace_task
    for writer_key in ("local_trace_writer", "controller_trace_writer"):
        trace_writer = entry_data.get(writer_key)
        if trace_writer is not None:
            await trace_writer.async_close()

    telemetry_logger = entry_data.get("telemetry_logger")
    logger_store = entry_data.get("logger_store")
    if telemetry_logger is not None and logger_store is not None:
        await logger_store.async_save(telemetry_logger.to_dict())

    logger_coordinator = entry_data.get("logger_coordinator")
    if logger_coordinator is not None:
        await logger_coordinator.async_shutdown()
    coordinator = entry_data.get("coordinator")
    if coordinator is not None:
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        api = entry_data["api"]
        await api.close()
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
