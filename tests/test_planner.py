from datetime import timedelta

from conftest import (
    CLIMATE,
    WEATHER,
    ClimateCalls,
    entity_id,
    make_entry,
    mock_forecast,
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
    EVENT_HEATING_STARTED,
    EVENT_LATE,
    EVENT_READY,
    PHASE_HEATING,
    PHASE_HOLDING,
    PHASE_IDLE,
    PHASE_SCHEDULED,
)


def near(a, b, tolerance=timedelta(minutes=1)) -> bool:
    return abs(a - b) <= tolerance


async def _fire(hass: HomeAssistant, freezer: FrozenDateTimeFactory, when) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()


async def test_schedules_start_and_runs_full_cycle(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    set_climate(hass)
    set_temps(hass, water=30, outside=5)
    entry = await setup_entry(hass, make_entry(seed_rate=2.0, hold_hours=1))
    controller = entry.runtime_data
    started = async_capture_events(hass, EVENT_HEATING_STARTED)
    ready = async_capture_events(hass, EVENT_READY)

    now = dt_util.utcnow()
    ready_by = now + timedelta(hours=10)
    await controller.async_set_ready_by(ready_by, target=38)
    await hass.async_block_till_done()

    # 8 °C at 2 °C/h with no learned cooling beyond the default: start a bit before 4 h ahead.
    assert controller.data.phase == PHASE_SCHEDULED
    plan = controller.plan
    assert plan.on_time
    assert ready_by - timedelta(hours=5) < plan.start < ready_by - timedelta(hours=4)
    assert hass.states.get(entity_id(hass, "sensor", entry, "status")).state == "scheduled"
    assert climate_calls.hvac_mode == []

    await _fire(hass, freezer, plan.start + timedelta(seconds=1))
    assert controller.data.phase == PHASE_HEATING
    assert climate_calls.modes == ["heat"]
    assert climate_calls.temperature[-1].data == {"entity_id": CLIMATE, "temperature": 38.0}
    assert len(started) == 1 and started[0].data["on_time"] is True

    await _fire(hass, freezer, ready_by + timedelta(seconds=1))
    assert len(ready) == 1
    assert controller.data.phase == PHASE_HOLDING

    await _fire(hass, freezer, ready_by + timedelta(hours=1, seconds=1))
    assert climate_calls.modes == ["heat", "off"]
    assert controller.data.phase == PHASE_IDLE
    assert controller.data.enabled is False
    assert controller.plan is None


async def test_late_start_fires_immediately(hass: HomeAssistant, climate_calls: ClimateCalls):
    set_climate(hass)
    set_temps(hass, water=20, outside=5)
    entry = await setup_entry(hass, make_entry(seed_rate=1.0))
    late = async_capture_events(hass, EVENT_LATE)

    await entry.runtime_data.async_set_ready_by(dt_util.utcnow() + timedelta(hours=2))
    await hass.async_block_till_done()

    assert entry.runtime_data.data.phase == PHASE_HEATING
    assert climate_calls.modes == ["heat"]
    assert len(late) == 1
    assert hass.states.get(entity_id(hass, "binary_sensor", entry, "on_time")).state == "off"


async def test_zero_hold_leaves_heater_on(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    set_climate(hass)
    set_temps(hass, water=20, outside=5)
    entry = await setup_entry(hass, make_entry(seed_rate=1.0, hold_hours=0))
    ready_by = dt_util.utcnow() + timedelta(hours=1)
    await entry.runtime_data.async_set_ready_by(ready_by)
    await hass.async_block_till_done()

    await _fire(hass, freezer, ready_by + timedelta(seconds=1))
    assert climate_calls.modes == ["heat"]
    assert entry.runtime_data.data.phase == PHASE_IDLE
    assert entry.runtime_data.data.enabled is False


async def test_target_change_replans_and_updates_heater(
    hass: HomeAssistant, climate_calls: ClimateCalls
):
    set_climate(hass)
    set_temps(hass, water=30, outside=5)
    entry = await setup_entry(hass, make_entry(seed_rate=2.0))
    controller = entry.runtime_data
    await controller.async_set_ready_by(dt_util.utcnow() + timedelta(hours=10), target=36)
    first = controller.plan.start

    await controller.async_set_target(40)
    assert controller.plan.start < first
    assert climate_calls.temperature == []

    await controller.async_set_ready_by(dt_util.utcnow() + timedelta(minutes=30))
    assert controller.data.phase == PHASE_HEATING
    await controller.async_set_target(39)
    assert climate_calls.temperature[-1].data["temperature"] == 39


async def test_disable_cancels_schedule(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    set_climate(hass)
    set_temps(hass, water=30, outside=5)
    entry = await setup_entry(hass, make_entry(seed_rate=2.0))
    controller = entry.runtime_data
    await controller.async_set_ready_by(dt_util.utcnow() + timedelta(hours=10))
    start = controller.plan.start

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id(hass, "switch", entry, "enabled")},
        blocking=True,
    )
    assert controller.plan is None
    await _fire(hass, freezer, start + timedelta(minutes=1))
    assert climate_calls.hvac_mode == []


async def test_replans_when_water_changes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    set_climate(hass)
    set_temps(hass, water=30, outside=5)
    entry = await setup_entry(hass, make_entry(seed_rate=2.0))
    controller = entry.runtime_data
    await controller.async_set_ready_by(dt_util.utcnow() + timedelta(hours=10))
    first = controller.plan.start

    hass.states.async_set("sensor.hot_tub_water", "26")
    await hass.async_block_till_done()
    # The debouncer runs the first call immediately.
    assert controller.plan.start < first - timedelta(hours=1)


async def test_no_plan_without_readings(hass: HomeAssistant, climate_calls: ClimateCalls):
    set_climate(hass)
    set_temps(hass, water=None, outside=5)
    entry = await setup_entry(hass, make_entry())
    await entry.runtime_data.async_set_ready_by(dt_util.utcnow() + timedelta(hours=5))
    assert entry.runtime_data.plan is None
    assert climate_calls.hvac_mode == []


async def test_forecast_is_used(hass: HomeAssistant, climate_calls: ClimateCalls):
    set_climate(hass)
    set_temps(hass, water=30, outside=10)
    now = dt_util.utcnow()
    calls = mock_forecast(hass, [(now + timedelta(hours=h), -10.0) for h in range(1, 24)])
    entry = await setup_entry(hass, make_entry(weather=WEATHER))
    controller = entry.runtime_data
    assert calls[0].data == {"entity_id": WEATHER, "type": "hourly"}
    assert len(controller.forecast) == 23
    assert controller.outside_at(now)(now + timedelta(hours=3)) == -10.0


async def test_missed_ready_by_disables(hass: HomeAssistant, climate_calls: ClimateCalls):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    controller = entry.runtime_data
    controller.data.enabled = True
    controller.data.ready_by = dt_util.utcnow() - timedelta(minutes=5)
    await controller.async_replan()
    assert controller.data.enabled is False
    assert climate_calls.hvac_mode == []


async def test_learns_sessions_from_climate(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, climate_calls: ClimateCalls
):
    set_climate(hass, mode="heat", action="idle")
    set_temps(hass, water=30, outside=5)
    entry = await setup_entry(hass, make_entry())
    controller = entry.runtime_data

    set_climate(hass, mode="heat", action="heating")
    await hass.async_block_till_done()
    freezer.tick(timedelta(hours=2))
    hass.states.async_set("sensor.hot_tub_water", "34")
    set_climate(hass, mode="heat", action="idle")
    await hass.async_block_till_done()

    assert len(controller.data.sessions) == 1
    assert controller.data.sessions[0].rate == 2.0
    assert hass.states.get(entity_id(hass, "sensor", entry, "sessions_learned")).state == "1"
