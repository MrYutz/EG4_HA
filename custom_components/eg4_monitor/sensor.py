"""Support for EG4 Monitor sensors."""

from __future__ import annotations

from datetime import UTC, datetime
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
    UnitOfMass,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
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


@dataclass(frozen=True)
class EG4LoggerSensorDescription:
    """Description for high-rate stateful logger sensors."""

    key: str
    name: str
    value_fn: Callable[[dict[str, Any]], Any]
    native_unit_of_measurement: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None


def _logger_get(data: dict[str, Any], section: str, key: str) -> Any:
    """Read a logger state field without turning missing data into zero."""
    section_data = data.get(section)
    return section_data.get(key) if isinstance(section_data, dict) else None


def _logger_timestamp(value: Any) -> datetime | None:
    """Convert a persisted logger timestamp into a HA timestamp value."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


SENSOR_TYPES: dict[str, EG4SensorEntityDescription] = {
    "battery_soc": EG4SensorEntityDescription(
        key="battery_soc",
        name="Battery State of Charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "SOC")),
    ),
    "battery_portal_soc": EG4SensorEntityDescription(
        key="battery_portal_soc",
        name="Battery Portal SOC (Unverified)",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "PortalSOC")
        ),
    ),
    "battery_local_soc": EG4SensorEntityDescription(
        key="battery_local_soc",
        name="Battery SOC (Local Inverter, Unverified)",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalSOC")
        ),
    ),
    "battery_local_bms_soc": EG4SensorEntityDescription(
        key="battery_local_bms_soc",
        name="Battery SOC (Local BMS)",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalBMSSOC")
        ),
    ),
    "battery_local_soh": EG4SensorEntityDescription(
        key="battery_local_soh",
        name="Battery SOH (Local Inverter, Unverified)",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalSOH")
        ),
    ),
    "battery_local_power": EG4SensorEntityDescription(
        key="battery_local_power",
        name="Battery Power (Local Dongle)",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalPower")
        ),
    ),
    "battery_local_charging_power": EG4SensorEntityDescription(
        key="battery_local_charging_power",
        name="Battery Charging Power (Local Dongle)",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalChargePower")
        ),
    ),
    "battery_local_discharging_power": EG4SensorEntityDescription(
        key="battery_local_discharging_power",
        name="Battery Discharging Power (Local Dongle)",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalDischargePower")
        ),
    ),
    "battery_local_voltage": EG4SensorEntityDescription(
        key="battery_local_voltage",
        name="Battery Voltage (Local)",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalVoltage")
        ),
    ),
    "battery_local_bms_current": EG4SensorEntityDescription(
        key="battery_local_bms_current",
        name="Battery Local BMS Current",
        native_unit_of_measurement="A",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalBMSCurrent")
        ),
    ),
    "battery_local_capacity_ah": EG4SensorEntityDescription(
        key="battery_local_capacity_ah",
        name="Battery Capacity (Local BMS)",
        native_unit_of_measurement="Ah",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalBMSCapacityAh")
        ),
    ),
    "battery_local_max_cell_temperature": EG4SensorEntityDescription(
        key="battery_local_max_cell_temperature",
        name="Battery Maximum Cell Temperature (Local BMS)",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalBMSMaxCellTemperature")
        ),
    ),
    "battery_local_min_cell_temperature": EG4SensorEntityDescription(
        key="battery_local_min_cell_temperature",
        name="Battery Minimum Cell Temperature (Local BMS)",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalBMSMinCellTemperature")
        ),
    ),
    "battery_local_max_cell_voltage": EG4SensorEntityDescription(
        key="battery_local_max_cell_voltage",
        name="Battery Local Maximum Cell Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalBMSMaxCellVoltage")
        ),
    ),
    "battery_local_min_cell_voltage": EG4SensorEntityDescription(
        key="battery_local_min_cell_voltage",
        name="Battery Local Minimum Cell Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalBMSMinCellVoltage")
        ),
    ),
    "battery_local_cycle_count": EG4SensorEntityDescription(
        key="battery_local_cycle_count",
        name="Battery Local Cycle Count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalBMSCycleCount")
        ),
    ),
    "local_dongle_count": EG4SensorEntityDescription(
        key="local_dongle_count",
        name="Local EG4 Dongles Available",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "LocalTelemetry", "Count")
        ),
    ),
    "battery_local_pack_count": EG4SensorEntityDescription(
        key="battery_local_pack_count",
        name="Battery Local Pack Count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "LocalBMSParallelCount")
        ),
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
    "battery_charging_energy_estimated": EG4SensorEntityDescription(
        key="battery_charging_energy_estimated",
        name="Battery Charging Energy (Estimated)",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "battery_discharging_energy_estimated": EG4SensorEntityDescription(
        key="battery_discharging_energy_estimated",
        name="Battery Discharging Energy (Estimated)",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
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
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "RemainingKWh")),
    ),
    "battery_current": EG4SensorEntityDescription(
        key="battery_current",
        name="Battery Current",
        native_unit_of_measurement="A",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "Current")),
    ),
    "battery_temperature": EG4SensorEntityDescription(
        key="battery_temperature",
        name="Battery Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "Temperature")
        ),
    ),
    "battery_type": EG4SensorEntityDescription(
        key="battery_type",
        name="Battery Type",
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "Type")),
    ),
    "battery_status": EG4SensorEntityDescription(
        key="battery_status",
        name="Battery Status",
        value_fn=lambda data: _safe_get(data, ("aggregate", "Battery", "Status")),
    ),
    "battery_cycle_count": EG4SensorEntityDescription(
        key="battery_cycle_count",
        name="Battery Cycle Count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "CycleCount")
        ),
    ),
    "battery_total_voltage": EG4SensorEntityDescription(
        key="battery_total_voltage",
        name="Battery Total Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "TotalVoltage")
        ),
    ),
    "battery_parallel_device_count": EG4SensorEntityDescription(
        key="battery_parallel_device_count",
        name="Battery Parallel Device Count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "ParallelDeviceCount")
        ),
    ),
    "battery_max_cell_temperature": EG4SensorEntityDescription(
        key="battery_max_cell_temperature",
        name="Battery Maximum Cell Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "MaxCellTemperature")
        ),
    ),
    "battery_min_cell_temperature": EG4SensorEntityDescription(
        key="battery_min_cell_temperature",
        name="Battery Minimum Cell Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "MinCellTemperature")
        ),
    ),
    "battery_bms_max_charge_current": EG4SensorEntityDescription(
        key="battery_bms_max_charge_current",
        name="Battery BMS Maximum Charge Current",
        native_unit_of_measurement="A",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "BMS", "MaxChargeCurrent")
        ),
    ),
    "battery_bms_max_discharge_current": EG4SensorEntityDescription(
        key="battery_bms_max_discharge_current",
        name="Battery BMS Maximum Discharge Current",
        native_unit_of_measurement="A",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Battery", "BMS", "MaxDischargeCurrent")
        ),
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
    "pv3_power": EG4SensorEntityDescription(
        key="pv3_power",
        name="PV3 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "Solar", "PV3")),
    ),
    "pv1_voltage": EG4SensorEntityDescription(
        key="pv1_voltage",
        name="PV1 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Solar", "PV1Voltage")
        ),
    ),
    "pv2_voltage": EG4SensorEntityDescription(
        key="pv2_voltage",
        name="PV2 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Solar", "PV2Voltage")
        ),
    ),
    "pv3_voltage": EG4SensorEntityDescription(
        key="pv3_voltage",
        name="PV3 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Solar", "PV3Voltage")
        ),
    ),
    "pv1_current": EG4SensorEntityDescription(
        key="pv1_current",
        name="PV1 Current",
        native_unit_of_measurement="A",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Solar", "PV1Current")
        ),
    ),
    "pv2_current": EG4SensorEntityDescription(
        key="pv2_current",
        name="PV2 Current",
        native_unit_of_measurement="A",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Solar", "PV2Current")
        ),
    ),
    "pv3_current": EG4SensorEntityDescription(
        key="pv3_current",
        name="PV3 Current",
        native_unit_of_measurement="A",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Solar", "PV3Current")
        ),
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
    "gridboss_grid_voltage": EG4SensorEntityDescription(
        key="gridboss_grid_voltage",
        name="GridBOSS Grid Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridBOSS", "GridVoltage")
        ),
    ),
    "gridboss_grid_frequency": EG4SensorEntityDescription(
        key="gridboss_grid_frequency",
        name="GridBOSS Grid Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridBOSS", "GridFrequency")
        ),
    ),
    "gridboss_generator_voltage": EG4SensorEntityDescription(
        key="gridboss_generator_voltage",
        name="GridBOSS Generator Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridBOSS", "GeneratorVoltage")
        ),
    ),
    "gridboss_generator_frequency": EG4SensorEntityDescription(
        key="gridboss_generator_frequency",
        name="GridBOSS Generator Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridBOSS", "GeneratorFrequency")
        ),
    ),
    "gridboss_generator_power": EG4SensorEntityDescription(
        key="gridboss_generator_power",
        name="GridBOSS Generator Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridBOSS", "GeneratorPower")
        ),
    ),
    "gridboss_generator_current": EG4SensorEntityDescription(
        key="gridboss_generator_current",
        name="GridBOSS Generator Current",
        native_unit_of_measurement="A",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridBOSS", "GeneratorCurrent")
        ),
    ),
    "gridboss_ups_voltage": EG4SensorEntityDescription(
        key="gridboss_ups_voltage",
        name="GridBOSS UPS Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridBOSS", "UPSVoltage")
        ),
    ),
    "gridboss_ups_power": EG4SensorEntityDescription(
        key="gridboss_ups_power",
        name="GridBOSS UPS Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "GridBOSS", "UPSPower")),
    ),
    "gridboss_transfer_state": EG4SensorEntityDescription(
        key="gridboss_transfer_state",
        name="GridBOSS Transfer Observation",
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridBOSS", "TransferState")
        ),
    ),
    "flexboss_local_grid_voltage": EG4SensorEntityDescription(
        key="flexboss_local_grid_voltage",
        name="FlexBOSS Local AC Input Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "LocalTelemetry", "ByRole", "flexboss_master", "GridVoltage")
        ),
    ),
    "flexboss_local_grid_frequency": EG4SensorEntityDescription(
        key="flexboss_local_grid_frequency",
        name="FlexBOSS Local AC Input Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "LocalTelemetry", "ByRole", "flexboss_master", "GridFrequency")
        ),
    ),
    "flexboss_local_eps_voltage": EG4SensorEntityDescription(
        key="flexboss_local_eps_voltage",
        name="FlexBOSS Local EPS Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "LocalTelemetry", "ByRole", "flexboss_master", "EPSVoltage")
        ),
    ),
    "flexboss_local_eps_frequency": EG4SensorEntityDescription(
        key="flexboss_local_eps_frequency",
        name="FlexBOSS Local EPS Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "LocalTelemetry", "ByRole", "flexboss_master", "EPSFrequency")
        ),
    ),
    "flexboss_local_eps_power": EG4SensorEntityDescription(
        key="flexboss_local_eps_power",
        name="FlexBOSS Local EPS Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "LocalTelemetry", "ByRole", "flexboss_master", "EPSPower")
        ),
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
    "today_battery_charge": EG4SensorEntityDescription(
        key="today_battery_charge",
        name="Battery Charge Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Today", "Battery Charge")
        ),
    ),
    "today_battery_discharge": EG4SensorEntityDescription(
        key="today_battery_discharge",
        name="Battery Discharge Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Today", "Battery Discharge")
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
    "total_battery_charge": EG4SensorEntityDescription(
        key="total_battery_charge",
        name="Battery Charge Total",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Total", "Battery Charge")
        ),
    ),
    "total_battery_discharge": EG4SensorEntityDescription(
        key="total_battery_discharge",
        name="Battery Discharge Total",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Total", "Battery Discharge")
        ),
    ),
    "total_co2_reduction": EG4SensorEntityDescription(
        key="total_co2_reduction",
        name="Total CO2 Reduction",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Environmental", "CO2 Reduction")
        ),
    ),
    "total_coal_reduction": EG4SensorEntityDescription(
        key="total_coal_reduction",
        name="Total Coal Reduction",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Energy", "Environmental", "Coal Reduction")
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
    "inverter_status": EG4SensorEntityDescription(
        key="inverter_status",
        name="Inverter Status",
        value_fn=lambda data: _safe_get(data, ("aggregate", "Device", "Status")),
    ),
    "inverter_model": EG4SensorEntityDescription(
        key="inverter_model",
        name="Inverter Model",
        value_fn=lambda data: _safe_get(data, ("aggregate", "Device", "ModelText")),
    ),
    "inverter_firmware": EG4SensorEntityDescription(
        key="inverter_firmware",
        name="Inverter Firmware",
        value_fn=lambda data: _safe_get(data, ("aggregate", "Device", "Firmware")),
    ),
    "inverter_ac_power": EG4SensorEntityDescription(
        key="inverter_ac_power",
        name="Inverter AC Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "InverterDetails", "ACPower")
        ),
    ),
    "inverter_rectifier_power": EG4SensorEntityDescription(
        key="inverter_rectifier_power",
        name="Inverter Rectifier Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "InverterDetails", "RectifierPower")
        ),
    ),
    "inverter_vbus_1": EG4SensorEntityDescription(
        key="inverter_vbus_1",
        name="Inverter VBus 1",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "InverterDetails", "VBus1")
        ),
    ),
    "inverter_vbus_2": EG4SensorEntityDescription(
        key="inverter_vbus_2",
        name="Inverter VBus 2",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "InverterDetails", "VBus2")
        ),
    ),
    "inverter_inner_temperature": EG4SensorEntityDescription(
        key="inverter_inner_temperature",
        name="Inverter Inner Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Temperatures", "InverterInner")
        ),
    ),
    "radiator_1_temperature": EG4SensorEntityDescription(
        key="radiator_1_temperature",
        name="Radiator 1 Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Temperatures", "Radiator1")
        ),
    ),
    "radiator_2_temperature": EG4SensorEntityDescription(
        key="radiator_2_temperature",
        name="Radiator 2 Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Temperatures", "Radiator2")
        ),
    ),
    "environment_temperature": EG4SensorEntityDescription(
        key="environment_temperature",
        name="Environment Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Temperatures", "Environment")
        ),
    ),
    "ac_coupled_power": EG4SensorEntityDescription(
        key="ac_coupled_power",
        name="AC Coupled Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "ACCouple", "Power")),
    ),
    "smart_load_power": EG4SensorEntityDescription(
        key="smart_load_power",
        name="Smart Load Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(data, ("aggregate", "SmartLoad", "Power")),
    ),
    "grid_quality_state": EG4SensorEntityDescription(
        key="grid_quality_state",
        name="Grid Quality State",
        value_fn=lambda data: _safe_get(data, ("aggregate", "GridQuality", "State")),
    ),
    "grid_outage_duration_24h": EG4SensorEntityDescription(
        key="grid_outage_duration_24h",
        name="Grid Outage Duration 24h",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridQuality", "OutageDurationMinutes24h")
        ),
    ),
    "grid_outage_count_24h": EG4SensorEntityDescription(
        key="grid_outage_count_24h",
        name="Grid Outage Count 24h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridQuality", "OutageCount24h")
        ),
    ),
    "grid_brownout_count_24h": EG4SensorEntityDescription(
        key="grid_brownout_count_24h",
        name="Grid Brownout Count 24h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "GridQuality", "BrownoutCount24h")
        ),
    ),
    "battery_estimated_runtime": EG4SensorEntityDescription(
        key="battery_estimated_runtime",
        name="Battery Estimated Runtime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Battery", "EstimatedRuntimeHours")
        ),
    ),
    "battery_health_confidence": EG4SensorEntityDescription(
        key="battery_health_confidence",
        name="Battery Health Confidence",
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Battery", "HealthConfidence")
        ),
    ),
    "battery_pack_count": EG4SensorEntityDescription(
        key="battery_pack_count",
        name="Battery Pack Count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            _safe_get(data, ("aggregate", "Backup", "Battery", "PackCount"))
            or _safe_get(data, ("aggregate", "Battery", "ParallelDeviceCount"))
            or _safe_get(data, ("aggregate", "Battery", "TotalNumber"))
        ),
    ),
    "battery_lowest_voltage": EG4SensorEntityDescription(
        key="battery_lowest_voltage",
        name="Lowest Battery Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Battery", "LowestVoltage")
        ),
    ),
    "battery_highest_voltage": EG4SensorEntityDescription(
        key="battery_highest_voltage",
        name="Highest Battery Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Battery", "HighestVoltage")
        ),
    ),
    "battery_voltage_spread": EG4SensorEntityDescription(
        key="battery_voltage_spread",
        name="Battery Voltage Spread",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Battery", "VoltageSpread")
        ),
    ),
    "diesel_fuel_level": EG4SensorEntityDescription(
        key="diesel_fuel_level",
        name="Diesel Fuel Level",
        native_unit_of_measurement="gal",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Fuel", "FuelLevelGallons")
        ),
    ),
    "diesel_fuel_percent": EG4SensorEntityDescription(
        key="diesel_fuel_percent",
        name="Diesel Fuel Percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Fuel", "FuelPercent")
        ),
    ),
    "diesel_burn_rate": EG4SensorEntityDescription(
        key="diesel_burn_rate",
        name="Diesel Burn Rate",
        native_unit_of_measurement="gal/h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Fuel", "BurnRateGPH")
        ),
    ),
    "generator_fuel_hours_remaining": EG4SensorEntityDescription(
        key="generator_fuel_hours_remaining",
        name="Generator Fuel Hours Remaining",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _safe_get(
            data, ("aggregate", "Backup", "Fuel", "HoursRemaining")
        ),
    ),
}


LOGGER_SENSOR_TYPES: tuple[EG4LoggerSensorDescription, ...] = (
    EG4LoggerSensorDescription(
        "generator_run_state",
        "Generator Run State",
        lambda data: _logger_get(data, "Generator", "RunState"),
    ),
    EG4LoggerSensorDescription(
        "generator_presence_state",
        "Generator AC Presence State",
        lambda data: _logger_get(data, "Generator", "PresenceState"),
    ),
    EG4LoggerSensorDescription(
        "generator_supply_state",
        "Generator Supply State",
        lambda data: _logger_get(data, "Generator", "SupplyState"),
    ),
    EG4LoggerSensorDescription(
        "generator_state_quality",
        "Generator State Quality",
        lambda data: _logger_get(data, "Generator", "Quality"),
    ),
    EG4LoggerSensorDescription(
        "generator_state_confidence",
        "Generator State Confidence",
        lambda data: _logger_get(data, "Generator", "Confidence"),
    ),
    EG4LoggerSensorDescription(
        "generator_run_duration",
        "Generator Run Duration",
        lambda data: _logger_get(data, "Generator", "RunDurationSeconds"),
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EG4LoggerSensorDescription(
        "generator_last_transition",
        "Generator Last Transition",
        lambda data: _logger_timestamp(_logger_get(data, "Generator", "LastTransition")),
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    EG4LoggerSensorDescription(
        "generator_last_on",
        "Generator Last On",
        lambda data: _logger_timestamp(_logger_get(data, "Generator", "LastOn")),
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    EG4LoggerSensorDescription(
        "generator_last_off",
        "Generator Last Off",
        lambda data: _logger_timestamp(_logger_get(data, "Generator", "LastOff")),
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    EG4LoggerSensorDescription(
        "generator_last_transition_reason",
        "Generator Last Transition Reason",
        lambda data: _logger_get(data, "Generator", "Reason"),
    ),
    EG4LoggerSensorDescription(
        "charge_session_state",
        "Battery Charge Session State",
        lambda data: _logger_get(data, "Charging", "State"),
    ),
    EG4LoggerSensorDescription(
        "charge_session_quality",
        "Battery Charge Session Quality",
        lambda data: _logger_get(data, "Charging", "Quality"),
    ),
    EG4LoggerSensorDescription(
        "charge_session_source",
        "Battery Charge Session Source",
        lambda data: _logger_get(data, "Charging", "Source"),
    ),
    EG4LoggerSensorDescription(
        "charge_session_duration",
        "Battery Charge Session Duration",
        lambda data: (
            _logger_get(data, "Charging", "ActiveSession").get("duration_seconds")
            if isinstance(_logger_get(data, "Charging", "ActiveSession"), dict)
            else None
        ),
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EG4LoggerSensorDescription(
        "charge_session_start",
        "Battery Charge Session Start",
        lambda data: _logger_timestamp(
            (
                _logger_get(data, "Charging", "ActiveSession") or {}
            ).get("started_at")
            if isinstance(_logger_get(data, "Charging", "ActiveSession"), dict)
            else None
        ),
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    EG4LoggerSensorDescription(
        "last_charge_session_start",
        "Last Battery Charge Session Start",
        lambda data: _logger_timestamp(
            (
                _logger_get(data, "Charging", "LastSession") or {}
            ).get("started_at")
            if isinstance(_logger_get(data, "Charging", "LastSession"), dict)
            else None
        ),
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    EG4LoggerSensorDescription(
        "last_charge_session_end",
        "Last Battery Charge Session End",
        lambda data: _logger_timestamp(
            (
                _logger_get(data, "Charging", "LastSession") or {}
            ).get("ended_at")
            if isinstance(_logger_get(data, "Charging", "LastSession"), dict)
            else None
        ),
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    EG4LoggerSensorDescription(
        "last_charge_session_duration",
        "Last Battery Charge Session Duration",
        lambda data: (
            _logger_get(data, "Charging", "LastSession").get("duration_seconds")
            if isinstance(_logger_get(data, "Charging", "LastSession"), dict)
            else None
        ),
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EG4LoggerSensorDescription(
        "charge_session_count",
        "Battery Charge Session Count",
        lambda data: _logger_get(data, "Charging", "SessionCount"),
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    EG4LoggerSensorDescription(
        "charge_session_energy_estimated",
        "Battery Charge Session Energy (Estimated)",
        lambda data: (
            _logger_get(data, "Charging", "ActiveSession").get("energy_estimated_kwh")
            if isinstance(_logger_get(data, "Charging", "ActiveSession"), dict)
            else (
                _logger_get(data, "Charging", "LastSession").get("energy_estimated_kwh")
                if isinstance(_logger_get(data, "Charging", "LastSession"), dict)
                else None
            )
        ),
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EG4 sensors based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    logger_coordinator = hass.data[DOMAIN][entry.entry_id].get("logger_coordinator")
    device_info = {
        "identifiers": {(DOMAIN, f"{entry.entry_id}_site")},
        "name": "EG4 Site",
        "manufacturer": "EG4 Electronics",
        "model": "Cloud Site",
    }

    entities = []
    for description in SENSOR_TYPES.values():
        if description.key in {
            "battery_charging_energy_estimated",
            "battery_discharging_energy_estimated",
        }:
            continue
        if entry.options.get(description.key, True):
            entities.append(
                EG4Sensor(
                    logger_coordinator
                    if description.key == "local_dongle_count"
                    and logger_coordinator is not None
                    else coordinator,
                    description,
                    entry.entry_id,
                    device_info,
                )
            )

    if entry.options.get("battery_charging_energy_estimated", True):
        entities.append(
            EG4IntegratedEnergySensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                key="battery_charging_energy_estimated",
                name="Battery Charging Energy (Estimated)",
                source_path=("aggregate", "Battery", "ChargePower"),
                device_info=device_info,
            )
        )

    if entry.options.get("battery_discharging_energy_estimated", True):
        entities.append(
            EG4IntegratedEnergySensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                key="battery_discharging_energy_estimated",
                name="Battery Discharging Energy (Estimated)",
                source_path=("aggregate", "Battery", "DischargePower"),
                device_info=device_info,
            )
        )

    if logger_coordinator is not None:
        entities.extend(
            EG4LoggerSensor(
                logger_coordinator,
                description,
                entry.entry_id,
                device_info,
            )
            for description in LOGGER_SENSOR_TYPES
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
        """Expose diagnostic context for derived and summary sensors."""
        data = self.coordinator.data or {}
        key = self.entity_description.key

        if key.startswith("gridboss_") or key.startswith("flexboss_local_"):
            telemetry = _safe_get(data, ("aggregate", "GridBOSS"))
            if key.startswith("flexboss_local_"):
                telemetry = _safe_get(
                    data,
                    ("aggregate", "LocalTelemetry", "ByRole", "flexboss_master"),
                )
            return telemetry if isinstance(telemetry, dict) else None

        if key == "grid_quality_state":
            quality = _safe_get(data, ("aggregate", "GridQuality"))
            return quality if isinstance(quality, dict) else None

        if key == "battery_pack_count":
            packs = _safe_get(data, ("aggregate", "Battery", "PackVoltages"))
            backup_battery = _safe_get(data, ("aggregate", "Backup", "Battery"))
            attrs = {"packs": packs} if isinstance(packs, list) else {}
            if isinstance(backup_battery, dict):
                attrs.update(backup_battery)
            return attrs or None

        if key == "local_dongle_count":
            local = _safe_get(data, ("aggregate", "LocalTelemetry"))
            return local if isinstance(local, dict) else None

        if key.startswith("battery_"):
            backup_battery = _safe_get(data, ("aggregate", "Backup", "Battery"))
            battery = _safe_get(data, ("aggregate", "Battery"))
            attrs = dict(backup_battery) if isinstance(backup_battery, dict) else {}
            if isinstance(battery, dict):
                for attr_key in (
                    "PortalSOC",
                    "PortalSOCSource",
                    "PortalSOCQuality",
                    "SOCSource",
                    "SOCQuality",
                    "SOCUnavailableReason",
                    "PortalCurrent",
                    "CurrentSource",
                    "LocalSOC",
                    "LocalSOH",
                    "LocalSOCSource",
                    "LocalSOCQuality",
                    "LocalBMSSOC",
                    "LocalBMSSource",
                    "LocalBMSQuality",
                    "LocalDongleSerial",
                    "LocalInverterSerial",
                    "LocalBMSRole",
                    "LocalVoltage",
                    "LocalPower",
                    "LocalChargePower",
                    "LocalDischargePower",
                    "LocalBMSAvailable",
                    "LocalBMSParallelCount",
                    "LocalBMSCapacityAh",
                    "LocalBMSCurrent",
                    "LocalBMSMaxCellVoltage",
                    "LocalBMSMinCellVoltage",
                    "LocalBMSMaxCellTemperature",
                    "LocalBMSMinCellTemperature",
                    "LocalBMSCycleCount",
                ):
                    if attr_key in battery:
                        attrs[attr_key] = battery[attr_key]
            return attrs or None

        if key.startswith("diesel_") or key == "generator_fuel_hours_remaining":
            fuel = _safe_get(data, ("aggregate", "Backup", "Fuel"))
            return fuel if isinstance(fuel, dict) else None

        if key == "uncleared_error_summary":
            top = _safe_get(data, ("aggregate", "Events", "OpenTop"))
            if isinstance(top, list):
                return {"open_events": top}

        return None


class EG4LoggerSensor(CoordinatorEntity, SensorEntity):
    """High-rate state/session sensor backed by the EG4 logger coordinator."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: EG4LoggerSensorDescription,
        entry_id: str,
        device_info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self.description = description
        self._attr_name = description.name
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_suggested_object_id = description.key
        self._attr_device_info = device_info
        if description.native_unit_of_measurement is not None:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        if description.device_class is not None:
            self._attr_device_class = description.device_class
        if description.state_class is not None:
            self._attr_state_class = description.state_class

    @property
    def suggested_object_id(self) -> str:
        """Keep logger entity IDs stable for dashboard and automation use."""
        return self.description.key

    @property
    def native_value(self) -> Any:
        """Return the current logger value."""
        data = self.coordinator.data or {}
        try:
            return self.description.value_fn(data)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the full relevant logger section for dashboard forensics."""
        data = self.coordinator.data or {}
        section = "Generator" if self.description.key.startswith("generator_") else "Charging"
        value = data.get(section)
        return value if isinstance(value, dict) else None


class EG4IntegratedEnergySensor(CoordinatorEntity, SensorEntity, RestoreEntity):
    """Integrate positive power samples into total increasing kWh."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry_id: str,
        key: str,
        name: str,
        source_path: tuple[str, ...],
        device_info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = device_info
        self._source_path = source_path
        self._energy_kwh: float = 0.0
        self._last_sample_ts: datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._energy_kwh = float(last_state.state)
            except (TypeError, ValueError):
                self._energy_kwh = 0.0

    @property
    def native_value(self) -> StateType:
        return round(self._energy_kwh, 5)

    def _handle_coordinator_update(self) -> None:
        ts = datetime.now(UTC) if self.coordinator.last_update_success else None
        data = self.coordinator.data or {}
        if _safe_get(data, ("aggregate", "DataFreshness", "IsStale")):
            # Do not integrate cached last-good power as if it were live.
            if ts is not None:
                self._last_sample_ts = ts
            self.async_write_ha_state()
            return

        power = _safe_get(data, self._source_path)
        try:
            power_w = max(float(power or 0.0), 0.0)
        except (TypeError, ValueError):
            power_w = 0.0

        if ts is not None and self._last_sample_ts is not None:
            delta_seconds = (ts - self._last_sample_ts).total_seconds()
            if delta_seconds > 0:
                self._energy_kwh += (power_w * (delta_seconds / 3600.0)) / 1000.0

        if ts is not None:
            self._last_sample_ts = ts

        self.async_write_ha_state()
