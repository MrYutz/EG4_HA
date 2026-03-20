"""The EG4 Monitor integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_GRIDBOSS_SERIAL,
    CONF_MASTER_SERIAL,
    CONF_SLAVE_SERIAL,
    DOMAIN,
)
from .eg4_api import EG4API

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
DEFAULT_SCAN_INTERVAL = 120


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
            "TotalCapacityKWh": _safe_get(
                battery_source, ("Battery", "TotalCapacityKWh")
            ),
            "RemainingKWh": _safe_get(battery_source, ("Battery", "RemainingKWh")),
        },
        "Solar": {
            "TotalPower": _safe_get(site_source, ("Solar", "TotalPower")),
            "PV1": _safe_get(site_source, ("Solar", "PV1", "Power")),
            "PV2": _safe_get(site_source, ("Solar", "PV2", "Power")),
            "PV3": _safe_get(site_source, ("Solar", "PV3", "Power")),
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
        "Energy": {
            "Today": {
                "Solar Generation": _safe_get(
                    site_source, ("Energy", "Today", "Solar Generation")
                ),
                "Usage": _safe_get(site_source, ("Energy", "Today", "Usage")),
                "Grid Import": _safe_get(
                    site_source, ("Energy", "Today", "Grid Import")
                ),
                "Grid Export": _safe_get(
                    site_source, ("Energy", "Today", "Grid Export")
                ),
            },
            "Total": {
                "Solar Generation": _safe_get(
                    site_source, ("Energy", "Total", "Solar Generation")
                ),
                "Usage": _safe_get(site_source, ("Energy", "Total", "Usage")),
                "Grid Import": _safe_get(
                    site_source, ("Energy", "Total", "Grid Import")
                ),
                "Grid Export": _safe_get(
                    site_source, ("Energy", "Total", "Grid Export")
                ),
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
            aggregate["Battery"]["Power"] = float(battery_discharging)
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

    if today_export is not None:
        energy_today["Grid Export"] = today_export
    if total_export is not None:
        energy_total["Grid Export"] = total_export
    if today_usage is not None:
        energy_today["Usage"] = today_usage
        # legacy compatibility alias
        energy_today["Grid Import"] = today_usage
    if total_usage is not None:
        energy_total["Usage"] = total_usage
        # legacy compatibility alias
        energy_total["Grid Import"] = total_usage
    if today_ups is not None:
        energy_today["UPS Usage"] = today_ups
    if total_ups is not None:
        energy_total["UPS Usage"] = total_ups
    if today_solar is not None:
        energy_today["Solar Generation"] = today_solar
    if total_solar is not None:
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

    async def async_update_data() -> dict[str, Any]:
        """Fetch data for all discovered serials and build aggregate values."""
        try:
            device_data: dict[str, dict[str, Any]] = {}
            for serial in serials:
                payload = await api.get_system_data(serial_to_fetch=serial)
                if payload:
                    payload["_meta"] = inverters_by_serial.get(serial, {})
                    payload["_role"] = role_map.get(serial, "unknown")
                    device_data[serial] = payload

            if not device_data:
                raise UpdateFailed("No data returned for any EG4 serial")

            aggregate = _build_aggregate(device_data, role_map)

            gridboss_serial = next(
                (serial for serial, role in role_map.items() if role == "gridboss"),
                None,
            )
            if gridboss_serial:
                midbox_runtime = await api.get_midbox_runtime(gridboss_serial)
                _apply_midbox_runtime_snapshot(aggregate, midbox_runtime)

                date_text = datetime.now().strftime("%Y-%m-%d")
                chart = await api.get_day_multiline_parallel(
                    serial_num=gridboss_serial,
                    date_text=date_text,
                )
                _apply_parallel_day_snapshot(aggregate, chart)

            event_payload = await api.get_event_list(
                page=1, rows=100, event_text="_all"
            )
            _apply_event_snapshot(aggregate, event_payload, set(serials))

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

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=async_update_data,
        update_interval=timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "serials": serials,
        "inverters": inverters_by_serial,
        "roles": role_map,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        api = hass.data[DOMAIN][entry.entry_id]["api"]
        await api.close()
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
