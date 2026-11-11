"""Config flow for EG4 Monitor integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .backup_metrics import (
    DEFAULT_BATTERY_CRITICAL_VOLTAGE,
    DEFAULT_BATTERY_HIGH_SOC_MIN_VOLTAGE,
    DEFAULT_BATTERY_HIGH_SOC_THRESHOLD,
    DEFAULT_BATTERY_IMBALANCE_THRESHOLD,
    DEFAULT_BATTERY_RESERVE_PERCENT,
    DEFAULT_DIESEL_REORDER_PERCENT,
    DEFAULT_GENERATOR_FULL_LOAD_BURN_GPH,
    DEFAULT_GENERATOR_IDLE_BURN_GPH,
    DEFAULT_GENERATOR_MIN_SUPPLY_POWER_W,
    DEFAULT_GENERATOR_RATED_POWER_KW,
    DEFAULT_LOAD_SHED_RUNTIME_HOURS,
)
from .const import (
    CONF_BATTERY_CRITICAL_VOLTAGE,
    CONF_BATTERY_DETAIL_INTERVAL,
    CONF_BATTERY_HIGH_SOC_MIN_VOLTAGE,
    CONF_BATTERY_HIGH_SOC_THRESHOLD,
    CONF_BATTERY_IMBALANCE_THRESHOLD,
    CONF_BATTERY_RESERVE_PERCENT,
    CONF_DIESEL_FUEL_LEVEL_GALLONS,
    CONF_DIESEL_REORDER_PERCENT,
    CONF_DIESEL_TANK_CAPACITY_GALLONS,
    CONF_GENERATOR_FULL_LOAD_BURN_GPH,
    CONF_GENERATOR_IDLE_BURN_GPH,
    CONF_GENERATOR_MIN_SUPPLY_POWER_W,
    CONF_GENERATOR_RATED_POWER_KW,
    CONF_GRIDBOSS_SERIAL,
    CONF_LOAD_SHED_RUNTIME_HOURS,
    CONF_LOCAL_AUTO_DISCOVERY,
    CONF_LOCAL_DONGLE_PORT,
    CONF_LOCAL_GRIDBOSS_FAST_MODE,
    CONF_LOCAL_GRIDBOSS_HOST,
    CONF_LOCAL_MASTER_HOST,
    CONF_LOCAL_SLAVE_HOST,
    CONF_LOCAL_TRACE_BMS_INTERVAL,
    CONF_LOCAL_TRACE_ENABLED,
    CONF_LOCAL_TRACE_IDLE_INTERVAL,
    CONF_LOCAL_TRACE_INCIDENT_MAX_BYTES,
    CONF_LOCAL_TRACE_INTERVAL,
    CONF_LOCAL_TRACE_JOURNAL_MAX_BYTES,
    CONF_LOCAL_TRACE_RAW_MAX_BYTES,
    CONF_MASTER_SERIAL,
    CONF_SLAVE_SERIAL,
    DEFAULT_BATTERY_DETAIL_INTERVAL,
    DEFAULT_LOCAL_AUTO_DISCOVERY,
    DEFAULT_LOCAL_DONGLE_PORT,
    DEFAULT_LOCAL_GRIDBOSS_FAST_MODE,
    DEFAULT_LOCAL_GRIDBOSS_HOST,
    DEFAULT_LOCAL_TRACE_BMS_INTERVAL,
    DEFAULT_LOCAL_TRACE_ENABLED,
    DEFAULT_LOCAL_TRACE_IDLE_INTERVAL,
    DEFAULT_LOCAL_TRACE_INTERVAL,
    DOMAIN,
    LOCAL_GRIDBOSS_FAST_MODES,
)
from .eg4_api import EG4API

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

SENSOR_TYPES = {
    "battery_soc": "Battery State of Charge",
    "battery_portal_soc": "Battery Portal SOC (Unverified)",
    "battery_power": "Battery Power",
    "battery_charging_power": "Battery Charging Power",
    "battery_discharging_power": "Battery Discharging Power",
    "battery_power_direction": "Battery Power Direction",
    "battery_charging_energy_estimated": "Battery Charging Energy (Estimated)",
    "battery_discharging_energy_estimated": "Battery Discharging Energy (Estimated)",
    "battery_voltage": "Battery Voltage",
    "battery_remaining_kwh": "Battery Energy Remaining",
    "battery_current": "Battery Current",
    "battery_temperature": "Battery Temperature",
    "battery_type": "Battery Type",
    "battery_status": "Battery Status",
    "battery_cycle_count": "Battery Cycle Count",
    "battery_total_voltage": "Battery Total Voltage",
    "battery_parallel_device_count": "Battery Parallel Device Count",
    "battery_max_cell_temperature": "Battery Maximum Cell Temperature",
    "battery_min_cell_temperature": "Battery Minimum Cell Temperature",
    "battery_bms_max_charge_current": "Battery BMS Maximum Charge Current",
    "battery_bms_max_discharge_current": "Battery BMS Maximum Discharge Current",
    "solar_power": "Solar Power",
    "pv1_power": "PV1 Power",
    "pv2_power": "PV2 Power",
    "pv3_power": "PV3 Power",
    "pv1_voltage": "PV1 Voltage",
    "pv2_voltage": "PV2 Voltage",
    "pv3_voltage": "PV3 Voltage",
    "pv1_current": "PV1 Current",
    "pv2_current": "PV2 Current",
    "pv3_current": "PV3 Current",
    "grid_power": "Grid Power",
    "grid_voltage": "Grid Voltage",
    "grid_frequency": "Grid Frequency",
    "gridboss_grid_voltage": "GridBOSS Grid Voltage",
    "gridboss_grid_frequency": "GridBOSS Grid Frequency",
    "gridboss_generator_voltage": "GridBOSS Generator Voltage",
    "gridboss_generator_frequency": "GridBOSS Generator Frequency",
    "gridboss_generator_power": "GridBOSS Generator Power",
    "gridboss_generator_current": "GridBOSS Generator Current",
    "gridboss_ups_voltage": "GridBOSS UPS Voltage",
    "gridboss_ups_power": "GridBOSS UPS Power",
    "gridboss_transfer_state": "GridBOSS Transfer Observation",
    "flexboss_local_grid_voltage": "FlexBOSS Local AC Input Voltage",
    "flexboss_local_grid_frequency": "FlexBOSS Local AC Input Frequency",
    "flexboss_local_eps_voltage": "FlexBOSS Local EPS Voltage",
    "flexboss_local_eps_frequency": "FlexBOSS Local EPS Frequency",
    "flexboss_local_eps_power": "FlexBOSS Local EPS Power",
    "load_power": "Load Power",
    "eps_power": "EPS Power",
    "eps_voltage": "EPS Voltage",
    "eps_frequency": "EPS Frequency",
    "today_solar": "Today's Solar Yield",
    "today_grid_import": "Today's Usage",
    "today_grid_export": "Today Export",
    "today_ups_usage": "UPS Usage Today",
    "today_battery_charge": "Battery Charge Today",
    "today_battery_discharge": "Battery Discharge Today",
    "total_solar": "Total Solar Yield",
    "total_grid_import": "Total Usage",
    "total_grid_export": "Total Export",
    "total_ups_usage": "UPS Usage Total",
    "total_battery_charge": "Battery Charge Total",
    "total_battery_discharge": "Battery Discharge Total",
    "total_co2_reduction": "Total CO2 Reduction",
    "total_coal_reduction": "Total Coal Reduction",
    "generator_voltage": "Generator Voltage",
    "generator_frequency": "Generator Frequency",
    "generator_power": "Generator Power",
    "generator_status_source": "Generator Status Source",
    "uncleared_error_count": "Uncleared Error Count",
    "uncleared_error_summary": "Uncleared Error Summary",
    "eg4_device_count": "EG4 Device Count",
    "inverter_status": "Inverter Status",
    "inverter_model": "Inverter Model",
    "inverter_firmware": "Inverter Firmware",
    "inverter_ac_power": "Inverter AC Power",
    "inverter_rectifier_power": "Inverter Rectifier Power",
    "inverter_vbus_1": "Inverter VBus 1",
    "inverter_vbus_2": "Inverter VBus 2",
    "inverter_inner_temperature": "Inverter Inner Temperature",
    "radiator_1_temperature": "Radiator 1 Temperature",
    "radiator_2_temperature": "Radiator 2 Temperature",
    "environment_temperature": "Environment Temperature",
    "ac_coupled_power": "AC Coupled Power",
    "smart_load_power": "Smart Load Power",
    "grid_quality_state": "Grid Quality State",
    "grid_outage_duration_24h": "Grid Outage Duration 24h",
    "grid_outage_count_24h": "Grid Outage Count 24h",
    "grid_brownout_count_24h": "Grid Brownout Count 24h",
    "battery_estimated_runtime": "Battery Estimated Runtime",
    "battery_health_confidence": "Battery Health Confidence",
    "battery_local_soc": "Battery SOC (Local Inverter, Unverified)",
    "battery_local_bms_soc": "Battery SOC (Local BMS)",
    "battery_local_soh": "Battery SOH (Local Inverter, Unverified)",
    "battery_local_voltage": "Battery Voltage (Local Dongle)",
    "battery_local_power": "Battery Power (Local Dongle)",
    "battery_local_charging_power": "Battery Charging Power (Local Dongle)",
    "battery_local_discharging_power": "Battery Discharging Power (Local Dongle)",
    "battery_local_bms_current": "Battery Local BMS Current",
    "battery_local_capacity_ah": "Battery Capacity (Local BMS)",
    "battery_local_max_cell_voltage": "Battery Local Maximum Cell Voltage",
    "battery_local_min_cell_voltage": "Battery Local Minimum Cell Voltage",
    "battery_local_max_cell_temperature": "Battery Maximum Cell Temperature (Local BMS)",
    "battery_local_min_cell_temperature": "Battery Minimum Cell Temperature (Local BMS)",
    "battery_local_cycle_count": "Battery Local Cycle Count",
    "battery_local_pack_count": "Battery Local Pack Count",
    "local_dongle_count": "Local EG4 Dongles Available",
    "battery_pack_count": "Battery Pack Count",
    "battery_lowest_voltage": "Lowest Battery Voltage",
    "battery_highest_voltage": "Highest Battery Voltage",
    "battery_voltage_spread": "Battery Voltage Spread",
    "diesel_fuel_level": "Diesel Fuel Level",
    "diesel_fuel_percent": "Diesel Fuel Percent",
    "diesel_burn_rate": "Diesel Burn Rate",
    "generator_fuel_hours_remaining": "Generator Fuel Hours Remaining",
}

DEFAULT_SCAN_INTERVAL = 120


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    _LOGGER.debug("Starting validation of EG4 credentials")

    session = async_get_clientsession(hass)

    api: EG4API | None = None
    try:
        api = EG4API(
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            session=session,
        )

        _LOGGER.debug("Attempting to login to EG4")
        login_result = await api.login()

        if not login_result:
            _LOGGER.error("Login failed with invalid credentials")
            raise InvalidAuth

        _LOGGER.debug("Login successful, attempting to get plant and inverter list")
        plant_info = await api.get_plant_info()
        if not plant_info or not api.plant_id:
            _LOGGER.error("Could not retrieve plant info after successful login")
            raise CannotConnect

        inverters = await api.get_all_inverters(plant_id_to_fetch=api.plant_id)
        if not inverters:
            _LOGGER.error("Could not retrieve inverter list for plant")
            raise CannotConnect

        serials = [
            str(item["serialNum"]) for item in inverters if item.get("serialNum")
        ]

        _LOGGER.debug("Successfully validated credentials and inverter list")
        return {
            "title": f"EG4 Monitor ({data[CONF_USERNAME]})",
            "serials": serials,
        }

    except aiohttp.ClientError as err:
        _LOGGER.error("Connection error occurred: %s", err)
        raise CannotConnect from err
    except Exception as err:
        _LOGGER.exception("Unexpected error occurred during validation: %s", err)
        raise CannotConnect from err
    finally:
        if api is not None:
            await api.close()


class SensorSelectFlow(config_entries.OptionsFlow):
    """Handle sensor selection options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle sensor selection."""
        if user_input is not None:
            # Advanced host-retention ceilings have no additional UI controls.
            data = {
                key: self.config_entry.options[key]
                for key in (
                    CONF_LOCAL_TRACE_RAW_MAX_BYTES,
                    CONF_LOCAL_TRACE_JOURNAL_MAX_BYTES,
                    CONF_LOCAL_TRACE_INCIDENT_MAX_BYTES,
                )
                if key in self.config_entry.options
            }
            return self.async_create_entry(title="", data={**data, **user_input})

        options: dict[Any, Any] = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            ): vol.All(int, vol.Range(min=30, max=3600)),
            vol.Optional(
                CONF_BATTERY_DETAIL_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_BATTERY_DETAIL_INTERVAL, DEFAULT_BATTERY_DETAIL_INTERVAL
                ),
            ): vol.All(int, vol.Range(min=60, max=86400)),
            vol.Optional(
                CONF_LOCAL_AUTO_DISCOVERY,
                default=self.config_entry.options.get(
                    CONF_LOCAL_AUTO_DISCOVERY, DEFAULT_LOCAL_AUTO_DISCOVERY
                ),
            ): bool,
            vol.Optional(
                CONF_LOCAL_MASTER_HOST,
                default=self.config_entry.options.get(CONF_LOCAL_MASTER_HOST, ""),
            ): str,
            vol.Optional(
                CONF_LOCAL_SLAVE_HOST,
                default=self.config_entry.options.get(CONF_LOCAL_SLAVE_HOST, ""),
            ): str,
            vol.Optional(
                CONF_LOCAL_GRIDBOSS_HOST,
                default=self.config_entry.options.get(
                    CONF_LOCAL_GRIDBOSS_HOST, DEFAULT_LOCAL_GRIDBOSS_HOST
                ),
            ): str,
            vol.Optional(
                CONF_LOCAL_DONGLE_PORT,
                default=self.config_entry.options.get(
                    CONF_LOCAL_DONGLE_PORT, DEFAULT_LOCAL_DONGLE_PORT
                ),
            ): vol.All(int, vol.Range(min=1, max=65535)),
            vol.Optional(
                CONF_LOCAL_TRACE_ENABLED,
                default=self.config_entry.options.get(
                    CONF_LOCAL_TRACE_ENABLED, DEFAULT_LOCAL_TRACE_ENABLED
                ),
            ): bool,
            vol.Optional(
                CONF_LOCAL_TRACE_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_LOCAL_TRACE_INTERVAL, DEFAULT_LOCAL_TRACE_INTERVAL
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=1, max=60)),
            vol.Optional(
                CONF_LOCAL_TRACE_BMS_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_LOCAL_TRACE_BMS_INTERVAL, DEFAULT_LOCAL_TRACE_BMS_INTERVAL
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=2, max=300)),
            vol.Optional(
                CONF_LOCAL_GRIDBOSS_FAST_MODE,
                default=self.config_entry.options.get(
                    CONF_LOCAL_GRIDBOSS_FAST_MODE, DEFAULT_LOCAL_GRIDBOSS_FAST_MODE
                ),
            ): vol.In(LOCAL_GRIDBOSS_FAST_MODES),
            vol.Optional(
                CONF_LOCAL_TRACE_IDLE_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_LOCAL_TRACE_IDLE_INTERVAL, DEFAULT_LOCAL_TRACE_IDLE_INTERVAL
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=600)),
            vol.Optional(
                CONF_BATTERY_HIGH_SOC_THRESHOLD,
                default=self.config_entry.options.get(
                    CONF_BATTERY_HIGH_SOC_THRESHOLD,
                    DEFAULT_BATTERY_HIGH_SOC_THRESHOLD,
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Optional(
                CONF_BATTERY_HIGH_SOC_MIN_VOLTAGE,
                default=self.config_entry.options.get(
                    CONF_BATTERY_HIGH_SOC_MIN_VOLTAGE,
                    DEFAULT_BATTERY_HIGH_SOC_MIN_VOLTAGE,
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_BATTERY_CRITICAL_VOLTAGE,
                default=self.config_entry.options.get(
                    CONF_BATTERY_CRITICAL_VOLTAGE,
                    DEFAULT_BATTERY_CRITICAL_VOLTAGE,
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_BATTERY_IMBALANCE_THRESHOLD,
                default=self.config_entry.options.get(
                    CONF_BATTERY_IMBALANCE_THRESHOLD,
                    DEFAULT_BATTERY_IMBALANCE_THRESHOLD,
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_BATTERY_RESERVE_PERCENT,
                default=self.config_entry.options.get(
                    CONF_BATTERY_RESERVE_PERCENT, DEFAULT_BATTERY_RESERVE_PERCENT
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Optional(
                CONF_DIESEL_TANK_CAPACITY_GALLONS,
                default=self.config_entry.options.get(
                    CONF_DIESEL_TANK_CAPACITY_GALLONS, 0
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_DIESEL_FUEL_LEVEL_GALLONS,
                default=self.config_entry.options.get(
                    CONF_DIESEL_FUEL_LEVEL_GALLONS, 0
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_DIESEL_REORDER_PERCENT,
                default=self.config_entry.options.get(
                    CONF_DIESEL_REORDER_PERCENT, DEFAULT_DIESEL_REORDER_PERCENT
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Optional(
                CONF_GENERATOR_IDLE_BURN_GPH,
                default=self.config_entry.options.get(
                    CONF_GENERATOR_IDLE_BURN_GPH, DEFAULT_GENERATOR_IDLE_BURN_GPH
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_GENERATOR_FULL_LOAD_BURN_GPH,
                default=self.config_entry.options.get(
                    CONF_GENERATOR_FULL_LOAD_BURN_GPH,
                    DEFAULT_GENERATOR_FULL_LOAD_BURN_GPH,
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_GENERATOR_RATED_POWER_KW,
                default=self.config_entry.options.get(
                    CONF_GENERATOR_RATED_POWER_KW,
                    DEFAULT_GENERATOR_RATED_POWER_KW,
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_GENERATOR_MIN_SUPPLY_POWER_W,
                default=self.config_entry.options.get(
                    CONF_GENERATOR_MIN_SUPPLY_POWER_W,
                    DEFAULT_GENERATOR_MIN_SUPPLY_POWER_W,
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_LOAD_SHED_RUNTIME_HOURS,
                default=self.config_entry.options.get(
                    CONF_LOAD_SHED_RUNTIME_HOURS, DEFAULT_LOAD_SHED_RUNTIME_HOURS
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
        }

        serials = self.config_entry.data.get("serials", [])
        role_choices = {"": "Auto"}
        for serial in serials:
            role_choices[str(serial)] = str(serial)

        if role_choices:
            options[
                vol.Optional(
                    CONF_MASTER_SERIAL,
                    default=self.config_entry.options.get(CONF_MASTER_SERIAL, ""),
                )
            ] = vol.In(role_choices)
            options[
                vol.Optional(
                    CONF_SLAVE_SERIAL,
                    default=self.config_entry.options.get(CONF_SLAVE_SERIAL, ""),
                )
            ] = vol.In(role_choices)
            options[
                vol.Optional(
                    CONF_GRIDBOSS_SERIAL,
                    default=self.config_entry.options.get(CONF_GRIDBOSS_SERIAL, ""),
                )
            ] = vol.In(role_choices)

        for sensor_id, description in SENSOR_TYPES.items():
            options[
                vol.Optional(
                    sensor_id,
                    default=self.config_entry.options.get(sensor_id, True),
                    description=description,
                )
            ] = bool

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(options),
            description_placeholders={
                "sensor_descriptions": "\n".join(
                    f"- {description}" for description in SENSOR_TYPES.values()
                )
            },
        )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EG4 Monitor."""

    VERSION = 1

    @staticmethod
    @config_entries.HANDLERS.register(DOMAIN)
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return SensorSelectFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                options: dict[str, Any] = {
                    sensor_id: True for sensor_id in SENSOR_TYPES
                }
                options[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
                options[CONF_BATTERY_DETAIL_INTERVAL] = DEFAULT_BATTERY_DETAIL_INTERVAL
                options[CONF_MASTER_SERIAL] = ""
                options[CONF_SLAVE_SERIAL] = ""
                options[CONF_GRIDBOSS_SERIAL] = ""
                options[CONF_LOCAL_AUTO_DISCOVERY] = DEFAULT_LOCAL_AUTO_DISCOVERY
                options[CONF_LOCAL_MASTER_HOST] = ""
                options[CONF_LOCAL_SLAVE_HOST] = ""
                options[CONF_LOCAL_GRIDBOSS_HOST] = DEFAULT_LOCAL_GRIDBOSS_HOST
                options[CONF_LOCAL_DONGLE_PORT] = DEFAULT_LOCAL_DONGLE_PORT
                options[CONF_LOCAL_TRACE_ENABLED] = DEFAULT_LOCAL_TRACE_ENABLED
                options[CONF_LOCAL_TRACE_INTERVAL] = DEFAULT_LOCAL_TRACE_INTERVAL
                options[CONF_LOCAL_TRACE_BMS_INTERVAL] = DEFAULT_LOCAL_TRACE_BMS_INTERVAL

                entry_data = dict(user_input)
                entry_data["serials"] = info.get("serials", [])

                return self.async_create_entry(
                    title=info["title"],
                    data=entry_data,
                    options=options,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
