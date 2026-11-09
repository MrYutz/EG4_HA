"""Support for EG4 Monitor binary sensors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN


def _safe_get(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


@dataclass(frozen=True)
class EG4BinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description for EG4 binary sensors."""

    value_fn: Callable[[dict[str, Any]], bool | None] | None = None


@dataclass(frozen=True)
class EG4LoggerBinarySensorDescription(BinarySensorEntityDescription):
    """Description for high-rate logger binary sensors."""

    value_fn: Callable[[dict[str, Any]], bool | None] | None = None


def _logger_get(data: dict[str, Any], section: str, key: str) -> Any:
    """Read a logger state field without inventing a false value."""
    section_data = data.get(section)
    return section_data.get(key) if isinstance(section_data, dict) else None


BINARY_SENSOR_TYPES: dict[str, EG4BinarySensorEntityDescription] = {
    "battery_charging": EG4BinarySensorEntityDescription(
        key="battery_charging",
        name="Battery Charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "Direction"))
        == "Charging",
    ),
    "battery_discharging": EG4BinarySensorEntityDescription(
        key="battery_discharging",
        name="Battery Discharging",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "Direction"))
        == "Discharging",
    ),
    "generator_running": EG4BinarySensorEntityDescription(
        key="generator_running",
        name="Generator Running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Generator", "RunStatus")),
    ),
    "generator_supplying_power": EG4BinarySensorEntityDescription(
        key="generator_supplying_power",
        name="Generator Supplying Power",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Generator", "SupplyingPower")
        ),
    ),
    "uncleared_error_active": EG4BinarySensorEntityDescription(
        key="uncleared_error_active",
        name="Uncleared Error Active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Events", "HasOpen")),
    ),
    "inverter_lost": EG4BinarySensorEntityDescription(
        key="inverter_lost",
        name="Inverter Lost",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Device", "Lost")),
    ),
    "battery_local_bms_available": EG4BinarySensorEntityDescription(
        key="battery_local_bms_available",
        name="Battery Local BMS Available",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "LocalTelemetry", "BMSAvailable")
        ),
    ),
    "battery_bms_charging_allowed": EG4BinarySensorEntityDescription(
        key="battery_bms_charging_allowed",
        name="Battery BMS Charging Allowed",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "BMS", "ChargeEnabled")
        ),
    ),
    "battery_bms_discharging_allowed": EG4BinarySensorEntityDescription(
        key="battery_bms_discharging_allowed",
        name="Battery BMS Discharging Allowed",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "BMS", "DischargeEnabled")
        ),
    ),
    "battery_bms_force_charge": EG4BinarySensorEntityDescription(
        key="battery_bms_force_charge",
        name="Battery BMS Force Charge",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "BMS", "ForceChargeEnabled")
        ),
    ),
    "data_stale": EG4BinarySensorEntityDescription(
        key="data_stale",
        name="EG4 Data Stale",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(data, ("aggregate", "DataFreshness", "IsStale")),
    ),
    "grid_outage_active": EG4BinarySensorEntityDescription(
        key="grid_outage_active",
        name="Grid Outage Active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(data, ("aggregate", "GridQuality", "State"))
        == "outage",
    ),
    "grid_brownout_active": EG4BinarySensorEntityDescription(
        key="grid_brownout_active",
        name="Grid Brownout Active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(data, ("aggregate", "GridQuality", "State"))
        == "brownout",
    ),
    "battery_soc_voltage_mismatch": EG4BinarySensorEntityDescription(
        key="battery_soc_voltage_mismatch",
        name="Battery SOC Voltage Mismatch",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Battery", "SOCVoltageMismatch")
        ),
    ),
    "battery_critical_voltage": EG4BinarySensorEntityDescription(
        key="battery_critical_voltage",
        name="Battery Critical Voltage",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Battery", "CriticalVoltage")
        ),
    ),
    "battery_imbalance_detected": EG4BinarySensorEntityDescription(
        key="battery_imbalance_detected",
        name="Battery Imbalance Detected",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Battery", "ImbalanceDetected")
        ),
    ),
    "generator_running_without_load": EG4BinarySensorEntityDescription(
        key="generator_running_without_load",
        name="Generator Running Without Load",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Generator", "RunningWithoutLoad")
        ),
    ),
    "diesel_fuel_reorder_needed": EG4BinarySensorEntityDescription(
        key="diesel_fuel_reorder_needed",
        name="Diesel Fuel Reorder Needed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Fuel", "FuelReorderNeeded")
        ),
    ),
    "load_shedding_recommended": EG4BinarySensorEntityDescription(
        key="load_shedding_recommended",
        name="Load Shedding Recommended",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "LoadShedding", "Recommended")
        ),
    ),
}


