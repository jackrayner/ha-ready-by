"""Config and options flows for Ready By."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BUFFER_MINUTES,
    CONF_CLIMATE,
    CONF_HOLD_HOURS,
    CONF_IMPORT_HISTORY,
    CONF_OUTSIDE,
    CONF_SEED_RATE,
    CONF_WATER,
    CONF_WEATHER,
    DEFAULT_BUFFER_MINUTES,
    DEFAULT_HOLD_HOURS,
    DEFAULT_IMPORT_HISTORY,
    DEFAULT_NAME,
    DEFAULT_SEED_RATE,
    DOMAIN,
)

_TEMPERATURE_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.TEMPERATURE)
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default=DEFAULT_NAME): selector.TextSelector(),
        vol.Required(CONF_CLIMATE): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="climate")
        ),
        vol.Optional(CONF_WATER): _TEMPERATURE_SENSOR,
        vol.Required(CONF_OUTSIDE): _TEMPERATURE_SENSOR,
        vol.Optional(CONF_WEATHER): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather")
        ),
    }
)


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_BUFFER_MINUTES,
                default=options.get(CONF_BUFFER_MINUTES, DEFAULT_BUFFER_MINUTES),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=240,
                    step=5,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SEED_RATE, default=options.get(CONF_SEED_RATE, DEFAULT_SEED_RATE)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.1,
                    max=10,
                    step=0.1,
                    unit_of_measurement="°C/h",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_HOLD_HOURS, default=options.get(CONF_HOLD_HOURS, DEFAULT_HOLD_HOURS)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=24,
                    step=0.5,
                    unit_of_measurement="h",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_IMPORT_HISTORY,
                default=options.get(CONF_IMPORT_HISTORY, DEFAULT_IMPORT_HISTORY),
            ): selector.BooleanSelector(),
        }
    )


class ReadyByConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CLIMATE])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data=user_input,
                options={
                    CONF_BUFFER_MINUTES: DEFAULT_BUFFER_MINUTES,
                    CONF_SEED_RATE: DEFAULT_SEED_RATE,
                    CONF_HOLD_HOURS: DEFAULT_HOLD_HOURS,
                    CONF_IMPORT_HISTORY: DEFAULT_IMPORT_HISTORY,
                },
            )
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> ReadyByOptionsFlow:
        return ReadyByOptionsFlow()


class ReadyByOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(dict(self.config_entry.options))
        )
