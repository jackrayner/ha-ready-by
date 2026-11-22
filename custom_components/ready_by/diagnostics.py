"""Diagnostics for Ready By."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .planner import ReadyByConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ReadyByConfigEntry
) -> dict[str, Any]:
    controller = entry.runtime_data
    archive = controller.archive
    files = await hass.async_add_executor_job(archive.files)
    size = await hass.async_add_executor_job(archive.size)
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        **controller.diagnostics(),
        "archive": {"files": [f.name for f in files], "bytes": size},
    }
