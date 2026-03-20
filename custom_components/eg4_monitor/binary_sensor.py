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
}


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
        """Expose open-event details on the uncleared error binary sensor."""
        if self.entity_description.key != "uncleared_error_active":
            return None

        data = self.coordinator.data or {}
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
