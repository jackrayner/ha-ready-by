"""Binary sensors for Ready By."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ReadyByEntity
from .planner import ReadyByConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReadyByConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([OnTimeBinarySensor(entry.runtime_data, "on_time")])


class OnTimeBinarySensor(ReadyByEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        plan = self.controller.plan
        return plan.on_time if plan else None
