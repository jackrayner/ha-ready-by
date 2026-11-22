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
    CONF_ARCHIVE_MB,
    CONF_BUFFER_MINUTES,
    CONF_CLIMATE,
    CONF_IMPORT_HISTORY,
    CONF_OUTSIDE,
    CONF_POWER,
    CONF_POWER_THRESHOLD,
    CONF_SEED_RATE,
    CONF_WATER,
    CONF_WEATHER,
    DEFAULT_ARCHIVE_MB,
    DEFAULT_BUFFER_MINUTES,
    DEFAULT_IMPORT_HISTORY,
    DEFAULT_NAME,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_SEED_RATE,
    DOMAIN,
)

ENTITY_KEYS = (CONF_CLIMATE, CONF_WATER, CONF_OUTSIDE, CONF_WEATHER, CONF_POWER)

DEFAULT_OPTIONS = {
    CONF_POWER_THRESHOLD: DEFAULT_POWER_THRESHOLD,
    CONF_BUFFER_MINUTES: DEFAULT_BUFFER_MINUTES,
    CONF_SEED_RATE: DEFAULT_SEED_RATE,
    CONF_IMPORT_HISTORY: DEFAULT_IMPORT_HISTORY,
    CONF_ARCHIVE_MB: DEFAULT_ARCHIVE_MB,
}


def _sensor(device_class: SensorDeviceClass) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class=device_class)
    )


def _number(minimum: float, maximum: float, step: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _entities_schema(current: dict[str, Any]) -> dict[vol.Marker, Any]:
    """Entity choices, prefilled with suggested values so optional ones can be cleared."""

    def suggested(key: str) -> dict[str, Any]:
        return {"suggested_value": current.get(key)}

    return {
        vol.Required(CONF_CLIMATE, description=suggested(CONF_CLIMATE)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="climate")
        ),
        vol.Optional(CONF_WATER, description=suggested(CONF_WATER)): _sensor(
            SensorDeviceClass.TEMPERATURE
        ),
        vol.Required(CONF_OUTSIDE, description=suggested(CONF_OUTSIDE)): _sensor(
            SensorDeviceClass.TEMPERATURE
        ),
        vol.Optional(CONF_WEATHER, description=suggested(CONF_WEATHER)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather")
        ),
        vol.Optional(CONF_POWER, description=suggested(CONF_POWER)): _sensor(
            SensorDeviceClass.POWER
        ),
    }


def _settings_schema(options: dict[str, Any]) -> dict[vol.Marker, Any]:
    def default(key: str) -> Any:
        return options.get(key, DEFAULT_OPTIONS[key])

    return {
        vol.Required(CONF_POWER_THRESHOLD, default=default(CONF_POWER_THRESHOLD)): _number(
            0, 10000, 10, "W"
        ),
        vol.Required(CONF_BUFFER_MINUTES, default=default(CONF_BUFFER_MINUTES)): _number(
            0, 240, 5, "min"
        ),
        vol.Required(CONF_SEED_RATE, default=default(CONF_SEED_RATE)): _number(
            0.1, 10, 0.1, "°C/h"
        ),
        vol.Required(
            CONF_IMPORT_HISTORY, default=default(CONF_IMPORT_HISTORY)
        ): selector.BooleanSelector(),
        vol.Required(CONF_ARCHIVE_MB, default=default(CONF_ARCHIVE_MB)): _number(
            10, 2000, 10, "MB"
        ),
    }


def _split(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate entity choices (entry data) from settings (entry options).

    A cleared optional selector is left out of the input, so it's stored as None.
    """
    entities = {key: user_input.get(key) or None for key in ENTITY_KEYS}
    settings = {k: v for k, v in user_input.items() if k not in ENTITY_KEYS}
    return entities, settings


class ReadyByConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CLIMATE])
            self._abort_if_unique_id_configured()
            entities, _ = _split(user_input)
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={CONF_NAME: user_input[CONF_NAME], **entities},
                options=dict(DEFAULT_OPTIONS),
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): selector.TextSelector(),
                **_entities_schema({}),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> ReadyByOptionsFlow:
        return ReadyByOptionsFlow()


class ReadyByOptionsFlow(OptionsFlow):
    """Every setting, including the entities chosen at setup."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            entities, settings = _split(user_input)
            climate = entities[CONF_CLIMATE]
            taken = any(
                other.unique_id == climate
                for other in self.hass.config_entries.async_entries(DOMAIN)
                if other.entry_id != entry.entry_id
            )
            if taken:
                errors[CONF_CLIMATE] = "already_configured"
            else:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **entities}, unique_id=climate
                )
                return self.async_create_entry(data=settings)

        current = {**entry.data, **(user_input or {})}
        schema = vol.Schema(
            {
                **_entities_schema(current),
                **_settings_schema({**entry.options, **(user_input or {})}),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
