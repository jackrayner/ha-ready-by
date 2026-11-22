from datetime import UTC, datetime, timedelta

import pytest
from conftest import (
    CLIMATE,
    OUTSIDE,
    POWER,
    WATER,
    make_entry,
    set_climate,
    set_temps,
    setup_entry,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ready_by.model import HeatingModel, detect
from custom_components.ready_by.tracker import Sources, climate_is_heating, events_from_history

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
    sessions, _ = detect(events_from_history(states, Sources(CLIMATE, OUTSIDE, WATER)))
    assert len(sessions) == 1
    assert sessions[0].rate == 1.5


def test_events_from_history_uses_climate_water():
    h = timedelta(hours=1)
    states = [
        _state(OUTSIDE, "4", T0),
        _state(CLIMATE, "heat", T0, {"hvac_action": "heating", "current_temperature": 30}),
        _state(CLIMATE, "heat", T0 + 2 * h, {"hvac_action": "idle", "current_temperature": 34}),
    ]
    sessions, _ = detect(events_from_history(states, Sources(CLIMATE, OUTSIDE)))
    assert sessions[0].water_start == 30
    assert sessions[0].water_end == 34


async def test_live_tracker_without_water_sensor(hass: HomeAssistant, freezer):
    set_climate(hass, mode="heat", action="heating", current=30)
    set_temps(hass, outside=5)
    entry = await setup_entry(hass, make_entry(water=None))
    freezer.tick(timedelta(hours=1))
    set_climate(hass, mode="heat", action="idle", current=32)
    await hass.async_block_till_done()
    later = dt_util.utcnow() + timedelta(minutes=15)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.data.sessions) == 1
    assert entry.runtime_data.tracker.water == 32


def test_power_sensor_drives_heating_in_history():
    h = timedelta(hours=1)
    sources = Sources(CLIMATE, OUTSIDE, WATER, power_id=POWER, power_threshold=100)
    states = [
        _state(OUTSIDE, "4", T0),
        _state(WATER, "30", T0),
        _state(POWER, "5", T0),
        # The climate entity's guess is ignored once a power sensor is configured.
        _state(CLIMATE, "heat", T0, {"current_temperature": 30, "temperature": 38}),
        _state(POWER, "1500", T0 + h),
        _state(POWER, "unavailable", T0 + 2 * h),
        _state(POWER, "8", T0 + 3 * h),
        _state(WATER, "34", T0 + 3 * h),
    ]
    sessions, _ = detect(events_from_history(states, sources))
    assert len(sessions) == 1
    assert (sessions[0].start, sessions[0].end) == (T0 + h, T0 + 3 * h)


def test_short_power_dip_does_not_split_session():
    sources = Sources(CLIMATE, OUTSIDE, WATER, power_id=POWER, power_threshold=100)
    m = timedelta(minutes=1)
    states = [
        _state(OUTSIDE, "4", T0),
        _state(WATER, "30", T0),
        _state(POWER, "1500", T0),
        _state(WATER, "31", T0 + 40 * m),
        _state(POWER, "20", T0 + 40 * m),
        _state(POWER, "1500", T0 + 46 * m),
        _state(WATER, "33", T0 + 100 * m),
        _state(POWER, "20", T0 + 100 * m),
    ]
    sessions, _ = detect(events_from_history(states, sources))
    assert len(sessions) == 1
    assert sessions[0].end == T0 + 100 * m
    assert sessions[0].water_end == 33


async def test_live_power_sensor(hass: HomeAssistant, freezer):
    set_climate(hass, mode="heat", action=None)
    set_temps(hass, water=30, outside=5)
    hass.states.async_set(POWER, "0")
    entry = await setup_entry(hass, make_entry(power=POWER))
    hass.states.async_set(POWER, "1800")
    await hass.async_block_till_done()
    assert entry.runtime_data.tracker.heating is True
    freezer.tick(timedelta(hours=2))
    hass.states.async_set(WATER, "34")
    hass.states.async_set(POWER, "10")
    await hass.async_block_till_done()
    later = dt_util.utcnow() + timedelta(minutes=15)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.data.sessions) == 1


def test_power_in_kilowatts_is_converted():
    sources = Sources(CLIMATE, OUTSIDE, power_id=POWER, power_threshold=100)
    kw = {"unit_of_measurement": "kW"}
    assert sources.events(_state(POWER, "0.4", T0, kw)) == [(T0, "heating", True)]
    assert sources.events(_state(POWER, "0.05", T0, kw)) == [(T0, "heating", False)]
    assert sources.events(_state(POWER, "50", T0)) == [(T0, "heating", False)]


def test_heat_pump_night_with_defrost_and_modulation():
    """A typical inverter heat pump run: a short defrost dip, then modulating at setpoint."""
    m = timedelta(minutes=1)
    kw = {"unit_of_measurement": "kW"}
    sources = Sources(CLIMATE, OUTSIDE, WATER, power_id=POWER, power_threshold=100)
    states = [
        _state(OUTSIDE, "6", T0),
        _state(WATER, "34", T0),
        _state(POWER, "0", T0, kw),
        _state(POWER, "1.4", T0 + 30 * m, kw),
        _state(WATER, "35", T0 + 70 * m),
        _state(POWER, "0.0", T0 + 75 * m, kw),
        _state(POWER, "1.1", T0 + 80 * m, kw),
        _state(WATER, "36", T0 + 120 * m),
        _state(WATER, "37", T0 + 170 * m),
        _state(WATER, "38", T0 + 220 * m),
        _state(POWER, "0.45", T0 + 230 * m, kw),
        _state(POWER, "0", T0 + 360 * m, kw),
    ]
    sessions, _ = detect(events_from_history(states, sources))
    assert len(sessions) == 1
    s = sessions[0]
    assert (s.start, s.end) == (T0 + 30 * m, T0 + 220 * m)
    assert s.rate == pytest.approx(4 / (190 / 60))


def test_climate_preset_mode_is_tracked():
    h = timedelta(hours=1)
    sources = Sources(CLIMATE, OUTSIDE, WATER)
    states = [
        _state(OUTSIDE, "4", T0),
        _state(WATER, "30", T0),
        _state(CLIMATE, "heat", T0, {"hvac_action": "idle", "preset_mode": "sleep"}),
        _state(CLIMATE, "heat", T0 + h, {"hvac_action": "heating", "preset_mode": "sleep"}),
        _state(WATER, "33", T0 + 3 * h),
        _state(CLIMATE, "heat", T0 + 3 * h, {"hvac_action": "idle", "preset_mode": "sleep"}),
    ]
    sessions, _ = detect(events_from_history(states, sources))
    assert sessions[0].mode == "sleep"


async def test_controller_plans_with_current_mode(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass, water=30, outside=5)
    entry = await setup_entry(hass, make_entry(seed_rate=2.0))
    controller = entry.runtime_data
    controller.model = HeatingModel(kind="seed", c0=2.0, mode_factors={"sleep": 0.5})
    assert controller.heating_rate == 2.0

    hass.states.async_set(
        CLIMATE,
        "heat",
        {
            "hvac_action": "idle",
            "current_temperature": 30,
            "temperature": 38,
            "preset_mode": "sleep",
        },
    )
    await hass.async_block_till_done()
    assert controller.tracker.mode == "sleep"
    assert controller.heating_rate == 1.0
