"""End the hot tub session now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ReadyByEntity
from .planner import ReadyByConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReadyByConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([EndSessionButton(entry.runtime_data, "end_session")])


class EndSessionButton(ReadyByEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self.controller.async_end_session()
