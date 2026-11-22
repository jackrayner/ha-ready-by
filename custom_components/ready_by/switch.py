"""Turns ready-by scheduling on and off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
            EnabledSwitch(controller, "enabled"),
            DailyEnabledSwitch(controller, "daily_enabled"),
            DailyOffAtReadyBySwitch(controller, "daily_off_at_ready_by"),
        ]
    )


class EnabledSwitch(ReadyByEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return self.controller.data.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_enabled(False)


class DailyEnabledSwitch(ReadyByEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return self.controller.data.daily_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_daily_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_daily_enabled(False)


class DailyOffAtReadyBySwitch(ReadyByEntity, SwitchEntity):
    """Turn the heater off at the daily ready-by time instead of holding."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool:
        return self.controller.data.daily_off_at_ready_by

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_daily_off_at_ready_by(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_daily_off_at_ready_by(False)
