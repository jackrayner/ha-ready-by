"""The Ready By integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_COOLING,
    ATTR_READY_BY,
    ATTR_SESSIONS,
    ATTR_TARGET,
    DOMAIN,
    MAX_TARGET,
    MIN_TARGET,
    SERVICE_IMPORT_SESSIONS,
    SERVICE_RESET_MODEL,
    SERVICE_SET_READY_BY,
)
from .model import CoolingSample, Session
from .planner import ReadyByConfigEntry, ReadyByController

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_SAMPLE_SCHEMA = vol.Schema(
    {
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
        vol.Required("water_start"): vol.Coerce(float),
        vol.Required("water_end"): vol.Coerce(float),
        vol.Required("outside_avg"): vol.Coerce(float),
    }
)

SET_READY_BY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_READY_BY): cv.datetime,
        vol.Optional(ATTR_TARGET): vol.All(
            vol.Coerce(float), vol.Range(min=MIN_TARGET, max=MAX_TARGET)
        ),
    }
)
IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_SESSIONS, default=list): [_SAMPLE_SCHEMA],
        vol.Optional(ATTR_COOLING, default=list): [_SAMPLE_SCHEMA],
    }
)
RESET_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})


def _aware(sample: dict) -> dict:
    """Treat naive times as local, as Home Assistant does elsewhere."""
    return {
        **sample,
        "start": dt_util.as_utc(sample["start"]),
        "end": dt_util.as_utc(sample["end"]),
    }


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""

    def controller_for(call: ServiceCall) -> ReadyByController:
        entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded"
            )
        return entry.runtime_data

    async def set_ready_by(call: ServiceCall) -> None:
        await controller_for(call).async_set_ready_by(
            call.data[ATTR_READY_BY], call.data.get(ATTR_TARGET)
        )

    async def import_sessions(call: ServiceCall) -> ServiceResponse:
        sessions = [Session(**_aware(s)) for s in call.data[ATTR_SESSIONS]]
        cooling = [CoolingSample(**_aware(s)) for s in call.data[ATTR_COOLING]]
        added_sessions, added_cooling = await controller_for(call).async_import(sessions, cooling)
        return {"sessions_added": added_sessions, "cooling_added": added_cooling}

    async def reset_model(call: ServiceCall) -> None:
        await controller_for(call).async_reset_model()

    hass.services.async_register(DOMAIN, SERVICE_SET_READY_BY, set_ready_by, SET_READY_BY_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_SESSIONS,
        import_sessions,
        IMPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_RESET_MODEL, reset_model, RESET_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ReadyByConfigEntry) -> bool:
    """Set up Ready By from a config entry."""
    controller = ReadyByController(hass, entry)
    await controller.async_setup()
    entry.runtime_data = controller
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ReadyByConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ReadyByConfigEntry) -> None:
    """Delete the learned data when the entry is removed."""
    from .store import ReadyByStore

    await ReadyByStore(hass, entry.entry_id).async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: ReadyByConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