LOGGER_BINARY_SENSOR_TYPES: tuple[EG4LoggerBinarySensorDescription, ...] = (
    EG4LoggerBinarySensorDescription(
        key="generator_gridboss_ac_present",
        name="Generator AC Present (GridBOSS)",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda data: _logger_get(data, "Generator", "PresenceState") == "present",
    ),
    EG4LoggerBinarySensorDescription(
        key="generator_gridboss_supplying",
        name="Generator Supplying Power (GridBOSS)",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda data: _logger_get(data, "Generator", "SupplyState") == "supplying",
    ),
    EG4LoggerBinarySensorDescription(
        key="generator_gridboss_state_unknown",
        name="Generator GridBOSS State Unknown",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _logger_get(data, "Generator", "RunState") == "unknown",
    ),
    EG4LoggerBinarySensorDescription(
        key="battery_charge_session_active",
        name="Battery Charge Session Active",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: _logger_get(data, "Charging", "Active") is True,
    ),
    EG4LoggerBinarySensorDescription(
        key="battery_charge_session_interrupted",
        name="Battery Charge Session Interrupted",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _logger_get(data, "Charging", "Interrupted") is True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EG4 binary sensors based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_info = {
        "identifiers": {(DOMAIN, f"{entry.entry_id}_site")},
        "name": "EG4 Site",
        "manufacturer": "EG4 Electronics",
        "model": "Cloud Site",
    }

    entities = [
        EG4BinarySensor(coordinator, description, entry.entry_id, device_info)
        for description in BINARY_SENSOR_TYPES.values()
    ]
    logger_coordinator = hass.data[DOMAIN][entry.entry_id].get("logger_coordinator")
    if logger_coordinator is not None:
        entities.extend(
            EG4LoggerBinarySensor(
                logger_coordinator,
                description,
                entry.entry_id,
                device_info,
            )
            for description in LOGGER_BINARY_SENSOR_TYPES
        )
    async_add_entities(entities)


class EG4BinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of an EG4 binary sensor."""

    entity_description: EG4BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: EG4BinarySensorEntityDescription,
        entry_id: str,
        device_info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{self.entity_description.key}"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        try:
            value = self.entity_description.value_fn(data)
            return None if value is None else bool(value)
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose diagnostic context for problem sensors."""
        data = self.coordinator.data or {}
        key = self.entity_description.key

        if key == "uncleared_error_active":
            events = _safe_get(data, ("aggregate", "Events"))
            if not isinstance(events, dict):
                return None
            attrs: dict[str, Any] = {
                "open_count": events.get("OpenCount", 0),
                "open_summary": events.get("OpenSummary", "none"),
            }
            top = events.get("OpenTop")
            if isinstance(top, list):
                attrs["open_events"] = top
            return attrs

        if key in {
            "battery_soc_voltage_mismatch",
            "battery_critical_voltage",
            "battery_imbalance_detected",
        }:
            battery = _safe_get(data, ("aggregate", "Backup", "Battery"))
            return battery if isinstance(battery, dict) else None

        if key in {"grid_outage_active", "grid_brownout_active", "data_stale"}:
            quality = _safe_get(data, ("aggregate", "GridQuality"))
            freshness = _safe_get(data, ("aggregate", "DataFreshness"))
            attrs = {}
            if isinstance(quality, dict):
                attrs.update(quality)
            if isinstance(freshness, dict):
                attrs.update(freshness)
            return attrs or None

        if key.startswith("diesel_"):
            fuel = _safe_get(data, ("aggregate", "Backup", "Fuel"))
            return fuel if isinstance(fuel, dict) else None

        if key in {"generator_running_without_load", "load_shedding_recommended"}:
            backup = _safe_get(data, ("aggregate", "Backup"))
            return backup if isinstance(backup, dict) else None

        return None


class EG4LoggerBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """High-rate binary state backed by the EG4 logger coordinator."""

    entity_description: EG4LoggerBinarySensorDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: EG4LoggerBinarySensorDescription,
        entry_id: str,
        device_info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_has_entity_name = False
        self._attr_name = description.name
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_suggested_object_id = description.key
        self._attr_device_info = device_info
        self._attr_device_class = description.device_class

    @property
    def suggested_object_id(self) -> str:
        """Keep logger binary entity IDs stable for automations."""
        return self.entity_description.key

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        try:
            value = self.entity_description.value_fn(data)
            return None if value is None else bool(value)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the complete logger section for diagnostics."""
        data = self.coordinator.data or {}
        section = (
            "Generator"
            if self.entity_description.key.startswith("generator_")
            else "Charging"
        )
        value = data.get(section)
        return value if isinstance(value, dict) else None
