"""Diagnostics for Ready By."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .planner import ReadyByConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ReadyByConfigEntry
) -> dict[str, Any]:
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        **entry.runtime_data.diagnostics(),
    }
