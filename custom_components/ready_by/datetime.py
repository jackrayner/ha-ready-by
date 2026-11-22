"""The ready-by time."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ReadyByEntity
from .planner import ReadyByConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReadyByConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ReadyByDateTime(entry.runtime_data, "ready_by")])


class ReadyByDateTime(ReadyByEntity, DateTimeEntity):
    @property
    def native_value(self) -> datetime | None:
        return self.controller.data.ready_by

    async def async_set_value(self, value: datetime) -> None:
        await self.controller.async_set_ready_by(value)
