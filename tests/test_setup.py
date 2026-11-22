from datetime import timedelta

from conftest import ClimateCalls, entity_id, make_entry, set_climate, set_temps, setup_entry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ready_by.const import (
    CONF_POWER,
    PHASE_HEATING,
    PHASE_SCHEDULED,
)
from custom_components.ready_by.diagnostics import async_get_config_entry_diagnostics
from custom_components.ready_by.model import Session


async def test_setup_creates_entities_and_unloads(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    for platform, key in [
        ("datetime", "ready_by"),
        ("switch", "enabled"),
        ("number", "target_temperature"),
        ("binary_sensor", "on_time"),
        ("sensor", "status"),
        ("sensor", "start_time"),
        ("sensor", "expected_ready"),
        ("sensor", "heat_duration"),
        ("sensor", "heating_rate"),
        ("sensor", "sessions_learned"),
        ("sensor", "fit_r2"),
        ("sensor", "cooling_k"),
    ]:
        assert hass.states.get(entity_id(hass, platform, entry, key)) is not None
    rate = hass.states.get(entity_id(hass, "sensor", entry, "heating_rate"))
    assert float(rate.state) == 2.0
    assert rate.attributes["kind"] == "seed"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_state_survives_reload(hass: HomeAssistant, climate_calls: ClimateCalls):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    controller = entry.runtime_data
    start = dt_util.utcnow() - timedelta(days=1)
    await controller.async_import([Session(start, start + timedelta(hours=2), 30, 34, 5)], [])
    ready_by = dt_util.utcnow() + timedelta(hours=10)
    await controller.async_set_ready_by(ready_by, target=37)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    controller = entry.runtime_data
    assert len(controller.data.sessions) == 1
    assert controller.data.ready_by == ready_by
    assert controller.data.target == 37
    assert controller.data.phase == PHASE_SCHEDULED


async def test_heating_resumes_after_reload(hass: HomeAssistant, climate_calls: ClimateCalls):
    set_climate(hass)
    set_temps(hass, water=20)
    entry = await setup_entry(hass, make_entry(seed_rate=1.0))
    await entry.runtime_data.async_set_ready_by(dt_util.utcnow() + timedelta(hours=2))
    assert entry.runtime_data.data.phase == PHASE_HEATING

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    controller = entry.runtime_data
    assert controller.data.phase == PHASE_HEATING
    assert controller._ready_unsub is not None
    assert climate_calls.modes == ["heat"]


async def test_datetime_and_number_entities_drive_controller(
    hass: HomeAssistant, climate_calls: ClimateCalls
):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    when = (dt_util.now() + timedelta(hours=12)).replace(microsecond=0)
    await hass.services.async_call(
        "datetime",
        "set_value",
        {"entity_id": entity_id(hass, "datetime", entry, "ready_by"), "datetime": when},
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id(hass, "number", entry, "target_temperature"), "value": 39},
        blocking=True,
    )
    controller = entry.runtime_data
    assert controller.data.ready_by == dt_util.as_utc(when)
    assert controller.data.target == 39
    assert controller.data.enabled
    assert hass.states.get(entity_id(hass, "switch", entry, "enabled")).state == "on"
    assert hass.states.get(entity_id(hass, "sensor", entry, "start_time")).state != "unknown"


async def test_diagnostics(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["model"]["kind"] == "seed"
    assert diag["water"] == 30.0
    assert diag["outside"] == 5.0


async def test_changing_sources_relearns(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    start = dt_util.utcnow() - timedelta(days=1)
    await entry.runtime_data.async_import(
        [Session(start, start + timedelta(hours=2), 30, 34, 5)], []
    )
    entry.runtime_data.data.history_imported = True

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_POWER: "sensor.heat_pump_power"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    controller = entry.runtime_data
    # Discarded, then relearned (from an empty archive, with recorder import off).
    assert controller.data.sessions == []
    assert controller.data.history_imported is True

    # Reloading with the same sources keeps what has been learned since.
    await controller.async_import([Session(start, start + timedelta(hours=2), 30, 34, 5)], [])
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(entry.runtime_data.data.sessions) == 1
