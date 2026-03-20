"""Support for EG4 Monitor sensors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
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


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class EG4SensorEntityDescription(SensorEntityDescription):
    """Class describing EG4 sensor entities."""

    value_fn: Callable[[dict[str, Any]], StateType] | None = None


SENSOR_TYPES: dict[str, EG4SensorEntityDescription] = {
    "battery_soc": EG4SensorEntityDescription(
        key="battery_soc",
        name="Battery State of Charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "SOC")),
    ),
    "battery_power": EG4SensorEntityDescription(
        key="battery_power",
        name="Battery Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "Power")),
    ),
    "battery_charging_power": EG4SensorEntityDescription(
        key="battery_charging_power",
        name="Battery Charging Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "ChargePower")),
    ),
    "battery_discharging_power": EG4SensorEntityDescription(
        key="battery_discharging_power",
        name="Battery Discharging Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "DischargePower")
        ),
    ),
    "battery_power_direction": EG4SensorEntityDescription(
        key="battery_power_direction",
        name="Battery Power Direction",
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "Direction")),
    ),
    "battery_voltage": EG4SensorEntityDescription(
        key="battery_voltage",
        name="Battery Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "Voltage")),
    ),
    "battery_remaining_kwh": EG4SensorEntityDescription(
        key="battery_remaining_kwh",
        name="Battery Energy Remaining",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "RemainingKWh")),
    ),
    "solar_power": EG4SensorEntityDescription(
        key="solar_power",
        name="Solar Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Solar", "TotalPower")),
    ),
    "pv1_power": EG4SensorEntityDescription(
        key="pv1_power",
        name="PV1 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Solar", "PV1")),
    ),
    "pv2_power": EG4SensorEntityDescription(
        key="pv2_power",
        name="PV2 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Solar", "PV2")),
    ),
    "grid_power": EG4SensorEntityDescription(
        key="grid_power",
        name="Grid Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Grid", "PowerNet")),
    ),
    "grid_voltage": EG4SensorEntityDescription(
        key="grid_voltage",
        name="Grid Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Grid", "Voltage")),
    ),
    "grid_frequency": EG4SensorEntityDescription(
        key="grid_frequency",
        name="Grid Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Grid", "Frequency")),
    ),
    "load_power": EG4SensorEntityDescription(
        key="load_power",
        name="Load Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Load", "Power")),
    ),
    "eps_power": EG4SensorEntityDescription(
        key="eps_power",
        name="EPS Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "EPS", "Power")),
    ),
    "eps_voltage": EG4SensorEntityDescription(
        key="eps_voltage",
        name="EPS Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "EPS", "Voltage")),
    ),
    "eps_frequency": EG4SensorEntityDescription(
        key="eps_frequency",
        name="EPS Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "EPS", "Frequency")),
    ),
    "today_solar": EG4SensorEntityDescription(
        key="today_solar",
        name="Today's Solar Yield",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Today", "Solar Generation")
        ),
    ),
    "today_grid_import": EG4SensorEntityDescription(
        key="today_grid_import",
        name="Today's Usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _first_non_none(
            _safe_get(data, ("aggregate", "Energy", "Today", "Usage")),
            _safe_get(data, ("aggregate", "Energy", "Today", "Grid Import")),
        ),
    ),
    "today_grid_export": EG4SensorEntityDescription(
        key="today_grid_export",
        name="Today Export",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Today", "Grid Export")
        ),
    ),
    "today_ups_usage": EG4SensorEntityDescription(
        key="today_ups_usage",
        name="UPS Usage Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Today", "UPS Usage")
        ),
    ),
    "total_solar": EG4SensorEntityDescription(
        key="total_solar",
        name="Total Solar Yield",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Total", "Solar Generation")
        ),
    ),
    "total_grid_import": EG4SensorEntityDescription(
        key="total_grid_import",
        name="Total Usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _first_non_none(
            _safe_get(data, ("aggregate", "Energy", "Total", "Usage")),
            _safe_get(data, ("aggregate", "Energy", "Total", "Grid Import")),
        ),
    ),
    "total_grid_export": EG4SensorEntityDescription(
        key="total_grid_export",
        name="Total Export",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Total", "Grid Export")
        ),
    ),
    "total_ups_usage": EG4SensorEntityDescription(
        key="total_ups_usage",
        name="UPS Usage Total",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Total", "UPS Usage")
        ),
    ),
    "generator_voltage": EG4SensorEntityDescription(
        key="generator_voltage",
        name="Generator Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Generator", "Voltage")),
    ),
    "generator_frequency": EG4SensorEntityDescription(
        key="generator_frequency",
        name="Generator Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Generator", "Frequency")),
    ),
    "generator_power": EG4SensorEntityDescription(
        key="generator_power",
        name="Generator Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Generator", "Power")),
    ),
    "generator_status_source": EG4SensorEntityDescription(
        key="generator_status_source",
        name="Generator Status Source",
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Generator", "StatusSource")
        ),
    ),
    "uncleared_error_count": EG4SensorEntityDescription(
        key="uncleared_error_count",
        name="Uncleared Error Count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Events", "OpenCount")),
    ),
    "uncleared_error_summary": EG4SensorEntityDescription(
        key="uncleared_error_summary",
        name="Uncleared Error Summary",
        value_fn=lambda data: _safe_get(data, ("aggregate", "Events", "OpenSummary")),
    ),
    "eg4_device_count": EG4SensorEntityDescription(
        key="eg4_device_count",
        name="EG4 Device Count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "device_count")),
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EG4 sensors based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_info = {
        "identifiers": {(DOMAIN, f"{entry.entry_id}_site")},
        "name": "EG4 Site",
        "manufacturer": "EG4 Electronics",
        "model": "Cloud Site",
    }

    entities = []
    for description in SENSOR_TYPES.values():
        if entry.options.get(description.key, True):
            entities.append(
                EG4Sensor(coordinator, description, entry.entry_id, device_info)
            )

    async_add_entities(entities)


class EG4Sensor(CoordinatorEntity, SensorEntity):
    """Representation of an EG4 sensor."""

    entity_description: EG4SensorEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: EG4SensorEntityDescription,
        entry_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{self.entity_description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        data = self.coordinator.data or {}
        try:
            return self.entity_description.value_fn(data)
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose top open-event rows for the summary sensor."""
        if self.entity_description.key != "uncleared_error_summary":
            return None
        data = self.coordinator.data or {}
        top = _safe_get(data, ("aggregate", "Events", "OpenTop"))
        if isinstance(top, list):
            return {"open_events": top}
        return None
