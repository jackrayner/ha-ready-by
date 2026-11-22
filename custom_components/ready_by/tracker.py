"""Feed live (or recorded) Home Assistant states into the session detector."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .model import CoolingSample, Session, SessionDetector

FLUSH_INTERVAL = timedelta(minutes=5)
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


_WATTS_PER_UNIT = {"mW": 0.001, "W": 1.0, "kW": 1000.0, "MW": 1_000_000.0}


def power_watts(state: State | None) -> float | None:
    """A power sensor's reading in watts, converting from its unit."""
    value = float_state(state)
    if value is None:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, "W")
    return value * _WATTS_PER_UNIT.get(unit, 1.0)


def climate_water(state: State | None) -> float | None:
    if state is None:
        return None
    return _to_float(state.attributes.get(ATTR_CURRENT_TEMPERATURE))


@dataclass(frozen=True)
class Sources:
    """The entities a Ready By entry reads."""

    climate_id: str
    outside_id: str
    water_id: str | None = None
    power_id: str | None = None
    power_threshold: float = 0.0

    @property
    def entity_ids(self) -> list[str]:
        return [e for e in (self.climate_id, self.outside_id, self.water_id, self.power_id) if e]

    def events(self, state: State) -> list[tuple[datetime, str, Any]]:
        """Detector events implied by one state of one of our entities."""
        when = state.last_updated
        events: list[tuple[datetime, str, Any]] = []
        if state.entity_id == self.climate_id:
            if self.water_id is None:
                events.append((when, "water", climate_water(state)))
            if self.power_id is None:
                heating = climate_is_heating(state)
                if heating is not None:
                    events.append((when, "heating", heating))
        if state.entity_id == self.power_id:
            power = power_watts(state)
            if power is not None:
                events.append((when, "heating", power > self.power_threshold))
        if state.entity_id == self.water_id:
            events.append((when, "water", float_state(state)))
        if state.entity_id == self.outside_id:
            events.append((when, "outside", float_state(state)))
        return events


def events_from_history(
    states: Iterable[State], sources: Sources
) -> list[tuple[datetime, str, Any]]:
    events: list[tuple[datetime, str, Any]] = []
    for state in states:
        events.extend(sources.events(state))
    # Within one timestamp, apply readings before the heating transition they accompany.
    return sorted(events, key=lambda e: (e[0], e[1] == "heating"))


class SessionTracker:
    """Watches the source entities and reports completed samples."""

    def __init__(
        self,
        hass: HomeAssistant,
        sources: Sources,
        on_sample: Callable[[Session | CoolingSample], None],
        on_change: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._sources = sources
        self._on_sample = on_sample
        self._on_change = on_change
        self._detector = SessionDetector()
        self._unsubs: list[CALLBACK_TYPE] = []

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
        for entity_id in self._sources.entity_ids:
            state = self._hass.states.get(entity_id)
            if state is not None:
                self._apply(state, when=now)
        self._unsubs = [
            async_track_state_change_event(
                self._hass, self._sources.entity_ids, self._async_state_changed
            ),
            async_track_time_interval(self._hass, self._async_flush, FLUSH_INTERVAL),
        ]

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        state = event.data["new_state"]
        if state is None:
            return
        self._apply(state)
        self._on_change()

    @callback
    def _async_flush(self, now: datetime) -> None:
        self._report(self._detector.flush(now))

    def _apply(self, state: State, when: datetime | None = None) -> None:
        events = self._sources.events(state)
        for event_time, kind, value in sorted(events, key=lambda e: e[1] == "heating"):
            event_time = when or event_time
            if kind == "water":
                done = self._detector.update_water(event_time, value)
            elif kind == "outside":
                done = self._detector.update_outside(event_time, value)
            else:
                done = self._detector.update_heating(event_time, value)
            self._report(done)

    def _report(self, samples: list[Session | CoolingSample]) -> None:
        for sample in samples:
            self._on_sample(sample)
