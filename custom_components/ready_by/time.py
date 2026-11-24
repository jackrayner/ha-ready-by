"""The daily ready-by and earliest start times."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ReadyByEntity
from .planner import ReadyByConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReadyByConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    async_add_entities(
        [
            DailyReadyByTime(controller, "daily_ready_by"),
            DailyEarliestStartTime(controller, "daily_earliest_start"),
        ]
    )


class DailyReadyByTime(ReadyByEntity, TimeEntity):
    @property
    def native_value(self) -> time | None:
        return self.controller.data.daily_time

    async def async_set_value(self, value: time) -> None:
        await self.controller.async_set_daily_time(value)


class DailyEarliestStartTime(ReadyByEntity, TimeEntity):
    """Daily runs don't start before this, for example when a cheap rate begins."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> time | None:
        return self.controller.data.daily_earliest_start

    async def async_set_value(self, value: time) -> None:
        await self.controller.async_set_daily_earliest_start(value)
