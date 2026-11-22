from datetime import datetime, time, timedelta

import pytest
from conftest import (
    ClimateCalls,
    entity_id,
    make_entry,
    set_climate,
    set_temps,
    setup_entry,
)
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.ready_by.const import (
    DOMAIN,
    EVENT_HEATING_STARTED,
    PHASE_HEATING,
    PHASE_HOLDING,
    PHASE_IDLE,
    PHASE_SCHEDULED,
    SCHEDULE_DAILY,
    SCHEDULE_ONE_OFF,
)

NOON = datetime.fromisoformat("2026-11-23T12:00:00+00:00")


@pytest.fixture
async def london(hass: HomeAssistant, freezer: FrozenDateTimeFactory):
    await hass.config.async_set_time_zone("Europe/London")
    freezer.move_to(NOON)


def local(hour: int, minute: int = 0, days: int = 0) -> datetime:
    day = dt_util.as_local(NOON).date() + timedelta(days=days)
    return dt_util.as_utc(
        datetime.combine(day, time(hour, minute), tzinfo=dt_util.get_default_time_zone())
    )


async def _fire(hass: HomeAssistant, freezer: FrozenDateTimeFactory, when: datetime) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, **kwargs):
    set_climate(hass)
    set_temps(hass, water=30, outside=5)
    entry = await setup_entry(hass, make_entry(seed_rate=2.0, hold_hours=1, **kwargs))
    return entry, entry.runtime_data


