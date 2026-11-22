"""Feed live (or recorded) Home Assistant states into the session detector."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .model import CoolingSample, Session, SessionDetector

HEATING_ACTIONS = {HVACAction.HEATING, HVACAction.PREHEATING}
HEATING_MODES = {HVACMode.HEAT, HVACMode.HEAT_COOL, HVACMode.AUTO}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def float_state(state: State | None) -> float | None:
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    return _to_float(state.state)


def climate_is_heating(state: State | None) -> bool | None:
    """Whether the climate entity is actively heating.

    Uses hvac_action when the integration reports it; otherwise assumes it heats
    whenever it's in a heating mode and below its setpoint.
    """
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    if ATTR_HVAC_ACTION in state.attributes:
        return state.attributes[ATTR_HVAC_ACTION] in HEATING_ACTIONS
    current = _to_float(state.attributes.get(ATTR_CURRENT_TEMPERATURE))
    setpoint = _to_float(state.attributes.get(ATTR_TEMPERATURE))
    if state.state not in HEATING_MODES or current is None or setpoint is None:
        return False
    return current < setpoint


def climate_water(state: State | None) -> float | None:
    if state is None:
        return None
    return _to_float(state.attributes.get(ATTR_CURRENT_TEMPERATURE))


class _Readers:
    def __init__(self, climate_id: str, water_id: str | None, outside_id: str) -> None:
        self.climate_id = climate_id
        self.water_id = water_id
        self.outside_id = outside_id

    @property
    def entity_ids(self) -> list[str]:
        ids = [self.climate_id, self.outside_id]
        if self.water_id:
            ids.append(self.water_id)
        return ids

    def events(self, state: State) -> list[tuple[datetime, str, Any]]:
        """Detector events implied by one state of one of our entities."""
        when = state.last_updated
        events: list[tuple[datetime, str, Any]] = []
        if state.entity_id == self.climate_id:
            if self.water_id is None:
                events.append((when, "water", climate_water(state)))
            heating = climate_is_heating(state)
            if heating is not None:
                events.append((when, "heating", heating))
        if state.entity_id == self.water_id:
            events.append((when, "water", float_state(state)))
        if state.entity_id == self.outside_id:
            events.append((when, "outside", float_state(state)))
        return events


def events_from_history(
    states: Iterable[State], climate_id: str, water_id: str | None, outside_id: str
) -> list[tuple[datetime, str, Any]]:
    readers = _Readers(climate_id, water_id, outside_id)
    events: list[tuple[datetime, str, Any]] = []
    for state in states:
        events.extend(readers.events(state))
    # Within one timestamp, apply readings before the heating transition they accompany.
    return sorted(events, key=lambda e: (e[0], e[1] == "heating"))


class SessionTracker:
    """Watches the climate and temperature entities and reports completed samples."""

    def __init__(
        self,
        hass: HomeAssistant,
        climate_id: str,
        water_id: str | None,
        outside_id: str,
        on_sample: Callable[[Session | CoolingSample], None],
        on_change: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._readers = _Readers(climate_id, water_id, outside_id)
        self._on_sample = on_sample
        self._on_change = on_change
        self._detector = SessionDetector()
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def water(self) -> float | None:
        return self._detector.water

    @property
    def outside(self) -> float | None:
        return self._detector.outside

    @property
    def heating(self) -> bool | None:
        return self._detector.heating

    @callback
    def async_start(self) -> None:
        now = dt_util.utcnow()
        for entity_id in self._readers.entity_ids:
            state = self._hass.states.get(entity_id)
            if state is not None:
                self._apply(state, when=now)
        self._unsub = async_track_state_change_event(
            self._hass, self._readers.entity_ids, self._async_state_changed
        )

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        state = event.data["new_state"]
        if state is None:
            return
        self._apply(state)
        self._on_change()

    def _apply(self, state: State, when: datetime | None = None) -> None:
        events = self._readers.events(state)
        for event_time, kind, value in sorted(events, key=lambda e: e[1] == "heating"):
            event_time = when or event_time
            if kind == "water":
                self._detector.update_water(event_time, value)
            elif kind == "outside":
                self._detector.update_outside(event_time, value)
            else:
                result = self._detector.update_heating(event_time, value)
                if result is not None:
                    self._on_sample(result)
