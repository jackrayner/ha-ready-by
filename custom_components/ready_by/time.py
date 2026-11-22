"""The daily ready-by time."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ReadyByEntity
from .planner import ReadyByConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReadyByConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DailyReadyByTime(entry.runtime_data, "daily_ready_by")])


class DailyReadyByTime(ReadyByEntity, TimeEntity):
    @property
    def native_value(self) -> time | None:
        return self.controller.data.daily_time

    async def async_set_value(self, value: time) -> None:
        await self.controller.async_set_daily_time(value)