async def test_daily_runs_every_day(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    entry, controller = await _setup(hass)
    await controller.async_set_daily_time(time(19, 0))

    assert controller.data.daily_enabled
    assert controller.data.active == SCHEDULE_DAILY
    assert controller.data.active_ready_by == local(19)
    assert controller.data.phase == PHASE_SCHEDULED
    start = controller.plan.start
    assert local(14) < start < local(15)
    next_state = hass.states.get(entity_id(hass, "sensor", entry, "next_ready_by"))
    assert dt_util.parse_datetime(next_state.state) == local(19)
    assert next_state.attributes["schedule"] == SCHEDULE_DAILY

    await _fire(hass, freezer, start + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HEATING
    await _fire(hass, freezer, local(19, 0) + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HOLDING
    await _fire(hass, freezer, local(20, 0) + timedelta(seconds=1))

    assert climate_calls.modes == ["heat", "off"]
    assert controller.data.daily_enabled
    assert controller.data.active == SCHEDULE_DAILY
    assert controller.data.active_ready_by == local(19, days=1)
    assert controller.data.phase == PHASE_SCHEDULED


async def test_one_off_before_daily_goes_first_then_turns_off(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    await controller.async_set_daily_time(time(7, 0))
    await controller.async_set_ready_by(local(20, 0))

    assert controller.data.active == SCHEDULE_ONE_OFF
    start = controller.plan.start
    await _fire(hass, freezer, start + timedelta(seconds=1))
    await _fire(hass, freezer, local(20) + timedelta(seconds=1))
    await _fire(hass, freezer, local(21) + timedelta(seconds=1))

    assert climate_calls.modes == ["heat", "off"]
    assert controller.data.enabled is False
    assert controller.data.active == SCHEDULE_DAILY
    assert controller.data.active_ready_by == local(7, days=1)


async def test_daily_before_one_off_keeps_one_off(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    await controller.async_set_ready_by(local(22, 0, days=1))
    await controller.async_set_daily_time(time(19, 0))
    assert controller.data.active == SCHEDULE_DAILY

    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    await _fire(hass, freezer, local(19) + timedelta(seconds=1))
    await _fire(hass, freezer, local(20) + timedelta(seconds=1))

    assert controller.data.enabled is True
    assert controller.data.active == SCHEDULE_DAILY
    assert controller.data.active_ready_by == local(19, days=1)


async def test_no_off_between_back_to_back_runs(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    started = async_capture_events(hass, EVENT_HEATING_STARTED)
    await controller.async_set_daily_time(time(19, 0))
    await controller.async_set_ready_by(local(20, 30))
    assert controller.data.active == SCHEDULE_DAILY

    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    await _fire(hass, freezer, local(19) + timedelta(seconds=1))
    await _fire(hass, freezer, local(20) + timedelta(seconds=1))

    # The water (still reading 30 °C) needs heating for 20:30, so it goes straight on.
    assert "off" not in climate_calls.modes
    assert controller.data.active == SCHEDULE_ONE_OFF
    assert controller.data.phase == PHASE_HEATING
    assert [e.data["schedule"] for e in started] == [SCHEDULE_DAILY, SCHEDULE_ONE_OFF]


async def test_earlier_one_off_takes_over_running_daily(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    await controller.async_set_daily_time(time(19, 0))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HEATING

    await controller.async_set_ready_by(local(18, 0))
    assert controller.data.active == SCHEDULE_ONE_OFF
    assert controller.data.active_ready_by == local(18)

    await _fire(hass, freezer, local(18) + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HOLDING


async def test_turning_off_a_schedule_only_stops_its_own_run(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    await controller.async_set_daily_time(time(19, 0))
    await controller.async_set_ready_by(local(22, 0, days=2))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    assert controller.data.active == SCHEDULE_DAILY

    await controller.async_set_enabled(False)
    assert controller.data.phase == PHASE_HEATING

    await controller.async_set_daily_enabled(False)
    assert controller.data.phase == PHASE_IDLE
    assert controller.data.active is None
    assert controller.plan is None
    assert climate_calls.modes == ["heat"]


async def test_running_daily_resumes_after_reload(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    entry, controller = await _setup(hass)
    await controller.async_set_daily_time(time(19, 0))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    controller = entry.runtime_data
    assert controller.data.phase == PHASE_HEATING
    assert controller.data.active == SCHEDULE_DAILY
    assert controller.data.daily_time == time(19, 0)

    await _fire(hass, freezer, local(19) + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HOLDING


async def test_store_without_schedules_resumes_as_one_off(hass: HomeAssistant, london):
    _, controller = await _setup(hass)
    controller.data.enabled = True
    controller.data.ready_by = local(19)
    controller.data.phase = PHASE_HEATING
    controller.data.active = None
    controller._resume()
    assert controller.data.active == SCHEDULE_ONE_OFF
    assert controller.data.active_ready_by == local(19)


async def test_set_daily_action_and_entities(
    hass: HomeAssistant, london, climate_calls: ClimateCalls
):
    entry, controller = await _setup(hass)
    await hass.services.async_call(
        DOMAIN,
        "set_daily",
        {"config_entry_id": entry.entry_id, "time": "06:30", "target": 39},
        blocking=True,
    )
    assert controller.data.daily_time == time(6, 30)
    assert controller.data.daily_enabled
    assert controller.data.target == 39
    assert hass.states.get(entity_id(hass, "time", entry, "daily_ready_by")).state == "06:30:00"
    assert hass.states.get(entity_id(hass, "switch", entry, "daily_enabled")).state == "on"

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id(hass, "switch", entry, "daily_enabled")},
        blocking=True,
    )
    assert controller.data.daily_enabled is False
    assert controller.plan is None

    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": entity_id(hass, "time", entry, "daily_ready_by"), "time": "07:15:00"},
        blocking=True,
    )
    assert controller.data.daily_time == time(7, 15)
    assert controller.data.daily_enabled
    assert controller.data.active_ready_by == local(7, 15, days=1)


async def test_daily_time_follows_local_clock_change(hass: HomeAssistant, london):
    _, controller = await _setup(hass)
    # Clocks go forward in London at 01:00 UTC on 28 March 2027.
    before = datetime.fromisoformat("2027-03-27T12:00:00+00:00")
    assert controller._daily_at(before, time(7, 0)) == datetime.fromisoformat(
        "2027-03-28T06:00:00+00:00"
    )
