from datetime import timedelta
from unittest.mock import patch

from conftest import make_entry, set_climate, set_temps
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ready_by.const import CONF_IMPORT_HISTORY
from custom_components.ready_by.model import CoolingSample, Session


async def test_history_imported_once_when_recorder_loaded(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    hass.config.components.add("recorder")
    start = dt_util.utcnow() - timedelta(days=2)
    loaded = (
        [Session(start, start + timedelta(hours=2), 30, 34, 5)],
        [CoolingSample(start - timedelta(hours=8), start, 36, 30, 5)],
    )
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_IMPORT_HISTORY: True}
    )
    with patch(
        "custom_components.ready_by.planner.async_load_history", return_value=loaded
    ) as load:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert load.call_count == 1
        controller = entry.runtime_data
        assert len(controller.data.sessions) == 1
        assert controller.cooling_n == 1
        assert controller.data.history_imported

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert load.call_count == 1


async def test_history_failure_is_logged(hass: HomeAssistant, caplog):
    set_climate(hass)
    set_temps(hass)
    hass.config.components.add("recorder")
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_IMPORT_HISTORY: True}
    )
    with patch(
        "custom_components.ready_by.planner.async_load_history", side_effect=RuntimeError("db")
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert "Could not import recorder history" in caplog.text
    assert not entry.runtime_data.data.history_imported


async def test_reset_model_relearns_from_history(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    hass.config.components.add("recorder")
    start = dt_util.utcnow() - timedelta(days=2)
    loaded = ([Session(start, start + timedelta(hours=2), 30, 34, 5, "boost")], [])
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_IMPORT_HISTORY: True}
    )
    with patch(
        "custom_components.ready_by.planner.async_load_history", return_value=loaded
    ) as load:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        controller = entry.runtime_data
        controller.data.sessions.append(Session(start, start + timedelta(hours=1), 30, 32, 5))
        await controller.async_reset_model()
        assert load.call_count == 2
    assert [s.mode for s in controller.data.sessions] == ["boost"]
