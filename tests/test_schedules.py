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
    EVENT_LATE,
    EVENT_SESSION_ENDED,
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
    entry = await setup_entry(hass, make_entry(**{"seed_rate": 2.0, "hold_hours": 1, **kwargs}))
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
    assert controller.data.daily_target == 39
    assert controller.data.target == 38
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


async def test_daily_off_at_ready_by(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    entry, controller = await _setup(hass)
    await hass.services.async_call(
        DOMAIN,
        "set_daily",
        {"config_entry_id": entry.entry_id, "time": "19:00", "off_at_ready_by": True},
        blocking=True,
    )
    assert hass.states.get(entity_id(hass, "switch", entry, "daily_off_at_ready_by")).state == "on"

    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    await _fire(hass, freezer, local(19) + timedelta(seconds=1))

    assert climate_calls.modes == ["heat", "off"]
    assert controller.data.phase == PHASE_SCHEDULED
    assert controller.data.active_ready_by == local(19, days=1)


async def test_off_at_ready_by_does_not_apply_to_one_off(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    await controller.async_set_daily_off_at_ready_by(True)
    await controller.async_set_ready_by(local(19))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    await _fire(hass, freezer, local(19) + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HOLDING
    assert climate_calls.modes == ["heat"]


async def test_turning_on_off_at_ready_by_during_daily_hold_ends_it(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    await controller.async_set_daily_time(time(19, 0))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    await _fire(hass, freezer, local(19) + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HOLDING

    await controller.async_set_daily_off_at_ready_by(True)
    assert climate_calls.modes == ["heat", "off"]
    assert controller.data.active_ready_by == local(19, days=1)


async def test_session_length_entity_and_migration(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    entry, controller = await _setup(hass)
    ended = async_capture_events(hass, EVENT_SESSION_ENDED)
    # make_entry sets the old hold_hours option to 1; it's carried over.
    assert controller.data.session_hours == 1
    number = entity_id(hass, "number", entry, "session_length")
    assert float(hass.states.get(number).state) == 1

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 3}, blocking=True
    )
    await controller.async_set_ready_by(local(19))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    await _fire(hass, freezer, local(19) + timedelta(seconds=1))
    await _fire(hass, freezer, local(21) + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HOLDING
    await _fire(hass, freezer, local(22) + timedelta(seconds=1))
    assert climate_calls.modes == ["heat", "off"]
    assert [(e.data["reason"], e.data["heater_off"]) for e in ended] == [("scheduled", True)]


async def test_shortening_session_during_hold(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    await controller.async_set_session_length(4)
    await controller.async_set_ready_by(local(19))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    await _fire(hass, freezer, local(20) + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HOLDING

    await controller.async_set_session_length(0.5)
    await hass.async_block_till_done()
    assert climate_calls.modes == ["heat", "off"]
    assert controller.data.phase == PHASE_IDLE


async def test_zero_session_length_leaves_heater_on(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    await controller.async_set_session_length(0)
    await controller.async_set_ready_by(local(19))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    await _fire(hass, freezer, local(19) + timedelta(seconds=1))
    assert climate_calls.modes == ["heat"]
    assert controller.data.phase == PHASE_IDLE


async def test_end_session_button_overrides_scheduled_end(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    entry, controller = await _setup(hass)
    ended = async_capture_events(hass, EVENT_SESSION_ENDED)
    await controller.async_set_daily_time(time(7, 0))
    await controller.async_set_ready_by(local(19))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    await _fire(hass, freezer, local(19, 20))
    assert controller.data.phase == PHASE_HOLDING

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_id(hass, "button", entry, "end_session")},
        blocking=True,
    )
    assert climate_calls.modes == ["heat", "off"]
    assert controller.data.enabled is False
    assert controller.data.active == SCHEDULE_DAILY
    assert controller.data.active_ready_by == local(7, days=1)
    assert [(e.data["reason"], e.data["schedule"], e.data["heater_off"]) for e in ended] == [
        ("manual", SCHEDULE_ONE_OFF, True)
    ]
    # The old scheduled end doesn't fire later.
    await _fire(hass, freezer, local(20) + timedelta(seconds=1))
    assert climate_calls.modes == ["heat", "off"]


async def test_end_session_while_preheating_and_when_idle(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    entry, controller = await _setup(hass)
    await controller.async_set_daily_time(time(19, 0))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HEATING

    await hass.services.async_call(
        DOMAIN, "end_session", {"config_entry_id": entry.entry_id}, blocking=True
    )
    assert climate_calls.modes == ["heat", "off"]
    assert controller.data.daily_enabled
    assert controller.data.active_ready_by == local(19, days=1)

    # With nothing running it still turns the heater off.
    await hass.services.async_call(
        DOMAIN, "end_session", {"config_entry_id": entry.entry_id}, blocking=True
    )
    assert climate_calls.modes == ["heat", "off", "off"]


async def test_daily_and_one_off_heat_to_their_own_targets(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    entry, controller = await _setup(hass)
    # Before it's set, the daily target starts as a copy of the shared one.
    assert controller.daily_target == 38
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id(hass, "number", entry, "daily_target_temperature"), "value": 40},
        blocking=True,
    )
    await controller.async_set_daily_time(time(19, 0))
    await controller.async_set_ready_by(local(21, 0), target=37)

    daily_plan = controller.plan
    assert controller.data.active == SCHEDULE_DAILY
    await _fire(hass, freezer, daily_plan.start + timedelta(seconds=1))
    assert climate_calls.temperature[-1].data["temperature"] == 40

    # A one-off target change doesn't touch a running daily run.
    await controller.async_set_target(36)
    assert climate_calls.temperature[-1].data["temperature"] == 40
    # A daily target change does.
    await controller.async_set_daily_target(39.5)
    assert climate_calls.temperature[-1].data["temperature"] == 39.5

    await _fire(hass, freezer, local(19) + timedelta(seconds=1))
    await _fire(hass, freezer, local(20) + timedelta(seconds=1))
    # The one-off at 21:00 follows straight on, at its own target.
    assert controller.data.active == SCHEDULE_ONE_OFF
    assert controller.data.phase == PHASE_HEATING
    assert climate_calls.temperature[-1].data["temperature"] == 36
    assert "off" not in climate_calls.modes


async def test_daily_target_plans_longer_heating(hass: HomeAssistant, london):
    _, controller = await _setup(hass)
    await controller.async_set_daily_time(time(19, 0))
    at_38 = controller.plan.start
    await controller.async_set_daily_target(40)
    assert controller.plan.start < at_38 - timedelta(minutes=50)


async def test_one_off_taking_over_daily_switches_setpoint(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    _, controller = await _setup(hass)
    await controller.async_set_daily_target(40)
    await controller.async_set_daily_time(time(19, 0))
    await _fire(hass, freezer, controller.plan.start + timedelta(seconds=1))
    assert climate_calls.temperature[-1].data["temperature"] == 40

    await controller.async_set_ready_by(local(18, 0))
    assert controller.data.active == SCHEDULE_ONE_OFF
    assert climate_calls.temperature[-1].data["temperature"] == 38


async def test_daily_earliest_start_waits_and_reports_late(
    hass: HomeAssistant, london, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    # 30 → 40 °C at 1 °C/h needs 10 h, but 23:30 → 05:30 is only 6 h.
    entry, controller = await _setup(hass, seed_rate=1.0)
    late = async_capture_events(hass, EVENT_LATE)
    await controller.async_set_daily_target(40, replan=False)
    await controller.async_set_daily_time(time(5, 30))
    assert controller.plan.start < local(23, 30)

    await hass.services.async_call(
        DOMAIN,
        "set_daily",
        {"config_entry_id": entry.entry_id, "earliest_start": "23:30"},
        blocking=True,
    )
    assert controller.data.daily_earliest_enabled
    assert controller.plan.start == local(23, 30)
    assert not controller.plan.on_time
    assert hass.states.get(entity_id(hass, "time", entry, "daily_earliest_start")).state == (
        "23:30:00"
    )
    assert hass.states.get(entity_id(hass, "binary_sensor", entry, "on_time")).state == "off"

    await _fire(hass, freezer, local(23, 0))
    assert controller.data.phase == PHASE_SCHEDULED
    await _fire(hass, freezer, local(23, 30) + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HEATING
    assert climate_calls.modes == ["heat"]
    assert len(late) == 1


async def test_daily_earliest_start_switch_and_clearing(hass: HomeAssistant, london):
    entry, controller = await _setup(hass, seed_rate=1.0)
    await controller.async_set_daily_target(40, replan=False)
    await controller.async_set_daily_time(time(5, 30))
    unlimited = controller.plan.start
    await controller.async_set_daily_earliest_start(time(23, 30))
    assert controller.plan.start == local(23, 30)

    switch = entity_id(hass, "switch", entry, "daily_earliest_enabled")
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert controller.plan.start == unlimited
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert controller.plan.start == local(23, 30)

    await hass.services.async_call(
        DOMAIN,
        "set_daily",
        {"config_entry_id": entry.entry_id, "earliest_start": None},
        blocking=True,
    )
    assert not controller.data.daily_earliest_enabled
    assert controller.data.daily_earliest_start == time(23, 30)
    assert controller.plan.start == unlimited


async def test_earliest_start_does_not_apply_to_one_off(hass: HomeAssistant, london):
    _, controller = await _setup(hass, seed_rate=1.0)
    await controller.async_set_daily_earliest_start(time(23, 30))
    await controller.async_set_ready_by(local(5, 30, days=1), target=40)
    assert controller.data.active == SCHEDULE_ONE_OFF
    assert controller.plan.start < local(23, 30)


async def test_earliest_start_is_the_occurrence_before_ready_by(hass: HomeAssistant, london):
    _, controller = await _setup(hass)
    await controller.async_set_daily_earliest_start(time(23, 30))
    assert controller.earliest_start(SCHEDULE_DAILY, local(5, 30, days=1)) == local(23, 30)
    # Same day when the earliest start is before the ready-by time.
    await controller.async_set_daily_earliest_start(time(2, 0))
    assert controller.earliest_start(SCHEDULE_DAILY, local(5, 30, days=1)) == local(2, 0, days=1)
    assert controller.earliest_start(SCHEDULE_ONE_OFF, local(5, 30, days=1)) is None
