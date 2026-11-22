from conftest import CLIMATE, OUTSIDE, WATER, make_entry, set_climate, set_temps, setup_entry
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ready_by.const import (
    CONF_BUFFER_MINUTES,
    CONF_CLIMATE,
    CONF_HOLD_HOURS,
    CONF_IMPORT_HISTORY,
    CONF_OUTSIDE,
    CONF_POWER,
    CONF_POWER_THRESHOLD,
    CONF_SEED_RATE,
    CONF_WATER,
    CONF_WEATHER,
    DOMAIN,
)


async def test_user_flow_creates_entry(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Spa", CONF_CLIMATE: CLIMATE, CONF_WATER: WATER, CONF_OUTSIDE: OUTSIDE},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Spa"
    assert result["result"].unique_id == CLIMATE
    assert result["options"][CONF_IMPORT_HISTORY] is True


async def test_duplicate_heater_aborts(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    await setup_entry(hass, make_entry())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Again", CONF_CLIMATE: CLIMATE, CONF_OUTSIDE: OUTSIDE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_and_reloads(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_BUFFER_MINUTES: 30,
            CONF_SEED_RATE: 1.5,
            CONF_HOLD_HOURS: 0,
            CONF_IMPORT_HISTORY: False,
            CONF_POWER: "sensor.heat_pump_power",
            CONF_POWER_THRESHOLD: 250,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.sources.power_id == "sensor.heat_pump_power"
    assert entry.runtime_data.sources.power_threshold == 250
    assert entry.runtime_data.seed_rate == 1.5
    assert entry.runtime_data.buffer.total_seconds() == 1800


async def test_options_set_and_clear_weather(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    assert entry.runtime_data.weather_id is None
    base = {
        CONF_BUFFER_MINUTES: 15,
        CONF_SEED_RATE: 1.0,
        CONF_HOLD_HOURS: 2,
        CONF_IMPORT_HISTORY: False,
        CONF_POWER_THRESHOLD: 100,
    }

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, CONF_WEATHER: "weather.home"}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.weather_id == "weather.home"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], base)
    await hass.async_block_till_done()
    assert entry.runtime_data.weather_id is None
    assert entry.runtime_data.sources.power_id is None


async def test_options_weather_overrides_setup_choice(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry(weather="weather.setup"))
    assert entry.runtime_data.weather_id == "weather.setup"
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_WEATHER: None})
    await hass.async_block_till_done()
    assert entry.runtime_data.weather_id is None
