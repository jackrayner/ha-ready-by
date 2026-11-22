"""Sensors for Ready By."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PHASES
from .entity import ReadyByEntity
from .planner import ReadyByConfigEntry, ReadyByController


def _duration_minutes(c: ReadyByController) -> float | None:
    if c.plan is None or c.plan.duration is None:
        return None
    return round(c.plan.duration.total_seconds() / 60)


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


@dataclass(frozen=True, kw_only=True)
class ReadyBySensorDescription(SensorEntityDescription):
    value_fn: Callable[[ReadyByController], float | str | datetime | None]


SENSORS: tuple[ReadyBySensorDescription, ...] = (
    ReadyBySensorDescription(
        key="status",
        device_class=SensorDeviceClass.ENUM,
        options=PHASES,
        value_fn=lambda c: c.data.phase,
    ),
    ReadyBySensorDescription(
        key="next_ready_by",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.next_ready_by[1] if c.next_ready_by else None,
    ),
    ReadyBySensorDescription(
        key="start_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.plan.start if c.plan else None,
    ),
    ReadyBySensorDescription(
        key="expected_ready",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.plan.expected_ready if c.plan else None,
    ),
    ReadyBySensorDescription(
        key="heat_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=_duration_minutes,
    ),
    ReadyBySensorDescription(
        key="heating_rate",
        native_unit_of_measurement="°C/h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda c: _rounded(c.heating_rate, 3),
    ),
    ReadyBySensorDescription(
        key="sessions_learned",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: len(c.data.sessions),
    ),
    ReadyBySensorDescription(
        key="fit_r2",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda c: _rounded(c.model.r2, 3),
    ),
    ReadyBySensorDescription(
        key="cooling_k",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="1/h",
        suggested_display_precision=4,
        value_fn=lambda c: _rounded(c.cooling_k, 5),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReadyByConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(ReadyBySensor(entry.runtime_data, d) for d in SENSORS)


class ReadyBySensor(ReadyByEntity, SensorEntity):
    entity_description: ReadyBySensorDescription

    def __init__(
        self, controller: ReadyByController, description: ReadyBySensorDescription
    ) -> None:
        super().__init__(controller, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | datetime | None:
        return self.entity_description.value_fn(self.controller)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.key == "heating_rate":
            return self.controller.model.as_dict()
        if self.entity_description.key == "next_ready_by":
            upcoming = self.controller.next_ready_by
            return {"schedule": upcoming[0] if upcoming else None}
        return None
