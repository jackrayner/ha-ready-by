from conftest import CLIMATE, OUTSIDE, POWER, WATER, make_entry, set_climate, set_temps, setup_entry
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ready_by.const import (
    CONF_BUFFER_MINUTES,
    CONF_CLIMATE,
    CONF_IMPORT_HISTORY,
    CONF_OUTSIDE,
    CONF_POWER,
    CONF_POWER_THRESHOLD,
    CONF_SEED_RATE,
    CONF_WATER,
    CONF_WEATHER,
    DOMAIN,
)

SETTINGS = {
    CONF_POWER_THRESHOLD: 100,
    CONF_BUFFER_MINUTES: 15,
    CONF_SEED_RATE: 1.0,
    CONF_IMPORT_HISTORY: False,
}


async def _options(hass: HomeAssistant, entry, user_input: dict):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    await hass.async_block_till_done()
    return result


async def test_user_flow_creates_entry(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Spa",
            CONF_CLIMATE: CLIMATE,
            CONF_WATER: WATER,
            CONF_OUTSIDE: OUTSIDE,
            CONF_POWER: POWER,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Spa"
    assert result["result"].unique_id == CLIMATE
    assert result["data"][CONF_POWER] == POWER
    assert result["data"][CONF_WEATHER] is None
    assert result["options"][CONF_IMPORT_HISTORY] is True
    assert result["options"][CONF_POWER_THRESHOLD] == 100


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


async def test_options_edit_settings_and_entities(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    result = await _options(
        hass,
        entry,
        {
            CONF_CLIMATE: CLIMATE,
            CONF_WATER: WATER,
            CONF_OUTSIDE: "sensor.garden",
            CONF_WEATHER: "weather.home",
            CONF_POWER: POWER,
            **SETTINGS,
            CONF_BUFFER_MINUTES: 30,
            CONF_POWER_THRESHOLD: 250,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    controller = entry.runtime_data
    assert controller.buffer.total_seconds() == 1800
    assert controller.outside_id == "sensor.garden"
    assert controller.weather_id == "weather.home"
    assert controller.sources.power_id == POWER
    assert controller.sources.power_threshold == 250
    assert CONF_POWER not in entry.options


async def test_options_clear_optional_entities(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry(weather="weather.home", power=POWER))
    # Cleared selectors are simply missing from the submitted form.
    await _options(hass, entry, {CONF_CLIMATE: CLIMATE, CONF_OUTSIDE: OUTSIDE, **SETTINGS})
    controller = entry.runtime_data
    assert controller.weather_id is None
    assert controller.water_id is None
    assert controller.sources.power_id is None


async def test_options_change_heater_updates_unique_id(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    await _options(
        hass, entry, {CONF_CLIMATE: "climate.new_pump", CONF_OUTSIDE: OUTSIDE, **SETTINGS}
    )
    assert entry.unique_id == "climate.new_pump"
    assert entry.runtime_data.climate_id == "climate.new_pump"


async def test_options_reject_heater_used_by_another_entry(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    MockConfigEntry(domain=DOMAIN, unique_id="climate.other", data={}).add_to_hass(hass)
    entry = await setup_entry(hass, make_entry())
    result = await _options(
        hass, entry, {CONF_CLIMATE: "climate.other", CONF_OUTSIDE: OUTSIDE, **SETTINGS}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CLIMATE: "already_configured"}
    assert entry.unique_id == CLIMATE
