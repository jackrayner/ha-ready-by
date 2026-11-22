"""The target temperature."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_TARGET, MIN_TARGET
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
            TargetTemperatureNumber(controller, "target_temperature"),
            SessionLengthNumber(controller, "session_length"),
        ]
    )


class TargetTemperatureNumber(ReadyByEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = MIN_TARGET
    _attr_native_max_value = MAX_TARGET
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float:
        return self.controller.data.target

    async def async_set_native_value(self, value: float) -> None:
        await self.controller.async_set_target(value)


class SessionLengthNumber(ReadyByEntity, NumberEntity):
    """How long to keep heating after the ready-by time. 0 never turns the heater off."""

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_native_min_value = 0
    _attr_native_max_value = 24
    _attr_native_step = 0.25
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float:
        return self.controller.session_hours

    async def async_set_native_value(self, value: float) -> None:
        await self.controller.async_set_session_length(value)
