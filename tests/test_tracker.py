from datetime import UTC, datetime, timedelta

from conftest import CLIMATE, OUTSIDE, WATER, make_entry, set_climate, set_temps, setup_entry
from homeassistant.core import HomeAssistant, State

from custom_components.ready_by.model import detect
from custom_components.ready_by.tracker import climate_is_heating, events_from_history

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)


def test_climate_is_heating_uses_hvac_action():
    assert climate_is_heating(State(CLIMATE, "heat", {"hvac_action": "heating"}))
    assert not climate_is_heating(State(CLIMATE, "heat", {"hvac_action": "idle"}))
    assert climate_is_heating(State(CLIMATE, "unavailable")) is None
    assert climate_is_heating(None) is None


def test_climate_is_heating_fallback_without_action():
    below = {"current_temperature": 30, "temperature": 38}
    above = {"current_temperature": 38.5, "temperature": 38}
    assert climate_is_heating(State(CLIMATE, "heat", below))
    assert not climate_is_heating(State(CLIMATE, "heat", above))
    assert not climate_is_heating(State(CLIMATE, "off", below))


def _state(entity_id, state, when, attributes=None):
    return State(entity_id, state, attributes or {}, last_changed=when, last_updated=when)


def test_events_from_history_detects_session():
    h = timedelta(hours=1)
    states = [
        _state(OUTSIDE, "4", T0),
        _state(WATER, "30", T0),
        _state(CLIMATE, "heat", T0, {"hvac_action": "idle"}),
        _state(CLIMATE, "heat", T0 + h, {"hvac_action": "heating"}),
        _state(WATER, "33", T0 + 3 * h),
        _state(CLIMATE, "heat", T0 + 3 * h, {"hvac_action": "idle"}),
    ]
    sessions, _ = detect(events_from_history(states, CLIMATE, WATER, OUTSIDE))
    assert len(sessions) == 1
    assert sessions[0].rate == 1.5


def test_events_from_history_uses_climate_water():
    h = timedelta(hours=1)
    states = [
        _state(OUTSIDE, "4", T0),
        _state(CLIMATE, "heat", T0, {"hvac_action": "heating", "current_temperature": 30}),
        _state(CLIMATE, "heat", T0 + 2 * h, {"hvac_action": "idle", "current_temperature": 34}),
    ]
    sessions, _ = detect(events_from_history(states, CLIMATE, None, OUTSIDE))
    assert sessions[0].water_start == 30
    assert sessions[0].water_end == 34


async def test_live_tracker_without_water_sensor(hass: HomeAssistant, freezer):
    set_climate(hass, mode="heat", action="heating", current=30)
    set_temps(hass, outside=5)
    entry = await setup_entry(hass, make_entry(water=None))
    freezer.tick(timedelta(hours=1))
    set_climate(hass, mode="heat", action="idle", current=32)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.data.sessions) == 1
    assert entry.runtime_data.tracker.water == 32
