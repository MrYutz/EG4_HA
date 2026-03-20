"""Config flow for EG4 Monitor integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
import aiohttp

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_GRIDBOSS_SERIAL,
    CONF_MASTER_SERIAL,
    CONF_SLAVE_SERIAL,
    DOMAIN,
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
    "battery_power": "Battery Power",
    "battery_charging_power": "Battery Charging Power",
    "battery_discharging_power": "Battery Discharging Power",
    "battery_power_direction": "Battery Power Direction",
    "battery_voltage": "Battery Voltage",
    "battery_remaining_kwh": "Battery Energy Remaining",
    "solar_power": "Solar Power",
    "pv1_power": "PV1 Power",
    "pv2_power": "PV2 Power",
    "grid_power": "Grid Power",
    "grid_voltage": "Grid Voltage",
    "grid_frequency": "Grid Frequency",
    "load_power": "Load Power",
    "eps_power": "EPS Power",
    "eps_voltage": "EPS Voltage",
    "eps_frequency": "EPS Frequency",
    "today_solar": "Today's Solar Yield",
    "today_grid_import": "Today's Usage",
    "today_grid_export": "Today Export",
    "today_ups_usage": "UPS Usage Today",
    "total_solar": "Total Solar Yield",
    "total_grid_import": "Total Usage",
    "total_grid_export": "Total Export",
    "total_ups_usage": "UPS Usage Total",
    "generator_voltage": "Generator Voltage",
    "generator_frequency": "Generator Frequency",
    "generator_power": "Generator Power",
    "generator_status_source": "Generator Status Source",
    "uncleared_error_count": "Uncleared Error Count",
    "uncleared_error_summary": "Uncleared Error Summary",
    "eg4_device_count": "EG4 Device Count",
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

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle sensor selection."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options: dict[Any, Any] = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            ): int,
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
        return SensorSelectFlow(config_entry)

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
                options[CONF_MASTER_SERIAL] = ""
                options[CONF_SLAVE_SERIAL] = ""
                options[CONF_GRIDBOSS_SERIAL] = ""

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
